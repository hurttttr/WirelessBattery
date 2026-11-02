# -*- coding: utf-8 -*-
"""调试输出开关。

按需求：不写任何日志文件，只有 --debug 时才有控制台输出。
"""

import sys
import time

DEBUG = False


def set_debug(on):
    global DEBUG
    DEBUG = bool(on)


def dbg(*args):
    if not DEBUG:
        return
    ts = time.strftime("%H:%M:%S")
    print("[%s]" % ts, *args, flush=True)


def dbg_hex(prefix, data, limit=None):
    """--debug 时按 16 字节一行打印十六进制。"""
    if not DEBUG:
        return
    if isinstance(data, (bytes, bytearray, memoryview)):
        data = bytes(data)
    if limit:
        data = data[:limit]
    ts = time.strftime("%H:%M:%S")
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        hexs = " ".join("%02X" % b for b in chunk)
        print("[%s] %s +%03X  %s" % (ts, prefix, off, hexs), flush=True)


def warn(msg):
    """即使在 pythonw 下也安全（写 stderr 失败就忽略）。"""
    try:
        if sys.stderr is not None:
            print(msg, file=sys.stderr, flush=True)
    except Exception:
        pass
