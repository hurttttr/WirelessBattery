# -*- coding: utf-8 -*-
"""按"当前插着的设备"生成 LiteMonitor 插件 JSON。

LiteMonitor 的 output 列表是静态的：文件里写了哪几项，设置界面就会列出哪几项。
可外设不是每天都插着，写死一堆只会让面板里多出几行 ⛔。
所以这里按现场情况生成——插着什么就写什么，改完设备重跑一次即可。
"""

import glob
import json
import os

from . import proto

# 按"大类"给图标：一台电脑上通常就是鼠标、键盘、手柄、耳机这几类。
# 同类出现多个时，短标签加序号（🖱1 / 🖱2）方便区分；
# 想要更贴切的名字，在 LiteMonitor 里改该项的显示名即可——
# 它的优先级是 UserLabel > 运行时标签 > 模板，重载插件也不会被覆盖。
CATEGORY = {
    "mouse": ("🖱", "鼠标"),
    "keyboard": ("⌨", "键盘"),
    "gamepad": ("🎮", "手柄"),
    "headset": ("🎧", "耳机"),
}

# 面板上的名字用「品牌 + 大类」，这样两个鼠标一眼能分开
BRAND = {
    "raptoo": "雷柏",
    "kb": "迈从",
    "jd": "京造",
    "atk": "ATK",
    "jl": "DARKRIM",
    "pad": "飞智手柄",   # 只写"飞智"太含糊，手柄这一类还是带上类名
}

PLUGIN_ID = "PeripheralBattery"

# LiteMonitor 常见安装位置（用于自动找 plugins 目录）
_SEARCH = [
    r"D:\MyTools\LiteMonitor*\resources\plugins",
    r"C:\Program Files\LiteMonitor*\resources\plugins",
    r"C:\Program Files (x86)\LiteMonitor*\resources\plugins",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "LiteMonitor*", "resources", "plugins"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "LiteMonitor*", "resources", "plugins"),
]


def present_keys():
    """当前真正插着的、我们有协议的设备 key 列表。"""
    here = proto.present_vidpids()
    # 用 proto._profile_present：同一台设备可能有多个 VID:PID（手柄切模式会变）
    return [p["key"] for p in proto.PROFILES if proto._profile_present(p, here)]


def _profile(key):
    for p in proto.PROFILES:
        if p["key"] == key:
            return p
    return {"key": key, "name": key, "category": ""}


def display_for(key, same_category):
    """返回 (面板名, 短标签)。

    面板名 = 图标 + 品牌。不再重复"鼠标/键盘"——图标已经说了。
    短标签 = 纯图标；同类多个才加序号，任务栏里不再叠第二个 emoji
    （两个 emoji 并排会撑出一段空隙，看着很散）。
    """
    p = _profile(key)
    icon, cname = CATEGORY.get(p.get("category") or "", ("🔋", "外设"))
    brand = BRAND.get(key, "")
    if brand and brand[-1].isascii() and brand[-1].isalnum():
        brand += " "
    label = ("%s %s" % (icon, brand or cname)).strip()
    short = icon
    if len(same_category) > 1:
        short = "%s%d" % (icon, same_category.index(key) + 1)
    return label, short


def build_plugin(keys, port=18763):
    # 先按大类分组，决定同类的短标签要不要加序号
    groups = {}
    for key in keys:
        cat = _profile(key).get("category") or ""
        groups.setdefault(cat, []).append(key)

    outputs, extract, names = [], {}, []
    for key in keys:
        cat = _profile(key).get("category") or ""
        label, short = display_for(key, groups[cat])
        names.append(label.replace(CATEGORY.get(cat, ("", ""))[0], "").strip())
        # _text 默认没用到，但留出来给"想在数值里加文字"的人改，缺了会导致整步报错
        for suffix in ("", "_state", "_mark", "_text"):
            extract[key + suffix] = key + suffix
        outputs.append({
            "key": key,
            # 充电标记放在数值前面（⚡29%）：一是只有一个 emoji，不会和图标挤在一起；
            # 二是数值不受"自定义名称"影响，改了名字这个提示还在。
            "label": label,
            "short_label": short,
            "format_val": "{{%s_mark}}{{%s}}" % (key, key),
            "color": "{{%s_state}}" % key,
            "unit": "%",
        })

    return {
        "id": PLUGIN_ID,
        "meta": {
            "name": "无线外设电量",
            "version": "1.0",
            "author": "my_devices.py",
            "description": "读取本机 2.4G 无线外设的电量。当前检测到：%s。"
                           "需要在本机运行 my_devices.py --serve（默认 http://127.0.0.1:%d/battery）。"
                           % ("、".join(names) or "（无）", port),
        },
        "execution": {
            "type": "chain",
            "interval": 30,
            "min_interval": 10,
            "steps": [{
                "id": "fetch",
                "url": "http://127.0.0.1:%d/battery" % port,
                "method": "GET",
                "response_format": "json",
                "cache_minutes": 0,
                "extract": extract,
            }],
        },
        "outputs": outputs,
    }


def find_plugin_dirs():
    """找 LiteMonitor 的 resources/plugins 目录（可能多个版本并存，用最新的那个）。"""
    found = []
    for pattern in _SEARCH:
        for path in glob.glob(pattern):
            exe = os.path.join(os.path.dirname(os.path.dirname(path)), "LiteMonitor.exe")
            if os.path.isdir(path):
                found.append((os.path.getmtime(exe) if os.path.exists(exe) else 0, path))
    found.sort(reverse=True)
    return [p for _, p in found]


def write_plugin(keys, target, port=18763):
    data = build_plugin(keys, port)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return data
