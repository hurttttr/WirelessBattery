#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把项目打成一份 exe。

    python build.py             # 目录版 dist/WirelessBattery/（冷启动 ~240ms，推荐）
    python build.py --onefile   # 单文件 dist/WirelessBattery.exe（分发省事，启动慢 ~0.4s）

产物是 **GUI 子系统**（`--windowed`）：双击和开机自启完全不会出现黑窗；
从终端启动时由 `wbc.console.attach_parent()` 挂到父控制台，`--once / --status`
这些照样能打印。一份 exe 两种形态，不需要额外的 pythonw 版本。

代价：cmd 不会等它跑完，提示符立刻返回 —— 靠退出码判断的脚本会受影响。

打包需要 PyInstaller，而且必须装在「带 tkinter 的那个解释器」里。
本脚本会自己挑解释器，挑不到会直接告诉你该装什么：

    "D:\\Program Files\\Python314\\python.exe" -m pip install pyinstaller
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 输出是管道、且系统代码页不是 UTF-8 时（CI 上常见），中文一 print 就
# UnicodeEncodeError。放宽 errors 保证不崩；想正常显示中文就设 PYTHONUTF8=1。
for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream is not None and hasattr(_stream, "reconfigure"):
            _stream.reconfigure(errors="replace")
    except Exception:
        pass

NAME = "WirelessBattery"

# http.server 会把 OpenSSL 拖进来占 5.8 MB，而本地只读 GET 桥根本用不上；
# unittest / pydoc / doctest / pdb 是标准库里顺手被牵连进来的，一并剔掉。
EXCLUDES = ("ssl", "_ssl", "hashlib", "unittest", "pydoc", "doctest", "pdb")

NO_WINDOW = 0x08000000


def _run(argv, **kw):
    return subprocess.run(argv, creationflags=NO_WINDOW, **kw)


def _tk_ok(exe):
    try:
        return _run([exe, "-c", "import tkinter"], capture_output=True,
                    timeout=30).returncode == 0
    except Exception:
        return False


def _pyinstaller_ok(exe):
    try:
        return _run([exe, "-m", "PyInstaller", "--version"], capture_output=True,
                    timeout=90).returncode == 0
    except Exception:
        return False


def find_python():
    """挑一个既带 tkinter、又装了 PyInstaller 的解释器。

    不能直接用 sys.executable —— PATH 上那个常见是精简版，没有 tkinter，
    用它打包出来的 exe 一启动就 `_tk_missing()`。
    """
    from wbc import autostart

    cands = [sys.executable]
    cands += autostart._candidate_interpreters(windowed=False)  # noqa: SLF001

    seen, ordered = set(), []
    for c in cands:
        try:
            key = os.path.normcase(os.path.abspath(c))
        except Exception:
            continue
        if key in seen or not os.path.exists(c):
            continue
        seen.add(key)
        ordered.append(c)

    for exe in ordered:
        if _tk_ok(exe) and _pyinstaller_ok(exe):
            return exe
    for exe in ordered:
        if _tk_ok(exe):
            raise SystemExit(
                "找到带 tkinter 的解释器，但它没装 PyInstaller：\n  %s\n"
                "先装：\"%s\" -m pip install pyinstaller" % (exe, exe))
    raise SystemExit(
        "没找到带 tkinter 的 64 位 Python 3.9+。\n"
        "用 `py -0p` 看看装了哪些，挑一个非精简版再跑本脚本。")


def make_icon(work_dir):
    """用项目自己的光栅化器生成 exe 图标 —— 不引入任何第三方依赖。"""
    from wbc import icons

    os.makedirs(work_dir, exist_ok=True)
    path = os.path.join(work_dir, "app.ico")
    try:
        icons.write_ico(path)
    except Exception as e:
        print("图标生成失败（继续，用默认图标）：%s" % e)
        return None
    return path if os.path.exists(path) and os.path.getsize(path) > 1024 else None


def tree_size(path):
    path = os.path.abspath(path)
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def main():
    ap = argparse.ArgumentParser(
        prog="build.py", description="把 WirelessBattery 打成一份 exe")
    ap.add_argument("--onefile", action="store_true",
                    help="打成单文件（默认是目录版：冷启动更快、杀软误报更少）")
    args = ap.parse_args()

    py = find_python()
    print("解释器：%s" % py)

    dist = os.path.join(HERE, "dist")
    work = os.path.join(HERE, "build")

    # 不在这里删 dist/build：PyInstaller 的 --noconfirm 会自己清掉旧产物，
    # build/ 留下来当增量缓存，重打包会快不少。两者都在 .gitignore 里。
    icon = make_icon(work)
    print("图标：%s" % (icon or "（默认）"))

    cmd = [py, "-m", "PyInstaller",
           "--noconfirm", "--clean",
           "--windowed",                      # GUI 子系统：双击/自启完全不会有黑窗
           "--name", NAME,
           "--distpath", dist,
           "--workpath", work,
           "--specpath", work]
    cmd.append("--onefile" if args.onefile else "--onedir")
    if icon:
        cmd += ["--icon", icon]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(os.path.join(HERE, "my_devices.py"))

    print("打包中……（首次约 20 秒）")
    rc = subprocess.call(cmd)
    if rc != 0:
        raise SystemExit("PyInstaller 失败，退出码 %d" % rc)

    target = (os.path.join(dist, NAME + ".exe") if args.onefile
              else os.path.join(dist, NAME, NAME + ".exe"))
    if not os.path.exists(target):
        raise SystemExit("没找到产物：%s" % target)

    folder = os.path.dirname(target)
    # 单文件版量它自己那份；量 dist/ 会把同时存在的目录版一起算进去
    measured = target if args.onefile else folder
    print("")
    print("产物：%s" % target)
    print("体积：%.1f MB" % (tree_size(measured) / 1048576.0))
    if not args.onefile:
        print("分发：整个 %s 目录一起给（exe 离不开旁边的 _internal\\）" % NAME)

    print("")
    print("下一步：")
    print('  "%s" --selftest' % target)
    print('  "%s" --status' % target)
    print('  "%s" --install     # 自启 + 桌面链接，快捷方式会隐藏控制台' % target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
