#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""无线外设电量小工具 —— 入口。

零第三方依赖：只用标准库 + ctypes，GUI 用 tkinter。

    python my_devices.py --once        读一次，打印两行电量
    python my_devices.py               托盘常驻（默认）
    python my_devices.py --panel       托盘常驻 + 启动时显示简洁面板
    python my_devices.py --debug-ui    完整调试版

不写任何日志文件；只有加 --debug 才有控制台输出。
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from wbc import console  # noqa: E402

# 一份 exe 两种形态：托盘模式下把系统为本进程新建的控制台藏掉，别弹黑窗。
# 必须排在 proto 前面 —— 那几个模块导入要几十毫秒，黑窗就是在这段时间露出来的。
if console.quiet_argv():
    console.hide_if_ours()

from wbc import app_dir, frozen, logx  # noqa: E402
from wbc import proto  # noqa: E402
from wbc import __version__  # noqa: E402

APP_TITLE = "无线外设电量"

# 用户看得见、可写的目录：源码运行时是项目根目录，打包后是 exe 所在目录。
# 不要用 HERE —— 打包后它指向包内部（onefile 还是退出即删的临时目录）。
APP_HOME = app_dir()

# 打包后 stdout 会退回 ANSI 代码页（中文机器上是 GBK），print 到图标字符就抛
# UnicodeEncodeError。这里只放宽 errors：encoding 不能改成 utf-8，否则往 GBK
# 控制台写 UTF-8 字节，中文会整段变乱码。
for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream is not None and hasattr(_stream, "reconfigure"):
            _stream.reconfigure(errors="replace")
    except Exception:
        pass


# ---------------------------------------------------------------- 文字排版

def _disp_width(s):
    w = 0
    for ch in s:
        w += 2 if ord(ch) > 0x2E80 else 1
    return w


def _pad(s, width):
    return s + " " * max(0, width - _disp_width(s))


# ---------------------------------------------------------------- 一次性读取

def run_once(show_time=False):
    logx.dbg("开始一次性读取")
    rows = []
    readings = proto.read_all()
    profiles = {p["key"]: p for p in proto.PROFILES}
    for key, r in readings.items():
        p = profiles.get(key, {})
        name = _pad(r.name or p.get("name") or key, 22)
        vidpid = "(%04X:%04X)" % (p.get("vid", 0), p.get("pid", 0))
        if r.ok:
            extra = ""
            if r.extra.get("charging") is True:
                extra = " 充电中"
            elif r.extra.get("charging") is False:
                extra = " 未充电"
            rows.append("%s %s  %3d%%%s   %s" % (name, vidpid, r.percent, extra, r.source))
        else:
            rows.append("%s %s   --    %s" % (name, vidpid, r.note or "读取失败"))
    for line in rows:
        print(line)
    if show_time:
        print("（%s）" % time.strftime("%H:%M:%S"))
    return 0 if all(readings[k].ok for k in readings) else 2


# ---------------------------------------------------------------- 状态

