# -*- coding: utf-8 -*-
"""本地 HTTP 桥。

有些宿主程序（比如 LiteMonitor）的插件系统只支持"发 HTTP 请求 + 解析 JSON"，
不允许执行本地程序。想把 HID 电量喂给它，就得在本机开一个小接口，
把这些读数以 JSON 暴露出去。

设计取舍：
    * 只绑 127.0.0.1 —— 不触发防火墙弹窗，也不会被局域网里的机器看到。
    * 带 TTL 缓存 —— HID 读取要几秒，宿主可能每隔几十秒来一次，
      缓存能让重复请求立刻返回，也避免频繁打扰设备。
    * 同一时刻只允许一次真实读取（锁），并发请求排队等同一份结果。
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import logx, proto

DEFAULT_PORT = 18763
DEFAULT_TTL = 10.0


def level_state(pct):
    """和面板同一套阈值：>=60 绿(0) / 30-59 黄(1) / <30 红(2)。"""
    if pct is None:
        return "0"
    if pct >= 60:
        return "0"
    if pct >= 30:
        return "1"
    return "2"


def build_payload(readings):
    """把 Reading 字典摊平成宿主好提取的 JSON。

    既有扁平字段（`raptoo` / `raptoo_state` / `raptoo_mark`），
    也有一份 `devices` 数组，方便别的程序整体消费。
    """
    flat = {"ok": True, "ts": time.strftime("%H:%M:%S"), "devices": []}

    # 先把所有"已知设备"的字段铺成空值，再拿真实读数覆盖。
    # 这一步不能省：LiteMonitor 的插件执行器只要发现某个 extract 取到"未知"
    # （字段在 JSON 里根本不存在），就会在整步抛异常，把所有监控项一起标成 ❌。
    # 换句话说，设备没插的时候也必须"字段在、值为空"，不能整个字段消失。
    for p in proto.PROFILES:
        blank = {
            p["key"]: "",
            p["key"] + "_name": p["name"],
            p["key"] + "_state": "0",
            p["key"] + "_state_charge": "0",
            p["key"] + "_mark": "",
            p["key"] + "_text": "",
            p["key"] + "_charging": 0,
            p["key"] + "_online": 0,
            p["key"] + "_stale": 0,
            p["key"] + "_age": 0,
            p["key"] + "_source": "",
        }
        for k, v in blank.items():
            flat.setdefault(k, v)

    for key, r in readings.items():
        pct = r.percent if r.ok else None
        extra = getattr(r, "extra", None) or {}
        charging = extra.get("charging")
        online = extra.get("online", True)
        flat[key] = pct if pct is not None else ""
        flat[key + "_name"] = r.name or key
        flat[key + "_state"] = level_state(pct)
        # 可选：充电时按"安全"（绿）显示。默认不用它——低电量该红就红，
        # 想用的人把插件里的 {{xxx_state}} 换成 {{xxx_state_charge}} 即可。
        flat[key + "_state_charge"] = "0" if charging is True else level_state(pct)
        flat[key + "_mark"] = "⚡" if charging is True else ""
        flat[key + "_text"] = ("充电中（推断）" if extra.get("charge_src") == "trend"
                               else "充电中") if charging is True else ""
        flat[key + "_charging"] = 1 if charging is True else 0
        flat[key + "_online"] = 1 if (r.ok and online) else 0
        # 设备休眠时会退回"上次读数"：把新鲜度也暴露出去，宿主想显示就显示，不想显示也不误导
        stale = bool(extra.get("stale"))
        flat[key + "_stale"] = 1 if stale else 0
        flat[key + "_age"] = int(extra.get("stale_age", 0) // 60) if stale else 0
        flat[key + "_source"] = r.source or r.note or ""
        flat["devices"].append({
            "key": key,
            "name": r.name or key,
            "percent": pct,
            "charging": charging,
            "online": bool(r.ok and online),
            "source": r.source or "",
            "note": r.note or "",
        })
    flat["count"] = len(flat["devices"])
    return flat


class _Cache(object):
    def __init__(self, ttl):
        self.ttl = float(ttl)
        self._lock = threading.Lock()
        self._data = None
        self._at = 0.0

    def get(self, fresh=False):
        with self._lock:                     # 只让一个线程真去读 HID
            if (not fresh and self._data is not None
                    and time.time() - self._at < self.ttl):
                return self._data
            started = time.time()
            self._data = build_payload(proto.read_all())
            self._at = time.time()
            dbg_ms = (self._at - started) * 1000
            logx.dbg("HTTP 桥刷新 %.0f ms" % dbg_ms)
            return self._data


class _Handler(BaseHTTPRequestHandler):
    server_version = "PeripheralBattery/1.0"
    protocol_version = "HTTP/1.1"          # 让宿主的 HttpClient 能复用连接

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/", "/battery", "/battery.json", "/json"):
            fresh = "fresh=1" in self.path
            try:
                data = self.server.cache.get(fresh=fresh)
            except Exception as e:                      # 设备异常不要 500 掉宿主
                logx.warn("HTTP 桥读取失败：%r" % e)
                data = {"ok": False, "error": str(e), "devices": []}
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._send(200, body)
            return
        if path == "/health":
            self._send(200, b'{"ok":true}', "application/json; charset=utf-8")
            return
        self._send(404, b'{"ok":false,"error":"not found"}')

    do_HEAD = do_GET

    def log_message(self, fmt, *args):
        # BaseHTTPRequestHandler 默认往 stderr 打访问日志；本项目"不写日志"，
        # 只在 --debug 时进控制台。
        logx.dbg("HTTP " + (fmt % args))


class Bridge(object):
    def __init__(self, port=DEFAULT_PORT, ttl=DEFAULT_TTL, host="127.0.0.1"):
        self.port, self.host, self.ttl = port, host, ttl
        self.httpd = None
        self.thread = None

    def _make(self):
        httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        httpd.daemon_threads = True
        httpd.cache = _Cache(self.ttl)
        return httpd

    def start(self):
        """后台线程启动，返回实际端口（端口被占时自动顺延）。"""
        last = None
        for port in range(self.port, self.port + 12):
            try:
                self.port = port
                self.httpd = self._make()
                break
            except OSError as e:
                last = e
                logx.dbg("端口 %d 不可用：%s" % (port, e))
        else:
            raise OSError("找不到可用端口：%s" % last)
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       name="http-bridge", daemon=True)
        self.thread.start()
        return self.port

    def stop(self):
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
        self.httpd = None


def run_forever(port=DEFAULT_PORT, ttl=DEFAULT_TTL):
    """阻塞式运行（给 `--serve` 单独使用时）。"""
    bridge = Bridge(port, ttl)
    real = bridge.start()
    print("本地电量接口已启动：http://127.0.0.1:%d/battery" % real)
    print("  返回 JSON，字段见下方；Ctrl+C 结束。")
    print("  例如：{\"ok\":true,\"raptoo\":96,\"raptoo_state\":\"0\",…}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        bridge.stop()
    return 0
