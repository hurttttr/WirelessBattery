# -*- coding: utf-8 -*-
"""完整调试版：列出所有 HID 接口，转储 Feature Report、探测 Report ID、
试发命令、监听 Input Report、手动标定协议。

所有耗时操作（尤其是 listen）都放到后台线程，结果经 queue 回到主线程，
避免把 Tk 卡死。
"""

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from . import hid
from . import settings
from . import theme
from . import win32 as wx
from .theme import S, font


def parse_hex(text):
    cleaned = text.replace("0x", " ").replace("0X", " ").replace(",", " ")
    out = bytearray()
    for part in cleaned.split():
        try:
            out.append(int(part, 16) & 0xFF)
        except ValueError:
            raise ValueError("无法识别的字节：%r" % part)
    return bytes(out)


def hex_dump(data, base=0, width=16):
    lines = []
    for off in range(0, len(data), width):
        chunk = data[off:off + width]
        hx = " ".join("%02X" % b for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append("%04X  %-*s  %s" % (base + off, width * 3 - 1, hx, asc))
    return "\n".join(lines)


class DebugWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("无线外设电量 · 调试版")
        self.configure(bg=theme.BG)
        self.geometry("%dx%d" % (S(940), S(640)))
        self.minsize(S(760), S(520))

        self.interfaces = []
        self.calib = settings.Calibration()
        self._q = queue.Queue()
        self._poll_job = None
        self._busy = False

        self._build()
        self.reload_devices()
        self._poll()

    # ------------------------------------------------------------ 布局

    def _build(self):
        pad = S(10)
        top = tk.Frame(self, bg=theme.BG)
        top.pack(fill="x", padx=pad, pady=(pad, 0))

        self._btn(top, "刷新设备列表", self.reload_devices).pack(side="left")
        self._btn(top, "打开选中", self.on_open).pack(side="left", padx=(S(6), 0))
        self._btn(top, "标定选中", self.on_calibrate).pack(side="left", padx=(S(6), 0))
        self._btn(top, "清空日志", self.on_clear).pack(side="left", padx=(S(6), 0))
        self.lbl_count = tk.Label(top, text="", bg=theme.BG, fg=theme.SUB, font=font(9))
        self.lbl_count.pack(side="right")

        row2 = tk.Frame(self, bg=theme.BG)
        row2.pack(fill="x", padx=pad, pady=(S(6), 0))
        tk.Label(row2, text="Report ID", bg=theme.BG, fg=theme.SUB, font=font(9)).pack(side="left")
        self.e_rid = tk.Entry(row2, width=5, font=font(9))
        self.e_rid.insert(0, "08")
        self.e_rid.pack(side="left", padx=(S(4), S(10)))
        tk.Label(row2, text="长度", bg=theme.BG, fg=theme.SUB, font=font(9)).pack(side="left")
        self.e_len = tk.Entry(row2, width=5, font=font(9))
        self.e_len.insert(0, "65")
        self.e_len.pack(side="left", padx=(S(4), S(10)))
        self._btn(row2, "读 Feature", self.on_feature).pack(side="left")
        self._btn(row2, "转储全部 Feature", self.on_dump_feature).pack(side="left", padx=(S(6), 0))
        self._btn(row2, "查看 Feature Caps", self.on_value_caps).pack(side="left", padx=(S(6), 0))

        row3 = tk.Frame(self, bg=theme.BG)
        row3.pack(fill="x", padx=pad, pady=(S(6), 0))
        tk.Label(row3, text="发送", bg=theme.BG, fg=theme.SUB, font=font(9)).pack(side="left")
        self.e_tx = tk.Entry(row3, font=("Consolas", -S(10)))
        self.e_tx.insert(0, "08 7D 72 02 00 01 07 01")
        self.e_tx.pack(side="left", fill="x", expand=True, padx=(S(4), S(6)))
        self._btn(row3, "发送 Output", self.on_send).pack(side="left")
        self._btn(row3, "监听 3 秒", lambda: self.on_listen(3.0)).pack(side="left", padx=(S(6), 0))
        self._btn(row3, "探测 Report ID", self.on_probe).pack(side="left", padx=(S(6), 0))

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=pad, pady=(S(8), 0))

        cols = ("vidpid", "usage", "report", "write", "product")
        self.tree = ttk.Treeview(body, columns=cols, show="headings", height=12)
        widths = (90, 130, 150, 60, 260)
        heads = ("VID:PID", "Usage", "报文(入/出/特)", "可写", "产品名 / 路径")
        for c, hd, wd in zip(cols, heads, widths):
            self.tree.heading(c, text=hd)
            self.tree.column(c, width=S(wd), anchor="w", stretch=(c == "product"))
        vs = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="top", fill="both", expand=False)
        vs.place(in_=self.tree, relx=1.0, rely=0, relheight=1.0, anchor="ne")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        logf = tk.Frame(self, bg=theme.BG)
        logf.pack(fill="both", expand=True, padx=pad, pady=(S(8), pad))
        self.log = tk.Text(logf, bg="#12161C", fg="#D5DBE3", insertbackground="#D5DBE3",
                           font=("Consolas", -S(10)), relief="flat", bd=0, wrap="none",
                           padx=S(8), pady=S(6))
        sb = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _btn(self, master, text, cmd):
        return tk.Button(master, text=text, command=cmd, relief="flat", bd=0,
                         bg=theme.CARD, fg=theme.TEXT, activebackground=theme.TRACK,
                         font=font(9), cursor="hand2", padx=S(9), pady=S(3),
                         highlightthickness=1, highlightbackground=theme.BORDER)

    # ------------------------------------------------------------ 日志与线程

    def write(self, text):
        self.log.insert("end", text.rstrip("\n") + "\n")
        self.log.see("end")

    def _post(self, text):
        self._q.put(text)

    def _poll(self):
        try:
            while True:
                try:
                    item = self._q.get_nowait()
                except queue.Empty:
                    break
                self.write(item)
        except Exception:
            pass
        if self.winfo_exists():
            self._poll_job = self.after(100, self._poll)

    def run_async(self, fn, *args):
        if self._busy:
            self.write("… 上一个操作还没结束")
            return
        self._busy = True

        def wrapper():
            try:
                fn(*args)
            except Exception as e:
                self._post("!! %r" % (e,))
            finally:
                self._busy = False
        threading.Thread(target=wrapper, daemon=True).start()

    # ------------------------------------------------------------ 设备列表

    def reload_devices(self):
        self.tree.delete(*self.tree.get_children())
        self.interfaces = []
        try:
            self.interfaces = hid.enumerate_interfaces(full=True)
        except Exception as e:
            self.write("枚举失败：%r" % (e,))
            return
        for i, iface in enumerate(self.interfaces):
            usage = "%04X/%04X" % (iface.usage_page, iface.usage)
            report = "%d / %d / %d" % (iface.input_len, iface.output_len, iface.feature_len)
            self.tree.insert("", "end", iid=str(i), values=(
                iface.vidpid, usage, report, "是" if iface.can_write else "否",
                (iface.product or "?") + "   " + iface.short_path()))
        self.lbl_count.configure(text="共 %d 个 HID 接口" % len(self.interfaces))
        self.write("已枚举 %d 个 HID 接口" % len(self.interfaces))

    def selected(self):
        sel = self.tree.selection()
        if not sel:
            self.write("请先在列表里选中一个接口")
            return None
        idx = int(sel[0])
        if idx >= len(self.interfaces):
            return None
        return self.interfaces[idx]

    def on_select(self, _evt=None):
        iface = self.selected()
        if iface is None:
            return
        self.e_rid.delete(0, "end")
        self.e_rid.insert(0, "00")
        self.e_len.delete(0, "end")
        self.e_len.insert(0, str(iface.output_len or iface.feature_len or 64))
        self.write("选中 %s  %s  usage=%04X/%04X  in=%d out=%d feat=%d" % (
            iface.vidpid, iface.product or "?", iface.usage_page, iface.usage,
            iface.input_len, iface.output_len, iface.feature_len))
        self.write("     " + iface.path)

    # ------------------------------------------------------------ 操作

    def on_open(self):
        iface = self.selected()
        if iface is None:
            return
        h, writable = wx.open_device(iface.path, write=True)
        if not h:
            self.write("打开失败 err=%d" % wx.last_error())
            return
        wx.kernel32.CloseHandle(h)
        self.write("打开成功，%s（%s）" % ("可读写" if writable else "只读——系统独占集合"))

    def on_feature(self):
        iface = self.selected()
        if iface is None:
            return
        try:
            rid = int(self.e_rid.get().strip() or "0", 16)
            length = int(self.e_len.get().strip() or str(iface.feature_len))
        except ValueError:
            self.write("Report ID / 长度 必须是数字")
            return

        def job():
            with hid.HidController(iface, prefer_write=False) as ctrl:
                data = ctrl.get_feature(rid, length)
                if data is None:
                    self._post("HidD_GetFeature 失败 err=%d" % wx.last_error())
                    return
                self._post("Feature Report ID=0x%02X 长度=%d\n%s" % (rid, len(data), hex_dump(data)))
        self.run_async(job)

    def on_dump_feature(self):
        iface = self.selected()
        if iface is None:
            return
        if not iface.feature_len:
            self.write("该接口没有 Feature Report")
            return

        def job():
            total = 0
            with hid.HidController(iface, prefer_write=False) as ctrl:
                for rid in range(0, 256):
                    data = ctrl.get_feature(rid, iface.feature_len)
                    if not data or all(b == 0 for b in data):
                        if rid > 32 and total:
                            break
                        continue
                    total += 1
                    self._post("── Feature ID=0x%02X\n%s" % (rid, hex_dump(data)))
            if not total:
                self._post("没有读到任何非空的 Feature Report（可能有 Report ID 限制）")
        self.run_async(job)

    def on_value_caps(self):
        iface = self.selected()
        if iface is None:
            return

        def job():
            caps = hid.feature_value_caps(iface)
            if not caps:
                self._post("没有 Feature Value Caps（或者读不到）")
                return
            self._post("Feature Value Caps（%d 项）：" % len(caps))
            for c in caps:
                self._post("  rpt=0x%02X usagePage=0x%04X usage=0x%04X bit=%d size=%d logical=%d..%d" % (
                    c.report_id, c.usage_page, c.usage, c.bit_field, c.bit_size,
                    c.logical_min, c.logical_max))
        self.run_async(job)

    def on_send(self):
        iface = self.selected()
        if iface is None:
            return
        try:
            payload = parse_hex(self.e_tx.get())
        except ValueError as e:
            self.write(str(e))
            return
        if not payload:
            self.write("发送内容为空")
            return
        if not iface.can_write:
            self.write("该接口只读（系统独占的顶层集合只能读 Feature），无法发送")
            return
        length = iface.output_len or len(payload)
        body = bytearray(length)
        body[:min(len(payload), length)] = payload[:length]
        if len(payload) > length:
            self.write("注意：发送内容超过 OutputReportByteLength(%d)，已截断" % length)

        def job():
            self._post("TX →\n" + hex_dump(payload))
            with hid.HidController(iface) as ctrl, hid.OverlappedReader(iface) as reader:
                reader.drain(60)
                reader.begin()
                ctrl.write_output(bytes(body))
                deadline = time.monotonic() + 1.5
                while True:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    data = reader.wait(int(left * 1000) + 1)
                    if data is None:
                        break
                    self._post("RX ←\n" + hex_dump(data))
                    reader.begin()
        self.run_async(job)

    def on_listen(self, seconds=3.0):
        iface = self.selected()
        if iface is None:
            return

        def job():
            self._post("监听 Input Report %.0f 秒…" % seconds)
            with hid.OverlappedReader(iface) as reader:
                reader.drain(50)
                deadline = time.monotonic() + seconds
                n = 0
                while True:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    reader.begin()
                    data = reader.wait(int(left * 1000) + 1)
                    if data is None:
                        continue
                    n += 1
                    self._post("IN ←\n" + hex_dump(data))
                self._post("监听结束，共 %d 帧" % n)
        self.run_async(job)

    def on_probe(self):
        iface = self.selected()
        if iface is None:
            return

        def job():
            self._post("探测 Report ID：监听 3 秒，记录输入报文的首字节…")
            seen = {}
            with hid.OverlappedReader(iface) as reader:
                reader.drain(50)
                deadline = time.monotonic() + 3.0
                while True:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    reader.begin()
                    data = reader.wait(int(left * 1000) + 1)
                    if not data:
                        continue
                    rid = data[0]
                    seen[rid] = seen.get(rid, 0) + 1
            if not seen:
                self._post("没有捕获到输入报文。对于没有 Feature Report 的设备，"
                           "需要先发一条指令才会产生回复。")
                return
            self._post("捕获到的 Report ID：")
            for rid in sorted(seen):
                self._post("  0x%02X  出现 %d 次" % (rid, seen[rid]))
        self.run_async(job)

    # ------------------------------------------------------------ 手动标定

    def on_calibrate(self):
        iface = self.selected()
        if iface is None:
            return
        CalibrateDialog(self, iface, self.calib,
                        on_saved=lambda entry: self._calib_saved(iface, entry))

    def _calib_saved(self, iface, entry):
        if entry:
            self.write("已保存标定：%s -> %s" % (
                settings.Calibration.key_for(iface), entry))
        else:
            self.write("已删除标定：%s" % settings.Calibration.key_for(iface))

    def on_clear(self):
        self.log.delete("1.0", "end")