def run_capture(target, seconds=60, ping=False):
    """抓包：把某个 VID:PID 所有可读接口的输入报文带时间戳打出来。

    用途：换设备时先看清它到底会不会主动上报，再决定用什么协议去问。
    键盘/鼠标这类系统独占集合是打不开读句柄的（Windows 限制），会被标成不可读。
    """
    from wbc import hid

    targets = [t.strip().upper() for t in (target or "").split(",") if t.strip()]
    ifs = [i for i in hid.enumerate_interfaces() if i.vidpid.upper() in targets]
    if not ifs:
        print("未找到 %s 的任何 HID 接口" % "/".join(targets))
        return 1

    print("== 抓包 %s，共 %d 个接口，持续 %d 秒 ==" % (
        "/".join(targets), len(ifs), seconds))
    readers = []
    for i in ifs:
        tag = "%s usage=%04X/%04X" % (i.short_path().split("#")[-1], i.usage_page, i.usage)
        if not i.input_len:
            print("  [跳过] %-28s 没有输入报文" % tag)
            continue
        try:
            r = hid.OverlappedReader(i)
            r.drain(40)
            r.begin()
            readers.append((i, r))
            print("  [监听] %-28s in=%d" % (tag, i.input_len))
        except OSError as e:
            print("  [不可读] %-26s %s（系统独占集合，属正常）" % (tag, e))

    writers = []
    if ping:
        for i in ifs:
            if i.can_write and i.output_len:
                try:
                    writers.append((i, hid.HidController(i)))
                    print("  [可写] %-28s out=%d" % (i.short_path().split("#")[-1], i.output_len))
                except OSError:
                    pass

    t0 = time.time()
    next_ping = t0 + 1.0
    next_beat = t0 + 15.0
    last_frame = {}
    count = 0
    pings = [b"\x00", b"\x01"]
    ping_idx = 0
    try:
        while time.time() - t0 < seconds:
            for i, r in readers:
                data = r.poll(40)          # 内部会自动重新挂起，超时也不会失聪
                if data is None:
                    continue
                count += 1
                ts = time.time() - t0
                tag = i.short_path().split("#")[-1]
                prev = last_frame.get(tag)
                last_frame[tag] = bytes(data)
                body = " ".join("%02X" % b for b in data)
                if prev is not None and len(prev) == len(data) and prev != data:
                    # 标出和上一帧不同的字节，找标志位时一眼就能看到
                    marks = " ".join("^^" if data[k] != prev[k] else "  " for k in range(len(data)))
                    print("  %7.3fs  %-14s  %s" % (ts, tag, body))
                    print("  %7s  %-14s  %s   << 变化" % ("", "", marks))
                else:
                    print("  %7.3fs  %-14s  %s" % (ts, tag, body))
            if time.time() >= next_beat:
                print("  %7.3fs  …仍在监听（已收 %d 帧）" % (time.time() - t0, count))
                next_beat = time.time() + 15.0
            if writers and time.time() >= next_ping and readers:
                if ping_idx < len(pings):
                    for i, ctrl in writers:
                        body = bytearray(i.output_len)
                        body[0] = 0xB2                       # 厂商输出报表
                        body[1:1 + len(pings[ping_idx])] = pings[ping_idx]
                        try:
                            ctrl.write_output(bytes(body))
                            print("  %7.3fs  >> 已写 0xB2 探测(%02X)" % (
                                time.time() - t0, pings[ping_idx][0]))
                        except OSError as e:
                            print("  写失败:", e)
                    ping_idx += 1
                next_ping = time.time() + 4.0
            elif not pings:
                next_ping = time.time() + 4.0
    except KeyboardInterrupt:
        print("\n已中断")
    finally:
        for i, r in readers:
            r.close()
        for i, c in writers:
            c.close()

    print("== 结束：共收到 %d 帧 ==" % count)
    if count == 0:
        print("  这 %d 秒内没有任何输入报文。常见原因：设备在休眠 / 没连上 / "
              "或者它只在被轮询时才回复。" % seconds)
    return 0


# 查询型设备：target -> (标题, 说明, 读取函数)
POLL_TARGETS = {
    "3554:FA09": ("迈从 X75 V2",
                  "命令 0x05 · 20 字节帧 · 第 6 字节 = 电量 · 第 7 字节 bit4 = 充电中",
                  "read_keyboard"),
    "362D:D107": ("京东京造 无线鼠标（Keychron Link-KM）",
                  "命令 0x06 · 64 字节帧 · 第 20 字节低 7 位 = 电量",
                  "read_keychron"),
    "04B4:2412": ("飞智 Vader 4 Pro（需 DInput 模式）",
                  "命令 0xEC · 12 字节帧 · 电量是 0..5 档 ×20%（不是 1% 粒度）",
                  "read_gamepad"),
    "045E:028E": ("飞智 Vader 4 Pro（XInput 模式）",
                  "XInput 模式没有厂商通道；看到提示要切 DInput 是正常现象",
                  "read_gamepad"),
}


