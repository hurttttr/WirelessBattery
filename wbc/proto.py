# -*- coding: utf-8 -*-
"""设备协议层。

已验证的两套协议：
    1) ATK / VXE 无线鼠标（Nordic 方案）—— 64 字节 Output Report 0x08
    2) DARKRIM SHADOW 耳机（杰理 JL_RCSP）—— 65 字节报文，CRC16-CCITT-FALSE

外加一条兜底链（换设备时用）：
    标准 HID 电量字段 → 厂商协议表 → 罗技 HID++ 2.0 → 监听 Input Report → 手动标定
"""

import json
import os
import threading
import time

from . import hid
from . import logx
from .logx import dbg

# ---------------------------------------------------------------- 设备定义

MOUSE_VID = 0x373B
MOUSE_PID = 0x1216
HEADSET_VID = 0x3654
HEADSET_PID = 0x6901
KEYBOARD_VID = 0x3554
KEYBOARD_PID = 0xFA09
# Keychron Link-KM（京东京造 K 系列同方案）的 2.4G 接收器
KEYCHRON_VID = 0x362D
KEYCHRON_PID = 0xD107

TARGETS = {
    "mouse": {
        "vid": MOUSE_VID, "pid": MOUSE_PID,
        "name": "ATK / VXE 无线鼠标", "subtitle": "2.4G NANO 接收器",
    },
    "headset": {
        "vid": HEADSET_VID, "pid": HEADSET_PID,
        "name": "DARKRIM SHADOW", "subtitle": "杰理 dongle",
    },
}


class Reading(object):
    """一次电量读取的结果。"""

    __slots__ = ("key", "name", "subtitle", "percent", "source", "note", "ok", "ts", "extra")

    def __init__(self, key, name="", subtitle="", percent=None, source="",
                 note="", ok=False, extra=None):
        self.key = key
        self.name = name
        self.subtitle = subtitle
        self.percent = percent
        self.source = source
        self.note = note
        self.ok = ok
        self.ts = time.time()
        self.extra = extra or {}

    @property
    def stamp(self):
        return time.strftime("%H:%M:%S", time.localtime(self.ts))

    def __repr__(self):
        if self.ok:
            return "<%s %s%% [%s]>" % (self.key, self.percent, self.source)
        return "<%s ERR %s>" % (self.key, self.note or "-")


def _target_reading(key, **kw):
    t = TARGETS.get(key, {})
    kw.setdefault("name", t.get("name", key))
    kw.setdefault("subtitle", t.get("subtitle", ""))
    return Reading(key, **kw)


# ---------------------------------------------------------------- 设备匹配

_cache = {}
_cache_lock = threading.Lock()


def _all_interfaces():
    return hid.enumerate_interfaces(full=True)


def match_mouse(ifaces):
    """ATK：优先 usage page 0xFF05 + 64/64 报文。"""
    cand = [i for i in ifaces if i.vendor_id == MOUSE_VID and i.product_id == MOUSE_PID]
    if not cand:
        return None
    def score(i):
        s = 0
        if i.usage_page == 0xFF05:
            s += 100
        if i.output_len == 64:
            s += 20
        if i.input_len == 64:
            s += 10
        if i.can_write:
            s += 30
        return s
    cand.sort(key=score, reverse=True)
    best = cand[0]
    if best.output_len < 8:
        # 全是非厂商集合，说明接收器在但接口不对
        return best if best.usage_page >= 0xFF00 else None
    return best


def match_headset(ifaces):
    """杰理 dongle：优先 65 字节报文的厂商集合。"""
    cand = [i for i in ifaces if i.vendor_id == HEADSET_VID and i.product_id == HEADSET_PID]
    if not cand:
        return None
    def score(i):
        s = 0
        if max(i.input_len, i.output_len) == 65:
            s += 100
        if i.is_vendor_page:
            s += 40
        if i.can_write:
            s += 20
        if i.feature_len:
            s -= 5
        return s
    cand.sort(key=score, reverse=True)
    return cand[0]


MATCHERS = {"mouse": match_mouse, "headset": match_headset}


def resolve(key, force=False):
    """返回当前可用的接口对象；找不到返回 (None, 原因)。"""
    with _cache_lock:
        cached = _cache.get(key)
    if cached and not force:
        return cached, ""
    ifaces = _all_interfaces()
    iface = MATCHERS[key](ifaces)
    if iface is None:
        with _cache_lock:
            _cache.pop(key, None)
        t = TARGETS[key]
        return None, "未发现设备（%04X:%04X）" % (t["vid"], t["pid"])
    with _cache_lock:
        _cache[key] = iface
    dbg("resolve", key, "->", iface)
    return iface, ""


def invalidate(key=None):
    with _cache_lock:
        if key is None:
            _cache.clear()
        else:
            _cache.pop(key, None)


# ---------------------------------------------------------------- CRC

def crc16_ccitt_false(data):
    """CRC16-CCITT-FALSE：poly 0x1021，init 0xFFFF，不反转，不异或。"""
    crc = 0xFFFF
    for b in data:
        crc ^= (b & 0xFF) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# ---------------------------------------------------------------- ATK / Nordic

ATK_REPORT_ID = 0x08
ATK_REQUEST = bytes([0x08, 0x7D, 0x72, 0x02, 0x00, 0x01, 0x07, 0x01])


def atk_build_request(out_len=64):
    buf = bytearray(out_len)
    n = min(len(ATK_REQUEST), out_len)
    buf[:n] = ATK_REQUEST[:n]
    return bytes(buf)


def atk_parse(resp):
    """回复校验：resp[1] == 0x72 且 resp[5] == 0x07，电量 = resp[7]。"""
    if not resp or len(resp) < 8:
        return None
    if resp[1] != 0x72 or resp[5] != 0x07:
        return None
    pct = resp[7]
    if 0 <= pct <= 100:
        return pct
    return None


def read_mouse(iface=None, attempts=2, timeout_ms=1300):
    iface = iface or resolve("mouse")[0]
    if iface is None:
        return _target_reading("mouse", ok=False, note="未发现接收器")
    if not iface.can_write:
        return _target_reading("mouse", ok=False, note="接口不可写（被系统独占）")

    req = atk_build_request(iface.output_len or 64)
    ctrl = None
    reader = None
    try:
        ctrl = hid.HidController(iface)
        reader = hid.OverlappedReader(iface)
        reader.drain(80)
        for attempt in range(attempts):
            try:
                reader.begin()
            except OSError as e:
                return _target_reading("mouse", ok=False, note=str(e))
            ctrl.write_output(req)
            deadline = time.monotonic() + timeout_ms / 1000.0
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                resp = reader.wait(int(left * 1000) + 1)
                if resp is None:
                    break
                pct = atk_parse(resp)
                if pct is not None:
                    dbg("ATK 电量", pct)
                    return _target_reading("mouse", ok=True, percent=pct,
                                           source="Nordic 0x08 报文")
                try:
                    reader.begin()
                except OSError:
                    break
            dbg("ATK 第 %d 次无回复" % (attempt + 1))
        return _target_reading("mouse", ok=False, note="无回复 · 鼠标可能在休眠，动一下鼠标")
    except OSError as e:
        return _target_reading("mouse", ok=False, note="读取失败：%s" % e)
    finally:
        if reader:
            reader.close()
        if ctrl:
            ctrl.close()


# ---------------------------------------------------------------- 杰理 JL_RCSP

JL_MARK = 0xFE
JL_FLAG_QUERY = 0x10
JL_FLAG_SET = 0x11
JL_OP_GET = 0x02
JL_OP_SET = 0x03

TLV_BATTERY = 0x01
TLV_NAME = 0x02
TLV_EQ = 0x03
TLV_DONGLE_FW = 0x04
TLV_EARBUD_FW = 0x05
TLV_STATUS = 0x06
TLV_ADDRESS = 0x0B

