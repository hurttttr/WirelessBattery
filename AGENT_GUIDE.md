# AGENT_GUIDE — 交给 AI Agent 的部署 / 适配手册

> 读者是 **AI Agent**，不是人。目标是让你（Agent）在拿到这个项目后，能独立完成两件事：
> **A. 帮用户把项目部署跑通**；**B. 帮用户适配他自己新买的无线外设**。
>
> 人的阅读版在 `PROTOCOLS.md`（协议细节）和 `LITEMONITOR.md`（接 LiteMonitor）。
> 本文只讲"你该按什么顺序做什么、做完怎么自证"。

---

## 0. 硬约束（违反即失败）

| 约束 | 说明 |
|---|---|
| **零第三方依赖** | 只用 Python 标准库 + `ctypes` + `tkinter`。**不要** `pip install hidapi/pywin32/Pillow/pysystray` |
| **不写日志文件** | 任何情况下不在项目目录产生 `.log`。调试信息只在 `--debug` 时打到控制台 |
| **不擅自改用户的系统设置** | 开机自启、桌面链接只在用户明确要求时创建；状态不一致时**只报告不纠正** |
| **不猜协议** | 电量字节/充电位必须有实测证据（充电递增、拔插对比）。拿不到就说拿不到 |
| **改完必须自证** | 每次改代码后跑 §5 的验收命令，把真实输出贴给用户 |

---

## 1. 环境自检（**第一步，别跳**）

```bash
cd <项目目录>
python -c "import sys;print(sys.version);print('64bit' if sys.maxsize>2**32 else '32bit')"
python -c "import tkinter;print('tk ok', tkinter.TkVersion)"
```

- **必须 64 位 Python**（项目里多处按 x64 结构体布局写死）。
- 如果 `import tkinter` 失败：说明这台机器上的 `python` 是精简版（常见于某些 IDE 自带）。
  用下面任一方式找到带 tkinter 的解释器，后续命令全部替换成它的绝对路径：

```bash
py -0p                     # 列出所有已安装的 Python
python my_devices.py --status   # 里面的"开机自启"一节会显示它实际选中的解释器
```

> 已知个案：某机器上 PATH 里的 `python` 没有 tkinter，可用的是 `D:\Program Files\Python314\python.exe`。
> `wbc/autostart.py` 会自动探测并写进快捷方式，你不需要手工指定。

---

## 2. 项目结构（改哪里）

| 文件 | 行数 | 职责 | 什么时候改 |
|---|---:|---|---|
| `my_devices.py` | 571 | CLI 入口、参数分发、`--capture/--watch/--serve` 等子命令 | 加新的 CLI 功能 |
| `wbc/__init__.py` | 20 | `__version__`、`APP_NAME`、`APP_ID` | 改应用名 / 版本号 |
| `wbc/app.py` | 343 | 进程装配：单实例锁、线程模型、面板唤起、退出路径 | 改运行时生命周期 |
| `wbc/proto.py` | 1625 | **协议层**：`PROFILES` 设备清单、各厂商读取器、`read_all()`、自检 | **适配新设备主要改这里** |
| `wbc/hid.py` | 456 | HID 枚举、`HidController`（写）、`OverlappedReader`（异步读） | 极少需要改 |
| `wbc/win32.py` | 611 | ctypes 封装：SetupAPI / User32 / GDI / Shell / COM | 极少需要改 |
| `wbc/tray.py` | 367 | 托盘宿主窗口、`SetTimer`、`Shell_NotifyIcon` | 加托盘交互 |
| `wbc/icons.py` | 346 | 纯 Python 光栅化数字图标 → `HICON` | 改图标样式 |
| `wbc/panel.py` | 350 | 简洁面板（两张/多张卡片） | 改面板布局 |
| `wbc/debugui.py` | 465 | 完整调试版（列设备、转储、探测、标定） | 加调试工具 |
| `wbc/httpserve.py` | 211 | 本地 HTTP 桥（给 LiteMonitor 这类宿主） | 改 JSON 字段 |
| `wbc/lmplugin.py` | 152 | 按当前设备生成 LiteMonitor 插件 JSON | 改插件图标/命名 |
| `wbc/autostart.py` | 368 | 启动文件夹 `.lnk`（纯 ctypes COM） | 改自启行为 |
| `wbc/links.py` | 141 | 桌面目录/文件链接（symlink → junction → 硬链接） | 极少 |
| `wbc/settings.py` | 128 | `settings.json` / `calibration.json` | 加配置项 |
| `wbc/theme.py` | 82 | DPI 缩放、字体、配色 | 改视觉 |
| `wbc/logx.py` | 46 | 调试输出开关 | 极少 |
| `PROTOCOLS.md` | 566 | 六台设备的协议汇编 + 逆向方法论 | **适配完新设备必须更新** |
| `LITEMONITOR.md` | 283 | LiteMonitor 接入手册 | 改插件行为时更新 |
| `README.md` | 280 | 面向人的门面：特性、设备表、CLI、FAQ | 加设备 / 改 CLI / 改定位时同步 |