def _watch_poll(target, seconds, every=2.0):
    """主动查询型设备：每隔几秒问一次，把状态打出来。"""
    from wbc import proto

    title, hint, fn = POLL_TARGETS[target]
    read = getattr(proto, fn)
    print("== 查询 %s（%s），每 %.0f 秒一次，持续 %d 秒 ==" % (title, target, every, seconds))
    print("   " + hint)
    t0 = time.time()
    try:
        while time.time() - t0 < seconds:
            r = read()
            if r.ok:
                bits = ["电量 %3d%%" % r.percent]
                if r.extra.get("charging") is True:
                    bits.append("充电中")
                elif r.extra.get("charging") is False:
                    bits.append("未充电")
                if r.extra.get("flags") is not None:
                    bits.append("flags=0x%02X" % r.extra["flags"])
                frame = r.extra.get("frame", b"")
                print("  %6.1fs  %s   [%s]" % (
                    time.time() - t0, "  ".join(bits),
                    " ".join("%02X" % b for b in frame[:22])))
            else:
                print("  %6.1fs  %s" % (time.time() - t0, r.note))
            time.sleep(every)
    except KeyboardInterrupt:
        print("  已中断")
    return 0


def run_watch(target, seconds=60):
    """盯住设备的电量（推送型 / 查询型都支持）。

    雷柏这类只推不答，只能挂着句柄等；CX 键盘这类问一次答一次，就定时去问。
    """
    from wbc import proto

    target = (target or "").upper()

    if target in POLL_TARGETS:
        return _watch_poll(target, seconds)

    spec = proto.PUSH_DEVICES.get(target)
    if not spec:
        print("没有 %s 的推送协议。已知：%s" % (target, ", ".join(proto.PUSH_DEVICES)))
        print("可以先用 --capture 抓原始报文。")
        return 1

    mon = proto.get_monitor(target.replace(":", "_").lower(), **spec)
    print("== 监听 %s（%s），持续 %d 秒 ==" % (spec["name"], target, seconds))
    print("   协议：%d 字节推送报文，第 %d 字节 = 电量" % (spec["length"], spec["level_index"]))
    t0 = time.time()
    last = None
    try:
        while time.time() - t0 < seconds:
            pct, charge, online, frame, ts = mon.state()
            key = (pct, charge, online)
            if pct is not None and key != last:
                last = key
                bits = ["电量 %d%%" % pct]
                if charge is True:
                    bits.append("充电中")
                elif charge is False:
                    bits.append("未充电")
                bits.append("在线" if online else "离线")
                print("  %6.1fs  %s    [%s]" % (
                    time.time() - t0, "  ".join(bits), " ".join("%02X" % b for b in frame)))
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    pct, charge, online, frame, ts = mon.state()
    if pct is None:
        print("  这段时间没有收到推送。请把设备唤醒（动一下鼠标 / 按一下键 / 关开电源）后再试。")
    else:
        print("  最后读数：%d%%%s（%.0f 秒前）" % (
            pct, "，充电中" if charge else "", time.time() - ts))
    mon.stop()
    return 0


def run_make_plugin(plugin_dir=None, port=18763):
    """按当前插着的设备生成 LiteMonitor 插件 JSON。"""
    import shutil
    from wbc import lmplugin, proto, settings

    keys = lmplugin.present_keys()
    if not keys:
        keys = [p["key"] for p in proto.PROFILES]
        print("提示：当前没检测到任何已知设备，按支持的机型全部生成。")

    out = os.path.join(APP_HOME, "litemonitor", "PeripheralBattery.json")
    try:
        lmplugin.write_plugin(keys, out, port)
    except OSError:
        # 程序装在 Program Files 之类只读位置时退到数据目录
        out = os.path.join(settings.data_dir(), "litemonitor", "PeripheralBattery.json")
        lmplugin.write_plugin(keys, out, port)
    print("已生成插件：%s" % out)
    print("  包含 %d 台设备：%s" % (len(keys), "、".join(
        lmplugin.display_for(k, [k])[0] for k in keys)))

    target_dir = plugin_dir
    if not target_dir:
        found = lmplugin.find_plugin_dirs()
        if found:
            target_dir = found[0]
            if len(found) > 1:
                print("  找到多个 LiteMonitor：%s" % "，".join(found))
                print("  用最近安装的那个；要指定别的请加 --plugin-dir")
        else:
            print("  没找到 LiteMonitor 的 plugins 目录，请手动拷过去，或用 --plugin-dir 指定。")

    if target_dir:
        dst = os.path.join(target_dir, "PeripheralBattery.json")
        try:
            shutil.copyfile(out, dst)
            print("已写入：%s" % dst)
            print("  到 LiteMonitor 里「设置 → 插件 → 重载插件」即可生效。")
        except OSError as e:
            print("写入 %s 失败：%s" % (dst, e))
            return 1
    return 0


