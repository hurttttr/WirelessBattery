# -*- coding: utf-8 -*-
"""Win32 / COM 的 ctypes 基础层。

这里只做两件事：
1. 把 HID 相关（setupapi / hid.dll / kernel32）的结构体、函数原型声明齐全；
2. 提供几个小工具（GUID 解析、已知文件夹、DPI、窗口类）。

所有返回 HANDLE 的函数都必须显式声明 restype，否则在 64 位下会被截断成
32 位 int —— 这是 ctypes 最常见的崩溃来源。
"""

import ctypes
import os
from ctypes import wintypes as w

IS64 = ctypes.sizeof(ctypes.c_void_p) == 8

LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)

# ---------------------------------------------------------------- 常量

INVALID_HANDLE_VALUE = w.HANDLE(-1).value
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
FILE_ATTRIBUTE_NORMAL = 0x80
ERROR_IO_PENDING = 997
ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32
ERROR_NO_MORE_ITEMS = 259

DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010

WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258

HIDP_STATUS_SUCCESS = 0x00110000
HidP_Input = 0
HidP_Output = 1
HidP_Feature = 2

# 窗口消息
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_TIMER = 0x0113
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_APP = 0x8000

# 通知区域
NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIM_SETVERSION = 4
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_INFO = 0x10
NIF_SHOWTIP = 0x80
NOTIFYICON_VERSION_4 = 4

# GDI
DIB_RGB_COLORS = 0
BI_RGB = 0
BI_BITFIELDS = 3
LCS_sRGB = 0x73524742
SM_CXSMICON = 49
SM_CYSMICON = 50

# 已知文件夹 GUID（必须用字符串给出，内部按 split("-", 3) 解析）
FOLDERID_Desktop = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"
FOLDERID_Startup = "{B97D20BB-F46A-4C97-BA10-5E3608430854}"
FOLDERID_LocalAppData = "{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}"

CLSCTX_INPROC_SERVER = 0x1
COINIT_APARTMENTTHREADED = 0x2


# ---------------------------------------------------------------- GUID

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", w.DWORD),
        ("Data2", w.WORD),
        ("Data3", w.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __repr__(self):
        d4 = "".join("%02X" % b for b in self.Data4)
        return "{%08X-%04X-%04X-%s-%s}" % (self.Data1, self.Data2, self.Data3, d4[:4], d4[4:])


def guid_from_string(s):
    """把 "{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}" 解析成 GUID。

    注意：不能用 s.split("-") 直接得到 5 段后再拼——这里按需求用
    split("-", 3) 只切 3 刀，最后一节里还留着一个连字符，必须再拆一次。
    """
    if isinstance(s, GUID):
        return s
    body = s.strip().strip("{}")
    parts = body.split("-", 3)
    if len(parts) != 4:
        raise ValueError("非法 GUID 字符串: %r" % (s,))
    tail = parts[3].split("-")          # 最后一节里还有连字符
    if len(tail) != 2 or len(tail[0]) != 4 or len(tail[1]) != 12:
        raise ValueError("非法 GUID 字符串: %r" % (s,))
    g = GUID()
    g.Data1 = int(parts[0], 16)
    g.Data2 = int(parts[1], 16)
    g.Data3 = int(parts[2], 16)
    raw = bytes.fromhex(tail[0]) + bytes.fromhex(tail[1])
    for i in range(8):
        g.Data4[i] = raw[i]
    return g


# ---------------------------------------------------------------- SetupAPI

class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", w.DWORD),
        ("Reserved", ULONG_PTR),
    ]


class SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.DWORD),
        ("DevicePath", ctypes.c_wchar * 1),
    ]