> 行数只作为规模参考，会随改动漂移；要准确值就 `wc -l wbc/*.py my_devices.py *.md`。

**数据结构约定**：`proto.PROFILES` 是设备清单的唯一真相，每条形如

```python
dict(key="jd", kind="kch", vid=0x362D, pid=0xD107,
     category="mouse", name="京东京造 无线鼠标", subtitle="Keychron Link-KM")
```

`key` 会一路串到 HTTP JSON 字段名、LiteMonitor output key、面板/托盘的槽位名。**改了 key 等于改了外部接口**，不要随意改。

---

## 3. 部署流程（按序执行，每步验证）

### 3.1 跑起来（1 分钟）

```bash
python my_devices.py --selftest     # 协议自检，不碰硬件
python my_devices.py --once         # 读一次真实设备
```

期望：

```
自检全部通过
雷柏 Rapoo 无线鼠标    (24AE:1412)   96%   设备推送 13B 报文 · 第 8 字节
迈从 X75 V2            (3554:FA09)  100%   0x05 查询 · 20B 帧第 6 字节
京东京造 无线鼠标      (362D:D107)   31% 充电中   0x06 查询 · 64B 帧第 20 字节
```

- **没插任何受支持的设备**时会打印 `未发现接收器` 之类的提示，这是正常的，不要当成故障。
- 退出码：全部成功 `0`，有设备失败 `2`。

### 3.2 装常驻（用户明确要求再做）

```bash
python my_devices.py --install            # 开机自启 + 桌面链接
python my_devices.py --install --serve    # 顺便把本地 HTTP 接口写进启动项
python my_devices.py --status             # 核对
python my_devices.py --uninstall          # 撤销
```

`--status` 会打印：设备列表、自启状态、桌面链接类型（`symlink` / `junction` / `hardlink` / `.lnk`）、数据目录、是否有多余日志文件。

### 3.3 可选：接 LiteMonitor

```bash
python my_devices.py --tray --serve     # 托盘 + 本地接口（同一个进程！）
python my_devices.py --make-plugin      # 按当前设备生成插件并自动装到安装目录
```

然后让用户在 LiteMonitor 里「设置 → 插件 → 重载插件」。细节见 `LITEMONITOR.md`。

> **同一时刻只能有一个进程读 HID**。两个进程同时读同一批接口会互相抢报文，
> 表现为"数据忽有忽无"。所以桥和托盘要在**同一个进程**里（`--tray --serve`）。

---


> 已适配的设备清单见 `PROTOCOLS.md` §0 速查表。其中**飞智手柄是"换模式就换 VID:PID"的典型**：
> XInput `045E:028E` 只有 15 字节摇杆报文、没有输出报表（命令发不出去），
> 必须切到 DInput `04B4:2412` 才有厂商通道；`PROFILES` 里用 `also` 字段把第二个
> VID:PID 也认进来，这样即使用户没切模式，面板上也会出现一张卡提示怎么切。

## 4. 适配新设备（核心任务）

### 4.1 先问用户三件事

1. 设备**型号**（有些 dongle 固件会自报名字，能省一半功夫）。
2. 是否支持**有线模式**（有线时通常枚举成另一个更"好说话"的 HID 设备）。
3. 是否装过**官方驱动/网页配置工具**（如果有，它的前端 JS 里往往直接写着命令字）。

### 4.2 枚举：这台机器上到底有什么

```bash
python -c "from wbc import hid; [print('%-9s up=%04X u=%04X in=%3d out=%3d feat=%3d w=%-5s %s' % (i.vidpid,i.usage_page,i.usage,i.input_len,i.output_len,i.feature_len,i.can_write,i.short_path())) for i in hid.enumerate_interfaces(full=True)]"
```

