# -*- coding: utf-8 -*-
"""HID 枚举与安全的读写通道。

关键设计（对应"已知的坑"第 1 条）：
    写句柄和读句柄是两个完全独立的 CreateFile。
    读句柄带 FILE_FLAG_OVERLAPPED + 手工事件 + CancelIoEx，写/IOCTL 走另一个
    同步句柄。绝不在同一个句柄上"阻塞 ReadFile 同时并发 WriteFile"——那会
    直接杀进程，连异常栈都没有。
"""

import ctypes
import time
from ctypes import wintypes as w

from . import logx
from . import win32 as wx
from .logx import dbg, dbg_hex


class HidInterface(object):
    """一个 HID 接口（= 一个设备接口路径，可能对应设备里的一个集合）。"""

    __slots__ = ("path", "vendor_id", "product_id", "version", "usage_page", "usage",
                 "input_len", "output_len", "feature_len", "product", "manufacturer",
                 "serial", "can_write")

    def __init__(self, path):
        self.path = path
        self.vendor_id = 0
        self.product_id = 0
        self.version = 0
        self.usage_page = 0
        self.usage = 0
        self.input_len = 0
        self.output_len = 0
        self.feature_len = 0
        self.product = ""
        self.manufacturer = ""
        self.serial = ""
        self.can_write = False

    @property
    def vidpid(self):
        return "%04X:%04X" % (self.vendor_id, self.product_id)

    @property
    def is_vendor_page(self):
        return self.usage_page >= 0xFF00

    @property
    def exclusive(self):
        return wx.is_exclusive_usage(self.usage_page, self.usage)

    def short_path(self):
        # \\?\hid#vid_373b&pid_1216&mi_02#7&2f...&0000#{4d1e55b2-...}
        p = self.path
        if p.startswith("\\\\?\\"):
            p = p[4:]
        head = p.split("#")
        if len(head) >= 2:
            return head[0] + "#" + head[1]
        return p

    def __repr__(self):
        return "<HID %s up=%04X u=%04X in=%d out=%d feat=%d>" % (
            self.vidpid, self.usage_page, self.usage,
            self.input_len, self.output_len, self.feature_len)


def _read_string(fn, handle, chars=126):
    buf = ctypes.create_unicode_buffer(chars)
    try:
        ok = fn(handle, ctypes.cast(buf, ctypes.c_void_p), ctypes.sizeof(buf))
    except Exception:
        return ""
    if not ok:
        return ""
    return buf.value


def _device_paths():
    """用 SetupAPI 列出所有当前存在的 HID 设备接口路径。"""
    guid = wx.GUID()
    wx.hid.HidD_GetHidGuid(ctypes.byref(guid))
    flags = wx.DIGCF_PRESENT | wx.DIGCF_DEVICEINTERFACE
    hdev = wx.setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, flags)
    if not hdev or hdev == wx.INVALID_HANDLE_VALUE:
        raise OSError("SetupDiGetClassDevsW 失败 (err=%d)" % wx.last_error())

    paths = []
    try:
        idx = 0
        while True:
            did = wx.SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(did)
            ok = wx.setupapi.SetupDiEnumDeviceInterfaces(
                hdev, None, ctypes.byref(guid), idx, ctypes.byref(did))
            if not ok:
                err = wx.last_error()
                if err == wx.ERROR_NO_MORE_ITEMS:
                    break
                break
            idx += 1

            req = w.DWORD(0)
            wx.setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(did), None, 0, ctypes.byref(req), None)
            if req.value <= 0:
                continue
            buf = ctypes.create_string_buffer(req.value + 8)
            # 缓冲区头部就是 SP_DEVICE_INTERFACE_DETAIL_DATA_W 的 cbSize，
            # x64 必须写 8、x86 写 6，写错这里会直接失败。
            ctypes.cast(buf, ctypes.POINTER(w.DWORD))[0] = wx.DETAIL_CBSIZE
            ok = wx.setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(did), ctypes.cast(buf, ctypes.c_void_p),
                req.value, ctypes.byref(req), None)
            if not ok:
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + wx.DETAIL_PATH_OFFSET)
            if path:
                paths.append(path)
    finally:
        wx.setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return paths