class CalibrateDialog(tk.Toplevel):
    def __init__(self, master, iface, calib, on_saved=None):
        super().__init__(master)
        self.title("手动标定")
        self.configure(bg=theme.BG)
        self.resizable(False, False)
        self.iface = iface
        self.calib = calib
        self.on_saved = on_saved
        self.transient(master)

        pad = S(14)
        tk.Label(self, text="%s  %s" % (iface.vidpid, iface.product or ""),
                 bg=theme.BG, fg=theme.TEXT, font=font(10, True)).pack(
            anchor="w", padx=pad, pady=(pad, 0))
        tk.Label(self, text="usage %04X/%04X · 报文 %d/%d/%d" % (
            iface.usage_page, iface.usage, iface.input_len, iface.output_len, iface.feature_len),
            bg=theme.BG, fg=theme.SUB, font=font(9)).pack(anchor="w", padx=pad)

        form = tk.Frame(self, bg=theme.BG)
        form.pack(fill="x", padx=pad, pady=(S(10), 0))
        self.vars = {}
        fields = [("report_id", "Report ID", ""), ("byte_index", "电量字节下标", "7"),
                  ("scale", "倍率", "1"), ("offset", "偏移", "0")]
        existing = calib.get(iface) or {}
        for i, (key, label, default) in enumerate(fields):
            tk.Label(form, text=label, bg=theme.BG, fg=theme.SUB, font=font(9)).grid(
                row=i, column=0, sticky="w", pady=S(3))
            var = tk.StringVar(value=str(existing.get(key, default)))
            tk.Entry(form, textvariable=var, width=18, font=font(9)).grid(
                row=i, column=1, sticky="w", padx=(S(10), 0), pady=S(3))
            self.vars[key] = var

        tk.Label(self, text="留空 Report ID 表示该报文没有 Report ID；"
                            "电量 = 原始字节 × 倍率 + 偏移",
                 bg=theme.BG, fg=theme.SUB, font=font(8), wraplength=S(320),
                 justify="left").pack(anchor="w", padx=pad, pady=(S(8), 0))

        btns = tk.Frame(self, bg=theme.BG)
        btns.pack(fill="x", padx=pad, pady=S(12))
        tk.Button(btns, text="保存", command=self.save, relief="flat", bd=0,
                  bg=theme.ACCENT, fg="white", activebackground=theme.ACCENT,
                  font=font(9), padx=S(16), pady=S(4), cursor="hand2").pack(side="right")
        tk.Button(btns, text="删除已有标定", command=self.remove, relief="flat", bd=0,
                  bg=theme.CARD, fg=theme.TEXT, font=font(9), padx=S(12), pady=S(4),
                  highlightthickness=1, highlightbackground=theme.BORDER,
                  cursor="hand2").pack(side="right", padx=(0, S(8)))

    def save(self):
        try:
            rid_txt = self.vars["report_id"].get().strip()
            rid = int(rid_txt, 16) if rid_txt else None
            idx = int(self.vars["byte_index"].get().strip() or "0")
            scale = float(self.vars["scale"].get().strip() or "1")
            offset = float(self.vars["offset"].get().strip() or "0")
        except ValueError as e:
            self.bell()
            self.title("手动标定（输入有误）")
            return
        entry = self.calib.put(self.iface, rid, idx, scale, offset)
        if self.on_saved:
            self.on_saved(entry)
        self.destroy()

    def remove(self):
        self.calib.remove(self.iface)
        if self.on_saved:
            self.on_saved({})
        self.destroy()
