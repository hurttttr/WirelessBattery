# -*- coding: utf-8 -*-
"""打包成 GUI 子系统后，怎么让托盘不弹黑窗、命令行还能用。

## 为什么是 GUI 子系统（`--windowed`）

console 子系统的 exe 双击时会被系统配一个控制台，就是那个黑窗。它没法在
Python 代码里"提前"消掉——窗口在解释器起来之前就已经画出来了，只能事后
`SW_HIDE`，于是每次双击都要闪一下（实测目录版约 0.1 秒，单文件版要 1~2 秒，
因为单文件版还得先解包）。

改成 GUI 子系统后**根本不会有控制台**，代价是从终端启动时 `cmd` 不等它跑完：
提示符立刻回来，`--once` 的输出会打在提示符后面。写脚本靠退出码判断的场合
会受影响，人肉看输出没问题。

## 那命令行怎么办

GUI 子系统下 Python 的 sys.stdout 是 None，print 出去什么也没有。所以：

    attach_parent()

挂到父进程的控制台上（`AttachConsole(ATTACH_PARENT_PROCESS)`），再从
`GetStdHandle` 把 stdout/stderr 接回来。用句柄而不是 `CONOUT$`，顺带把
`> out.txt` 这种重定向也一起支持了。

双击 / 开机自启时父进程是 explorer，挂不上，安静跑就行——正好是我们要的。

## 还留着 hide_if_ours

给 console 子系统的构建兜底（万一以后有人改回 `--console`）。
GUI 子系统下没有控制台窗口，它是个空操作。
"""

import ctypes
import locale
import os
import sys

SW_HIDE = 0

#: 带这些开关就是要看输出的：GUI 子系统下要先挂到父控制台，否则 print 全丢。
#: 只放“自己会打印”的开关；`--tray` `--serve` `--interval` 这类修饰项不在其中。
_OUTPUT_FLAGS = frozenset((
    "--once", "--selftest", "--status", "--watch", "--capture",
    "--make-plugin", "--install", "--uninstall", "--make-links",
    "--debug", "--debug-ui", "--version", "-h", "--help",
))

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_MAX_CONSOLE_PROCS = 32
_ATTACH_PARENT_PROCESS = 0xFFFFFFFF
_STD_OUTPUT_HANDLE = 0xFFFFFFF5        # -11
_STD_ERROR_HANDLE = 0xFFFFFFF4         # -12
_INVALID_HANDLE = ctypes.c_void_p(-1).value


def _k32():
    """用到的 kernel32 函数。

    在 x64 上凡是返回句柄的都要显式声明 restype，否则被截成 32 位，
    后面拿去调用就是残缺句柄、静默失败（这里踩过两次）。
    """
    k32 = ctypes.windll.kernel32
    k32.GetConsoleWindow.restype = ctypes.c_void_p
    k32.GetConsoleProcessList.restype = ctypes.c_uint32
    k32.GetConsoleProcessList.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32]
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    k32.QueryFullProcessImageNameW.restype = ctypes.c_bool
    k32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    k32.CloseHandle.restype = ctypes.c_bool
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.AttachConsole.restype = ctypes.c_bool
    k32.AttachConsole.argtypes = [ctypes.c_uint32]
    k32.GetStdHandle.restype = ctypes.c_void_p
    k32.GetStdHandle.argtypes = [ctypes.c_uint32]
    k32.GetConsoleOutputCP.restype = ctypes.c_uint32
    k32.GetConsoleMode.restype = ctypes.c_bool
    k32.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    return k32


def _user32():
    """ShowWindow 在 user32，不在 kernel32 —— 这里踩过一次。"""
    u32 = ctypes.windll.user32
    u32.ShowWindow.restype = ctypes.c_bool
    u32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return u32


def _image_of(pid):
    """某个 pid 的可执行文件全路径；拿不到返回 ""。"""
    k32 = _k32()
    handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_uint32(1024)
        if not k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value
    finally:
        k32.CloseHandle(handle)


# ---------------------------------------------------------------- 命令行输出

