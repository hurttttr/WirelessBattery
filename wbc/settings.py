# -*- coding: utf-8 -*-
"""运行时配置与手动标定。

按需求「项目目录里不产生任何日志文件」，所有运行时数据都放在
%LOCALAPPDATA%\\WirelessBattery\\ 下：settings.json、calibration.json、app.ico。
"""

import json
import os

from . import icons
from . import win32 as wx

APP_DIR_NAME = "WirelessBattery"

DEFAULT_INTERVAL = 60
MIN_INTERVAL = 30
MAX_INTERVAL = 1800


def data_dir():
    d = os.path.join(wx.local_appdata_dir(), APP_DIR_NAME)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = os.path.join(os.path.expanduser("~"), "." + APP_DIR_NAME)
        os.makedirs(d, exist_ok=True)
    return d


def _path(name):
    return os.path.join(data_dir(), name)


def _read_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def clamp_interval(v):
    try:
        v = int(v)
    except Exception:
        return DEFAULT_INTERVAL
    return max(MIN_INTERVAL, min(MAX_INTERVAL, v))


class Settings(object):
    def __init__(self):
        self.path = _path("settings.json")
        data = _read_json(self.path, {})
        self.interval = clamp_interval(data.get("interval", DEFAULT_INTERVAL))
        self.geometry = data.get("geometry") or ""
        self.last_panel = bool(data.get("last_panel", True))
        # 本地 HTTP 接口（LiteMonitor 插件靠它取数）。面板上的开关会写回这里，
        # 下次启动照着它恢复，所以 --serve 不用一直挂在自启参数里。
        self.serve = bool(data.get("serve", False))
        # LiteMonitor 的 resources\plugins 目录。自动找过一次（或用户手选过）就
        # 记住，下次点「装插件」直接命中 —— 便携版装在哪儿只有用户自己清楚。
        self.plugin_dir = str(data.get("plugin_dir") or "")

    def save(self):
        try:
            _write_json(self.path, {
                "interval": self.interval,
                "geometry": self.geometry,
                "last_panel": self.last_panel,
                "serve": self.serve,
                "plugin_dir": self.plugin_dir,
            })
        except Exception:
            pass


# ---------------------------------------------------------------- 手动标定

class Calibration(object):
    """把 Report ID / 电量字节下标 / 倍率 存成 JSON，供兜底链最后一步使用。"""

    def __init__(self):
        self.path = _path("calibration.json")
        data = _read_json(self.path, {})
        if not isinstance(data, dict):
            data = {}
        self.entries = data

    @staticmethod
    def key_for(iface):
        return "%04X:%04X" % (iface.vendor_id, iface.product_id)

    def get(self, iface):
        if iface is None:
            return None
        return self.entries.get(self.key_for(iface))

    def put(self, iface, report_id, byte_index, scale=1.0, offset=0.0):
        e = {
            "report_id": int(report_id) if report_id is not None else None,
            "byte_index": int(byte_index),
            "scale": float(scale),
            "offset": float(offset),
            "usage_page": iface.usage_page,
            "usage": iface.usage,
            "product": iface.product,
        }
        self.entries[self.key_for(iface)] = e
        _write_json(self.path, self.entries)
        return e

    def remove(self, iface):
        self.entries.pop(self.key_for(iface), None)
        _write_json(self.path, self.entries)


# ---------------------------------------------------------------- 程序图标

def app_icon_path():
    """生成/复用 app.ico（快捷方式与窗口图标用），放在数据目录而不是项目目录。"""
    path = _path("app.ico")
    if os.path.exists(path) and os.path.getsize(path) > 1024:
        return path
    try:
        icons.write_ico(path)
        return path
    except Exception:
        return None