def describe_path(path):
    """打开接口（只读，access=0 最稳），读出属性与 caps。"""
    iface = HidInterface(path)
    h = wx.kernel32.CreateFileW(
        path, 0, wx.FILE_SHARE_READ | wx.FILE_SHARE_WRITE, None, wx.OPEN_EXISTING, 0, None)
    if not h or h == wx.INVALID_HANDLE_VALUE:
        return None
    try:
        att = wx.HIDD_ATTRIBUTES()
        att.Size = ctypes.sizeof(att)
        if wx.hid.HidD_GetAttributes(h, ctypes.byref(att)):
            iface.vendor_id = att.VendorID
            iface.product_id = att.ProductID
            iface.version = att.VersionNumber

        iface.product = _read_string(wx.hid.HidD_GetProductString, h)
        iface.manufacturer = _read_string(wx.hid.HidD_GetManufacturerString, h, 64)
        iface.serial = _read_string(wx.hid.HidD_GetSerialNumberString, h, 64)

        pp = ctypes.c_void_p()
        if wx.hid.HidD_GetPreparsedData(h, ctypes.byref(pp)) and pp:
            try:
                caps = wx.HIDP_CAPS()
                st = wx.hid.HidP_GetCaps(pp, ctypes.byref(caps))
                if st == wx.HIDP_STATUS_SUCCESS:
                    iface.usage_page = caps.UsagePage
                    iface.usage = caps.Usage
                    iface.input_len = caps.InputReportByteLength
                    iface.output_len = caps.OutputReportByteLength
                    iface.feature_len = caps.FeatureReportByteLength
            finally:
                wx.hid.HidD_FreePreparsedData(pp)

        # 试一下能不能写（独占顶层集合不行）
        if not iface.exclusive:
            probe, writable = wx.open_device(path, write=True)
            if probe:
                iface.can_write = bool(writable)
                wx.kernel32.CloseHandle(probe)
        return iface
    finally:
        wx.kernel32.CloseHandle(h)


def enumerate_interfaces(full=True):
    """枚举全部 HID 接口。full=True 时逐个读取属性（约几十毫秒）。"""
    out = []
    for path in _device_paths():
        if not full:
            iface = HidInterface(path)
            out.append(iface)
            continue
        iface = describe_path(path)
        if iface is not None:
            out.append(iface)
    return out


# ---------------------------------------------------------------- 控制句柄

class HidController(object):
    """同步控制句柄：写 Output Report / 读写 Feature Report。"""

    def __init__(self, iface, prefer_write=True):
        self.iface = iface
        h, writable = wx.open_device(iface.path, write=prefer_write)
        if not h:
            raise OSError("无法打开设备 %s (err=%d)" % (iface.vidpid, wx.last_error()))
        self.handle = h
        self.writable = writable

    def close(self):
        if self.handle:
            wx.kernel32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def write_output(self, data):
        """data 必须已经包含 Report ID（第一个字节）。长度需等于 OutputReportByteLength。"""
        if not self.writable:
            raise OSError("设备以只读方式打开（系统独占集合），无法发送指令")
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        written = w.DWORD(0)
        ok = wx.kernel32.WriteFile(
            self.handle, ctypes.cast(buf, ctypes.c_void_p), len(data),
            ctypes.byref(written), None)
        if not ok:
            raise OSError("WriteFile 失败 (err=%d)" % wx.last_error())
        dbg_hex("TX", data)
        return written.value

    def get_feature(self, report_id, length):
        buf = ctypes.create_string_buffer(length)
        if report_id:
            buf[0] = bytes([report_id & 0xFF])
        ok = wx.hid.HidD_GetFeature(self.handle, ctypes.cast(buf, ctypes.c_void_p), length)
        if not ok:
            return None
        data = buf.raw[:length]
        dbg_hex("FEAT<", data)
        return data

    def set_feature(self, data):
        if not self.writable:
            raise OSError("只读句柄无法写 Feature Report")
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        ok = wx.hid.HidD_SetFeature(self.handle, ctypes.cast(buf, ctypes.c_void_p), len(data))
        if not ok:
            raise OSError("HidD_SetFeature 失败 (err=%d)" % wx.last_error())
        dbg_hex("FEAT>", data)


