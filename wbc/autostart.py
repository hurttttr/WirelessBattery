# -*- coding: utf-8 -*-
"""开机自启：在「启动」文件夹里放一个 .lnk。

为什么不用注册表 Run：
    HKCU\\...\\Run 虽然也能被任务管理器的「启动」标签识别，但启动文件夹里的
    快捷方式更直观、可以直接看见、双击就能验证，删除也更干净。

创建 .lnk 全走 ctypes COM：
    CoCreateInstance(CLSID_ShellLink) → IShellLinkW → QueryInterface(IPersistFile) → Save()

IShellLinkW 的 vtable 下标（IUnknown 占 0/1/2）：
    GetPath=3  GetIDList=4  SetIDList=5  GetDescription=6  SetDescription=7
    GetWorkingDirectory=8  SetWorkingDirectory=9  GetArguments=10  SetArguments=11
    GetHotkey=12  SetHotkey=13  GetShowCmd=14  SetShowCmd=15  GetIconLocation=16
    SetIconLocation=17  SetRelativePath=18  Resolve=19  SetPath=20
IPersistFile：
    GetClassID=3  IsDirty=4  Load=5  Save=6  SaveCompleted=7  GetCurFile=8
"""

import ctypes
import os
import sys

from . import logx
from . import settings
from . import win32 as wx
from .logx import dbg

CLSID_ShellLink = "{00021401-0000-0000-C000-000000000046}"
IID_IShellLinkW = "{000214F9-0000-0000-C000-000000000046}"
IID_IPersistFile = "{0000010B-0000-0000-C000-000000000046}"

LNK_NAME = "电量托盘.lnk"
DESCRIPTION = "无线外设电量托盘（ATK 鼠标 / DARKRIM 耳机）"

# vtable 下标
SL_GET_PATH = 3
SL_SET_DESCRIPTION = 7
SL_GET_WORKINGDIR = 8
SL_SET_WORKINGDIR = 9
SL_GET_ARGUMENTS = 10
SL_SET_ARGUMENTS = 11
SL_SET_ICONLOCATION = 17
SL_SET_PATH = 20
PF_LOAD = 5
PF_SAVE = 6

HRESULT = ctypes.c_long


def _vt(ptr, index, restype, *argtypes):
    """取 COM 对象第 index 个虚函数。"""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])


def _release(ptr):
    if ptr:
        try:
            _vt(ptr, 2, ctypes.c_ulong)(ptr)
        except Exception:
            pass


class ShellLinkError(OSError):
    pass


def _co_init():
    try:
        hr = wx.ole32.CoInitializeEx(None, wx.COINIT_APARTMENTTHREADED)
        # S_OK=0, S_FALSE=1, RPC_E_CHANGED_MODE=-2147417850
        return hr
    except Exception:
        return 0


def create_shortcut(lnk_path, target, arguments="", workdir="",
                    description="", icon_path=None, icon_index=0):
    """用纯 ctypes 创建 .lnk。返回 lnk_path。"""
    _co_init()
    clsid = wx.guid_from_string(CLSID_ShellLink)
    iid_link = wx.guid_from_string(IID_IShellLinkW)
    iid_pf = wx.guid_from_string(IID_IPersistFile)

    punk = ctypes.c_void_p()
    hr = wx.ole32.CoCreateInstance(
        ctypes.byref(clsid), None, wx.CLSCTX_INPROC_SERVER, ctypes.byref(iid_link),
        ctypes.byref(punk))
    if hr != 0 or not punk:
        raise ShellLinkError("CoCreateInstance(ShellLink) 失败 hr=0x%08X" % (hr & 0xFFFFFFFF))

    try:
        set_path = _vt(punk, SL_SET_PATH, HRESULT, ctypes.c_wchar_p)
        if set_path(punk, str(target)) != 0:
            raise ShellLinkError("IShellLink::SetPath 失败")

        if arguments:
            _vt(punk, SL_SET_ARGUMENTS, HRESULT, ctypes.c_wchar_p)(punk, arguments)
        if workdir:
            _vt(punk, SL_SET_WORKINGDIR, HRESULT, ctypes.c_wchar_p)(punk, workdir)
        if description:
            _vt(punk, SL_SET_DESCRIPTION, HRESULT, ctypes.c_wchar_p)(punk, description)
        if icon_path:
            _vt(punk, SL_SET_ICONLOCATION, HRESULT, ctypes.c_wchar_p, ctypes.c_int)(
                punk, str(icon_path), int(icon_index))

        pf = ctypes.c_void_p()
        hr = _vt(punk, 0, HRESULT, ctypes.POINTER(wx.GUID), ctypes.POINTER(ctypes.c_void_p))(
            punk, ctypes.byref(iid_pf), ctypes.byref(pf))
        if hr != 0 or not pf:
            raise ShellLinkError("QueryInterface(IPersistFile) 失败 hr=0x%08X" % (hr & 0xFFFFFFFF))
        try:
            save = _vt(pf, PF_SAVE, HRESULT, ctypes.c_wchar_p, wx.w.BOOL)
            hr = save(pf, str(lnk_path), True)
            if hr != 0:
                raise ShellLinkError("IPersistFile::Save 失败 hr=0x%08X" % (hr & 0xFFFFFFFF))
        finally:
            _release(pf)
    finally:
        _release(punk)
    return lnk_path


