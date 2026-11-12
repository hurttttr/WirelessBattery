# -*- coding: utf-8 -*-
"""按"当前插着的设备"生成 LiteMonitor 插件 JSON。

LiteMonitor 的 output 列表是静态的：文件里写了哪几项，设置界面就会列出哪几项。
可外设不是每天都插着，写死一堆只会让面板里多出几行 ⛔。
所以这里按现场情况生成——插着什么就写什么，改完设备重跑一次即可。
"""

import json
import os
import shutil
import time

from . import proto
from . import win32 as wx

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

# LiteMonitor 的插件就放在 exe 旁边的 resources\plugins 里
LM_EXE = "LiteMonitor.exe"
PLUGIN_SUBDIR = ("resources", "plugins")

# ---------------------------------------------------------------- 找目录
#
# 以前这里只有一张写死的 glob 表（D:\MyTools\LiteMonitor* 之类），换台电脑
# 基本必然落空：便携版可能叫 LiteMonitor_v1.3.4-win-x64，也可能躺在 D:\工具
# 或者桌面。现在按"可信度从高到低"多路找：
#
#   1. 上次用过的目录（用户手选过就记住，一次就够）
#   2. 正在运行的 LiteMonitor 进程 —— 进程自己报的镜像路径，最准
#   3. 注册表：卸载项 / App Paths / MuiCache（跑过就留痕，便携版也躲不掉）
#   4. 开始菜单和桌面上的快捷方式
#   5. 浅扫固定磁盘（兜底，限层数 + 限时）

# 兜底扫描时跳过的目录名（扫进去纯属浪费时间，还可能很慢）
_PRUNE = frozenset((
    "windows", "windows.old", "$recycle.bin", "system volume information",
    "recovery", "msocache", "winsxs", "windowsapps", "packages", "installer",
    "appdata", "node_modules", "site-packages", "temp", "tmp", "cache", "caches",
    "程序文件", "系统卷信息",
))
_SCAN_DEPTH = 3          # 盘根往下最多三层：D:\MyTools\LiteMonitor_xxx 刚好够
_SCAN_LIMIT = 20000      # 最多看这么多个目录项
_SCAN_SECONDS = 1.5      # 再慢也不扫了，面板上不能卡太久

_UNINSTALL_KEYS = (
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
)
_APP_PATHS = (
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\App Paths\LiteMonitor.exe"),
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\App Paths\LiteMonitor.exe"),
)
# 用户跑过的每个 exe 都会在这里留一条，名字就是完整路径（便携版也跑不掉）
_MUI_CACHE = (
    r"Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache",
    r"Software\Microsoft\Windows\ShellNoRoam\MuiCache",
)
_APP_COMPAT_STORE = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Compatibility Assistant\Store"


def cached_dir():
    """上次手选（或上次写成功）的目录，没有返回 ""。"""
    try:
        from . import settings
        return (settings.Settings().plugin_dir or "").strip()
    except Exception:
        return ""


def remember_dir(folder):
    """记下这次用的目录 —— 下次点「装插件」就不用再满世界找了。"""
    folder = os.path.abspath(folder)
    try:
        from . import settings
        s = settings.Settings()
        if os.path.normcase(s.plugin_dir or "") == os.path.normcase(folder):
            return
        s.plugin_dir = folder
        s.save()
    except Exception:
        pass


def plugin_dir_for_exe(exe):
    """由 LiteMonitor.exe 推出它的插件目录（目录不存在也返回"应该在"的路径）。"""
    base = os.path.dirname(os.path.abspath(exe))
    cands = [
        os.path.join(base, *PLUGIN_SUBDIR),
        os.path.join(os.path.dirname(base), *PLUGIN_SUBDIR),   # exe 在 bin\ 这类子目录里
    ]
    for c in cands:
        if os.path.isdir(c):
            return c
    for c in cands:                                            # 退一步：只要有 resources
        if os.path.isdir(os.path.dirname(c)):
            return c
    return cands[0]


def normalize_plugin_dir(picked):
    """"用户选的东西" → 真正的 plugins 目录；认不出来返回 None。

    人手动选目录时很容易选偏：选到 LiteMonitor 根目录、选到 resources、
    甚至选中 exe 本身。以前是直接往选中的目录里写，JSON 落在根目录下，
    LiteMonitor 的插件列表里当然看不到。这里统一往回收敛。
    """
    p = os.path.abspath(os.path.expandvars(picked or ""))
    if not p:
        return None
    if os.path.isfile(p):
        return plugin_dir_for_exe(p)                # 选中的是 exe

    parts = [x.lower() for x in p.replace("/", "\\").split("\\") if x]
    if not parts:
        return None
    if parts[-1] == "plugins":
        return p
    if parts[-1] == "resources":
        return os.path.join(p, "plugins")
    if len(parts) >= 3 and parts[-3] == "resources" and parts[-2] == "plugins":
        return os.path.dirname(p)                   # 选到 plugins 里某个子目录了

    if os.path.isfile(os.path.join(p, LM_EXE)):
        return os.path.join(p, *PLUGIN_SUBDIR)
    if os.path.isdir(os.path.join(p, *PLUGIN_SUBDIR)):
        return os.path.join(p, *PLUGIN_SUBDIR)
    # 只有 resources 不够：C:\Windows\resources 也是这个名字（主题目录）。
    # 认安装根目录得靠目录名带 litemonitor，或者里面就有 LiteMonitor.exe。
    if "litemonitor" in parts[-1]:
        return os.path.join(p, *PLUGIN_SUBDIR)
    return None