# ---------------------------------------------------------------- 异步读句柄

class OverlappedReader(object):
    """独立的异步读句柄。

    用法：
        r = OverlappedReader(iface, timeout_len)
        r.begin()          # 先把读挂起
        ctrl.write_output(req)
        resp = r.wait(1200)   # 最多等 1.2 秒
    先 begin 再写，才不会漏掉回来的报文。
    """

    def __init__(self, iface, length=None):
        self.iface = iface
        self.length = length or iface.input_len or 64
        self.handle = wx.kernel32.CreateFileW(
            iface.path, wx.GENERIC_READ, wx.FILE_SHARE_READ | wx.FILE_SHARE_WRITE,
            None, wx.OPEN_EXISTING, wx.FILE_FLAG_OVERLAPPED, None)
        if not self.handle or self.handle == wx.INVALID_HANDLE_VALUE:
            raise OSError("无法打开异步读句柄 %s (err=%d)" % (iface.vidpid, wx.last_error()))
        self.event = wx.kernel32.CreateEventW(None, True, False, None)
        if not self.event:
            wx.kernel32.CloseHandle(self.handle)
            raise OSError("CreateEventW 失败")
        self.buf = ctypes.create_string_buffer(self.length)
        self.ov = wx.OVERLAPPED()
        self.ov.hEvent = self.event
        self.pending = False

    def close(self):
        if self.pending:
            wx.kernel32.CancelIoEx(self.handle, ctypes.byref(self.ov))
            wx.kernel32.WaitForSingleObject(self.event, 300)
            self.pending = False
        if self.handle:
            wx.kernel32.CloseHandle(self.handle)
            self.handle = None
        if self.event:
            wx.kernel32.CloseHandle(self.event)
            self.event = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def begin(self):
        if self.pending:
            return True
        wx.kernel32.ResetEvent(self.event)
        self.ov.Internal = 0
        self.ov.InternalHigh = 0
        self.ov.Offset = 0
        self.ov.OffsetHigh = 0
        nread = w.DWORD(0)
        ok = wx.kernel32.ReadFile(
            self.handle, ctypes.cast(self.buf, ctypes.c_void_p), self.length,
            ctypes.byref(nread), ctypes.byref(self.ov))
        if not ok:
            err = wx.last_error()
            if err != wx.ERROR_IO_PENDING:
                raise OSError("ReadFile 失败 (err=%d)" % err)
        self.pending = True
        return True

    def wait(self, timeout_ms):
        """返回 bytes 或 None（超时/已取消）。"""
        if not self.pending:
            return None
        rc = wx.kernel32.WaitForSingleObject(self.event, int(timeout_ms))
        if rc != wx.WAIT_OBJECT_0:
            # 超时：必须取消挂起的 IO 并等它真正收尾，否则缓冲区可能被后续操作踩踏
            wx.kernel32.CancelIoEx(self.handle, ctypes.byref(self.ov))
            wx.kernel32.WaitForSingleObject(self.event, 400)
            nread = w.DWORD(0)
            wx.kernel32.GetOverlappedResult(
                self.handle, ctypes.byref(self.ov), ctypes.byref(nread), False)
            self.pending = False
            return None
        nread = w.DWORD(0)
        ok = wx.kernel32.GetOverlappedResult(
            self.handle, ctypes.byref(self.ov), ctypes.byref(nread), False)
        self.pending = False
        if not ok:
            return None
        data = self.buf.raw[:nread.value]
        dbg_hex("RX", data)
        return data

    def poll(self, timeout_ms):
        """轮询式取一帧：没挂起就先挂起，取到就自动重新挂起。

        注意：wait() 超时后会把这次 ReadFile 取消掉（pending=False），
        所以轮询循环里必须用这个方法，不能直接反复调 wait()——那样第一轮
        超时之后就再也收不到东西了。
        """
        if not self.pending:
            try:
                self.begin()
            except OSError:
                return None
        data = self.wait(timeout_ms)
        if data is not None:
            try:
                self.begin()
            except OSError:
                pass
        return data

    def drain(self, max_ms=120):
        """把已经排队的旧报文读掉，避免拿到上一次的回复。"""
        got = []
        deadline = time.monotonic() + max_ms / 1000.0
        while time.monotonic() < deadline:
            try:
                self.begin()
            except OSError:
                break
            data = self.wait(int(max(1, (deadline - time.monotonic()) * 1000)))
            if data is None:
                break
            got.append(data)
        return got


