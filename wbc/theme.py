# -*- coding: utf-8 -*-
"""界面主题：DPI 缩放、配色、字体。"""

from . import win32 as wx

SCALE = 1.0
FONT_UI = "Microsoft YaHei UI"
FONT_NUM = "Segoe UI"

BG = "#F4F5F7"
CARD = "#FFFFFF"
BORDER = "#E3E6EA"
TEXT = "#1B1F24"
SUB = "#7A828C"
TRACK = "#ECEFF3"
ACCENT = "#2F6FEB"

GREEN = "#2FA36B"
AMBER = "#E0A32E"
RED = "#D64545"
GREY = "#A8B0BA"


def init_scale():
    global SCALE
    try:
        SCALE = wx.get_dpi_scale()
    except Exception:
        SCALE = 1.0
    if SCALE < 1.0:
        SCALE = 1.0
    return SCALE


def S(v):
    """逻辑像素 -> 物理像素（DPI 已声明，不会糊）。"""
    return int(round(v * SCALE))


def font(size, bold=False, family=None):
    return (family or FONT_UI, -S(size), "bold") if bold else (family or FONT_UI, -S(size))


def level_color(percent, ok=True):
    if not ok or percent is None:
        return GREY
    if percent >= 60:
        return GREEN
    if percent >= 30:
        return AMBER
    return RED


def round_rect(canvas, x0, y0, x1, y1, r, fill=None, outline=None, width=1, tags=()):
    """精确的圆角矩形：两块矩形 + 四个整圆填充，四条直线 + 四段圆弧描边。

    Tk 画布没有抗锯齿，但这样画出来的边一定连续、不会出现断线或缺角。
    """
    items = []
    r = max(1.0, min(r, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    tag = tags or ()
    if fill:
        items.append(canvas.create_rectangle(x0 + r, y0, x1 - r, y1,
                                             fill=fill, outline="", tags=tag))
        items.append(canvas.create_rectangle(x0, y0 + r, x1, y1 - r,
                                             fill=fill, outline="", tags=tag))
        for cx, cy in ((x0, y0), (x1 - 2 * r, y0), (x0, y1 - 2 * r), (x1 - 2 * r, y1 - 2 * r)):
            items.append(canvas.create_oval(cx, cy, cx + 2 * r, cy + 2 * r,
                                            fill=fill, outline="", tags=tag))
    if outline:
        for x0_, y0_, x1_, y1_, start in (
                (x0, y0, x0 + 2 * r, y0 + 2 * r, 90),
                (x1 - 2 * r, y0, x1, y0 + 2 * r, 0),
                (x1 - 2 * r, y1 - 2 * r, x1, y1, 270),
                (x0, y1 - 2 * r, x0 + 2 * r, y1, 180)):
            items.append(canvas.create_arc(x0_, y0_, x1_, y1_, start=start, extent=90,
                                           style="arc", outline=outline, width=width, tags=tag))
        items.append(canvas.create_line(x0 + r, y0, x1 - r, y0, fill=outline, width=width, tags=tag))
        items.append(canvas.create_line(x0 + r, y1, x1 - r, y1, fill=outline, width=width, tags=tag))
        items.append(canvas.create_line(x0, y0 + r, x0, y1 - r, fill=outline, width=width, tags=tag))
        items.append(canvas.create_line(x1, y0 + r, x1, y1 - r, fill=outline, width=width, tags=tag))
    return items