_seq = [0]
_seq_lock = threading.Lock()


def jl_next_seq():
    with _seq_lock:
        v = _seq[0]
        _seq[0] = (v + 1) & 0xFF
        return v


def jl_frame(seq, payload=b"", flag=JL_FLAG_QUERY, opcode=JL_OP_GET,
             fragment=0, more=0, total=65):
    """组帧：
    00 FE <seq> <10|11> 00 <02|03> <分片> <more> <len_LE16> <TLV> <CRC_BE16> 00...
    CRC 覆盖 帧[1 : 10+len]，大端写在 帧[10+len]、帧[11+len]。
    """
    buf = bytearray(total)
    buf[0] = 0x00                       # 无 Report ID，首字节固定 0
    buf[1] = JL_MARK
    buf[2] = seq & 0xFF
    buf[3] = flag & 0xFF
    buf[4] = 0x00
    buf[5] = opcode & 0xFF
    buf[6] = fragment & 0xFF
    buf[7] = more & 0xFF
    n = len(payload)
    buf[8] = n & 0xFF
    buf[9] = (n >> 8) & 0xFF
    if n:
        buf[10:10 + n] = payload
    crc = crc16_ccitt_false(bytes(buf[1:10 + n]))
    buf[10 + n] = (crc >> 8) & 0xFF
    buf[11 + n] = crc & 0xFF
    return bytes(buf)


def jl_query_payload(tlv_type, variant=0):
    """查询 = 把 TLV 长度置 0 发出去。

    variant 0：只发类型字节（长度隐含为 0）
    variant 1：类型 + 显式 2 字节长度 0
    两种写法在不同固件上都出现过，实测哪台吃哪个就在这里收。
    """
    if variant == 0:
        return bytes([tlv_type & 0xFF])
    return bytes([tlv_type & 0xFF, 0x00, 0x00])


def jl_parse_tlv(payload):
    """容错 TLV 解析：同时支持 类型+LE16长度+值 和 类型+值 两种写法。"""
    out = {}
    i = 0
    n = len(payload)
    while i < n:
        t = payload[i]
        i += 1
        if i + 2 <= n:
            ln = payload[i] | (payload[i + 1] << 8)
            if i + 2 + ln <= n:
                out[t] = bytes(payload[i + 2:i + 2 + ln])
                i += 2 + ln
                continue
        out[t] = bytes(payload[i:])
        break
    return out


def jl_parse(buf, strict_crc=True):
    if not buf or len(buf) < 12:
        return None
    if buf[1] != JL_MARK:
        return None
    n = buf[8] | (buf[9] << 8)
    if n <= 0 or 10 + n + 2 > len(buf):
        return None
    crc_rx = (buf[10 + n] << 8) | buf[11 + n]
    crc_calc = crc16_ccitt_false(bytes(buf[1:10 + n]))
    if strict_crc and crc_rx != crc_calc:
        dbg("JL CRC 不匹配 rx=%04X calc=%04X" % (crc_rx, crc_calc))
        return None
    return {
        "seq": buf[2],
        "flag": buf[3],
        "opcode": buf[5],
        "frag": buf[6],
        "more": buf[7],
        "tlv": jl_parse_tlv(buf[10:10 + n]),
        "crc_ok": crc_rx == crc_calc,
    }


def read_headset(iface=None, timeout_ms=900):
    iface = iface or resolve("headset")[0]
    if iface is None:
        return _target_reading("headset", ok=False, note="未发现 dongle")
    if not iface.can_write:
        return _target_reading("headset", ok=False, note="接口不可写（被系统独占）")

    total = iface.output_len or 65
    ctrl = None
    reader = None
    try:
        ctrl = hid.HidController(iface)
        reader = hid.OverlappedReader(iface)
        reader.drain(120)
        for variant in (0, 1):
            seq = jl_next_seq()
            frame = jl_frame(seq, jl_query_payload(TLV_BATTERY, variant), total=total)
            try:
                reader.begin()
            except OSError as e:
                return _target_reading("headset", ok=False, note=str(e))
            ctrl.write_output(frame)
            deadline = time.monotonic() + timeout_ms / 1000.0
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                resp = reader.wait(int(left * 1000) + 1)
                if resp is None:
                    break
                info = jl_parse(resp)
                if info:
                    tlv = info["tlv"]
                    if TLV_BATTERY in tlv and tlv[TLV_BATTERY]:
                        pct = tlv[TLV_BATTERY][0]
                        if 0 <= pct <= 100:
                            extra = {}
                            if TLV_NAME in tlv:
                                try:
                                    extra["name"] = tlv[TLV_NAME].decode("utf-8", "ignore").strip("\x00")
                                except Exception:
                                    pass
                            for k, tag in ((TLV_DONGLE_FW, "dongle_fw"), (TLV_EARBUD_FW, "bud_fw")):
                                if k in tlv and tlv[k]:
                                    extra[tag] = tlv[k].decode("utf-8", "ignore").strip("\x00")
                            dbg("JL 电量", pct, "variant", variant, extra)
                            return _target_reading("headset", ok=True, percent=pct,
                                                   source="JL_RCSP TLV 0x01", extra=extra)
                try:
                    reader.begin()
                except OSError:
                    break
            dbg("JL variant %d 无有效回复" % variant)
        return _target_reading(
            "headset", ok=False, note="无回复 · 耳机可能没开机或没连上 dongle")
    except OSError as e:
        return _target_reading("headset", ok=False, note="读取失败：%s" % e)
    finally:
        if reader:
            reader.close()
        if ctrl:
            ctrl.close()


def query_tlv(tlv_type, iface=None, timeout_ms=900, variant=0):
    """调试版用：读任意一个 TLV（设备名 / 固件 / 状态码 / 地址）。"""
    iface = iface or resolve("headset")[0]
    if iface is None:
        return None
    total = iface.output_len or 65
    ctrl = None
    reader = None
    try:
        ctrl = hid.HidController(iface)
        reader = hid.OverlappedReader(iface)
        reader.drain(100)
        seq = jl_next_seq()
        reader.begin()
        ctrl.write_output(jl_frame(seq, jl_query_payload(tlv_type, variant), total=total))
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            resp = reader.wait(int(left * 1000) + 1)
            if resp is None:
                break
            info = jl_parse(resp)
            if info and tlv_type in info["tlv"]:
                return info["tlv"][tlv_type], info
            reader.begin()
        return None
    except OSError:
        return None
    finally:
        if reader:
            reader.close()
        if ctrl:
            ctrl.close()


# ------------------------------------------------- 迈从 X75 V2（CX 方案 dongle）
# 3554:FA09（厂商字符串 "CX"）是 MCHOSE / 迈从 X75 V2 的 2.4G 接收器，
# 键鼠复合设备，厂商通道 mi_01&col01：
#   usage FF02/0002，Report ID 0x13，输入 / 输出都是 20 字节。
#
# 帧结构（20 字节，定长）：
#   [0]    = 0x13         Report ID
#   [1]    = 命令 / 响应码
#   [4]    = N            载荷长度
#   [5:5+N]= 载荷
#   [19]   = 校验         = 前 19 字节求和取低 8 位（实测两帧都对得上）
#
# 查询命令 0x05：写一条只有 [0]=0x13、[1]=0x05 的 20 字节报文，设备必回两帧：
#   13 05 01 00 0A <10 字节应答> …… FD            应答帧
#   13 0A 01 00 04 05 <电量> <标志> 00 ……          数据帧
# 数据帧的 载荷[0]=0x05 表示"这是对 0x05 的答复"；载荷[1] = 电量百分比；
# 载荷[2] 的 bit4 = 充电中（插线 0x10 / 拔线 0x01，拔线瞬间会短暂路过 0x00）。
# 另有一种 0x07 事件帧（载荷 07 00 00 00），不是电量，必须跳过。

