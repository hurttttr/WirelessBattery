# -*- coding: utf-8 -*-
"""通知区域宿主。

这个线程自己 RegisterClass + CreateWindowEx + GetMessage 循环，
Tk 留在主线程，两边只通过 PostMessage / queue 交互。

三个必须做对的地方：
  1. WM_DESTROY 里一定要 PostQuitMessage(0)，否则 DestroyWindow 之后
     窗口没了、图标也没了、进程却还活着（pythonw 下用户完全看不出来），
     而且它还占着单实例锁，导致之后再也起不来。
  2. 更新图标用 NIM_MODIFY，不能 NIM_DELETE 再 NIM_ADD——删掉的图标
     用 MODIFY 是改不回来的。
  3. 窗口必须是普通顶层窗口（不能是 message-only），否则收不到
     TaskbarCreated 广播，Explorer 重启后图标就再也不出现了。
"""

import ctypes
import threading
import time

from . import icons
from . import logx
from . import win32 as wx
from .logx import dbg

CLASS_NAME = "WirelessBatteryTrayWnd"
WINDOW_TITLE = "无线外设电量托盘"

WM_TRAY_CALLBACK = wx.WM_APP + 1
WM_OPEN_PANEL = wx.WM_APP + 2
WM_REFRESH = wx.WM_APP + 3
WM_UPDATE_ICONS = wx.WM_APP + 4
WM_SHUTDOWN = wx.WM_APP + 5
WM_SET_INTERVAL = wx.WM_APP + 6

TIMER_REFRESH = 0xB411

GREEN = (47, 163, 107)
AMBER = (224, 163, 46)
RED = (214, 69, 69)
GREY = (140, 148, 160)

# 图标槽位是动态的：面板上有什么设备，就摆几个图标



def level_color(percent):
    if percent is None:
        return GREY
    if percent >= 60:
        return GREEN
    if percent >= 30:
        return AMBER
    return RED