看三件事：

- `usage_page` 是 `0xFF00~0xFFFF`（或 `0x008C` / `0xFF60`）的接口 = **厂商通道**，命令从这里发。
- 鼠标/键盘顶层集合（`0x0001/0x0002`、`0x0001/0x0006`）`can_write=False`、打不开读句柄（`err=5`），**这是系统独占，属正常**，不要试图绕。
- `feat>0` 说明有 Feature Report，先试 `HidD_GetFeature`——最省事。

### 4.3 判断交互模型（决定后面怎么走）

| 现象 | 模型 | 参考实现 |
|---|---|---|
| 挂 60~120 秒，什么都不发也有固定长度帧 | **主动推送** | `PushBatteryMonitor` + `PUSH_DEVICES`（雷柏） |
| 发一条命令回一条（或两条） | **请求-应答** | `read_keyboard()`（迈从）、`read_keychron()`（京造） |
| 只有 Feature Report | Feature 读取 | `_read_standard_battery()` |

### 4.4 被动抓包（零风险，先做这个）

```bash
python my_devices.py --capture 362D:D107 --seconds 120
```

- 输出会**自动标出与上一帧不同的字节**（`<< 变化`），找标志位时一眼就能看到。
- 抓包期间请用户**正常使用设备**（动鼠标 / 敲键盘 / 关开电源）。
- 抓不到就说明是"查询型"，转 4.5。

### 4.5 主动探测（有风险，先说明再动手）

原则：**参数全填 0**，只变命令字，避免改动设备设置。

```python
# 模板：扫命令字，只打印"不是 0xE4/不是回显"的回应
import sys, time
sys.path.insert(0, r"<项目目录>")
from wbc import hid
ifs = [i for i in hid.enumerate_interfaces(full=True) if i.vidpid.upper() == "VID:PID"]
i = [x for x in ifs if x.output_len == 64 and x.can_write][0]   # ← 换成长度正确的那个
with hid.HidController(i) as ctrl, hid.OverlappedReader(i) as r:
    r.drain(150)
    for cmd in range(0x00, 0x100):
        body = bytearray(i.output_len); body[0] = 0xB3; body[1] = cmd   # ← 换 Report ID
        ctrl.write_output(bytes(body))
        d = r.poll(120)
        if d and not (len(d) > 2 and d[1] == 0xE4):    # 0xE4 = 不支持
            print("0x%02X -> %s" % (cmd, " ".join("%02X" % b for b in d[:24])))
```

要点：

- **两个进程不能同时读同一接口**。探测前先确认没有 `--capture` 或 `--serve` 在跑。
- 写入长度必须**等于** `OutputReportByteLength`，短一个字节就是 `err=87`。
- 首字节是 **Report ID**，不是命令。

### 4.6 定位电量字节（唯一可靠的方法）

> ❗ 不要看到 `0x64`(100) 就认定是电量。它可能是音量、亮度、档位。

1. 让设备**进入充电**，每 20 秒采样一次命令回应，持续 5~10 分钟。
2. **每 20 秒递增 1 的那个字节**就是电量百分比。
3. 顺带确认：电量应单调上升，且在 `0..100`（或该字段的低 7 位）范围内。

```bash
python my_devices.py --watch VID:PID --seconds 300    # 已支持查询型/推送型
```

### 4.7 定位充电位（拔插对比法）

1. 设备**插着充电线**时抓一帧完整回应，存下来（程序里存 `bytes`，**不要手抄十六进制**——手抄漏一个字节就会让后面所有偏移错位，这个坑踩过）。
2. 让用户**拔掉充电线**，再抓一帧。
3. 逐字节对比：**只有它变了的那一位**才是充电标志。
4. 注意过渡态：刚拔线瞬间可能出现 `0x00`，等 2~3 秒稳定后再取。

### 4.8 落地代码（5 处，缺一不可）

以"新增一台 `XXXX:YYYY` 的鼠标"为例：

**① `wbc/proto.py` — 加常量与清单**

```python
NEW_VID, NEW_PID = 0xXXXX, 0xYYYY

PROFILES = [
    ...
    dict(key="newmouse", kind="new", vid=NEW_VID, pid=NEW_PID,
         category="mouse", name="品牌 型号", subtitle="2.4G dongle"),
]
```

**② 写读取器**（照抄 `read_keyboard()` 的结构：`HidController` + `OverlappedReader` + `attempts` 重试 + `drain` 防串味）