KB_REPORT_ID = 0x13
KB_LEN = 20
KB_CMD_STATUS = 0x05
KB_RESP_DATA = 0x0A
KB_EVENT = 0x07
KB_CHARGE_BIT = 0x10


def kb_checksum(frame):
    return sum(frame[:-1]) & 0xFF


def kb_build(cmd, total=KB_LEN):
    body = bytearray(total)
    body[0] = KB_REPORT_ID
    body[1] = cmd & 0xFF
    body[-1] = kb_checksum(body)
    return bytes(body)


def kb_parse(frame):
    """解析一帧回应；不是本协议的帧返回 None。"""
    if not frame or len(frame) < 8 or frame[0] != KB_REPORT_ID:
        return None
    n = frame[4]
    if n < 1 or n > len(frame) - 6:
        return None
    return {
        "code": frame[1],
        "payload": bytes(frame[5:5 + n]),
        "crc_ok": (sum(frame[:-1]) & 0xFF) == frame[-1],
    }


def kb_parse_frame(frame):
    """从一帧里取出 (电量, 充电标志, 帧号)；不是状态数据帧返回 None。"""
    info = kb_parse(frame)
    if not info or info["code"] != KB_RESP_DATA:
        return None
    p = info["payload"]
    if len(p) < 2 or p[0] != KB_CMD_STATUS:
        return None
    pct = p[1]
    if not (0 <= pct <= 100):
        return None
    flags = p[2] if len(p) > 2 else 0
    return pct, bool(flags & KB_CHARGE_BIT), flags


def match_keyboard(ifaces):
    cand = [i for i in ifaces
            if i.vendor_id == KEYBOARD_VID and i.product_id == KEYBOARD_PID
            and i.output_len == KB_LEN]
    if not cand:
        return None
    cand.sort(key=lambda i: (not i.can_write, i.usage_page != 0xFF02))
    return cand[0]


def read_keyboard(iface=None, timeout_ms=800, attempts=3):
    iface = iface or match_keyboard(_all_interfaces())
    if iface is None:
        return _target_reading("kb", ok=False, note="未发现接收器")
    if not iface.can_write:
        return _target_reading("kb", ok=False, note="厂商通道不可写（被系统独占）")

    total = iface.output_len or KB_LEN
    ctrl = None
    reader = None
    try:
        ctrl = hid.HidController(iface)
        reader = hid.OverlappedReader(iface)
        reader.drain(150)
        for attempt in range(attempts):
            if attempt:
                time.sleep(0.08)
                reader.drain(60)     # 上一轮的残留帧先丢掉，免得串味
            try:
                reader.begin()
            except OSError as e:
                return _target_reading("kb", ok=False, note=str(e))
            ctrl.write_output(kb_build(KB_CMD_STATUS, total))
            deadline = time.monotonic() + timeout_ms / 1000.0
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                resp = reader.wait(int(left * 1000) + 1)
                if resp is None:
                    break
                got = kb_parse_frame(resp)
                if got:
                    pct, charging, flags = got
                    dbg("键盘电量", pct, "flags=0x%02X" % flags)
                    return _target_reading(
                        "kb", ok=True, percent=pct,
                        source="0x05 查询 · 20B 帧第 6 字节",
                        extra={"charging": charging, "online": True,
                               "flags": flags, "frame": bytes(resp)})
                try:
                    reader.begin()
                except OSError:
                    break
        return _target_reading(
            "kb", ok=False,
            note="无回复 · 键盘可能休眠了（闲置 30 分钟会断开），按一下键唤醒")
    except OSError as e:
        return _target_reading("kb", ok=False, note="读取失败：%s" % e)
    finally:
        if reader:
            reader.close()
        if ctrl:
            ctrl.close()


# ------------------------------------------------------ Keychron Link-KM dongle
# 362D:D107（固件自报 "Keychron Link-KM"），京东京造 K 系列用同一套方案。
# 厂商通道 mi_01&col02：usage 0xFFC1，64 字节输入 / 输出，Report ID 0xB3 写、0xB4 读。
#
# 帧（64 字节定长）：[0]=Report ID  [1]=命令  [2]=载荷长度  [3:]=载荷
# 回应：             [0]=0xB4      [1]=命令回显
# 不支持的命令统一回 0xE4（B4/B6 E4 <arg> <cmd>）。
#
# 查询 0x06（状态）：写一条 [0]=0xB3 [1]=0x06 的 64 字节，回 0xB4/0x06，
# **第 20 字节 = 电量百分比**。这个偏移来自 Keychron 开源内核模块
# keychron_battery.c 的 KEYCHRON_BATTERY_OFFSET=20，本机实测吻合。
#
# 命令地图（0x00~0xFF 全扫）：0x02/0x03/0x04/0x05/0x06/0x61/0x62/0x63 有响应，其余回 0xE4。
#   0x04 = 固件版本（"0.1.3"）+ 设备名 TLV
#   0x05 = DPI 档位 + 电量
#   0x06 = 状态（含电量）
KCH_VID = KEYCHRON_VID
KCH_PID = KEYCHRON_PID
KCH_LEN = 64
KCH_CMD_ID = 0xB3
KCH_RESP_ID = 0xB4
KCH_CMD_STATUS = 0x06
KCH_BATTERY_OFFSET = 20
KCH_ERROR = 0xE4


def kch_build(cmd, total=KCH_LEN):
    body = bytearray(total)
    body[0] = KCH_CMD_ID
    body[1] = cmd & 0xFF
    return bytes(body)


def kch_parse(frame):
    """返回 (电量, 是否充电, 原始帧)；不是状态回应返回 None。

    第 20 字节是一个"标志 + 数值"的组合，靠插拔充电线对比定死的：
        插着充电线：0x9C   拔掉：0x1C
    即 **最高位 bit7 = 充电中**，低 7 位 = 电量百分比（0x1C = 28%）。
    Keychron 的内核模块直接比较 `data[20] <= 100`，那是没有这一位的机型，
    所以这里必须拆开看，取低 7 位才通用。
    """
    if not frame or len(frame) <= KCH_BATTERY_OFFSET:
        return None
    if frame[0] != KCH_RESP_ID or frame[1] != KCH_CMD_STATUS:
        return None
    raw = frame[KCH_BATTERY_OFFSET]
    pct = raw & 0x7F
    if pct > 100:
        return None
    return pct, bool(raw & 0x80), bytes(frame)


def match_keychron(ifaces):
    cand = [i for i in ifaces
            if i.vendor_id == KCH_VID and i.product_id == KCH_PID
            and i.usage_page == 0xFFC1 and i.output_len == KCH_LEN]
    if not cand:
        return None
    cand.sort(key=lambda i: not i.can_write)
    return cand[0]


def read_keychron(iface=None, timeout_ms=900, attempts=3):
    iface = iface or match_keychron(_all_interfaces())
    if iface is None:
        return _target_reading("jd", ok=False, note="未发现接收器")
    if not iface.can_write:
        return _target_reading("jd", ok=False, note="厂商通道不可写（被系统独占）")

    ctrl = None
    reader = None
    try:
        ctrl = hid.HidController(iface)
        reader = hid.OverlappedReader(iface)
        reader.drain(150)
        for attempt in range(attempts):
            if attempt:
                time.sleep(0.1)
                reader.drain(60)
            try:
                reader.begin()
            except OSError as e:
                return _target_reading("jd", ok=False, note=str(e))
            ctrl.write_output(kch_build(KCH_CMD_STATUS))
            deadline = time.monotonic() + timeout_ms / 1000.0
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                resp = reader.wait(int(left * 1000) + 1)
                if resp is None:
                    break
                got = kch_parse(resp)
                if got:
                    pct, charging, frame = got
                    dbg("Keychron 电量", pct, "充电" if charging else "未充电")
                    return _target_reading(
                        "jd", ok=True, percent=pct,
                        source="0x06 查询 · 64B 帧第 20 字节",
                        extra={"charging": charging, "online": True, "frame": frame})
                try:
                    reader.begin()
                except OSError:
                    break
        return _target_reading("jd", ok=False, note="无回复 · 接收器可能在休眠")
    except OSError as e:
        return _target_reading("jd", ok=False, note="读取失败：%s" % e)
    finally:
        if reader:
            reader.close()
        if ctrl:
            ctrl.close()