def _is_console_handle(handle):
    """这个标准句柄背后是不是真的控制台（而不是管道 / 文件）。"""
    mode = ctypes.c_uint32()
    return bool(_k32().GetConsoleMode(handle, ctypes.byref(mode)))


def _stream_encoding(handle):
    """按句柄类型挑编码。

    真控制台：用控制台代码页（中文机器 936），写 UTF-8 会整段乱码。
    管道 / 文件：别管控制台代码页了，跟随 PYTHONUTF8 / 系统 locale，
                否则 `> out.txt` 出来的文件编码会莫名其妙。
    """
    if _is_console_handle(handle):
        cp = _k32().GetConsoleOutputCP()
        return "utf-8" if not cp or cp == 65001 else "cp%d" % cp
    if getattr(sys.flags, "utf8_mode", 0):
        return "utf-8"
    return locale.getpreferredencoding(False) or "utf-8"


def _bind_streams():
    """把 stdout/stderr 接回本进程的真实标准句柄。

    GUI 子系统下 sys.stdout 是 None。挂上父控制台之后必须自己重建，
    否则 print 依旧石沉大海。走句柄而不是 `CONOUT$`，`> out.txt` 也能用。
    """
    import msvcrt
    k32 = _k32()
    for name, which in (("stdout", _STD_OUTPUT_HANDLE), ("stderr", _STD_ERROR_HANDLE)):
        handle = k32.GetStdHandle(which)
        if not handle or handle == _INVALID_HANDLE:
            continue
        encoding = _stream_encoding(handle)
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)
        except (OSError, OverflowError):
            continue
        try:
            stream = open(fd, "w", encoding=encoding, errors="replace", buffering=1)
        except OSError:
            continue
        setattr(sys, name, stream)


def attach_parent():
    """挂到父进程的控制台上，让命令行模式还能打印。返回是否挂上。

    双击 / 开机自启时父进程是 explorer，没有控制台可挂 —— 返回 False，
    什么都不做，正好安静地跑托盘。
    """
    k32 = _k32()
    if k32.GetConsoleWindow():
        return True                     # 已经有控制台，直接用
    if not k32.AttachConsole(_ATTACH_PARENT_PROCESS):
        return False
    _bind_streams()
    return True


# ---------------------------------------------------------------- 控制台窗口

def quiet_argv(argv=None):
    """这次启动有没有东西要显示？没有就是托盘模式。"""
    argv = sys.argv[1:] if argv is None else argv
    return not any(a in _OUTPUT_FLAGS for a in argv)


def console_is_ours():
    """这个控制台是"系统为本程序新建的"吗？

    只数进程个数不够：PyInstaller 的单文件版先起一个引导进程解包，再拉起子
    进程，两个进程共用同一个控制台。所以看的是"除我们之外还有没有别的程序"：
    全都是同一个 exe 才算自己的；混进来一个别的（cmd / powershell）就不碰。
    """
    k32 = _k32()
    if not k32.GetConsoleWindow():
        return False
    pids = (ctypes.c_uint32 * _MAX_CONSOLE_PROCS)()
    n = k32.GetConsoleProcessList(pids, _MAX_CONSOLE_PROCS)
    if not 0 < n <= _MAX_CONSOLE_PROCS:
        return False                    # 0 或超出缓冲（数量异常）→ 保守起见不动
    mine = os.path.normcase(os.path.abspath(sys.executable))
    me = os.getpid()
    for i in range(n):
        pid = pids[i]
        if pid == me:
            continue
        other = _image_of(pid)
        if not other or os.path.normcase(os.path.abspath(other)) != mine:
            return False
    return True


def hide_if_ours():
    """藏掉系统给我们新建的控制台。返回是否真的藏了。

    GUI 子系统下没有控制台窗口，这里直接返回 False。留着是给 console 子系统
    的构建兜底。
    """
    if not console_is_ours():
        return False
    _user32().ShowWindow(_k32().GetConsoleWindow(), SW_HIDE)
    return True