def looks_like_plugin_dir(folder):
    """这个目录像不像 LiteMonitor 的插件目录（手选时用来拦一下明显选错的）。"""
    folder = os.path.abspath(folder)
    if os.path.basename(folder).lower() != "plugins":
        return False
    try:
        if any(n.lower().endswith(".json") for n in os.listdir(folder)):
            return True
    except OSError:
        return False
    # 空目录也认，但要求上层确实是 LiteMonitor
    parent = os.path.dirname(os.path.dirname(folder))
    return os.path.isfile(os.path.join(parent, LM_EXE))


def _reg_str(key, name):
    import winreg
    try:
        value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return ""
    return value.strip() if isinstance(value, str) else ""


def _registry_exes():
    """注册表里的线索：卸载项、App Paths、MuiCache、应用兼容性缓存。"""
    out = []
    try:
        import winreg
    except Exception:
        return out

    roots = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}

    for hive, path in _UNINSTALL_KEYS:
        try:
            with winreg.OpenKey(roots[hive], path, 0, winreg.KEY_READ) as parent:
                count = winreg.QueryInfoKey(parent)[0]
                names = []
                for i in range(count):
                    try:
                        names.append(winreg.EnumKey(parent, i))
                    except OSError:
                        break
        except OSError:
            continue
        for name in names:
            try:
                with winreg.OpenKey(roots[hive], path + "\\" + name, 0, winreg.KEY_READ) as k:
                    if "litemonitor" not in (_reg_str(k, "DisplayName") + name).lower():
                        continue
                    icon = _reg_str(k, "DisplayIcon")
                    if icon:
                        out.append(icon.split(",")[0].strip('"'))
                    loc = _reg_str(k, "InstallLocation")
                    if loc:
                        out.append(os.path.join(loc.strip('"'), LM_EXE))
            except OSError:
                continue

    for hive, path in _APP_PATHS:
        try:
            with winreg.OpenKey(roots[hive], path, 0, winreg.KEY_READ) as k:
                value, _ = winreg.QueryValueEx(k, None)
                if isinstance(value, str) and value.strip():
                    out.append(value.strip('"'))
        except OSError:
            pass

    # MuiCache / AppCompat Store 的"值名"就是 exe 全路径，跑过一次就留痕
    for path in _MUI_CACHE + (_APP_COMPAT_STORE,):
        try:
            with winreg.OpenKey(roots["HKCU"], path, 0, winreg.KEY_READ) as k:
                count = winreg.QueryInfoKey(k)[1]
                for i in range(count):
                    try:
                        name = winreg.EnumValue(k, i)[0]
                    except OSError:
                        break
                    low = name.lower()
                    if "litemonitor.exe" not in low:
                        continue
                    for suffix in (".friendlyappname", ".applicationcompany"):
                        if low.endswith(suffix):
                            name = name[:-len(suffix)]
                            break
                    out.append(name)
        except OSError:
            continue

    return [p for p in out if p and p.lower().endswith(".exe")]