# ------------------------------------------------- 飞智 Flydigi 手柄（厂商通道）
#
# 同一把手柄在不同模式下枚举成完全不同的设备：
#   XInput（045E:028E，1 个接口）：15 字节纯摇杆报文，没有输出/Feature 报表，
#       —— 命令根本发不出去，读不到电量。
#   DInput（04B4:2412，4 个接口）：其中接口 2（usage page 0xFFA0）是厂商通道，
#       输出 report id 5、输入 report id 4 —— 能问到电量。
# 切换方式（官方说明书）：长按 **FN + A** 三秒切 DInput（指示灯变蓝），
#   **FN + X** 三秒切回 XInput（白灯）。切换后 USB 会重新枚举。
#
# 电量不是百分比，而是 **0..5 共 6 档**（每档 20%）：
#   低 4 位 = 档位（0..5），高 4 位 = 1 表示充电中。
# 这是飞智软件自己的口径（`Battery = (b >> 4 == 1) ? 6 : (b & 0xF)`），
# 命令集里不存在更精细的值——所以本工具显示的是 20% 粒度，不是 1% 粒度。
FD_VID = 0x04B4
FD_PID = 0x2412
FD_XINPUT_VID = 0x045E
FD_XINPUT_PID = 0x028E

FD_OUT_ID = 5            # 输出报表 ID
FD_IN_ID = 4             # 输入报表 ID
FD_OUT_LEN = 12          # 05 <cmd> ... 补零到 12 字节；只发 5 字节会被静默忽略
FD_CMD_INFO = 0xEC       # 设备信息（经典协议；新协议是 0x01）
FD_BATTERY_OFFSET = 11   # 信息回包里的电量字节
FD_CONN_OFFSET = 13      # 连接方式：1 = 有线
FD_ECHO_OFFSET = 15      # 命令回显（信息回应里是 0xEC）
FD_MAX_LEVEL = 5         # 档位上限是 5 不是 15（飞智只有 6 档）

# XInput 通道（仅当描述符真的给得出输出报表时才有意义，详见 match_gamepad）
FD_XINPUT_CMD_INFO = 0x10
FD_XINPUT_LEN = 15
FD_XINPUT_BATTERY_OFFSET = 23


def fd_build(cmd, args=b"", total=FD_OUT_LEN):
    """DInput 查询帧：05 <cmd> <args…>，补零到 12 字节。"""
    body = bytearray(total)
    body[0] = FD_OUT_ID
    body[1] = cmd & 0xFF
    body[2:2 + len(args)] = bytes(args)[:total - 3]
    return bytes(body)


def fd_xinput_build(cmd, args=b""):
    """XInput 查询帧：A5 <cmd> <args…> <crc>，前 14 字节求和取低 8 位。"""
    body = bytearray(FD_XINPUT_LEN)
    body[0] = 0xA5
    body[1] = cmd & 0xFF
    body[2:2 + len(args)] = bytes(args)[:FD_XINPUT_LEN - 3]
    body[-1] = sum(body[:-1]) & 0xFF
    return bytes(body)


def _fd_level(raw):
    """把电量字节解成 (档位 0..5, 是否充电)。解不出来返回 None。

    签名必须卡死：高 4 位只可能是 0（未充电）或 1（充电中），低 4 位 ≤ 5。
    只判"低 4 位 ≤ 5"是不够的——MAC 地址里的 0x33 之类也会被当成 3 档，
    我第一版就是这样踩的坑。
    """
    if raw is None:
        return None
    hi = raw >> 4
    level = raw & 0x0F
    if hi not in (0, 1) or level > FD_MAX_LEVEL:
        return None
    return level, bool(hi)


def fd_parse(frame, offset=FD_BATTERY_OFFSET):
    """解析信息回包，返回 (电量, 是否充电, 是否有线, 电量字节下标, 原始帧)。

    先按文档给的 11 号偏移取；取出来不像档位（>5）就往邻近几个字节扫一遍——
    不同固件/不同型号（Apex 4 / Apex 5 / Vader）回包布局有差异，
    与其猜死一个数字，不如用"低 4 位 ≤ 5 或高 4 位 == 1"这个特征去找。
    """
    if not frame or len(frame) <= offset:
        return None
    if frame[0] != FD_IN_ID:
        return None
    # 这个接口每秒推上千帧实时状态（04 FE …），命令回应是 04 FF …。
    # 再看一眼命令回显，避免把别的回应当成设备信息。
    if frame[1] == 0xFE:
        return None
    if len(frame) > FD_ECHO_OFFSET and frame[FD_ECHO_OFFSET] not in (
            FD_CMD_INFO, FD_XINPUT_CMD_INFO):
        return None

    got = _fd_level(frame[offset])
    idx = offset
    if got is None:
        lo, hi = max(3, offset - 3), min(len(frame), offset + 4)
        cands = sorted(range(lo, hi), key=lambda k: (abs(k - offset), k))
        for cand in cands:
            if cand == offset:
                continue
            g = _fd_level(frame[cand])
            if g is not None:
                got, idx = g, cand
                dbg("飞智：文档偏移 %d 解不出档位，改用 %d（0x%02X）"
                    % (offset, cand, frame[cand]))
                break
    if got is None:
        return None

    level, charging = got
    wired = False
    if FD_CONN_OFFSET < len(frame):
        wired = frame[FD_CONN_OFFSET] == 1
    return level * 20, charging, wired, idx, bytes(frame)


def match_gamepad(ifaces):
    """找能发命令的厂商通道。返回 (接口, 通道类型)；找不到返回 (None, None)。

    通道类型有 "dinput"（0xFFA0 厂商接口，report 5/4）和
    "xinput"（045E:028E 里描述符给得出输出报表的那种，多见于 2.4G 接收器）。
    """
    cand = [i for i in ifaces
            if i.vendor_id == FD_VID and i.product_id == FD_PID
            and i.can_write and i.output_len >= FD_OUT_LEN]
    if cand:
        # 优先文档里写明 usage page 0xFFA0 的那个接口
        cand.sort(key=lambda i: (i.usage_page != 0xFFA0, -i.output_len))
        return cand[0], "dinput"

    # 2.4G 接收器：同样枚举成 045E:028E，但接口数更多，可能带得动 15 字节的 A5 帧
    cand = [i for i in ifaces
            if i.vendor_id == FD_XINPUT_VID and i.product_id == FD_XINPUT_PID
            and i.can_write and i.output_len >= FD_XINPUT_LEN
            and i.input_len > FD_XINPUT_BATTERY_OFFSET]
    if cand:
        cand.sort(key=lambda i: -i.input_len)
        return cand[0], "xinput"

    return None, None


def fd_mode(ifaces=None):
    """看手柄当前在什么模式：'dinput' / 'xinput' / None（没插）。"""
    ifs = ifaces if ifaces is not None else _all_interfaces()
    for i in ifs:
        if i.vendor_id == FD_VID and i.product_id == FD_PID:
            return "dinput"
    for i in ifs:
        # 045E:028E 是所有 Xbox 360 手柄通用的 ID，靠名字认是不是飞智
        if i.vendor_id == FD_XINPUT_VID and i.product_id == FD_XINPUT_PID:
            name = ("%s %s" % (i.product or "", i.manufacturer or "")).lower()
            if "flydigi" in name or "vader" in name:
                return "xinput"
    return None