def read_shortcut(lnk_path):
    """读回 .lnk 的 target / arguments / workdir（自检与诊断用）。"""
    _co_init()
    clsid = wx.guid_from_string(CLSID_ShellLink)
    iid_link = wx.guid_from_string(IID_IShellLinkW)
    iid_pf = wx.guid_from_string(IID_IPersistFile)
    punk = ctypes.c_void_p()
    hr = wx.ole32.CoCreateInstance(
        ctypes.byref(clsid), None, wx.CLSCTX_INPROC_SERVER, ctypes.byref(iid_link),
        ctypes.byref(punk))
    if hr != 0 or not punk:
        raise ShellLinkError("CoCreateInstance 失败")
    out = {"target": "", "arguments": "", "workdir": ""}
    try:
        pf = ctypes.c_void_p()
        hr = _vt(punk, 0, HRESULT, ctypes.POINTER(wx.GUID), ctypes.POINTER(ctypes.c_void_p))(
            punk, ctypes.byref(iid_pf), ctypes.byref(pf))
        if hr != 0 or not pf:
            raise ShellLinkError("QueryInterface 失败")
        try:
            load = _vt(pf, PF_LOAD, HRESULT, ctypes.c_wchar_p, ctypes.c_ulong)
            hr = load(pf, str(lnk_path), 0)      # STGM_READ = 0
            if hr != 0:
                raise ShellLinkError("IPersistFile::Load 失败 hr=0x%08X" % (hr & 0xFFFFFFFF))
        finally:
            _release(pf)

        buf = ctypes.create_unicode_buffer(1024)
        _vt(punk, SL_GET_PATH, HRESULT, ctypes.c_wchar_p, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_ulong)(punk, buf, len(buf), None, 0)
        out["target"] = buf.value

        buf2 = ctypes.create_unicode_buffer(1024)
        _vt(punk, SL_GET_ARGUMENTS, HRESULT, ctypes.c_wchar_p, ctypes.c_int)(punk, buf2, len(buf2))
        out["arguments"] = buf2.value

        buf3 = ctypes.create_unicode_buffer(1024)
        _vt(punk, SL_GET_WORKINGDIR, HRESULT, ctypes.c_wchar_p, ctypes.c_int)(punk, buf3, len(buf3))
        out["workdir"] = buf3.value
    finally:
        _release(punk)
    return out


# ---------------------------------------------------------------- 启动项

def startup_lnk_path():
    return os.path.join(wx.startup_dir(), LNK_NAME)


def python_executable(windowed=True):
    """优先 pythonw.exe —— 双击/开机启动都不能弹黑窗。

    还要确认目标解释器真的能 import tkinter：有些精简版 Python 没有 tkinter，
    用它写进快捷方式的话，pythonw 下会静默失败，用户什么都看不到。
    """
    global _PY_CACHE
    if _PY_CACHE:
        return _PY_CACHE

    exe = os.path.abspath(sys.executable)
    first = os.path.join(os.path.dirname(exe), "pythonw.exe") if windowed else exe
    if not os.path.exists(first):
        first = exe
    if _tk_ok(first):
        _PY_CACHE = first
        return first

    for cand in _candidate_interpreters(windowed):
        if _tk_ok(cand):
            _PY_CACHE = cand
            return cand
    _PY_CACHE = first
    return first


