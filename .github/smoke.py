# -*- coding: utf-8 -*-
"""打包产物冒烟测试。CI 用它，本地也能跑。

    python .github/smoke.py --dist onedir      # dist/WirelessBattery/WirelessBattery.exe
    python .github/smoke.py --dist onefile     # dist/WirelessBattery.exe

为什么用 Python 而不是 pwsh：
    exe 是 GUI 子系统，PowerShell 的 `&` 不会等它结束（提示符立刻回来），
    既拿不到输出、也读不准退出码。subprocess.run 会老老实实等。
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from wbc import __version__  # noqa: E402

_fails = []


def _decode(raw):
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def run(exe, *args, timeout=180):
    p = subprocess.run([exe] + list(args), capture_output=True, timeout=timeout)
    return p.returncode, _decode((p.stdout or b"") + (p.stderr or b""))


def check(label, cond, extra=""):
    print("  %-28s %s%s" % (label, "ok" if cond else "FAIL", ("  " + extra) if extra else ""))
    if not cond:
        _fails.append(label)


def target(dist):
    if dist == "onefile":
        return os.path.join(ROOT, "dist", "WirelessBattery.exe"), None
    folder = os.path.join(ROOT, "dist", "WirelessBattery")
    return os.path.join(folder, "WirelessBattery.exe"), os.path.join(folder, "_internal")


def main():
    ap = argparse.ArgumentParser(description="打包产物冒烟测试")
    ap.add_argument("--dist", choices=("onedir", "onefile"), help="按 dist 目录约定推导产物路径")
    ap.add_argument("--exe", help="直接指定 exe（给了就不看 --dist）")
    a = ap.parse_args()

    exe = os.path.abspath(a.exe) if a.exe else target(a.dist)[0]
    tk_dir = None if a.exe else target(a.dist)[1]
    print("产物：%s" % exe)

    check("产物存在", os.path.exists(exe))
    if not os.path.exists(exe):
        return 1

    code, out = run(exe, "--version")
    check("--version 退出码 0", code == 0, "rc=%d" % code)
    check("--version 程序名正确", out.strip().startswith("WirelessBattery.exe"),
          repr(out.strip()))
    check("--version 版本号一致", __version__ in out, "期望 %s" % __version__)

    code, _ = run(exe, "--selftest")
    check("--selftest 退出码 0", code == 0, "rc=%d" % code)

    code, _ = run(exe, "--status")
    check("--status 退出码 0", code == 0, "rc=%d" % code)

    if tk_dir:
        # --selftest 只跑协议层，碰不到 GUI；tcl/tk 漏打包时上面全绿、用户
        # 双击却没界面。这里直接查产物里的 tk 运行库。
        # 本机 Tcl 8.6 是 tcl86t.dll，跑器的 Tcl 9.0 是 tcl90.dll + tcl9tk90.dll。
        names = os.listdir(tk_dir) if os.path.isdir(tk_dir) else []
        dlls = [n for n in names if n.endswith(".dll")]
        check("有 _tkinter.pyd", "_tkinter.pyd" in names)
        check("有 tcl 运行库", any(n.startswith("tcl") for n in dlls), str(dlls))
        check("有 tk 运行库", any("tk" in n for n in dlls), str(dlls))

    print("")
    if _fails:
        print("失败 %d 项：%s" % (len(_fails), "、".join(_fails)))
        return 1
    print("冒烟测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