class TrayHost(threading.Thread):
    def __init__(self, app):
        super().__init__(name="tray-host", daemon=True)
        self.app = app
        self.hwnd = None
        self.ready = threading.Event()
        self._msg = None
        self._nid = {}
        self._hicon = {}
        self._slots = {}          # key -> uID
        self._next_uid = 1
        self._timer = False
        self._icon_size = 16
        self._class_atom = 0
        self._wndproc = None
        self._taskbar_created = 0
        self._shutdown = False

    # ------------------------------------------------------------ 线程入口

    def run(self):
        try:
            self._setup()
        except Exception as e:
            logx.warn("托盘启动失败：%s" % e)
            self.ready.set()
            return
        self.ready.set()
        self._message_loop()
        self._teardown()

    def _setup(self):
        user32 = wx.user32
        wx.set_dpi_aware()
        self._icon_size = wx.small_icon_size()
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")

        self._wndproc = wx.WNDPROC(self._on_message)   # 必须留引用，否则被 GC 掉会崩
        wc = wx.WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = wx.kernel32.GetModuleHandleW(None)
        wc.hIcon = 0
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))  # IDC_ARROW
        wc.hbrBackground = 0
        wc.lpszMenuName = None
        wc.lpszClassName = CLASS_NAME
        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom:
            err = wx.last_error()
            if err not in (0, 1410):        # 1410 = 类已注册
                raise OSError("RegisterClassW 失败 (err=%d)" % err)
        self._class_atom = atom

        self.hwnd = user32.CreateWindowExW(
            0, CLASS_NAME, WINDOW_TITLE, 0x80000000,   # WS_POPUP，永不 ShowWindow
            0, 0, 0, 0, None, None, wc.hInstance, None)
        if not self.hwnd:
            raise OSError("CreateWindowExW 失败 (err=%d)" % wx.last_error())
        dbg("托盘窗口 hwnd=%s icon=%dpx" % (self.hwnd, self._icon_size))

        self._sync_slots()
        self._update_icons()
        self._start_timer(self.app.interval)

    def _message_loop(self):
        msg = wx.MSG()
        while True:
            ret = wx.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            wx.user32.TranslateMessage(ctypes.byref(msg))
            wx.user32.DispatchMessageW(ctypes.byref(msg))
        dbg("消息循环结束")

    def _teardown(self):
        if self._timer and self.hwnd:
            wx.user32.KillTimer(self.hwnd, TIMER_REFRESH)
            self._timer = False
        for key, uid in list(self._slots.items()):
            self._delete_icon(uid)
        for uid, h in list(self._hicon.items()):
            icons.destroy_hicon(h)
        self._hicon.clear()
        if self.hwnd:
            try:
                wx.user32.DestroyWindow(self.hwnd)
            except Exception:
                pass
            self.hwnd = None
        dbg("托盘线程已退出")

    # ------------------------------------------------------------ 图标

    def _make_nid(self, uid, key):
        nid = wx.NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(wx.NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = uid
        nid.uFlags = wx.NIF_MESSAGE | wx.NIF_ICON | wx.NIF_TIP | wx.NIF_SHOWTIP
        nid.uCallbackMessage = WM_TRAY_CALLBACK
        nid.szTip = self._tip_for(key)
        h = self._render_icon(key)
        nid.hIcon = h
        self._hicon[uid] = h
        return nid

    def _tip_for(self, key):
        r = self.app.get_reading(key)
        name = r.name or key
        if r.ok:
            return "%s：%d%%（%s）" % (name, r.percent, r.stamp)
        note = r.note or "未连接"
        return "%s：%s（%s）" % (name, note, r.stamp)

    def _render_icon(self, key):
        r = self.app.get_reading(key)
        color = level_color(r.percent if r.ok else None)
        text = "%d" % r.percent if r.ok else "--"
        if r.ok and r.percent >= 100:
            text = "100"
        radius = 0.22 if (self._slots.get(key, 1) % 2) else 0.42   # 相邻图标形状错开，好区分
        if self._icon_size < 24:
            # 16px 下正圆四角吃掉的面积太多，数字会顶到边上
            radius = min(radius, 0.30)
        rgba = icons.render_number_icon(text, color, self._icon_size, radius)
        try:
            return icons.create_hicon(rgba, self._icon_size)
        except OSError as e:
            logx.warn("创建图标失败：%s" % e)
            return 0

    def _add_icon(self, key, uid):
        nid = self._make_nid(uid, key)
        ok = False
        for attempt in range(4):
            if wx.shell32.Shell_NotifyIconW(wx.NIM_ADD, ctypes.byref(nid)):
                ok = True
                break
            time.sleep(0.15)                # 只在启动阶段重试，不是空闲轮询
        if not ok:
            logx.warn("NIM_ADD 失败 uID=%d (err=%d)" % (uid, wx.last_error()))
            return False
        # 固定用 V4 以外的传统回调（lParam 直接是鼠标消息），兼容性最好
        self._nid[uid] = nid
        return True

    def _delete_all_icons(self):
        for key, uid in list(self._slots.items()):
            self._delete_icon(uid)

    def _sync_slots(self):
        """设备插拔后槽位会变：没了的删图标，新出现的加图标（NIM_ADD）。"""
        try:
            keys = self.app.reading_keys()
        except Exception:
            return
        changed = False
        for key, uid in list(self._slots.items()):
            if key not in keys:
                self._delete_icon(uid)
                del self._slots[key]
                changed = True
        for key in keys:
            if key not in self._slots:
                uid = self._next_uid
                self._next_uid += 1
                self._slots[key] = uid
                if self._add_icon(key, uid):
                    dbg("托盘加图标 %s -> uID=%d（当前 %d 个）" % (key, uid, len(self._slots)))
                changed = True
        return changed

    def _delete_icon(self, uid):
        nid = self._nid.pop(uid, None)
        if nid is None:
            return
        wx.shell32.Shell_NotifyIconW(wx.NIM_DELETE, ctypes.byref(nid))
        h = self._hicon.pop(uid, 0)
        if h:
            icons.destroy_hicon(h)

    def _update_icons(self):
        """刷新图标的内容与提示（NIM_MODIFY，绝不删了重加）。"""
        self._sync_slots()
        for key, uid in list(self._slots.items()):
            nid = self._nid.get(uid)
            if nid is None:
                continue
            tip = self._tip_for(key)
            new_h = self._render_icon(key)
            nid.uFlags = wx.NIF_ICON | wx.NIF_TIP | wx.NIF_SHOWTIP
            nid.szTip = tip[:120]
            nid.hIcon = new_h
            ok = wx.shell32.Shell_NotifyIconW(wx.NIM_MODIFY, ctypes.byref(nid))
            if not ok:
                # 极少数情况下（Explorer 刚重启）MODIFY 会失败，这时才重新 ADD
                dbg("NIM_MODIFY 失败，重新 ADD uID=%d" % uid)
                wx.shell32.Shell_NotifyIconW(wx.NIM_ADD, ctypes.byref(nid))
            old = self._hicon.get(uid)
            self._hicon[uid] = new_h
            if old and old != new_h:
                icons.destroy_hicon(old)

    # ------------------------------------------------------------ 定时器

    def _start_timer(self, seconds):
        if not self.hwnd:
            return
        if self._timer:
            wx.user32.KillTimer(self.hwnd, TIMER_REFRESH)
        ms = max(1, int(seconds)) * 1000
        wx.user32.SetTimer(self.hwnd, TIMER_REFRESH, ms, None)
        self._timer = True
        dbg("SetTimer %d ms" % ms)

    # ------------------------------------------------------------ 窗口过程

    def _on_message(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY_CALLBACK:
                uid = int(wparam)
                event = int(lparam) & 0xFFFF
                key = dict((u, k) for k, u in self._slots.items()).get(uid, "")
                if event == wx.WM_LBUTTONUP:
                    self.app.request_refresh("左键")
                elif event in (wx.WM_RBUTTONUP, wx.WM_LBUTTONDBLCLK):
                    self.app.open_panel(source="托盘")
            elif msg == WM_OPEN_PANEL:
                self.app.open_panel(source="托盘")
            elif msg == WM_REFRESH:
                self.app.request_refresh("命令")
            elif msg == WM_UPDATE_ICONS:
                self._update_icons()
            elif msg == WM_SET_INTERVAL:
                self._start_timer(int(wparam))
            elif msg == WM_SHUTDOWN:
                # 只销毁窗口是不够的：Tk 主循环还在主线程跑着，进程不会退出。
                # 这里必须请求"整个应用退出"，由主线程去 quit。
                self._delete_all_icons()
                wx.user32.DestroyWindow(hwnd)
                self.app.quit_from_tray()
            elif msg == wx.WM_TIMER:
                if int(wparam) == TIMER_REFRESH:
                    self.app.request_refresh("定时")
            elif msg == wx.WM_CLOSE:
                wx.user32.DestroyWindow(hwnd)
            elif msg == wx.WM_DESTROY:
                # 没有这一句，DestroyWindow 之后进程就成了僵尸
                self._timer = False
                wx.user32.KillTimer(hwnd, TIMER_REFRESH)
                for key, uid in list(self._slots.items()):
                    self._delete_icon(uid)
                wx.user32.PostQuitMessage(0)
            elif msg == self._taskbar_created and msg:
                # Explorer 重启：图标被系统清掉了，必须重新 ADD
                dbg("收到 TaskbarCreated，重载图标")
                for key, uid in list(self._slots.items()):
                    self._nid.pop(uid, None)
                    self._add_icon(key, uid)
                self._update_icons()
            else:
                return wx.user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        except Exception as e:
            logx.warn("窗口过程异常：%r" % (e,))
            return wx.user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        return 0

    # ------------------------------------------------------------ 跨线程调用

    def post_open(self):
        if self.hwnd:
            wx.user32.PostMessageW(self.hwnd, WM_OPEN_PANEL, 0, 0)

    def post_refresh(self):
        if self.hwnd:
            wx.user32.PostMessageW(self.hwnd, WM_REFRESH, 0, 0)

    def post_update(self):
        if self.hwnd:
            wx.user32.PostMessageW(self.hwnd, WM_UPDATE_ICONS, 0, 0)

    def set_interval(self, seconds):
        if self.hwnd:
            wx.user32.PostMessageW(self.hwnd, WM_SET_INTERVAL, int(seconds), 0)

    def stop(self, timeout=3.0):
        self._shutdown = True
        if self.hwnd:
            wx.user32.PostMessageW(self.hwnd, WM_SHUTDOWN, 0, 0)
        if self.is_alive():
            self.join(timeout)

    # ------------------------------------------------------------ 单实例辅助

    @staticmethod
    def find_existing():
        return wx.user32.FindWindowW(CLASS_NAME, None)

    @staticmethod
    def wake_existing(times=12, interval=0.08):
        """把已经开着的实例叫出来（打开面板），返回是否找到。"""
        for _ in range(times):
            hwnd = TrayHost.find_existing()
            if hwnd:
                wx.user32.PostMessageW(hwnd, WM_OPEN_PANEL, 0, 0)
                return True
            time.sleep(interval)
        return False