_PY_CACHE = ""


def _tk_ok(exe):
    import subprocess
    try:
        proc = subprocess.run([exe, "-c", "import tkinter"],
                              capture_output=True, timeout=10,
                              creationflags=0x08000000)
        return proc.returncode == 0
    except Exception:
        return False


def _candidate_interpreters(windowed=True):
    import glob
    import subprocess
    name = "pythonw.exe" if windowed else "python.exe"
    cands = []
    try:
        proc = subprocess.run(["py", "-0p"], capture_output=True, text=True,
                              timeout=10, creationflags=0x08000000)
        for line in (proc.stdout or "").splitlines():
            parts = line.split()
            if parts and parts[-1].lower().endswith("python.exe"):
                cands.append(os.path.join(os.path.dirname(parts[-1]), name))
    except Exception:
        pass
    for pattern in (r"C:\Program Files\Python3*", r"C:\Python3*",
                    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python3*"),
                    r"D:\Program Files\Python3*"):
        for d in glob.glob(pattern):
            cands.append(os.path.join(d, name))
    seen = set()
    out = []
    for c in cands:
        key = c.lower()
        if key in seen or not os.path.exists(c):
            continue
        seen.add(key)
        out.append(c)
    return out


def script_path():
    # wbc/autostart.py -> 上一级就是项目目录
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "my_devices.py"))


# ---- 任务管理器的「启用/禁用」状态

_STARTUP_APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"


def _approved_state(name):
    """读 StartupApproved：返回 True(启用) / False(被任务管理器禁用) / None(没有记录)。

    任务管理器禁用某项时会在 StartupApproved\\StartupFolder 下写 12 字节，
    首字节最低位为 1 表示禁用。
    """
    try:
        import winreg
    except Exception:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_APPROVED, 0, winreg.KEY_READ) as k:
            try:
                data, _ = winreg.QueryValueEx(k, name)
            except FileNotFoundError:
                return None
        if isinstance(data, (bytes, bytearray)) and len(data) >= 1:
            return not bool(data[0] & 1)
        return None
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _clear_approved(name):
    try:
        import winreg
    except Exception:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_APPROVED, 0,
                            winreg.KEY_SET_VALUE) as k:
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass
    except OSError:
        pass


def state():
    """真实状态：'absent' / 'enabled' / 'disabled'（被任务管理器禁用）。"""
    path = startup_lnk_path()
    if not os.path.exists(path):
        return "absent"
    approved = _approved_state(LNK_NAME)
    if approved is False:
        return "disabled"
    return "enabled"


def is_enabled():
    return state() == "enabled"


def current_extra_args(path=None):
    """读出当前自启快捷方式里 `--tray` 之外的开关（比如 --serve）。

    面板上勾选/取消「开机自启」时会整体重写快捷方式，这里把额外参数原样带过去，
    免得用户手工加过的 `--serve` 被悄悄抹掉。
    """
    info = read_shortcut(path or startup_lnk_path())
    if not info:
        return ""
    args = (info.get("arguments") or "").replace('"', " ").split()
    keep = [a for a in args if a.startswith("--") and a != "--tray"]
    return (" " + " ".join(keep)) if keep else ""


def enable(extra_args=""):
    """创建启动快捷方式，并确保它没被任务管理器禁用过。

    extra_args 追加在 `--tray` 后面，例如 " --serve"。
    """
    path = startup_lnk_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    create_shortcut(
        path,
        target=python_executable(windowed=True),
        arguments='"%s" --tray%s' % (script_path(), extra_args or ""),
        workdir=os.path.dirname(script_path()),
        description=DESCRIPTION,
        icon_path=settings.app_icon_path(),
        icon_index=0,
    )
    _clear_approved(LNK_NAME)
    dbg("已创建启动项", path)
    return path


def disable():
    path = startup_lnk_path()
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as e:
        raise OSError("删除启动项失败：%s" % e)
    _clear_approved(LNK_NAME)
    dbg("已删除启动项", path)
    return path


def describe():
    path = startup_lnk_path()
    st = state()
    info = {"path": path, "state": st, "target": "", "arguments": ""}
    if os.path.exists(path):
        try:
            info.update(read_shortcut(path))
        except Exception as e:
            info["error"] = str(e)
    return info
