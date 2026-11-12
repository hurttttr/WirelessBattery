# -*- coding: utf-8 -*-
"""简洁面板：两张卡片，大号百分比 + 进度条 + 数据来源。"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import app_dir
from . import proto
from . import theme
from . import win32 as wx
from .theme import S, font

CARD_W = 412
CARD_H = 86

INTERVAL_CHOICES = [
    (30, "30 秒"),
    (60, "1 分钟"),
    (120, "2 分钟"),
    (300, "5 分钟"),
    (600, "10 分钟"),
    (1800, "30 分钟"),
]


class BatteryCard(tk.Canvas):
    """一张设备卡片。整块画在 Canvas 上，才能有真正的圆角和圆角进度条。"""

    def __init__(self, master, key):
        w, h = S(CARD_W), S(CARD_H)
        super().__init__(master, width=w, height=h, bg=theme.BG,
                         highlightthickness=0, bd=0)
        self.key = key
        self.w, self.h = w, h
        self._build()
        self.update_reading(None)

    def _build(self):
        pad = S(16)
        theme.round_rect(self, 0.5, 0.5, self.w - 0.5, self.h - 0.5, S(10),
                         fill=theme.CARD, outline=theme.BORDER, width=1)
        self.item_name = self.create_text(
            pad, S(10), anchor="nw", text="", fill=theme.TEXT, font=font(11, True))
        self.item_sub = self.create_text(
            self.w - pad, S(11), anchor="ne", text="", fill=theme.SUB, font=font(9))
        self.item_pct = self.create_text(
            pad - S(2), S(20), anchor="nw", text="--", fill=theme.GREY,
            font=font(30, True, theme.FONT_NUM))

        self.bar_x0 = pad + S(88)
        self.bar_x1 = self.w - pad
        self.bar_y0 = S(38)
        self.bar_y1 = S(47)
        theme.round_rect(self, self.bar_x0, self.bar_y0, self.bar_x1, self.bar_y1, S(4.5),
                         fill=theme.TRACK)
        self.bar_fill = []

        self.item_src = self.create_text(
            pad, S(62), anchor="nw", text="", fill=theme.SUB, font=font(9))

    def update_reading(self, reading):
        if reading is None:
            self.itemconfigure(self.item_name, text=self.key)
            self.itemconfigure(self.item_src, text="尚未读取")
            return

        ok = getattr(reading, "ok", False)
        pct = reading.percent if ok else None
        color = theme.level_color(pct, ok)

        self.itemconfigure(self.item_name, text=reading.name or self.key)
        self.itemconfigure(self.item_sub, text=reading.subtitle or "")
        self.itemconfigure(self.item_pct, text=("%d%%" % pct) if ok else "--", fill=color)

        if ok and pct is not None:
            src = "%s · %s" % (reading.source, reading.stamp)
            extra = getattr(reading, "extra", None) or {}
            if extra.get("charging") is True:
                src = "充电中 · " + src
            elif extra.get("charging") is False:
                src = "未充电 · " + src
            elif extra.get("online") is False:
                src = "离线 · " + src
        else:
            src = reading.note or "未连接"
        self.itemconfigure(self.item_src, text=src,
                           fill=(theme.SUB if ok else theme.RED))

        self._set_bar(0 if pct is None else pct, color)

    def _set_bar(self, percent, color):
        for item in self.bar_fill:
            self.delete(item)
        self.bar_fill = []
        if percent <= 0:
            return
        span = self.bar_x1 - self.bar_x0
        x1 = self.bar_x0 + span * max(0, min(100, percent)) / 100.0
        if x1 - self.bar_x0 < S(4):
            x1 = self.bar_x0 + S(4)
        self.bar_fill = theme.round_rect(
            self, self.bar_x0, self.bar_y0, x1, self.bar_y1, S(4.5), fill=color)


class PanelWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("无线外设电量")
        self.configure(bg=theme.BG)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._closing = False
        self._nudging = False

        icon = None
        try:
            from . import settings
            icon = settings.app_icon_path()
            if icon:
                self.iconbitmap(icon)
        except Exception:
            pass

        self._build()
        self._place()
        self.refresh_autostart()
        self.refresh_interval()
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event):
        """窗口尺寸一变就把自己夹回工作区。

        `resizable(False, False)` 的 Toplevel 在内容变多时会自己长大，但**不会**
        跟着挪位置——插拔设备导致卡片增减时，窗口下半截就露到屏幕外，用户看到
        的是"面板被切掉了"。这里在每次尺寸变化后检查一次，越界就拉回来。
        """
        if self._nudging or event.widget is not self:
            return
        w, h = event.width, event.height
        left, top, aw, ah = wx.work_area()
        gap = S(12)          # 别紧贴屏幕边缘，留一点呼吸
        nx = max(left, min(event.x, left + aw - w - gap))
        ny = max(top, min(event.y, top + ah - h - gap))
        if (nx, ny) == (event.x, event.y):
            return
        self._nudging = True
        try:
            self.geometry("+%d+%d" % (nx, ny))
        finally:
            self._nudging = False

    # ------------------------------------------------------------ 布局

    def _build(self):
        pad = S(16)
        outer = tk.Frame(self, bg=theme.BG)
        outer.pack(fill="both", expand=True, padx=pad, pady=(S(12), S(12)))

        head = tk.Frame(outer, bg=theme.BG)
        head.pack(fill="x")
        tk.Label(head, text="无线外设电量", bg=theme.BG, fg=theme.TEXT,
                 font=font(15, True)).pack(side="left")
        self.btn_refresh = tk.Button(
            head, text="刷新", command=self.on_refresh, relief="flat", bd=0,
            bg=theme.BG, fg=theme.ACCENT, activebackground=theme.BG,
            activeforeground=theme.ACCENT, font=font(10), cursor="hand2",
            padx=S(6), pady=0)
        self.btn_refresh.pack(side="right")

        self.cards_frame = tk.Frame(outer, bg=theme.BG)
        self.cards_frame.pack(fill="x")
        self.cards = {}
        self._card_keys = []

        self.lbl_status = tk.Label(outer, text="", bg=theme.BG, fg=theme.SUB,
                                   font=font(9), anchor="w")
        self.lbl_status.pack(fill="x", pady=(S(8), 0))

        foot = tk.Frame(outer, bg=theme.BG)
        foot.pack(fill="x", pady=(S(4), 0))

        self.var_auto = tk.IntVar(value=0)
        self.chk_auto = tk.Checkbutton(
            foot, text="开机自启", variable=self.var_auto, command=self.on_toggle_autostart,
            bg=theme.BG, fg=theme.TEXT, activebackground=theme.BG, selectcolor=theme.CARD,
            font=font(9), bd=0, highlightthickness=0, cursor="hand2")
        self.chk_auto.pack(side="left")

        tk.Label(foot, text="间隔", bg=theme.BG, fg=theme.SUB, font=font(9)).pack(
            side="left", padx=(S(14), S(4)))
        self.var_interval = tk.StringVar(value="1 分钟")
        self.cbo = ttk.Combobox(
            foot, textvariable=self.var_interval, state="readonly", width=7,
            values=[c[1] for c in INTERVAL_CHOICES], font=font(9))
        self.cbo.pack(side="left")
        self.cbo.bind("<<ComboboxSelected>>", self.on_interval)

        self.btn_quit = self._flat_button(foot, "退出", self.on_quit)
        self.btn_quit.pack(side="right")
        self.btn_debug = self._flat_button(foot, "调试版", self.on_debug)
        self.btn_debug.pack(side="right", padx=(0, S(6)))
        self.btn_plugin = self._flat_button(foot, "装插件", self.on_plugin)
        self.btn_plugin.pack(side="right", padx=(0, S(6)))

    def _flat_button(self, master, text, command):
        return tk.Button(master, text=text, command=command, relief="flat", bd=0,
                         bg=theme.CARD, fg=theme.TEXT, activebackground=theme.TRACK,
                         activeforeground=theme.TEXT, font=font(9), cursor="hand2",
                         padx=S(10), pady=S(2), highlightthickness=1,
                         highlightbackground=theme.BORDER)

    def _clamp(self, x, y, w, h):
        """把窗口夹进工作区。

        卡片增减会改变窗口高度，如果只按原来的位置挪，很容易让窗口一半露到
        屏幕外——用户看到的就是"面板被切掉了"。
        """
        left, top, aw, ah = wx.work_area()
        x = max(left, min(int(x), left + aw - w))
        y = max(top, min(int(y), top + ah - h))
        return x, y

    def _place(self):
        self.update_idletasks()
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()
        left, top, aw, ah = wx.work_area()
        geo = self.app.settings.geometry
        if geo:
            try:
                x, y = geo.split("+")[1:]
                x, y = int(x), int(y)
                if left - S(200) <= x <= left + aw and top - S(100) <= y <= top + ah:
                    x, y = self._clamp(x, y, w, h)
                    self.geometry("+%d+%d" % (x, y))
                    return
            except Exception:
                pass
        x = left + aw - w - S(28)
        y = top + ah - h - S(60)
        x, y = self._clamp(x, y, w, h)
        self.geometry("+%d+%d" % (x, y))

    def _fit(self):
        """卡片是按数据动态建的，重建之后窗口要重新贴合内容。

        Toplevel 设了 resizable(False, False)，一旦映射就不再跟着内容变大，
        插拔设备导致卡片增减时会裁掉内容。这里显式量一次尺寸：已显示时
        按右下角锚定，免得窗口上下跳。
        """
        self.update_idletasks()
        want_w, want_h = self.winfo_reqwidth(), self.winfo_reqheight()
        if not self.winfo_ismapped() or self.winfo_width() <= 1:
            self.geometry("%dx%d" % (want_w, want_h))
            self._place()
            return
        cur_w, cur_h = self.winfo_width(), self.winfo_height()
        if (cur_w, cur_h) == (want_w, want_h):
            return
        x = self.winfo_x() + cur_w - want_w
        y = self.winfo_y() + cur_h - want_h
        x, y = self._clamp(x, y, want_w, want_h)
        self.geometry("%dx%d+%d+%d" % (want_w, want_h, x, y))

    # ------------------------------------------------------------ 数据

    def _sync_cards(self, keys):
        """设备数量是动态的（插拔会变），卡片按 key 重建。"""
        if keys == self._card_keys:
            return
        for card in self.cards.values():
            card.destroy()
        self.cards = {}
        for i, key in enumerate(keys):
            card = BatteryCard(self.cards_frame, key)
            card.pack(pady=(S(10) if i == 0 else S(8), 0))
            self.cards[key] = card
        self._card_keys = list(keys)
        # 此刻 pack 还没参与布局，reqwidth/reqheight 仍是旧值。等一帧空闲再量，
        # 否则算出来的尺寸是"加卡片之前"的，窗口就会长到屏幕外面去。
        self.after_idle(self._fit)

    def apply_readings(self, readings):
        self._sync_cards(list(readings.keys()))
        for key, card in self.cards.items():
            card.update_reading(readings.get(key))
        # 每张卡片自己已经写清了失败原因，这里不再重复一遍
        self.set_status("")

    def set_status(self, text, error=False, busy=False):
        if busy:
            self.lbl_status.configure(text=text or "正在读取…", fg=theme.SUB)
        else:
            self.lbl_status.configure(text=text or "", fg=(theme.RED if error else theme.SUB))

    def set_busy(self, busy):
        self.btn_refresh.configure(text="刷新中…" if busy else "刷新",
                                   state="disabled" if busy else "normal")

    def refresh_autostart(self):
        """只反映真实状态：启动文件夹里有没有、任务管理器有没有禁用它。"""
        try:
            from . import autostart
            st = autostart.state()
        except Exception:
            st = "absent"
        self.var_auto.set(1 if st == "enabled" else 0)
        if st == "disabled":
            self.chk_auto.configure(text="开机自启（已在任务管理器禁用）")
        else:
            self.chk_auto.configure(text="开机自启")

    def refresh_interval(self):
        secs = self.app.interval
        for value, label in INTERVAL_CHOICES:
            if value == secs:
                self.var_interval.set(label)
                return
        self.var_interval.set("%d 秒" % secs)

    # ------------------------------------------------------------ 事件

    def on_refresh(self):
        self.app.request_refresh("面板")

    def on_toggle_autostart(self):
        want = bool(self.var_auto.get())
        ok, reason = self.app.set_autostart(want)
        self.refresh_autostart()
        if ok:
            self.set_status("已开启开机自启" if want else "已关闭开机自启")
        else:
            self.set_status("开机自启设置失败：%s" % (reason or "原因未知"), error=True)

    def on_interval(self, _evt=None):
        label = self.var_interval.get()
        for value, text in INTERVAL_CHOICES:
            if text == label:
                self.app.set_interval(value)
                break

    def on_debug(self):
        self.app.open_debug()

    def on_plugin(self):
        """按当前插着的设备生成 LiteMonitor 插件，并复制进它的 plugins 目录。

        插件的内容必须跟着"这台电脑上现在插了什么"走 —— 发布包里塞一份写死
        设备清单的 JSON 只会让别人的 LiteMonitor 多出几行 ⛔，所以这里现生成。
        """
        from . import lmplugin, proto

        keys = lmplugin.present_keys()
        note = ""
        if not keys:
            if not messagebox.askyesno(
                    "没检测到设备",
                    "当前没插受支持的设备。\n\n"
                    "要按支持的全部机型生成吗？没插的会显示成 ⛔。",
                    parent=self):
                return
            keys = [p["key"] for p in proto.PROFILES]
            note = "\n\n注意：这次是按全部机型生成的，没插的会显示 ⛔。"

        out_dir = os.path.join(app_dir(), "litemonitor")
        try:
            res = lmplugin.install(keys, out_dir)
        except OSError as e:
            messagebox.showerror("生成失败", "插件文件写不出来：\n%s" % e, parent=self)
            return

        if res["dst"]:
            self._plugin_done(res["dst"], note)
            return

        msg = "插件已生成：\n%s\n\n没自动找到 LiteMonitor 的 plugins 目录。" % res["src"]
        if res["dirs"]:
            msg += "\n\n找过这些位置，但写不进去：\n" + "\n".join(res["dirs"])
        if res["error"]:
            msg += "\n\n最后一个错误：\n" + res["error"]
        msg += "\n\n要手动选一下目录吗？（一般是 LiteMonitor\\resources\\plugins）"
        if not messagebox.askyesno("没找到 LiteMonitor", msg, parent=self):
            return

        picked = filedialog.askdirectory(
            title="选择 LiteMonitor 的 resources\\plugins 目录", parent=self)
        if not picked:
            return
        try:
            res = lmplugin.install(keys, out_dir, target_dir=picked)
        except OSError as e:
            messagebox.showerror("写入失败", "%s" % e, parent=self)
            return
        if res["dst"]:
            self._plugin_done(res["dst"], note)
        else:
            messagebox.showerror(
                "写入失败", "写不进 %s：\n%s" % (picked, res["error"] or "未知原因"),
                parent=self)

    def _plugin_done(self, dst, note=""):
        extra = ""
        if getattr(self.app, "bridge", None) is None:
            extra = ("\n\n提醒：LiteMonitor 是从本地接口取数的，当前实例没开这个接口。\n"
                     "请用 --tray --serve 启动（自启就 --install --serve），"
                     "否则插件会显示 ❌。")
        messagebox.showinfo(
            "插件已写入",
            "已写入：\n%s\n\n"
            "接着去 LiteMonitor：\n"
            "  设置 → 插件 → 重载插件\n"
            "重载后没出来，就退出 LiteMonitor 再打开。%s%s" % (dst, note, extra),
            parent=self)
        self.set_status("插件已写入，去 LiteMonitor「设置 → 插件 → 重载插件」")

    def on_quit(self):
        self.app.quit_app()

    def on_close(self):
        self.app.close_panel()

    def remember_geometry(self):
        try:
            self.app.settings.geometry = "%dx%d+%d+%d" % (
                self.winfo_width(), self.winfo_height(), self.winfo_x(), self.winfo_y())
        except Exception:
            pass