def run_status():
    from wbc import autostart, links, settings, win32 as wx

    print("== 设备 ==")
    ifaces = None
    try:
        from wbc import hid
        ifaces = hid.enumerate_interfaces(full=True)
    except Exception as e:
        print("  枚举失败：%s" % e)
    for p in proto.PROFILES:
        found = [i for i in (ifaces or []) if i.vendor_id == p["vid"] and i.product_id == p["pid"]]
        if found:
            for i in found:
                print("  %-22s %04X:%04X  usage=%04X/%04X in=%d out=%d feat=%d 可写=%s" % (
                    p["name"], p["vid"], p["pid"], i.usage_page, i.usage,
                    i.input_len, i.output_len, i.feature_len, i.can_write))
        else:
            print("  %-22s %04X:%04X  未发现" % (p["name"], p["vid"], p["pid"]))

    print("== 开机自启 ==")
    st = autostart.state()
    info = autostart.describe()
    label = {"absent": "未安装", "enabled": "已启用", "disabled": "已被任务管理器禁用"}.get(st, st)
    print("  状态：%s" % label)
    print("  快捷方式：%s" % info["path"])
    if info.get("target"):
        print("  目标：%s %s" % (info["target"], info.get("arguments", "")))
        # 程序被挪过位置时 .lnk 照样在，只是开机时静默失败 —— 这是"设了自启
        # 却不生效"最常见的原因，直接指出来比重装一遍有用。
        if not os.path.exists(info["target"]):
            print("  !! 目标不存在 —— 开机自启会静默失败，重跑 --install 修好")

    print("== 桌面链接 ==")
    desktop = wx.desktop_dir()
    d = os.path.join(desktop, links.DIR_LINK_NAME)
    f1 = os.path.join(desktop, links.file_link_name())
    f2 = f1 + ".lnk"
    for path, label in ((d, "目录链接"), (f1, "文件链接"), (f2, "文件快捷方式")):
        if os.path.exists(path) or os.path.islink(path):
            kind = "symlink" if os.path.islink(path) else (
                "junction" if os.path.isdir(path) else "file")
            print("  有  %-12s %s  -> %s   [%s]" % (
                label, path, os.path.realpath(path), kind))
        else:
            print("  无  %-12s %s" % (label, path))

    print("== 数据目录 ==")
    print("  %s" % settings.data_dir())
    print("== %s ==（不应出现 *.log）" % ("程序目录" if frozen() else "项目目录"))
    try:
        names = sorted(os.listdir(APP_HOME))
    except OSError as e:
        names = []
        print("  目录读不到：%s" % e)
    junk = [n for n in names if n.lower().endswith((".log", ".err", ".out"))]
    if junk:
        for name in junk:
            print("  !! 发现日志文件：%s" % name)
    else:
        print("  未发现日志文件")
    return 0


# ---------------------------------------------------------------- 安装/卸载