# SP_DEVICE_INTERFACE_DETAIL_DATA_W：cbSize 在偏移 0，DevicePath 紧随其后在偏移 4
# （实测确认：x64 下 cbSize 必须写 8 —— 即 sizeof，但字符串仍然从 +4 开始，
#  写成 +8 会丢掉开头的 "\\?\" 两个字符，导致 CreateFileW 直接失败）。
DETAIL_CBSIZE = 8 if IS64 else 6
DETAIL_PATH_OFFSET = 4


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", w.ULONG),
        ("VendorID", w.USHORT),
        ("ProductID", w.USHORT),
        ("VersionNumber", w.USHORT),
    ]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", w.USHORT),
        ("UsagePage", w.USHORT),
        ("InputReportByteLength", w.USHORT),
        ("OutputReportByteLength", w.USHORT),
        ("FeatureReportByteLength", w.USHORT),
        ("Reserved", w.USHORT * 17),
        ("NumberLinkCollectionNodes", w.USHORT),
        ("NumberInputButtonCaps", w.USHORT),
        ("NumberInputValueCaps", w.USHORT),
        ("NumberInputDataIndices", w.USHORT),
        ("NumberOutputButtonCaps", w.USHORT),
        ("NumberOutputValueCaps", w.USHORT),
        ("NumberOutputDataIndices", w.USHORT),
        ("NumberFeatureButtonCaps", w.USHORT),
        ("NumberFeatureValueCaps", w.USHORT),
        ("NumberFeatureDataIndices", w.USHORT),
    ]


class _HIDP_USAGE_RANGE(ctypes.Structure):
    """HIDP_VALUE_CAPS 尾部 union 的 Range / NotRange 两个分支布局一致。"""
    _fields_ = [
        ("UsageMin", w.USHORT),
        ("UsageMax", w.USHORT),
        ("StringMin", w.USHORT),
        ("StringMax", w.USHORT),
        ("DesignatorMin", w.USHORT),
        ("DesignatorMax", w.USHORT),
        ("DataIndexMin", w.USHORT),
        ("DataIndexMax", w.USHORT),
    ]


class HIDP_VALUE_CAPS(ctypes.Structure):
    _fields_ = [
        ("UsagePage", w.USHORT),
        ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte),
        ("BitField", w.USHORT),
        ("LinkCollection", w.USHORT),
        ("LinkUsage", w.USHORT),
        ("LinkUsagePage", w.USHORT),
        ("IsRange", ctypes.c_ubyte),
        ("IsStringRange", ctypes.c_ubyte),
        ("IsDesignatorRange", ctypes.c_ubyte),
        ("IsAbsolute", ctypes.c_ubyte),
        ("HasNull", ctypes.c_ubyte),
        ("Reserved", ctypes.c_ubyte),
        ("BitSize", w.USHORT),
        ("ReportCount", w.USHORT),
        ("Reserved2", w.USHORT * 5),
        ("UnitsExp", w.ULONG),
        ("Units", w.ULONG),
        ("LogicalMin", ctypes.c_long),
        ("LogicalMax", ctypes.c_long),
        ("PhysicalMin", ctypes.c_long),
        ("PhysicalMax", ctypes.c_long),
        ("u", _HIDP_USAGE_RANGE),
    ]


# ---------------------------------------------------------------- 内核对象

class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ULONG_PTR),
        ("InternalHigh", ULONG_PTR),
        ("Offset", w.DWORD),
        ("OffsetHigh", w.DWORD),
        ("hEvent", w.HANDLE),
    ]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", w.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", w.BOOL),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", w.HWND),
        ("message", w.UINT),
        ("wParam", w.WPARAM),
        ("lParam", w.LPARAM),
        ("time", w.DWORD),
        ("pt", w.POINT),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", w.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", w.HINSTANCE),
        ("hIcon", w.HICON),
        ("hCursor", w.HANDLE),
        ("hbrBackground", w.HBRUSH),
        ("lpszMenuName", w.LPCWSTR),
        ("lpszClassName", w.LPCWSTR),
    ]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.DWORD),
        ("hWnd", w.HWND),
        ("uID", w.UINT),
        ("uFlags", w.UINT),
        ("uCallbackMessage", w.UINT),
        ("hIcon", w.HICON),
        ("szTip", w.WCHAR * 128),
        ("dwState", w.DWORD),
        ("dwStateMask", w.DWORD),
        ("szInfo", w.WCHAR * 256),
        ("uVersion", w.UINT),
        ("szInfoTitle", w.WCHAR * 64),
        ("dwInfoFlags", w.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", w.HICON),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", w.BOOL),
        ("xHotspot", w.DWORD),
        ("yHotspot", w.DWORD),
        ("hbmMask", w.HBITMAP),
        ("hbmColor", w.HBITMAP),
    ]