def read_gamepad(iface=None, timeout_ms=900, attempts=3):
    """读飞智手柄电量。"""
    if iface is None:
        iface, channel = match_gamepad(_all_interfaces())
    else:
        channel = "dinput" if (iface.vendor_id == FD_VID) else "xinput"

    if iface is None:
        mode = fd_mode()
        if mode == "xinput":
            return _target_reading(
                "pad", ok=False,
                note="手柄在 XInput 模式，读不到电量——长按 FN+A 三秒切到 DInput（蓝灯）")
        return _target_reading("pad", ok=False, note="未发现手柄")

    if channel == "xinput":
        query = fd_xinput_build(FD_XINPUT_CMD_INFO)
        offset = FD_XINPUT_BATTERY_OFFSET
    else:
        # 缓冲区必须凑够输出报表的长度：文档说补零到 12 字节即可（Apex 4 是 63B），
        # 但本机这个接口是 32B——WriteFile 要的就是 OutputReportByteLength。
        query = fd_build(FD_CMD_INFO, total=max(FD_OUT_LEN, iface.output_len))
        offset = FD_BATTERY_OFFSET

    ctrl = None
    reader = None
    try:
        ctrl = hid.HidController(iface)
        reader = hid.OverlappedReader(iface)
        reader.drain(150)
        for attempt in range(attempts):
            if attempt:
                time.sleep(0.15)
                reader.drain(60)
            try:
                reader.begin()
            except OSError as e:
                return _target_reading("pad", ok=False, note=str(e))
            ctrl.write_output(query)
            deadline = time.monotonic() + timeout_ms / 1000.0
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                resp = reader.wait(int(left * 1000) + 1)
                if resp is None:
                    break
                got = fd_parse(resp, offset)
                if got:
                    pct, charging, wired, idx, frame = got
                    # 只有两个可靠来源：
                    #   1) 充电位真的置起来了（固件自己说的）
                    #   2) conn == 1，有线接电脑——物理上必然在充电
                    # 2.4G 无线 + 充电头供电时固件根本不下发充电位，这里什么都不显示，
                    # 不猜、也不谎报"未充电"。详见 PROTOCOLS.md §7「边界情况」。
                    if wired:
                        charging = True
                    elif not charging:
                        charging = None
                    dbg("飞智电量 %d%%（档位 %d）充电=%s  帧=%s" % (
                        pct, pct // 20, charging, frame[:16].hex(" ")))
                    return _target_reading(
                        "pad", ok=True, percent=pct,
                        source="0x%02X 查询 · 第 %d 字节 · 6 档（20%% 粒度）"
                               % (FD_CMD_INFO, idx),
                        extra={"charging": charging, "online": True, "wired": wired,
                               "level": pct // 20, "frame": frame})
                try:
                    reader.begin()
                except OSError:
                    break
        # 接收器大约每两次请求就超时一次（文档实测），所以多试几轮再判定
        return _target_reading("pad", ok=False,
                               note="无回复 · 手柄可能已关机或没连上接收器")
    except OSError as e:
        return _target_reading("pad", ok=False, note="读取失败：%s" % e)
    finally:
        if reader:
            reader.close()
        if ctrl:
            ctrl.close()


# ---------------------------------------------------------------- 推送型设备

class PushBatteryMonitor(threading.Thread):
    """常驻监听型：设备自己往某个接口推固定长度的状态报文，里面某一位就是电量。

    雷柏（Rapoo）VT 系列就是这种：13 字节输入报文，第 8 字节 = 电量百分比。
    这类设备没法"问一次答一次"，只能一直挂着句柄等它推，所以单独开一个线程，
    把最近一次收到的值缓存起来给界面用。
    """

    def __init__(self, key, vid, pid, level_index=8, length=13, name="", subtitle="",
                 min_usage_page=0xFF00, charge_index=None, charge_mask=0x80,
                 online_timeout=15.0):
        super().__init__(name="push-%s" % key, daemon=True)
        self.key = key
        self.vid = vid
        self.pid = pid
        self.level_index = level_index
        self.length = length
        self.name = name
        self.subtitle = subtitle
        self.min_usage_page = min_usage_page
        self.charge_index = charge_index
        self.charge_mask = charge_mask
        self.online_timeout = online_timeout
        self.percent = None
        self.charge = None
        self.frame = b""
        self.stamp = 0.0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._iface = None
        self._history = []          # [(时间, 电量)]，用于在没有标志位时按趋势推断

    def _find(self):
        for i in hid.enumerate_interfaces(full=True):
            if (i.vendor_id == self.vid and i.product_id == self.pid
                    and i.input_len == self.length and i.usage_page >= self.min_usage_page):
                return i
        return None

    def run(self):
        while not self._stop.is_set():
            iface = self._find()
            if iface is None:
                self._iface = None
                self._stop.wait(3.0)
                continue
            self._iface = iface
            dbg("推送监听已挂上", self.key, iface.short_path())
            try:
                with hid.OverlappedReader(iface) as reader:
                    reader.drain(40)
                    reader.begin()
                    while not self._stop.is_set():
                        data = reader.wait(500)
                        if data is None:
                            try:
                                reader.begin()
                            except OSError:
                                break
                            continue
                        if len(data) == self.length:
                            val = data[self.level_index]
                            if 0 <= val <= 100:
                                with self._lock:
                                    self.percent = val
                                    self.frame = bytes(data)
                                    self.stamp = time.time()
                                    if self.charge_index is not None:
                                        self.charge = bool(data[self.charge_index] & self.charge_mask)
                                    self._history.append((self.stamp, val))
                                    del self._history[:-24]
                                dbg("推送电量 %s -> %d%%" % (self.key, val))
                        try:
                            reader.begin()
                        except OSError:
                            break
            except OSError as e:
                dbg("推送监听断开", self.key, e)
                self._stop.wait(1.5)

    def snapshot(self):
        with self._lock:
            return self.percent, self.stamp

    def _infer_charge(self):
        """没标定出充电标志位时，用电量趋势兜底：
        最近 5 分钟里电量在涨 -> 充电中；在掉 -> 放电中；持平 -> 不知道。"""
        now = time.time()
        recent = [(t, v) for t, v in self._history if now - t <= 300]
        if len(recent) < 4:
            return None
        if recent[-1][1] > recent[0][1]:
            return True
        if recent[-1][1] < recent[0][1]:
            return False
        return None

    def state(self):
        """返回 (电量, 是否在充电, 是否在线, 帧, 时间戳)。在线 = 最近还收到过推送。"""
        with self._lock:
            online = bool(self.stamp) and (time.time() - self.stamp) <= self.online_timeout
            charge = self.charge if self.charge_index is not None else self._infer_charge()
            return self.percent, charge, online, self.frame, self.stamp

    def stop(self):
        self._stop.set()


# 已知的推送型设备。雷柏 VT 系列的 13 字节状态报文，第 8 字节是电量百分比
# （与社区开源驱动 hid_rapoo_vt3 的 RAPOO_BATTERY_REPORT_SIZE / _CAPACITY_INDEX 一致）。
PUSH_DEVICES = {
    "24AE:1412": dict(vid=0x24AE, pid=0x1412, level_index=8, length=13,
                      charge_index=7, charge_mask=0x02,   # [7]=0x02 充电中 / 0x01 未充电
                      name="雷柏 Rapoo 无线鼠标", subtitle="VT 系列 2.4G"),
}

_monitors = {}
_monitor_lock = threading.Lock()


def get_monitor(key, **kw):
    with _monitor_lock:
        mon = _monitors.get(key)
        if mon is None:
            mon = PushBatteryMonitor(key, **kw)
            mon.start()
            _monitors[key] = mon
        return mon


def reading_from_monitor(mon, stale_after=None):
    """把监听线程的缓存值包成 Reading。

    三个状态是分开的：
        在线  = 最近 online_timeout 秒内还收到过推送（设备休眠/关机就掉线）
        电量  = 报文里那一位
        充电  = 报文中标定出来的标志位
    """
    stale_after = stale_after or mon.online_timeout
    pct, charge, online, frame, ts = mon.state()
    r = _target_reading(mon.key, name=mon.name, subtitle=mon.subtitle)
    if pct is None:
        r.ok = False
        r.note = "等待设备推送（动一下鼠标 / 按一下键唤醒）"
        r.ts = time.time()
        return r
    r.ok = True
    r.percent = pct
    r.source = "设备推送 %dB 报文 · 第 %d 字节" % (mon.length, mon.level_index)
    r.ts = ts
    r.extra["charging"] = charge
    r.extra["online"] = online
    r.extra["frame"] = frame
    if not online:
        r.note = "设备已离线（%.0f 秒没收到推送）" % (time.time() - ts)
    elif charge:
        r.note = "充电中"
    return r


# ---------------------------------------------------------------- 兜底链

# 厂商协议表：verified=False 的是"已知 VID 但命令未验证"的占位，
# 自动探测不会发送；在调试版里探明后用「手动标定」写进 calibration.json。
VENDOR_TABLE = [
    {
        "vids": (0x373B, 0x1915),          # ATK / 通用 Nordic 方案
        "name": "Nordic 0x08",
        "verified": True,
        "request": ATK_REQUEST,
        "report_id": 0x08,
        "parser": atk_parse,
    },
    {
        "vids": (0x3367,),                  # Endgame
        "name": "Endgame（未验证）",
        "verified": False,
        "request": None,
        "report_id": None,
        "parser": None,
    },
    {
        "vids": (0x3057,),                  # VAXEE
        "name": "VAXEE（未验证）",
        "verified": False,
        "request": None,
        "report_id": None,
        "parser": None,
    },
]

LOGITECH_VID = 0x046D


def _read_standard_battery(iface):
    """标准 HID 电量字段：Generic Device Controls (0x0006) / Battery Strength (0x0020)。"""
    for caps in hid.feature_value_caps(iface):
        if caps.usage_page != 0x0006 or caps.usage != 0x0020:
            continue
        length = max(iface.feature_len, 2)
        try:
            with hid.HidController(iface, prefer_write=False) as ctrl:
                data = ctrl.get_feature(caps.report_id, length)
        except OSError:
            continue
        if not data:
            continue
        val = hid.extract_bits(data, caps.report_id, caps.bit_field, caps.bit_size)
        if val is None:
            continue
        if 0 <= val <= 100:
            return val, "标准电量字段 0x0020"
    return None, ""


def _hiddpp_call(ctrl, dev_index, feature_index, func, params, report_id=0x10):
    head = bytes([report_id, dev_index & 0xFF, feature_index & 0xFF, (func & 0x0F) << 4 | 0x01])
    body = head + bytes(params)
    want = 20 if report_id == 0x11 else 7     # 长报文 20 字节 / 短报文 7 字节
    if len(body) < want:
        body += bytes(want - len(body))
    ctrl.write_output(body)
    return body


def _read_logitech(iface, timeout_ms=700):
    """罗技 HID++ 2.0：特性 0x1000 / 0x1004 / 0x1001。"""
    reader = hid.OverlappedReader(iface)
    ctrl = None
    try:
        ctrl = hid.HidController(iface)
        reader.drain(80)
        for dev_index in (0xFF, 0x01, 0x02):
            # 1) 用根特性 0x0000 解析目标特性的索引
            for fid, func in ((0x1000, 0), (0x1004, 1), (0x1001, 0)):
                reader.begin()
                ctrl.write_output(_hiddpp_call(ctrl, dev_index, 0x00, 0, [fid >> 8, fid & 0xFF]))
                resp = reader.wait(timeout_ms)
                if not resp or len(resp) < 5 or resp[1] != dev_index:
                    continue
                feat_index = resp[4]
                if feat_index == 0:
                    continue
                reader.begin()
                ctrl.write_output(_hiddpp_call(ctrl, dev_index, feat_index, func, []))
                resp = reader.wait(timeout_ms)
                if not resp or len(resp) < 5:
                    continue
                pct = resp[4]
                if 0 <= pct <= 100:
                    return pct, "HID++ 0x%04X" % fid
        return None, ""
    except OSError:
        return None, ""
    finally:
        if ctrl:
            ctrl.close()
        reader.close()


def _read_calibrated(iface, calib, timeout_ms=500):
    """手动标定：按 Report ID + 字节下标 + 倍率去读 Input Report。"""
    if not calib:
        return None, ""
    reader = hid.OverlappedReader(iface)
    try:
        reader.drain(60)
        reader.begin()
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            data = reader.wait(int(left * 1000) + 1)
            if data is None:
                break
            rid = calib.get("report_id")
            if rid is not None and data and data[0] != rid:
                reader.begin()
                continue
            idx = calib.get("byte_index", 0)
            if idx < len(data):
                raw = data[idx]
                pct = int(round(raw * float(calib.get("scale", 1)) + float(calib.get("offset", 0))))
                pct = max(0, min(100, pct))
                return pct, "手动标定"
            reader.begin()
        return None, ""
    finally:
        reader.close()


def read_generic(iface, calib=None):
    """按优先级跑兜底链。"""
    if iface is None:
        return None, "未找到可用设备"
    val, src = _read_standard_battery(iface)
    if val is not None:
        return val, src
    for entry in VENDOR_TABLE:
        if iface.vendor_id not in entry["vids"] or not entry["verified"]:
            continue
        if not iface.can_write:
            continue
        try:
            ctrl = hid.HidController(iface)
            reader = hid.OverlappedReader(iface)
        except OSError:
            continue
        try:
            reader.drain(60)
            reader.begin()
            req = bytearray(iface.output_len or 64)
            req[:len(entry["request"])] = entry["request"]
            ctrl.write_output(bytes(req))
            resp = reader.wait(900)
            if resp:
                pct = entry["parser"](resp)
                if pct is not None:
                    return pct, entry["name"]
        finally:
            reader.close()
            ctrl.close()
    if iface.vendor_id == LOGITECH_VID and iface.can_write:
        val, src = _read_logitech(iface)
        if val is not None:
            return val, src
    val, src = _read_calibrated(iface, calib)
    if val is not None:
        return val, src
    return None, "无法识别该设备的电量协议"


# ---------------------------------------------------------------- 汇总

# 支持的设备清单。key 是界面/托盘上的槽位名，顺序就是显示顺序。
# 只有当前真正插着的设备才会出现在面板里；一台都没插时退回显示默认的两台，
# 免得面板空着让人以为程序坏了。
PROFILES = [
    dict(key="raptoo", kind="push", vid=0x24AE, pid=0x1412, push="24AE:1412",
         category="mouse", name="雷柏 Rapoo 无线鼠标", subtitle="VT 系列 2.4G"),
    dict(key="kb", kind="cx", vid=KEYBOARD_VID, pid=KEYBOARD_PID,
         category="keyboard", name="迈从 X75 V2", subtitle="2.4G dongle · 3554:FA09"),
    dict(key="jd", kind="kch", vid=KEYCHRON_VID, pid=KEYCHRON_PID,
         category="mouse", name="京东京造 无线鼠标", subtitle="Keychron Link-KM · 362D:D107"),
    dict(key="atk", kind="atk", vid=MOUSE_VID, pid=MOUSE_PID,
         category="mouse", name="ATK / VXE 无线鼠标", subtitle="2.4G NANO 接收器"),
    dict(key="jl", kind="jl", vid=HEADSET_VID, pid=HEADSET_PID,
         category="headset", name="DARKRIM SHADOW", subtitle="杰理 dongle"),
    # 手柄：DInput 模式才读得到电量，但 XInput 模式也要出现在面板里——
    # 否则用户插上手柄什么都看不到，只会以为程序坏了。XInput 那张卡负责提示切模式。
    dict(key="pad", kind="fd", vid=FD_VID, pid=FD_PID,
         also=((FD_XINPUT_VID, FD_XINPUT_PID),),
         category="gamepad", name="飞智 Vader 4 Pro", subtitle="2.4G / 有线 · 04B4:2412"),
]

DEFAULT_KEYS = ("atk", "jl")


def present_vidpids():
    # 必须用 full=True：只列路径的话 vendor_id/product_id 都是 0
    try:
        return {(i.vendor_id, i.product_id) for i in hid.enumerate_interfaces(full=True)}
    except Exception:
        return set()


def _profile_present(p, here):
    """这台设备在不在？同一台设备可能有多个 VID:PID（切换模式会变）。"""
    if (p["vid"], p["pid"]) in here:
        return True
    for alt in p.get("also", ()):
        if tuple(alt) in here:
            return True
    return False


def active_profiles():
    """当前插着的、且我们有协议支持的设备。"""
    here = present_vidpids()
    ons = [p for p in PROFILES if _profile_present(p, here)]
    if ons:
        return ons
    return [p for p in PROFILES if p["key"] in DEFAULT_KEYS]


_last_good = {}
_last_good_lock = threading.Lock()


def _remember(key, r):
    """记下成功读数，供设备休眠时兜底。"""
    if r.ok and r.percent is not None:
        with _last_good_lock:
            _last_good[key] = (r.percent, time.time(), r.extra.get("charging"))
    return r


def _recall(key, r, max_age=3600):
    """本次没读到就退回上次的成功读数。

    键鼠闲置一段时间会断开 2.4G（迈从 X75 V2 是 30 分钟），这时面板只能显示
    `--`，很难看也没信息量。这里退回旧值，但必须把"多久以前的"写在来源里、
    并且标成离线——绝不假装是刚测的。
    """
    if r.ok:
        return r
    with _last_good_lock:
        prev = _last_good.get(key)
    if not prev:
        return r
    pct, ts, charge = prev
    age = time.time() - ts
    if age > max_age:
        return r
    r.ok = True
    r.percent = pct
    r.ts = time.time()
    r.note = "设备暂无回应，显示上次读数"
    if age < 90:
        r.source = "上次读数 · 刚刚"
    else:
        r.source = "上次读数 · %d 分钟前" % int(age // 60)
    r.extra["charging"] = None
    r.extra["online"] = False
    r.extra["stale"] = True
    r.extra["stale_age"] = age
    return r


def _read_profile(p):
    kind = p["kind"]
    if kind == "push":
        spec = dict(PUSH_DEVICES[p["push"]])
        spec["name"] = p["name"]
        spec["subtitle"] = p["subtitle"]
        mon = get_monitor(p["key"], **spec)
        # 这类设备每几秒推一帧，首次取值要给它一点时间；取到之后就不再等
        deadline = time.time() + 6.0
        while time.time() < deadline and mon.state()[0] is None:
            time.sleep(0.12)
        return reading_from_monitor(mon)

    if kind == "cx":
        iface = match_keyboard(_all_interfaces())
        if iface is None:
            return _target_reading(p["key"], name=p["name"], subtitle=p["subtitle"],
                                   ok=False, note="未发现 dongle")
        r = read_keyboard(iface)
        r.name, r.subtitle = p["name"], p["subtitle"]
        if not r.ok and "读取失败" in (r.note or ""):
            invalidate()
        return _recall(p["key"], _remember(p["key"], r))

    if kind == "kch":
        iface = match_keychron(_all_interfaces())
        if iface is None:
            return _target_reading(p["key"], name=p["name"], subtitle=p["subtitle"],
                                   ok=False, note="未发现接收器")
        r = read_keychron(iface)
        r.name, r.subtitle = p["name"], p["subtitle"]
        if not r.ok and "读取失败" in (r.note or ""):
            invalidate()
        return _recall(p["key"], _remember(p["key"], r))

    if kind == "fd":
        r = read_gamepad()
        r.name, r.subtitle = p["name"], p["subtitle"]
        if not r.ok and "读取失败" in (r.note or ""):
            invalidate()
        return _recall(p["key"], _remember(p["key"], r))

    if kind == "atk":
        iface = match_mouse(_all_interfaces())
        if iface is None:
            return _target_reading(p["key"], name=p["name"], subtitle=p["subtitle"],
                                   ok=False, note="未发现接收器")
        r = read_mouse(iface)
        r.name, r.subtitle = p["name"], p["subtitle"]
        if not r.ok:
            invalidate()            # 下次重新枚举，防止热插拔后路径失效
        return _recall(p["key"], _remember(p["key"], r))

    if kind == "jl":
        iface = match_headset(_all_interfaces())
        if iface is None:
            return _target_reading(p["key"], name=p["name"], subtitle=p["subtitle"],
                                   ok=False, note="未发现 dongle")
        r = read_headset(iface)
        r.name, r.subtitle = p["name"], p["subtitle"]
        if not r.ok and "读取失败" in (r.note or ""):
            invalidate()
        return r

    return _target_reading(p["key"], name=p["name"], subtitle=p["subtitle"],
                           ok=False, note="没有该设备的协议")


def read_all(debug_state=None):
    """按当前插着的设备读取，返回有序的 {key: Reading}。"""
    out = {}
    for p in active_profiles():
        try:
            out[p["key"]] = _read_profile(p)
        except Exception as e:
            logx.warn("读取 %s 异常：%r" % (p["key"], e))
            out[p["key"]] = _target_reading(p["key"], name=p["name"],
                                            subtitle=p["subtitle"], ok=False,
                                            note="读取异常：%s" % e)
    return out


def read_by_iface(iface, calib=None):
    """调试版：对任意接口跑一遍兜底链。

    已知厂商协议的接口先走专用实现——兜底链认不出 CX 的 0x13/20 字节帧。
    """
    if iface.output_len == KB_LEN and iface.vendor_id == KEYBOARD_VID \
            and iface.product_id == KEYBOARD_PID:
        r = read_keyboard(iface)
        if r.ok:
            return r
    # 飞智手柄：04B4:2412 的厂商接口，或 045E:028E 里给得出输出报表的那个
    if (iface.vendor_id, iface.product_id) in ((FD_VID, FD_PID),
                                               (FD_XINPUT_VID, FD_XINPUT_PID)) \
            and iface.can_write and iface.output_len >= FD_OUT_LEN:
        r = read_gamepad(iface)
        if r.ok:
            return r
    return read_generic(iface, calib)


# ---------------------------------------------------------------- 自检

def selftest():
    """不依赖硬件的协议自检：帧结构、CRC、TLV、ATK 回复解析。"""
    fails = []

    def check(name, cond, detail=""):
        if cond:
            print("  [ok]   %s" % name)
        else:
            print("  [FAIL] %s %s" % (name, detail))
            fails.append(name)

    print("CRC16-CCITT-FALSE 标准向量")
    check("crc16(\"123456789\") == 0x29B1", crc16_ccitt_false(b"123456789") == 0x29B1,
          hex(crc16_ccitt_false(b"123456789")))
    check("crc16(b\"\") == 0xFFFF", crc16_ccitt_false(b"") == 0xFFFF)

    print("ATK 请求报文")
    req = atk_build_request(64)
    check("长度 64", len(req) == 64)
    check("前 8 字节", req[:8] == bytes([0x08, 0x7D, 0x72, 0x02, 0x00, 0x01, 0x07, 0x01]),
          req[:8].hex(" "))

    print("ATK 回复解析")
    resp = bytearray(64)
    resp[0], resp[1], resp[5], resp[7] = 0x08, 0x72, 0x07, 87
    check("电量 87", atk_parse(bytes(resp)) == 87)
    resp[5] = 0x06
    check("非 0x07 拒绝", atk_parse(bytes(resp)) is None)

    print("杰理 JL_RCSP 组帧")
    f = jl_frame(0x05, jl_query_payload(TLV_BATTERY, 0), total=65)
    check("长度 65", len(f) == 65)
    check("帧头 00 FE 05 10 00 02 00 00",
          f[:8] == bytes([0x00, 0xFE, 0x05, 0x10, 0x00, 0x02, 0x00, 0x00]), f[:8].hex(" "))
    check("TLV 长度字段为 1", (f[8] | f[9] << 8) == 1)
    check("载荷是 TLV 0x01", f[10] == 0x01)
    crc = crc16_ccitt_false(f[1:11])
    check("CRC 大端落在 [11][12]", f[11] == (crc >> 8) and f[12] == (crc & 0xFF),
          "%02X %02X vs %04X" % (f[11], f[12], crc))
    check("尾部补 0", all(b == 0 for b in f[13:]))

    print("杰理 回复解析（自洽回环）")
    payload = bytes([0x01, 0x03, 0x00, 62, 0x01, 0x00])      # TLV 0x01 len=3 value=[62,1,0]
    reply = bytearray(jl_frame(0x05, payload, flag=0x11, opcode=0x03, total=65))
    info = jl_parse(bytes(reply))
    check("解析成功", info is not None)
    if info:
        check("电量 62", info["tlv"].get(0x01, b"")[:1] == bytes([62]), info["tlv"])
        check("CRC 通过", info["crc_ok"])
    reply[11] ^= 0xFF
    check("坏 CRC 被拒", jl_parse(bytes(reply)) is None)

    print("短格式 TLV（无长度字段）也能解析")
    info = jl_parse(jl_frame(1, bytes([0x01, 55]), flag=0x11, opcode=0x03, total=65))
    check("电量 55", info and info["tlv"].get(0x01) == bytes([55]),
          info["tlv"] if info else None)

    print("CX 键盘 dongle（0x13 / 20 字节）")
    q = kb_build(KB_CMD_STATUS)
    check("查询帧 20 字节", len(q) == 20)
    check("首两字节 13 05", q[:2] == bytes([0x13, 0x05]), q[:2].hex(" "))
    check("校验 = 前 19 字节和", q[-1] == sum(q[:-1]) & 0xFF)

    # 实测收到的帧（插着电 / 拔掉电），原样回放
    on = bytearray(20)
    on[0:8] = bytes([0x13, 0x0A, 0x01, 0x00, 0x04, 0x05, 0x64, 0x10])
    on[-1] = sum(on[:-1]) & 0xFF
    check("充电中帧校验 0x9B", on[-1] == 0x9B, "%02X" % on[-1])
    off = bytearray(on)
    off[7] = 0x01
    off[-1] = sum(off[:-1]) & 0xFF
    check("充电中 100%", kb_parse_frame(bytes(on)) == (100, True, 0x10), kb_parse_frame(bytes(on)))
    check("拔线后 100% 未充电", kb_parse_frame(bytes(off)) == (100, False, 0x01),
          kb_parse_frame(bytes(off)))
    ev = bytearray(on)
    ev[5], ev[6], ev[7], ev[8] = 0x07, 0x00, 0x00, 0x00
    ev[-1] = sum(ev[:-1]) & 0xFF
    check("0x07 事件帧被跳过", kb_parse_frame(bytes(ev)) is None)
    bad = bytearray(on)
    bad[-1] ^= 0xFF
    check("坏校验仍能解析但标记", kb_parse(bytes(bad))["crc_ok"] is False)

    print("Keychron Link-KM（0xB3 / 0x06，64 字节）")
    q = kch_build(KCH_CMD_STATUS)
    check("查询帧 64 字节", len(q) == KCH_LEN)
    check("首两字节 B3 06", q[:2] == bytes([0xB3, 0x06]), q[:2].hex(" "))
    body = "B4 06 00 21 21 01 20 03 40 06 60 09 80 0C A0 0F 15 05 04 0A 9C 00"
    frame = bytes.fromhex(body.replace(" ", "")) + bytes(KCH_LEN - 22)
    got = kch_parse(frame)
    check("插线 0x9C -> 28% 充电中", got and got[0] == 28 and got[1] is True, got)
    plain = bytearray(frame); plain[20] = 0x1C
    check("拔线 0x1C -> 28% 未充电",
          kch_parse(bytes(plain))[:2] == (28, False), kch_parse(bytes(plain)))
    err = bytearray(frame); err[1] = 0xE4
    check("0xE4 错误帧被拒", kch_parse(bytes(err)) is None)

    print("飞智 Flydigi 手柄（DInput 05 EC / 输入 report 4）")
    q = fd_build(FD_CMD_INFO)
    check("查询帧 12 字节 05 EC", len(q) == FD_OUT_LEN and q[:2] == bytes([5, 0xEC]),
          q.hex(" "))
    xq = fd_xinput_build(FD_XINPUT_CMD_INFO)
    check("XInput 帧 15 字节且校验和为和",
          len(xq) == FD_XINPUT_LEN and xq[-1] == (sum(xq[:-1]) & 0xFF))

    def fdr(battery, conn=0):
        f = bytearray(31)
        f[0] = FD_IN_ID
        f[1] = 0xFF                       # 命令回应（实时状态帧是 0xFE）
        f[3] = 0x55                       # deviceId
        f[5:9] = bytes([0x68, 0x92, 0x90, 0x38])   # MAC（反序）
        f[9:11] = bytes([0x33, 0x69])     # 固件 BCD
        f[FD_BATTERY_OFFSET] = battery
        f[FD_CONN_OFFSET] = conn
        f[FD_ECHO_OFFSET] = FD_CMD_INFO   # 命令回显
        return bytes(f)

    got = fd_parse(fdr(0x15, 1))          # 高 4 位 1 = 充电，档位 5
    check("0x15 -> 100% 充电中（有线）",
          got and got[0] == 100 and got[1] is True and got[2] is True, got)
    got = fd_parse(fdr(0x03))             # 档位 3，充电位为 0
    check("0x03 -> 60%（充电位 0 只解档位）", got and got[:3] == (60, False, False), got)
    got = fd_parse(fdr(0x00))
    check("0x00 -> 0%", got and got[0] == 0, got)
    check("档位上限 5（0x0F 这种要被拒）", _fd_level(0x0F) is None)
    check("报表 ID 不对要拒", fd_parse(bytes([0x05]) + fdr(0x03)[1:]) is None)

    odd = bytearray(fdr(0xFF))
    odd[10] = 0x02                        # 文档偏移解不出时，应该扫到邻域这个字节
    got = fd_parse(bytes(odd))
    check("偏移解不出时扫邻域", got and got[0] == 40 and got[3] == 10, got)

    # 本机实抓的一帧（2026-11-01，DInput 模式，无线连接）
    real = bytes.fromhex("04FFF0550068929038336904"
                         "020001EC0000DF0000000000".lower())
    real = real + bytes(31 - len(real))
    got = fd_parse(real)
    check("实抓帧 -> 80%（充电位 0，判定为未知）",
          got and got[:3] == (80, False, False), got)
    live = bytearray(real)
    live[1] = 0xFE                        # 实时状态帧不能被当成回应
    check("实时帧（04 FE）要忽略", fd_parse(bytes(live)) is None)

    print()
    if fails:
        print("自检失败 %d 项：%s" % (len(fails), ", ".join(fails)))
        return 1
    print("自检全部通过")
    return 0