def run_install(make_links_only=False, serve=False):
    from wbc import autostart, links
    rc = 0
    if not make_links_only:
        try:
            path = autostart.enable(extra_args=" --serve" if serve else "")
            info = autostart.read_shortcut(path) or {}
            print("已创建开机自启：%s" % path)
            print("  目标：%s %s" % (info.get("target", ""), info.get("arguments", "")))
            print("  任务管理器 →「启动」里应当能看到这一项，可直接启用/禁用")
        except Exception as e:
            print("创建开机自启失败：%s" % e)
            rc = 1

    # 打包后没有 my_devices.py 可指，桌面链接直接指向 exe 自己
    if frozen():
        results = links.make_desktop_links(APP_HOME, os.path.abspath(sys.executable))
    else:
        results = links.make_desktop_links(HERE, os.path.join(HERE, "my_devices.py"))
    # 桌面链接只是顺手放的快捷方式：跨卷硬链接、同名冲突都可能不成，
    # 但托盘和自启都不依赖它，别让它把整个安装判成失败。
    for r in results:
        if r.ok:
            print("桌面链接 %-8s %s -> %s" % (r.kind, r.path, r.target))
            if r.message:
                print("        %s" % r.message)
        else:
            print("桌面链接没做成（不影响自启和托盘）：%s —— %s" % (r.path, r.message))
    return rc


def run_uninstall():
    from wbc import autostart, links
    rc = 0
    try:
        if autostart.state() != "absent":
            autostart.disable()
            print("已移除开机自启：%s" % autostart.startup_lnk_path())
    except Exception as e:
        print("移除开机自启失败：%s" % e)
        rc = 1
    for p in links.remove_desktop_links():
        print("已移除桌面链接：%s" % p)
    return rc


# ---------------------------------------------------------------- GUI

class Options(object):
    def __init__(self, tray=True, show_panel=False, debug=False, interval=None,
                 exit_after=None, serve=False, port=18763, serve_ttl=10.0):
        self.tray = tray
        self.show_panel = show_panel
        self.debug = debug
        self.interval = interval
        self.exit_after = exit_after
        self.serve = serve
        self.port = port
        self.serve_ttl = serve_ttl


def _tk_missing():
    """返回 True 表示当前解释器没有 tkinter（并打印替代方案）。"""
    try:
        import tkinter  # noqa: F401
        return False
    except ImportError:
        pass
    print("当前 Python 没有 tkinter，无法启动图形界面。")
    print("  解释器：%s" % sys.executable)
    from wbc import autostart
    alts = autostart._candidate_interpreters()
    if alts:
        print("可以改用这些带 tkinter 的解释器：")
        for a in alts:
            print("  %s" % a)
    else:
        print("请安装带 tkinter 的 Python（官方安装包默认包含）。")
    print("（--once / --status / --selftest 不需要 tkinter，仍可正常使用）")
    return True


def run_gui(opts):
    if _tk_missing():
        return 3
    from wbc import app as appmod
    application = appmod.BatteryApp(opts)
    return application.run()


# ---------------------------------------------------------------- CLI