def _shortcut_exes():
    """开始菜单 / 桌面上的 .lnk 指向哪儿。"""
    out = []
    folders = []
    for env in ("ProgramData", "APPDATA", "PUBLIC"):
        base = os.environ.get(env)
        if base:
            folders.append(os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs"))
            if env == "PUBLIC":
                folders.append(os.path.join(base, "Desktop"))
    folders.append(wx.desktop_dir())

    try:
        from .autostart import read_shortcut
    except Exception:
        return out

    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            if root.count(os.sep) - folder.count(os.sep) > 2:
                dirs[:] = []
                continue
            for name in files:
                if not name.lower().endswith(".lnk"):
                    continue
                try:
                    target = (read_shortcut(os.path.join(root, name)) or {}).get("target", "")
                except Exception:
                    continue
                if target.lower().endswith(LM_EXE.lower()):
                    out.append(target)
    return out


def _scan_roots():
    """浅扫的起点。顺序有讲究：数据盘在前（便携工具大多放在 D/E），系统盘垫后。"""
    system = (os.environ.get("SystemDrive") or "C:").upper()
    drives = wx.fixed_drives()
    roots = [d for d in drives if not d.upper().startswith(system)]
    roots += [d for d in drives if d.upper().startswith(system)]
    roots.append(os.path.expanduser("~"))
    for key in ("LOCALAPPDATA", "APPDATA", "ProgramFiles", "ProgramW6432",
                "ProgramFiles(x86)", "PUBLIC"):
        value = os.environ.get(key)
        if value:
            roots.append(value)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(os.path.join(local, "Programs"))

    out, seen = [], set()
    for r in roots:
        r = os.path.abspath(r)
        key = os.path.normcase(r)
        if key in seen or not os.path.isdir(r):
            continue
        seen.add(key)
        out.append(r)
    return out


def _scan_one(root, seconds, limit, found):
    """从 root 往下浅扫，把带 LiteMonitor.exe 的目录记进 found。"""
    deadline = time.monotonic() + seconds
    stack = [(root, 0)]
    while stack:
        if limit <= 0 or time.monotonic() > deadline:
            return
        folder, depth = stack.pop()
        if os.path.isfile(os.path.join(folder, LM_EXE)):
            found.append(os.path.join(folder, LM_EXE))
        if depth >= _SCAN_DEPTH:
            continue
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    limit -= 1
                    if limit <= 0:
                        return
                    try:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    name = entry.name.lower()
                    if name in _PRUNE or name.startswith((".", "$")):
                        continue
                    stack.append((entry.path, depth + 1))
        except OSError:
            continue


def _scan_exes():
    """浅扫固定磁盘，找带 LiteMonitor.exe 的目录。

    每个根分到一份时间和数量配额：不然一个大盘的用户目录动辄几万个目录项，
    能把配额全吃光，D 盘上的 LiteMonitor 一次都没被看过。
    """
    found = []
    roots = _scan_roots()
    if not roots:
        return found
    seconds = max(0.25, _SCAN_SECONDS / len(roots))
    limit = max(800, _SCAN_LIMIT // len(roots))
    for root in roots:
        _scan_one(root, seconds, limit, found)
    return found


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
    """LiteMonitor 的 resources/plugins 目录，按可信度排序（去重、只要存在的）。"""
    dirs, seen = [], set()

    def put(folder):
        if not folder:
            return
        folder = os.path.abspath(folder)
        key = os.path.normcase(folder)
        if key in seen or not os.path.isdir(folder):
            return
        seen.add(key)
        dirs.append(folder)

    put(cached_dir())                     # 1. 上次用的

    for _, exe in wx.process_images(LM_EXE):        # 2. 正在跑的（最准）
        put(plugin_dir_for_exe(exe))
    for exe in _registry_exes():                    # 3. 注册表：跑过就留痕
        put(plugin_dir_for_exe(exe))
    for exe in _shortcut_exes():                    # 4. 开始菜单 / 桌面快捷方式
        put(plugin_dir_for_exe(exe))

    if not dirs:                                    # 5. 兜底：浅扫磁盘（这一步慢）
        for exe in _scan_exes():
            put(plugin_dir_for_exe(exe))
    return dirs


def write_plugin(keys, target, port=18763):
    data = build_plugin(keys, port)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return data


def install(keys, out_dir, target_dir=None, port=18763):
    """生成插件 JSON，并复制进 LiteMonitor 的 plugins 目录。

    target_dir 给了就用它（面板上手动选的，会先过一遍 normalize_plugin_dir），
    否则按"进程 → 注册表 → 快捷方式 → 扫盘"自动找。

    写成功之后把目录记进 settings，下次直接命中，不再满世界找。

    返回 dict：
        src    生成出来的文件 —— 一定会生成，写失败会抛 OSError
        dst    真正写进 LiteMonitor 的路径；None 表示没找到目录或没写成功
        dirs   这次考虑过的候选目录
        error  写入失败的原因（多个目录时保留最后一条）
        solo   目标目录里原先没有别的插件 —— 路径有可能选歪了，值得提醒一句
    """
    src = os.path.join(out_dir, PLUGIN_ID + ".json")
    write_plugin(keys, src, port)

    if target_dir:
        folder = normalize_plugin_dir(target_dir) or target_dir
        dirs = [folder]
    else:
        dirs = find_plugin_dirs()

    dst, error, solo = None, None, False
    for folder in dirs:
        try:
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, PLUGIN_ID + ".json")
            try:
                others = [n for n in os.listdir(folder)
                          if n.lower().endswith(".json")
                          and n.lower() != (PLUGIN_ID + ".json").lower()]
            except OSError:
                others = []
            shutil.copyfile(src, path)
            dst, solo = path, not others
            break
        except OSError as e:
            error = "%s：%s" % (folder, e)
    if dst:
        remember_dir(os.path.dirname(dst))
    return {"src": src, "dst": dst, "dirs": dirs, "error": error, "solo": solo}
