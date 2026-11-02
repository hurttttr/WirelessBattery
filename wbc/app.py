# -*- coding: utf-8 -*-
"""进程装配：单实例、Tk 主线程、后台读取线程、托盘线程、退出流程。

线程约定（对应"Tkinter 不是线程安全"这条坑）：
    后台线程只负责读设备，结果一律丢进 queue.Queue；
    所有 Tk 调用只在主线程的 after() 泵里发生；
    托盘线程要开面板时，也是往队列里放一条消息，绝不去碰 Tk。
"""

import ctypes
import queue
import threading
import time
import tkinter as tk

from . import autostart
from . import logx
from . import panel
from . import proto
from . import settings
from . import theme
from . import tray
from . import win32 as wx
from .logx import dbg

MUTEX_NAME = "Local\\WirelessBattery.SingleInstance"
ERROR_ALREADY_EXISTS = 183

PUMP_FAST = 120          # 面板可见时
PUMP_IDLE = 400          # 面板隐藏时


def _blank_readings():
    out = {}
    for p in proto.active_profiles():
        out[p["key"]] = proto.Reading(p["key"], name=p["name"], subtitle=p["subtitle"],
                                      note="尚未读取")
    return out


class BatteryApp(object):
    def __init__(self, opts):
        self.opts = opts
        self.settings = settings.Settings()
        forced = getattr(opts, "interval", None)
        if forced:
            self.interval = settings.clamp_interval(forced)
            self.settings.interval = self.interval
        else:
            self.interval = self.settings.interval
        self.readings = _blank_readings()
        self._lock = threading.Lock()
        self._queue = queue.Queue()
        self._busy = False
        self._quitting = False
        self.root = None
        self.panel = None
        self.debug_win = None
        self.tray = None
        self._mutex = None
        self._pump_delay = PUMP_IDLE
        self._started = 0

    # ------------------------------------------------------------ 生命周期

    def _acquire_instance(self):
        self._mutex = wx.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        err = ctypes.get_last_error()
        if err == ERROR_ALREADY_EXISTS:
            return False
        return True

    def run(self):
        # DPI 必须在任何窗口之前声明，否则托盘图标会被系统拉伸发糊
        wx.set_dpi_aware()
        theme.init_scale()
        dbg("UI 缩放 %.2f（%d DPI）" % (theme.SCALE, int(theme.SCALE * 96)))

        if not self._acquire_instance():
            dbg("已有实例在运行，尝试唤起它的面板")
            if tray.TrayHost.wake_existing():
                return 0
            logx.warn("已有实例在运行。")
            return 0

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("无线外设电量")
        try:
            ico = settings.app_icon_path()
            if ico:
                self.root.iconbitmap(ico)
        except Exception:
            pass

        if self.opts.tray:
            self.tray = tray.TrayHost(self)
            self.tray.start()
            self.tray.ready.wait(3.0)

        if self.opts.show_panel or not self.opts.tray:
            self.open_panel(source="启动")

        if getattr(self.opts, "serve", False):
            # HTTP 桥必须在同一个进程里跑：两个进程同时读同一批 HID 会互相抢报文
            from . import httpserve
            self.bridge = httpserve.Bridge(
                port=getattr(self.opts, "port", httpserve.DEFAULT_PORT),
                ttl=getattr(self.opts, "serve_ttl", httpserve.DEFAULT_TTL))
            try:
                real = self.bridge.start()
                dbg("HTTP 桥 http://127.0.0.1:%d/battery" % real)
            except OSError as e:
                logx.warn("HTTP 桥启动失败：%s" % e)
                self.bridge = None

        self._started = time.time()
        self.request_refresh("启动")
        self.root.after(200, self._pump)

        secs = getattr(self.opts, "exit_after", None)
        if secs:
            dbg("将在 %.0f 秒后自动退出（自测）" % secs)
            self.root.after(int(secs * 1000), self.quit_app)

        try:
            self.root.mainloop()
        finally:
            self._shutdown()
        return 0

    def _shutdown(self):
        dbg("开始退出")
        bridge = getattr(self, "bridge", None)
        if bridge is not None:
            bridge.stop()
            dbg("HTTP 桥已停止")
        if self.panel is not None:
            try:
                self.panel.remember_geometry()
            except Exception:
                pass
        self.settings.interval = self.interval
        self.settings.save()

        if self.tray is not None:
            self.tray.stop()
            dbg("托盘线程存活：%s" % self.tray.is_alive())

        try:
            if self.root is not None:
                self.root.destroy()
        except Exception:
            pass

        if self._mutex:
            wx.kernel32.CloseHandle(self._mutex)
            self._mutex = None
        dbg("退出完成（运行 %.1f 秒）" % (time.time() - self._started))

    def quit_from_tray(self):
        """托盘线程发来的退出请求：只能排队，Tk 的操作必须回主线程做。"""
        self._queue.put(("quit", None))

    def quit_app(self):
        if self._quitting:
            return
        self._quitting = True
        try:
            self.root.quit()
        except Exception:
            pass

    # ------------------------------------------------------------ 读取

    def request_refresh(self, reason=""):
        with self._lock:
            if self._busy or self._quitting:
                return
            self._busy = True
        dbg("刷新（%s）" % (reason or "-"))
        self._queue.put(("busy", True))
        threading.Thread(target=self._worker, name="reader", daemon=True).start()

    def _worker(self):
        try:
            readings = proto.read_all()
        except Exception as e:
            logx.warn("读取异常：%r" % (e,))
            readings = _blank_readings()
            for r in readings.values():
                r.note = "读取异常：%s" % e
        with self._lock:
            self.readings = readings
            self._busy = False
        self._queue.put(("readings", readings))
        if self.tray is not None:
            self.tray.post_update()

    def reading_keys(self):
        with self._lock:
            return list(self.readings.keys())

    def get_reading(self, key):
        with self._lock:
            return self.readings.get(key) or proto.Reading(key, note="尚未读取")

    # ------------------------------------------------------------ 主线程泵

    def _pump(self):
        try:
            while True:
                try:
                    kind, payload = self._queue.get_nowait()
                except queue.Empty:
                    break
                if kind == "readings":
                    self._on_readings(payload)
                elif kind == "busy":
                    if self.panel is not None and self.panel.winfo_exists():
                        self.panel.set_busy(bool(payload))
                elif kind == "open":
                    self._open_panel_now()
                elif kind == "debug":
                    self._open_debug_now()
                elif kind == "quit":
                    self.quit_app()
        except Exception as e:
            logx.warn("泵异常：%r" % (e,))
        if not self._quitting:
            visible = self._panel_visible()
            self._pump_delay = PUMP_FAST if visible else PUMP_IDLE
            self.root.after(self._pump_delay, self._pump)

    def _on_readings(self, readings):
        if self.panel is not None and self.panel.winfo_exists():
            self.panel.apply_readings(readings)
            self.panel.set_busy(False)

    def _panel_visible(self):
        try:
            return self.panel is not None and self.panel.winfo_exists() and \
                self.panel.state() != "withdrawn"
        except Exception:
            return False

    # ------------------------------------------------------------ 面板

    def open_panel(self, source=""):
        if threading.current_thread() is not threading.main_thread():
            self._queue.put(("open", None))
            return
        self._open_panel_now()

    def _open_panel_now(self):
        if self._quitting:
            return
        if self.panel is None or not self.panel.winfo_exists():
            self.panel = panel.PanelWindow(self)
            self.panel.apply_readings(self.readings)
            self.panel.set_busy(self._busy)
            if self._busy:
                self.panel.set_status("正在读取…", busy=True)
            self._raise_panel()
            return
        # 已开则不重复开：还原 + 前置
        try:
            if self.panel.state() in ("withdrawn", "iconic"):
                self.panel.deiconify()
            self._raise_panel()
        except Exception:
            pass

    def _raise_panel(self):
        """把面板提到最前。

        只 lift 在 Windows 上经常抢不过前台窗口（新窗口从后台进程创建时
        不会自动激活），所以短暂置顶一下再取消，最后强制拿焦点。
        """
        p = self.panel
        try:
            p.deiconify()
            p.lift()
            p.attributes("-topmost", True)
            p.after(150, lambda: p.attributes("-topmost", False))
            p.focus_force()
        except Exception:
            pass

    def close_panel(self):
        """面板的 X：有托盘就收起来，没托盘就直接退出。"""
        if self.panel is not None:
            try:
                self.panel.remember_geometry()
            except Exception:
                pass
        if self.opts.tray:
            if self.panel is not None:
                self.panel.withdraw()
            self.settings.save()
        else:
            self.quit_app()

    def open_debug(self):
        if threading.current_thread() is not threading.main_thread():
            self._queue.put(("debug", None))
            return
        self._open_debug_now()

    def _open_debug_now(self):
        if self.debug_win is not None and self.debug_win.winfo_exists():
            self.debug_win.deiconify()
            self.debug_win.lift()
            self.debug_win.focus_force()
            return
        from . import debugui
        self.debug_win = debugui.DebugWindow(self)

    # ------------------------------------------------------------ 设置

    def set_interval(self, seconds):
        self.interval = settings.clamp_interval(seconds)
        self.settings.interval = self.interval
        self.settings.save()
        if self.tray is not None:
            self.tray.set_interval(self.interval)
        dbg("刷新间隔 -> %d 秒" % self.interval)

    def set_autostart(self, want):
        """用户点了复选框才动手；程序自己绝不"自动纠正"启动项。"""
        try:
            if want:
                # 保留用户手工加过的开关（比如 --serve），不替他把启动项改回去
                autostart.enable(extra_args=autostart.current_extra_args())
            else:
                autostart.disable()
            ok = (autostart.state() == "enabled") == bool(want)
            if not ok:
                logx.warn("开机自启状态与预期不符：%s" % autostart.state())
            return ok
        except Exception as e:
            logx.warn("设置开机自启失败：%s" % e)
            return False