def build_parser():
    p = argparse.ArgumentParser(
        prog=os.path.basename(sys.executable) if frozen() else "my_devices.py",
        description="无线外设电量小工具（2.4G 鼠标 / 键盘 / 耳机，多厂商协议）",
        epilog="默认以托盘方式常驻。不写日志文件，调试信息只在加 --debug 时输出到控制台。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_argument_group("运行形态")
    g.add_argument("--once", action="store_true", help="读一次并打印每台设备的电量（不进 GUI）")
    g.add_argument("--tray", action="store_true", help="托盘常驻（默认）")
    g.add_argument("--panel", action="store_true", help="托盘常驻，并立即显示简洁面板")
    g.add_argument("--no-tray", action="store_true", help="不创建托盘图标（需配合 --panel）")
    g.add_argument("--debug-ui", action="store_true", help="打开完整调试版")

    g2 = p.add_argument_group("参数")
    g2.add_argument("--interval", type=int, metavar="秒", help="刷新间隔，30 ~ 1800 秒（默认 60）")
    g2.add_argument("--debug", action="store_true", help="输出调试信息到控制台")
    g2.add_argument("--exit-after", type=float, metavar="秒", help="N 秒后自动退出（自测用）")

    g3 = p.add_argument_group("安装与诊断")
    g3.add_argument("--install", action="store_true", help="创建开机自启 + 桌面链接")
    g3.add_argument("--uninstall", action="store_true", help="移除开机自启 + 桌面链接")
    g3.add_argument("--make-links", action="store_true", help="只创建桌面链接")
    g3.add_argument("--status", action="store_true", help="显示设备/自启/链接状态")
    g3.add_argument("--selftest", action="store_true", help="不依赖硬件的协议自检")
    g3.add_argument("--capture", metavar="VID:PID", help="抓取该设备的输入报文（例如 362D:D101）")
    g3.add_argument("--seconds", type=int, default=60, metavar="秒", help="抓包时长，默认 60")
    g3.add_argument("--ping", action="store_true", help="抓包时顺带向厂商输出报表写探测包")
    g3.add_argument("--watch", metavar="VID:PID", help="监听推送型设备的电量（如 24AE:1412）")
    g3.add_argument("--serve", action="store_true",
                    help="启动本地 HTTP 接口；单独使用时只跑接口，配合 --tray/--panel 则并行")
    g3.add_argument("--only-serve", action="store_true",
                    help="只跑 HTTP 接口（不建托盘），等价于单独使用 --serve")
    g3.add_argument("--port", type=int, default=18763, metavar="端口",
                    help="HTTP 接口端口，默认 18763（被占用会自动顺延）")
    g3.add_argument("--serve-ttl", type=float, default=10.0, metavar="秒",
                    help="HTTP 接口的读数缓存时间，默认 10 秒")
    g3.add_argument("--make-plugin", action="store_true",
                    help="按当前插着的设备生成 LiteMonitor 插件 JSON（并尝试自动装到安装目录）")
    g3.add_argument("--plugin-dir", metavar="目录",
                    help="LiteMonitor 的 resources\\plugins 目录（默认自动查找）")
    g3.add_argument("--version", action="version", version="%(prog)s " + __version__)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    logx.set_debug(args.debug)
    # `--serve` 单独出现时就是"只跑接口"；想同时保留托盘要显式写 --tray 或 --panel
    args.only_serve = bool(args.only_serve or
                           (args.serve and not (args.tray or args.panel or args.debug_ui)))

    if args.selftest:
        return proto.selftest()
    if args.once:
        return run_once()
    if args.watch:
        return run_watch(args.watch, args.seconds)
    if args.capture:
        return run_capture(args.capture, args.seconds, args.ping)
    if args.only_serve:
        from wbc import httpserve
        return httpserve.run_forever(args.port, args.serve_ttl)
    if args.make_plugin:
        return run_make_plugin(args.plugin_dir, args.port)
    if args.status:
        return run_status()
    if args.install:
        return run_install(serve=bool(args.serve))
    if args.make_links:
        return run_install(make_links_only=True)
    if args.uninstall:
        return run_uninstall()

    show_panel = bool(args.panel or args.debug_ui)
    tray_on = not args.no_tray
    if not tray_on and not show_panel:
        show_panel = True

    opts = Options(tray=tray_on, show_panel=show_panel, debug=args.debug,
                   interval=args.interval, exit_after=args.exit_after,
                   serve=bool(args.serve and not args.only_serve),
                   port=args.port, serve_ttl=args.serve_ttl)
    if args.interval:
        from wbc import settings
        opts.interval = settings.clamp_interval(args.interval)
    if args.debug_ui:
        # 调试版单独跑，不建托盘
        opts.tray = False
        opts.show_panel = False
        return run_debug_standalone(args.debug)

    return run_gui(opts)


def run_debug_standalone(debug=False):
    """只开调试窗口（不建托盘），用于排障。"""
    if _tk_missing():
        return 3
    import tkinter as tk
    from wbc import settings, theme, win32 as wx
    from wbc import debugui

    wx.set_dpi_aware()
    theme.init_scale()
    root = tk.Tk()
    root.withdraw()
    try:
        ico = settings.app_icon_path()
        if ico:
            root.iconbitmap(ico)
    except Exception:
        pass
    w = debugui.DebugWindow(type("A", (), {"root": root})())
    w.protocol("WM_DELETE_WINDOW", lambda: (w.destroy(), root.quit()))
    root.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
