# -*- coding: utf-8 -*-
"""一份 exe 同时当托盘程序和命令行工具。

## 为什么是 console 子系统

只有 console 子系统的 exe 能让 `cmd` / PowerShell 等它跑完、拿到真实退出码。
windowed + AttachConsole 虽然一点都不闪窗，但 cmd 会在程序还没跑完时就把提示符
还给你：`--once` 的输出会打在提示符后面，退出码永远读到 0，脚本里全废。

## 那黑窗怎么办

console 子系统在双击 / 开机自启时会被系统配一个新控制台，看起来就是黑窗。
两条规则把它消掉：

1. 进程启动后判断控制台是不是「系统为本进程新建的」——
   `GetConsoleProcessList` 里只列得出自己，说明没有 shell 跟我们一起用，
   立刻 `SW_HIDE`。从 cmd / PowerShell 启动时列表里还有 shell，绝不碰。
2. 开机自启和桌面的 .lnk 用 `SW_HIDE` 创建（见 `autostart.enable`），
   连启动那一瞬间的闪烁都没有。

两条规则都不需要改系统设置，也不需要额外的 exe。
"""

import ctypes
import os
import sys

SW_HIDE = 0

#: 带这些开关就是要看输出的，藏了窗口用户就什么都看不见了。
#: 只放“自己会打印”的开关；`--tray` `--serve` `--interval` 这类修饰项不在其中。
_OUTPUT_FLAGS = frozenset((
    "--once", "--selftest", "--status", "--watch", "--capture",
    "--make-plugin", "--install", "--uninstall", "--make-links",
    "--debug", "--debug-ui", "--version", "-h", "--help",
))

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_MAX_CONSOLE_PROCS = 32


def _k32():
    """kernel32 的这几个函数。

    在 x64 上 GetConsoleWindow 返回的是指针，不声明 restype 会被截成 32 位，
    后面 ShowWindow 拿到一个残缺句柄，静默失败。
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


def quiet_argv(argv=None):
    """这次启动有没有东西要显示？没有就是托盘模式，可以藏窗口。"""
    argv = sys.argv[1:] if argv is None else argv
    return not any(a in _OUTPUT_FLAGS for a in argv)


def console_is_ours():
    """这个控制台是"系统为本程序新建的"吗？

    只数进程个数不够：PyInstaller 的单文件版先起一个引导进程解包，再拉起子
    进程，两个进程共用同一个控制台。按"个数 > 1 就是跟 shell 共享"判断，单
    文件版的黑窗永远藏不掉——这正是用户报的那个现象。

    所以改成看"除我们之外还有没有别的程序"：全都是同一个 exe 才算是自己的；
    只要混进来一个别的（cmd / powershell / explorer），就一根手指都不碰。
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
    """藏掉系统给我们新建的控制台。返回是否真的藏了。"""
    if not console_is_ours():
        return False
    _user32().ShowWindow(_k32().GetConsoleWindow(), SW_HIDE)
    return True