# ---------------------------------------------------------------- Feature 电量字段

class FeatureValue(object):
    __slots__ = ("report_id", "usage_page", "usage", "bit_field", "bit_size",
                 "report_count", "logical_min", "logical_max")

    def __repr__(self):
        return "<FeatureValue rpt=%02X up=%04X u=%04X bits=%d@%d>" % (
            self.report_id, self.usage_page, self.usage, self.bit_size, self.bit_field)


def value_caps(iface, report_type=None):
    """读取某个 Report 类型（0=Input / 1=Output / 2=Feature）的所有 Value Caps。

    Value Caps 直接来自 HID 报告描述符，能看出设备到底暴露了哪些字段
    ——判断有没有标准电量字段（UsagePage 0x0006 / Usage 0x0020）就靠它。
    """
    if report_type is None:
        report_type = wx.HidP_Feature
    h = wx.kernel32.CreateFileW(
        iface.path, 0, wx.FILE_SHARE_READ | wx.FILE_SHARE_WRITE, None, wx.OPEN_EXISTING, 0, None)
    if not h or h == wx.INVALID_HANDLE_VALUE:
        return []
    try:
        pp = ctypes.c_void_p()
        if not wx.hid.HidD_GetPreparsedData(h, ctypes.byref(pp)) or not pp:
            return []
        try:
            caps = wx.HIDP_CAPS()
            if wx.hid.HidP_GetCaps(pp, ctypes.byref(caps)) != wx.HIDP_STATUS_SUCCESS:
                return []
            if report_type == wx.HidP_Input:
                n = caps.NumberInputValueCaps
            elif report_type == wx.HidP_Output:
                n = caps.NumberOutputValueCaps
            else:
                n = caps.NumberFeatureValueCaps
            if n <= 0:
                return []
            arr = (wx.HIDP_VALUE_CAPS * n)()
            length = w.USHORT(n)
            st = wx.hid.HidP_GetValueCaps(report_type, arr, ctypes.byref(length), pp)
            if st != wx.HIDP_STATUS_SUCCESS:
                return []
            out = []
            for i in range(length.value):
                c = arr[i]
                fv = FeatureValue()
                fv.report_id = c.ReportID
                fv.usage_page = c.UsagePage
                fv.usage = c.u.UsageMin
                fv.bit_field = c.BitField
                fv.bit_size = c.BitSize
                fv.report_count = c.ReportCount
                fv.logical_min = c.LogicalMin
                fv.logical_max = c.LogicalMax
                out.append(fv)
            return out
        finally:
            wx.hid.HidD_FreePreparsedData(pp)
    finally:
        wx.kernel32.CloseHandle(h)


def feature_value_caps(iface):
    """读取设备所有 Feature Report 的 Value Caps。"""
    return value_caps(iface, wx.HidP_Feature)


def extract_bits(data, report_id, bit_field, bit_size):
    """从 Feature Report 原始数据里取出某个位域。

    HidD_GetFeature 返回的缓冲区第 0 字节是 Report ID（当设备使用 Report ID 时），
    位偏移是相对 Reports 内容（不含 Report ID）的，所以要跳过 1 个字节。
    """
    base = 1 if report_id else 0
    start_bit = bit_field
    byte_index = base + start_bit // 8
    shift = start_bit % 8
    need = (bit_size + shift + 7) // 8
    if byte_index + need > len(data):
        return None
    chunk = int.from_bytes(bytes(data[byte_index:byte_index + need]), "little")
    return (chunk >> shift) & ((1 << bit_size) - 1)