```python
def read_newmouse(iface=None, timeout_ms=800, attempts=3):
    ...
    return _target_reading("newmouse", ok=True, percent=pct,
                           source="0xNN 查询 · NB 帧第 M 字节",
                           extra={"charging": charging, "online": True, "frame": bytes(resp)})
```

**③ `_read_profile()` 里加分支**，并套上 `_recall(_remember(...))`（设备休眠时退回上次读数）：

```python
if kind == "new":
    iface = match_newmouse(_all_interfaces())
    ...
    return _recall(p["key"], _remember(p["key"], r))
```

**④ `wbc/lmplugin.py` — 给显示名**：`BRAND["newmouse"] = "品牌"`（大类已在 `PROFILES.category`）。

**⑤ `proto.selftest()` — 加自检用例**（把 4.6/4.7 抓到的真实帧回放进去，这是**无硬件也能验证**的关键）：

```python
print("新设备（0xNN / NB 字节）")
q = new_build(0xNN)
check("查询帧长度", len(q) == NB)
chg = bytes.fromhex("...")          # 充电中实抓
dis = bytearray(chg); dis[20] &= 0x7F
check("充电中 30%", new_parse(chg) == (30, True), new_parse(chg))
check("未充电 30%", new_parse(bytes(dis)) == (30, False))
```

### 4.9 收尾（不做会被下一个人骂）

- **更新 `PROTOCOLS.md`**：速查表加一行 + 新增一节（接口/帧格式/真实报文/解析函数/命令地图）。**真实报文必须贴原文**。
- 跑一遍 §5 的验收。
- 如果用户在接 LiteMonitor：`python my_devices.py --make-plugin` 重生成。

---

## 5. 验收清单（改完必须逐条跑）

| # | 命令 | 期望 |
|---|---|---|
| 1 | `python my_devices.py --selftest` | 末尾 `自检全部通过` |
| 2 | `python my_devices.py --once` | 每台在插设备一行，含 `%（或 --）` 与来源 |
| 3 | `python my_devices.py --status` | 设备/自启/链接三段齐全，`未发现日志文件` |
| 4 | 托盘启停 3 次 | 每次退出码 0、耗时 < 1 秒、无残留窗口/进程 |
| 5 | 空闲 75 秒 CPU | < 0.5 秒（实测约 0.03 秒） |
| 6 | `ls *.log` | 无输出 |

托盘启停的自测脚本（可直接用）：

```python
import subprocess, sys, time
sys.path.insert(0, r"<项目目录>")
from wbc import win32 as wx
exe = r"<带 tkinter 的 pythonw.exe>"
for i in range(3):
    p = subprocess.Popen([exe, "my_devices.py", "--tray"], cwd=r"<项目目录>")
    time.sleep(3)
    hwnd = wx.user32.FindWindowW("WirelessBatteryTrayWnd", None)
    t0 = time.time()
    if hwnd: wx.user32.PostMessageW(hwnd, 0x8000 + 5, 0, 0)   # WM_SHUTDOWN
    print(i + 1, p.wait(timeout=8), "%.2fs" % (time.time() - t0))
```

---

## 6. 已知坑（症状 → 原因 → 规避）