class BITMAPV5HEADER(ctypes.Structure):
    _fields_ = [
        ("bV5Size", w.DWORD),
        ("bV5Width", ctypes.c_long),
        ("bV5Height", ctypes.c_long),
        ("bV5Planes", w.WORD),
        ("bV5BitCount", w.WORD),
        ("bV5Compression", w.DWORD),
        ("bV5SizeImage", w.DWORD),
        ("bV5XPelsPerMeter", ctypes.c_long),
        ("bV5YPelsPerMeter", ctypes.c_long),
        ("bV5ClrUsed", w.DWORD),
        ("bV5ClrImportant", w.DWORD),
        ("bV5RedMask", w.DWORD),
        ("bV5GreenMask", w.DWORD),
        ("bV5BlueMask", w.DWORD),
        ("bV5AlphaMask", w.DWORD),
        ("bV5CSType", w.DWORD),
        ("bV5Endpoints", ctypes.c_byte * 36),
        ("bV5GammaRed", w.DWORD),
        ("bV5GammaGreen", w.DWORD),
        ("bV5GammaBlue", w.DWORD),
        ("bV5Intent", w.DWORD),
        ("bV5ProfileData", w.DWORD),
        ("bV5ProfileSize", w.DWORD),
        ("bV5Reserved", w.DWORD),
    ]


# ---------------------------------------------------------------- 函数原型

kernel32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
kernel32.CreateFileW.restype = w.HANDLE
kernel32.CloseHandle.argtypes = [w.HANDLE]
kernel32.CloseHandle.restype = w.BOOL

kernel32.ReadFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(OVERLAPPED)]
kernel32.ReadFile.restype = w.BOOL
kernel32.WriteFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(OVERLAPPED)]
kernel32.WriteFile.restype = w.BOOL

kernel32.CreateEventW.argtypes = [ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
kernel32.CreateEventW.restype = w.HANDLE
kernel32.SetEvent.argtypes = [w.HANDLE]
kernel32.SetEvent.restype = w.BOOL
kernel32.ResetEvent.argtypes = [w.HANDLE]
kernel32.ResetEvent.restype = w.BOOL
kernel32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
kernel32.WaitForSingleObject.restype = w.DWORD
kernel32.CancelIoEx.argtypes = [w.HANDLE, ctypes.POINTER(OVERLAPPED)]
kernel32.CancelIoEx.restype = w.BOOL
kernel32.GetOverlappedResult.argtypes = [w.HANDLE, ctypes.POINTER(OVERLAPPED), ctypes.POINTER(w.DWORD), w.BOOL]
kernel32.GetOverlappedResult.restype = w.BOOL
kernel32.GetModuleHandleW.argtypes = [w.LPCWSTR]
kernel32.GetModuleHandleW.restype = w.HMODULE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
kernel32.CreateMutexW.restype = w.HANDLE
kernel32.ReleaseMutex.argtypes = [w.HANDLE]
kernel32.ReleaseMutex.restype = w.BOOL
kernel32.GetTickCount64.argtypes = []
kernel32.GetTickCount64.restype = ctypes.c_ulonglong

setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), w.LPCWSTR, w.HWND, w.DWORD]
setupapi.SetupDiGetClassDevsW.restype = w.HANDLE
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    w.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), w.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiEnumDeviceInterfaces.restype = w.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    w.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p, w.DWORD,
    ctypes.POINTER(w.DWORD), ctypes.c_void_p]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = w.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [w.HANDLE]
setupapi.SetupDiDestroyDeviceInfoList.restype = w.BOOL

hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
hid.HidD_GetHidGuid.restype = None
hid.HidD_GetAttributes.argtypes = [w.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetAttributes.restype = ctypes.c_ubyte
hid.HidD_GetPreparsedData.argtypes = [w.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_GetPreparsedData.restype = ctypes.c_ubyte
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidD_FreePreparsedData.restype = ctypes.c_ubyte
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidP_GetCaps.restype = ctypes.c_long
hid.HidP_GetValueCaps.argtypes = [
    ctypes.c_int, ctypes.POINTER(HIDP_VALUE_CAPS), ctypes.POINTER(w.USHORT), ctypes.c_void_p]
hid.HidP_GetValueCaps.restype = ctypes.c_long
hid.HidD_GetFeature.argtypes = [w.HANDLE, ctypes.c_void_p, w.ULONG]
hid.HidD_GetFeature.restype = ctypes.c_ubyte
hid.HidD_SetFeature.argtypes = [w.HANDLE, ctypes.c_void_p, w.ULONG]
hid.HidD_SetFeature.restype = ctypes.c_ubyte
hid.HidD_GetProductString.argtypes = [w.HANDLE, ctypes.c_void_p, w.ULONG]
hid.HidD_GetProductString.restype = ctypes.c_ubyte
hid.HidD_GetManufacturerString.argtypes = [w.HANDLE, ctypes.c_void_p, w.ULONG]
hid.HidD_GetManufacturerString.restype = ctypes.c_ubyte
hid.HidD_GetSerialNumberString.argtypes = [w.HANDLE, ctypes.c_void_p, w.ULONG]
hid.HidD_GetSerialNumberString.restype = ctypes.c_ubyte

user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = w.WORD
user32.CreateWindowExW.argtypes = [
    w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = w.HWND
user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.DestroyWindow.argtypes = [w.HWND]
user32.DestroyWindow.restype = w.BOOL
user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.PostMessageW.restype = w.BOOL
user32.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.SendMessageW.restype = LRESULT
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostQuitMessage.restype = None
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), w.HWND, w.UINT, w.UINT]
user32.GetMessageW.restype = w.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.TranslateMessage.restype = w.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.SetTimer.argtypes = [w.HWND, ULONG_PTR, w.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ULONG_PTR
user32.KillTimer.argtypes = [w.HWND, ULONG_PTR]
user32.KillTimer.restype = w.BOOL
user32.FindWindowW.argtypes = [w.LPCWSTR, w.LPCWSTR]
user32.FindWindowW.restype = w.HWND
user32.RegisterWindowMessageW.argtypes = [w.LPCWSTR]
user32.RegisterWindowMessageW.restype = w.UINT
user32.LoadCursorW.argtypes = [w.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = w.HANDLE
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.SetProcessDPIAware.argtypes = []
user32.SetProcessDPIAware.restype = w.BOOL
user32.GetDC.argtypes = [w.HWND]
user32.GetDC.restype = w.HDC
user32.ReleaseDC.argtypes = [w.HWND, w.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.DestroyIcon.argtypes = [w.HICON]
user32.DestroyIcon.restype = w.BOOL
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.CreateIconIndirect.restype = w.HICON
user32.SetForegroundWindow.argtypes = [w.HWND]
user32.SetForegroundWindow.restype = w.BOOL

gdi32.CreateCompatibleDC.argtypes = [w.HDC]
gdi32.CreateCompatibleDC.restype = w.HDC
gdi32.DeleteDC.argtypes = [w.HDC]
gdi32.DeleteDC.restype = w.BOOL
gdi32.CreateDIBSection.argtypes = [
    w.HDC, ctypes.POINTER(BITMAPV5HEADER), w.UINT, ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.DWORD]
gdi32.CreateDIBSection.restype = w.HBITMAP
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, w.UINT, w.UINT, ctypes.c_void_p]
gdi32.CreateBitmap.restype = w.HBITMAP
gdi32.DeleteObject.argtypes = [w.HGDIOBJ]
gdi32.DeleteObject.restype = w.BOOL
gdi32.GetDeviceCaps.argtypes = [w.HDC, ctypes.c_int]
gdi32.GetDeviceCaps.restype = ctypes.c_int

shell32.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = w.BOOL
shell32.SHGetKnownFolderPath.argtypes = [
    ctypes.POINTER(GUID), w.DWORD, w.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
shell32.SHGetKnownFolderPath.restype = ctypes.c_long
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
ole32.CoTaskMemFree.restype = None
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, w.DWORD]
ole32.CoInitializeEx.restype = ctypes.c_long
ole32.CoUninitialize.argtypes = []
ole32.CoUninitialize.restype = None
ole32.CoCreateInstance.argtypes = [
    ctypes.POINTER(GUID), ctypes.c_void_p, w.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
ole32.CoCreateInstance.restype = ctypes.c_long

LOGPIXELSX = 88


# ---------------------------------------------------------------- 小工具

def last_error():
    return ctypes.get_last_error()


def get_dpi_scale():
    """返回系统 DPI 缩放（1.0 = 96 DPI）。需在 SetProcessDPIAware 之后调用。"""
    hdc = user32.GetDC(None)
    try:
        dpi = gdi32.GetDeviceCaps(hdc, LOGPIXELSX) or 96
    finally:
        user32.ReleaseDC(None, hdc)
    return dpi / 96.0


def small_icon_size():
    """通知区域图标的真实像素尺寸（DPI 感知下会返回 16/20/24/32）。"""
    v = user32.GetSystemMetrics(SM_CXSMICON)
    return v if v and v > 8 else 16


def set_dpi_aware():
    """必须在创建任何窗口之前调用，否则托盘图标会被系统拉伸发糊。"""
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4（Win10 1703+）
        fn = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if fn is not None:
            fn.argtypes = [ctypes.c_void_p]
            fn.restype = w.BOOL
            if fn(ctypes.c_void_p(-4)):
                return True
    except Exception:
        pass
    try:
        return bool(user32.SetProcessDPIAware())
    except Exception:
        return False


def known_folder(guid_str):
    """SHGetKnownFolderPath —— 桌面/启动文件夹可能被 OneDrive 重定向，不能拼 %APPDATA%。"""
    g = guid_from_string(guid_str)
    out = ctypes.c_wchar_p()
    hr = shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out))
    if hr != 0:
        raise OSError("SHGetKnownFolderPath 失败: 0x%08X" % (hr & 0xFFFFFFFF))
    try:
        return out.value
    finally:
        ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))


