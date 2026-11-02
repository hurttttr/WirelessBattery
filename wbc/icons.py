# -*- coding: utf-8 -*-
"""图标光栅化：不用 Pillow，也不用 GDI 文字。

托盘图标的"内容就是电量数字"，所以这里自带一套点阵字体：
    两位数用 5x7，三位数（100）用 3x5。
每种形状/笔画都用超采样求覆盖率，边缘是抗锯齿的，
再直接写进 32bpp 顶朝下的 DIB，交给 CreateIconIndirect 变成 HICON。
"""

import ctypes
import struct
from ctypes import wintypes as w

from . import win32 as wx

# ---------------------------------------------------------------- 点阵字体

FONT_5x7 = {
    "0": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
}

FONT_3x5 = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    "-": ("000", "000", "111", "000", "000"),
}


# ---------------------------------------------------------------- 覆盖率

def _rrect_inside(px, py, x0, y0, x1, y1, r):
    r = max(0.0, min(r, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    cx = x0 + r if px < x0 + r else (x1 - r if px > x1 - r else px)
    cy = y0 + r if py < y0 + r else (y1 - r if py > y1 - r else py)
    dx = px - cx
    dy = py - cy
    return dx * dx + dy * dy <= r * r


def _coverage(size, inside, ss=4):
    """返回 size*size 的覆盖率列表（0.0 ~ 1.0）。"""
    step = 1.0 / ss
    half = step / 2.0
    grid = []
    for y in range(size):
        row = []
        for x in range(size):
            hit = 0
            for sy in range(ss):
                py = y + sy * step + half
                for sx in range(ss):
                    if inside(x + sx * step + half, py):
                        hit += 1
            row.append(hit / float(ss * ss))
        grid.append(row)
    return grid


def _text_coverage(size, text, ox, oy, scale, font, gw, gh, gap, ss=4):
    cell_w = gw + gap
    n = len(text)
    step = 1.0 / ss
    half = step / 2.0
    grid = []
    for y in range(size):
        row = []
        for x in range(size):
            hit = 0
            for sy in range(ss):
                gy = (y + sy * step + half - oy) / scale
                if 0 <= gy < gh:
                    gyi = int(gy)
                    for sx in range(ss):
                        gx = (x + sx * step + half - ox) / scale
                        if 0 <= gx < cell_w * n - gap:
                            ci = int(gx // cell_w)
                            gxi = int(gx - ci * cell_w)
                            if gxi < gw:
                                ch = text[ci]
                                glyph = font.get(ch)
                                if glyph is not None and glyph[gyi][gxi] == "1":
                                    hit += 1
            row.append(hit / float(ss * ss))
        grid.append(row)
    return grid


# ---------------------------------------------------------------- 图标渲染

def _fit_text(text, size):
    """选字体并算出 (font, gw, gh, gap, scale, ox, oy)。

    用整数倍缩放 + 整数原点：点阵格正好落在像素边界上，每个格子要么全白要么
    全透明。非整数缩放下 3x5 的字会卡在像素缝里，出来是一片糊的渐变，
    16~32px 的托盘尺寸下尤其明显。
    """
    pad = max(0.5, size * 0.03)
    avail = size - 2 * pad
    best = None
    for font, gw, gh in ((FONT_5x7, 5, 7), (FONT_3x5, 3, 5)):
        for gap in (1, 0):
            unit_w = (gw + gap) * len(text) - gap
            s = int(min(avail / unit_w, avail / gh))
            if s < 1:
                continue
            # 先保证有字间距（粘在一起比小一号更难认），再比字形高度
            rank = (gap, gh * s)
            if best is None or rank > best[0]:
                best = (rank, (font, gw, gh, gap, s, unit_w))
    if best is None:
        font, gw, gh, gap = FONT_3x5, 3, 5, 0
        unit_w = gw * len(text)
        s = 1
    else:
        font, gw, gh, gap, s, unit_w = best[1]
    ox = int(round((size - unit_w * s) / 2.0))
    oy = int(round((size - gh * s) / 2.0))
    return font, gw, gh, gap, float(s), float(ox), float(oy)


def _compose(size, bg_cov, txt_cov, bg_rgb, fg_rgb=(255, 255, 255)):
    """把白色数字叠在底色块上，输出 RGBA（top-down，逐行）。"""
    out = bytearray(size * size * 4)
    br, bg, bb = bg_rgb
    fr, fg, fb = fg_rgb
    for y in range(size):
        a_row = bg_cov[y]
        t_row = txt_cov[y]
        base = y * size * 4
        for x in range(size):
            a = a_row[x]
            if a <= 0.0 and t_row[x] <= 0.0:
                continue
            t = t_row[x] * a
            r = int(br + (fr - br) * t)
            g = int(bg + (fg - bg) * t)
            b = int(bb + (fb - bb) * t)
            i = base + x * 4
            out[i] = r
            out[i + 1] = g
            out[i + 2] = b
            out[i + 3] = int(255 * a)
    return bytes(out)


def render_number_icon(text, color, size, radius_ratio=0.22):
    """圆角方块底 + 白色数字。返回 RGBA bytes。"""
    ss = 4 if size <= 48 else 2
    inset = 0.4
    bg_cov = _coverage(
        size,
        lambda px, py: _rrect_inside(px, py, inset, inset, size - inset, size - inset,
                                     size * radius_ratio),
        ss)
    font, gw, gh, gap, scale, ox, oy = _fit_text(text, size)
    txt_cov = _text_coverage(size, text, ox, oy, scale, font, gw, gh, gap, ss)
    return _compose(size, bg_cov, txt_cov, color)


def render_app_icon(size, level=0.72):
    """程序图标：一节电池（描边 + 按电量填充）。"""
    ss = 4 if size <= 64 else 1
    s = float(size)
    G = dict(body=(0.05, 0.25, 0.75, 0.75, 0.11), cap=(0.75, 0.39, 0.90, 0.61, 0.035),
             inner=(0.11, 0.31, 0.69, 0.69, 0.05))

    def rr(key, px, py):
        x0, y0, x1, y1, r = G[key]
        return _rrect_inside(px, py, x0 * s, y0 * s, x1 * s, y1 * s, r * s)

    def shell(px, py):
        return rr("body", px, py) or rr("cap", px, py)

    def inner(px, py):
        return rr("inner", px, py)

    def fill(px, py):
        x0 = G["inner"][0] * s
        x1 = G["inner"][2] * s
        return rr("inner", px, py) and px <= x0 + (x1 - x0) * level

    cov_shell = _coverage(size, shell, ss)
    cov_inner = _coverage(size, inner, ss)
    cov_fill = _coverage(size, fill, ss)

    outline = (45, 55, 72)
    empty = (228, 233, 240)
    filled = (47, 163, 107)

    out = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            a = cov_shell[y][x]
            if a <= 0:
                continue
            h = cov_inner[y][x]
            f = cov_fill[y][x]
            if h > 0:
                # 内部：空的部分浅灰，填到的部分绿色
                r = int(empty[0] + (filled[0] - empty[0]) * f)
                g = int(empty[1] + (filled[1] - empty[1]) * f)
                b = int(empty[2] + (filled[2] - empty[2]) * f)
                alpha = int(255 * max(a, h))
            else:
                r, g, b = outline
                alpha = int(255 * a)
            i = (y * size + x) * 4
            out[i] = r
            out[i + 1] = g
            out[i + 2] = b
            out[i + 3] = alpha
    return bytes(out)


# ---------------------------------------------------------------- HICON

def create_hicon(rgba, size):
    """RGBA(top-down) -> HICON。

    注意方向：CreateIconIndirect 把 hbmColor 当成**底朝下**的 DIB 来读。
    这里必须用正高度，并把行序倒过来写，否则图标会上下翻转
    （圆角方块上下对称，看不出问题，但数字会变成倒的）。
    """
    bmi = wx.BITMAPV5HEADER()
    bmi.bV5Size = ctypes.sizeof(wx.BITMAPV5HEADER)
    bmi.bV5Width = size
    bmi.bV5Height = size              # 正高度 = 底朝下
    bmi.bV5Planes = 1
    bmi.bV5BitCount = 32
    bmi.bV5Compression = wx.BI_BITFIELDS
    bmi.bV5RedMask = 0x00FF0000
    bmi.bV5GreenMask = 0x0000FF00
    bmi.bV5BlueMask = 0x000000FF
    bmi.bV5AlphaMask = 0xFF000000
    bmi.bV5CSType = wx.LCS_sRGB

    hdc = wx.gdi32.CreateCompatibleDC(None)
    if not hdc:
        raise OSError("CreateCompatibleDC 失败")
    bits = ctypes.c_void_p()
    hbm = wx.gdi32.CreateDIBSection(
        hdc, ctypes.byref(bmi), wx.DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
    if not hbm or not bits:
        wx.gdi32.DeleteDC(hdc)
        raise OSError("CreateDIBSection 失败")

    try:
        row = size * 4
        bgra = bytearray(size * size * 4)
        for y in range(size):
            src = y * row                    # 源：顶朝下
            dst = (size - 1 - y) * row       # 目标：底朝下
            for i in range(0, row, 4):
                bgra[dst + i] = rgba[src + i + 2]
                bgra[dst + i + 1] = rgba[src + i + 1]
                bgra[dst + i + 2] = rgba[src + i]
                bgra[dst + i + 3] = rgba[src + i + 3]
        ctypes.memmove(bits, bytes(bgra), len(bgra))

        stride = ((size + 15) // 16) * 2
        mask_bytes = bytes(stride * size)     # 全 0 = 全不透明，透明度交给 alpha 通道
        mask = wx.gdi32.CreateBitmap(size, size, 1, 1, mask_bytes)
        info = wx.ICONINFO()
        info.fIcon = 1
        info.xHotspot = 0
        info.yHotspot = 0
        info.hbmMask = mask
        info.hbmColor = hbm
        hicon = wx.user32.CreateIconIndirect(ctypes.byref(info))
        if mask:
            wx.gdi32.DeleteObject(mask)
        if not hicon:
            raise OSError("CreateIconIndirect 失败")
        return hicon
    finally:
        wx.gdi32.DeleteObject(hbm)
        wx.gdi32.DeleteDC(hdc)


def destroy_hicon(hicon):
    if hicon:
        try:
            wx.user32.DestroyIcon(hicon)
        except Exception:
            pass


# ---------------------------------------------------------------- .ico 文件

def write_ico(path, sizes=(16, 20, 24, 32, 48, 64), renderer=None):
    """写出多尺寸 .ico（给快捷方式和窗口图标用）。"""
    renderer = renderer or render_app_icon
    images = []
    for s in sizes:
        images.append((s, renderer(s)))

    n = len(images)
    header = struct.pack("<HHH", 0, 1, n)
    entries = []
    payloads = []
    offset = 6 + 16 * n
    for size, rgba in images:
        bmp_header = struct.pack(
            "<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, size * size * 4, 0, 0, 0, 0)
        rows = []
        for y in range(size - 1, -1, -1):          # BMP 是底朝下
            row = bytearray()
            base = y * size * 4
            for x in range(size):
                i = base + x * 4
                row += bytes((rgba[i + 2], rgba[i + 1], rgba[i], rgba[i + 3]))
            rows.append(bytes(row))
        xor = b"".join(rows)
        stride = ((size + 31) // 32) * 4
        and_mask = bytes(stride * size)
        payload = bmp_header + xor + and_mask
        dim = 0 if size >= 256 else size
        entries.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(payload), offset))
        payloads.append(payload)
        offset += len(payload)

    with open(path, "wb") as f:
        f.write(header)
        for e in entries:
            f.write(e)
        for p in payloads:
            f.write(p)
    return path