| 症状 | 原因 | 规避 |
|---|---|---|
| 一读就整个进程消失，没有异常栈 | 同一句柄上阻塞 `ReadFile` + 并发 `WriteFile`/IOCTL | 读句柄单独开、带 `FILE_FLAG_OVERLAPPED`；写用另一个句柄 |
| 用户明明在动设备，却一帧都收不到 | 异步读超时后没重新发起 `ReadFile`，"失聪"了 | 用 `OverlappedReader.poll()`（内部自动重新挂起） |
| 数据忽有忽无、像随机丢包 | 两个进程/两个句柄同时读同一接口，互相抢帧 | 抓包与探测不同时进行；桥与托盘同进程 |
| `CreateFileW` 失败（路径少了开头的设备前缀） | x64 下 `SP_DEVICE_INTERFACE_DETAIL_DATA_W` 的 `cbSize=8`，但**路径字符串从偏移 4 开始** | 用 `win32.DETAIL_PATH_OFFSET`，别写死 8 |
| 鼠标/键盘接口打不开 | 系统独占顶层集合 | 只读打开（`dwDesiredAccess=0`），且别指望收报文 |
| 写命令报 `err=87` | 长度不等于 `OutputReportByteLength` | 严格按 `iface.output_len` 发送，首字节是 Report ID |
| 托盘图标数字上下颠倒 | `CreateIconIndirect` 把位图当**底朝上**读 | 行序倒着写（`icons.py` 已处理） |
| 托盘图标数字糊成一团 | 点阵字体在非整数缩放下被插值 | 整数倍缩放（`_fit_text` 已处理） |
| 卡片边框断成几段 | Tk 的 `smooth` 多边形在长边上走形 | 用 `theme.round_rect()`（两矩形+四圆） |
| 关托盘后进程赖着不走、还占单实例锁 | 只 `DestroyWindow` 没退出 Tk 主循环 | `WM_SHUTDOWN` 里删图标 + 销毁窗口 + 排队回主线程 `quit_app()` |
| 面板被切掉一半 / 露到屏幕外 | `resizable(False,False)` 的 Toplevel 内容变多会自己长大但**不挪位置**；且 pack 后同帧读 `reqwidth` 拿到的是旧值 | 绑 `<Configure>` 越界就拉回工作区（`panel._on_configure`） |
| LiteMonitor 里三个监控项一起变 ❌ | 插件的 `extract` 里有一项取到 `❓`（JSON 字段不存在）→ 执行器**整步 throw** | 桥必须**永远输出全部已知设备的字段**（没插=空串），见 `httpserve.build_payload` |
| LiteMonitor 里显示 `%{{jd_mark}}` 原文 | 注册监控项时把 `output.Unit` 的**原始字符串**写进去，只有刷新成功才被覆盖 | 别在 `unit` 里放模板；充电标记放 `format_val` |
| 改了设备名，充电 ⚡ 不见了 | `UserLabel` 优先级高于运行时标签 | 把标记放在 `format_val`（数值侧），不要放 `label` |

---

## 7. 故障树（按顺序排除）

```
--once 报"未发现接收器"
├─ --status 里也没有该 VID:PID
│   ├─ USB 枚举里也没有 → 设备没插好 / 走的蓝牙（不是 2.4G dongle） / 需要先配对
│   └─ USB 有、HID 没有 → 驱动没起来（设备管理器看黄色感叹号）
└─ --status 里有
    ├─ 接口 can_write=False → 只读打开，去找厂商通道（usage_page ≥ 0xFF00）
    └─ 能写但无回包 → 设备休眠（动一下）/ 命令字不对（重扫）/ 需要先发握手

--once 有值但明显不对（如恒为 100 或 0）
├─ 该字节可能是量程/档位而不是电量 → 用"充电时递增"重新验证
└─ 偏移差 1 字节 → 检查是否把 Report ID 算进了偏移

托盘图标不出现
├─ Win11 默认把新图标收进溢出区（^）→ 让用户拖出来，程序改不了
└─ --debug 看 NIM_ADD 是否失败

LiteMonitor 显示 ❌ / ⛔ / ❓
├─ ❌ = 整步异常 → 先看 --serve 是否在跑；再查 extract 字段是否都存在
├─ ⛔ = 值为空 → 该设备没插或休眠；重跑 --make-plugin 可把它从列表里去掉
└─ ❓ = JSON 路径取不到 → 字段名拼错，或桥的版本太旧（重启 --serve）
```

---

## 8. 交付给用户时说什么

改完之后，用这个结构汇报（不要只说"完成了"）：

1. **结论**：读到了什么（设备 → 百分比 → 来源字节），充电位是怎么定的。
2. **证据**：贴 2~3 条真实报文（充电中 / 拔线后），说明哪一位变了。
3. **改动**：动了哪几个文件、加了什么字段。
4. **验收**：贴 §5 里 1/2/4/6 四条的真实输出。
5. **未解决项**：明确说出"哪些没验证"（比如设备当时不在场，只做了自检回放）。

---

## 9. Definition of Done

- [ ] `--selftest` 全过，且**新设备有回放用例**
- [ ] `--once` 能读到新设备，数值经"充电递增"验证
- [ ] 充电位经过拔插对比确认（或明确写"未标定"）
- [ ] `PROTOCOLS.md` 已加该设备（含真实报文）
- [ ] 托盘启停 3 次干净、无日志、空闲 CPU < 0.5s/75s
- [ ] 若接 LiteMonitor：`--make-plugin` 已重跑，插件里没有用不上的 ⛔