def desktop_dir():
    try:
        return known_folder(FOLDERID_Desktop)
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Desktop")


def startup_dir():
    try:
        return known_folder(FOLDERID_Startup)
    except Exception:
        return os.path.join(os.environ.get("APPDATA", ""),
                            r"Microsoft\Windows\Start Menu\Programs\Startup")


def local_appdata_dir():
    try:
        return known_folder(FOLDERID_LocalAppData)
    except Exception:
        return os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")


def open_device(path, write=True):
    """打开 HID 接口。

    系统独占的顶层集合（鼠标/键盘/笔/触摸屏）只允许 dwDesiredAccess=0，
    这种句柄能读属性、读 Feature Report，但不能 WriteFile。
    先按读写打开，失败再降级。
    """
    share = FILE_SHARE_READ | FILE_SHARE_WRITE
    if write:
        h = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, share, None, OPEN_EXISTING, 0, None)
        if h and h != INVALID_HANDLE_VALUE:
            return h, True
        err = last_error()
        if err not in (ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION):
            # 其它错误也用只读模式再试一次
            pass
    h = kernel32.CreateFileW(path, 0, share, None, OPEN_EXISTING, 0, None)
    if h and h != INVALID_HANDLE_VALUE:
        return h, False
    return None, False


def is_exclusive_usage(usage_page, usage):
    """系统保留为独占的顶层集合。"""
    if usage_page == 0x01 and usage in (0x01, 0x02, 0x04, 0x05, 0x06, 0x07, 0x08):
        return True          # 鼠标 / 摇杆 / 手柄 / 键盘 / 小键盘 / 多轴 / 平板
    if usage_page == 0x0D and usage in (0x01, 0x02, 0x04, 0x05):
        return True          # 数字化仪 / 笔 / 触摸屏 / 触摸板
    return False


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


SPI_GETWORKAREA = 0x0030


def work_area():
    """桌面工作区（不含任务栏），用于把面板摆在右下角。"""
    r = RECT()
    try:
        fn = user32.SystemParametersInfoW
        fn.argtypes = [w.UINT, w.UINT, ctypes.c_void_p, w.UINT]
        fn.restype = w.BOOL
        if fn(SPI_GETWORKAREA, 0, ctypes.byref(r), 0):
            return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:
        pass
    return (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
