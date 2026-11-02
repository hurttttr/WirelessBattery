# WirelessBattery · 无线外设电量

Windows 上的无线鼠标 / 键盘 / 耳机 / 手柄电量小工具。托盘常驻，**图标本身就是电量数字**。

```text
┌──────────────────────────────┐
│  🖱  雷柏 Rapoo 无线鼠标      │
│      ████████████░░░░░░  96% │
├──────────────────────────────┤
│  ⌨  迈从 X75 V2              │
│      ██████████████████ 100% │
└──────────────────────────────┘
```

2.4G 外设没有统一的电量标准，厂商各发各的私有报文。这里的每一台设备都是**实测抓包**逆出来的，
协议、真实报文、解析代码全部公开在 [`PROTOCOLS.md`](PROTOCOLS.md)，可以整段抄走。

---

## 特性

- **零第三方依赖** —— 标准库 + `ctypes`，GUI 用 `tkinter`。不需要 `pip install` 任何东西
- **三种形态** —— 一次性读数 / 托盘常驻 / 完整调试版，同一个入口切换
- **图标即电量** —— 纯 Python 光栅化点阵数字，尺寸跟随系统 `SM_CXSMICON`
- **低占用** —— `SetTimer` 驱动（30 秒 ~ 30 分钟），空闲 75 秒实测约 0.03 秒 CPU
- **不写日志** —— 项目目录永不产生 `.log`，调试信息只在 `--debug` 时打到控制台
- **干净退出** —— 销毁图标、杀定时器、释放单实例锁，不留僵尸进程
- **自启不弹黑窗** —— 启动文件夹 `.lnk` 指向 `pythonw.exe`；桌面用链接而非复制
- **可接 LiteMonitor** —— 内置只读的本地 HTTP 桥

---

## 支持的设备

| 设备 | VID:PID | 方案 | 交互 | 报文 | 充电位 |
|---|---|---|---|---|---|
| 雷柏 Rapoo VT 系列 | `24AE:1412` | Rapoo 私有 | **主动推送** | Report `0x07`，13B，约 3s 一帧 | ✅ `frame[7]`：`0x02` 充电 / `0x01` 未充 |
| 迈从 MCHOSE X75 V2 | `3554:FA09` | CX 私有 | 请求-应答 | Report `0x13`，20B | ✅ 第 7 字节 `& 0x10` |
| 京东京造 无线鼠标 | `362D:D107` | Keychron Link-KM | 请求-应答 | Report `0xB3` 写 / `0xB4` 读，64B | ✅ 同字节 `bit7` |
| ATK / VXE 无线鼠标 | `373B:1216` | Nordic | 请求-应答 | Report ID `0x08`，64B | — |
| DARKRIM SHADOW 耳机 | `3654:6901` | 杰理 JL_RCSP | 请求-应答（TLV） | 首字节 `0x00`，65B | — |
| 飞智 Vader 4 Pro | `04B4:2412`（DInput） | Flydigi 经典 | 请求-应答 | Report `5` 写 / `4` 读，32B | ⚠️ 见下 |

- **只有插着的设备才出现在面板里**；一台都没插时退回显示默认的两台，免得面板空着像坏了
- **换模式就换 VID:PID**：手柄在 XInput（`045E:028E`）下没有厂商通道，必须切 DInput（`FN + A` 三秒）
- **飞智手柄读不到充电位**：固件在 2.4G 下不下发，插充电头充电和拔线两种状态的回应**逐字节相同**。
  代码只认"充电位置位"和"有线接电脑"两种情况，其余一律不显示——不猜。细节见 `PROTOCOLS.md` §7

---

## 快速开始

需要 Windows 10/11 + **64 位 Python 3.9+** 且带 `tkinter`（有些 IDE 内置的 Python 是精简版，没有它）：

```bash
python -c "import sys, tkinter; print(sys.version, 'tk', tkinter.TkVersion)"
```

```bash
git clone https://github.com/hurttttr/WirelessBattery.git
cd WirelessBattery
python my_devices.py --selftest    # 协议自检，不碰硬件
python my_devices.py --once        # 读一次真实设备
```

```text
雷柏 Rapoo 无线鼠标     (24AE:1412)   96%   设备推送 13B 报文 · 第 8 字节
迈从 X75 V2             (3554:FA09)  100%   0x05 查询 · 20B 帧第 6 字节
```

退出码：全部成功 `0`，有设备读不到 `2`。没插设备时打印"未发现接收器"，属正常。

---

## 常用命令

| 场景 | 命令 |
|---|---|
| 读一次，不进 GUI | `--once` |
| 托盘常驻（默认） | `--tray` |
| 托盘常驻 + 立刻弹面板 | `--panel` |
| 完整调试版（列设备 / 转储 / 探测 / 标定） | `--debug-ui` |
| 改刷新间隔 | `--interval 秒`（30 ~ 1800，默认 60） |
| 开机自启 + 桌面链接 | `--install` / `--uninstall` |
| 检查设备 / 自启 / 链接 / 日志残留 | `--status` |
| 不依赖硬件的协议自检 | `--selftest` |
| 抓某个设备的输入报文（自动标出变化字节） | `--capture VID:PID --seconds N` |
| 盯住推送型设备的电量 | `--watch VID:PID` |
| 本地 HTTP 桥 | `--serve`（默认 `:18763`，缓存 10 秒） |
| 生成 LiteMonitor 插件 | `--make-plugin` |

**托盘交互**：左键 = 立刻刷新，右键 / 双击 = 打开面板。
`--debug` 打调试信息，`--exit-after 秒` 用来自测退出是否干净。

---

## 接 LiteMonitor

LiteMonitor 的插件系统只能发 HTTP、不能执行本地程序，而电量必须靠 `ctypes` 读 HID。
所以本工具开一个**只读**的本地桥（只绑 `127.0.0.1`，只接受 GET），插件只负责取数和显示：

```text
HID ← ctypes → my_devices.py → 127.0.0.1:18763/battery → LiteMonitor 插件
```

```bash
python my_devices.py --tray --serve   # 托盘和桥必须在同一个进程
python my_devices.py --make-plugin    # 按现场插着的设备生成插件
```

然后在 LiteMonitor 里「设置 → 插件 → 重载插件」。

> ⚠️ **同一时刻只能有一个进程读 HID**。两个进程读同一批接口会互相抢报文，表现为"数据忽有忽无"。

字段说明、颜色阈值、排障见 [`LITEMONITOR.md`](LITEMONITOR.md)。

---

## 让 AI 帮你做

[`AGENT_GUIDE.md`](AGENT_GUIDE.md) 是**专门写给 Agent 的**：硬约束、环境自检、部署顺序、
适配九步法、验收清单、坑表、故障树。把仓库丢给 Claude Code / Codex / Cursor / WorkBuddy
这类编码 Agent，让它先读这份文件再动手，比直接说"帮我看看这个项目"靠谱得多。

### ① 部署到一台新机器

```text
读 AGENT_GUIDE.md，按 §1 和 §3 把这个项目在 Windows 上跑起来。
先做环境自检（必须 64 位 Python 且有 tkinter），再跑 --selftest 和 --once，
把真实输出贴给我。我没有明确要求之前，不要创建开机自启和桌面链接。
```

### ② 适配你新买的外设

先告诉它三件事，缺一个它就只能瞎猜：

1. 设备**型号**（有些 dongle 固件会自报名字，能省一半功夫）
2. 是否支持**有线模式**（有线时通常枚举成另一个更好说话的 HID 设备）
3. 有没有装过**官方驱动 / 网页配置工具**（前端 JS 里往往直接写着命令字）

```text
我的 <型号> 已经插上，VID:PID 是 <XXXX:YYYY>。按 AGENT_GUIDE.md §4 帮我逆出电量协议：
枚举接口找厂商通道 → 被动抓包 → 定位电量字节 → 定位充电位 → 落到 wbc/proto.py 的
PROFILES；跑 --selftest 和 --once 自证，并更新 PROTOCOLS.md。
拿不到就说拿不到，不要猜字节。
```

其中**定位电量字节**是最容易出错的一步：让设备进入充电，每 20 秒采样一次、持续 5~10 分钟，
每 20 秒递增 1 的那个字节才是电量——别看到 `0x64`（100）就认定是电量，它可能是音量、亮度或档位。

### ③ 排查"读不到"

```text
python my_devices.py --once 报 <现象>。按 AGENT_GUIDE.md §7 的故障树逐层排查，
先用 --status 和 --debug-ui 把证据收集全，再定位原因；改完跑 §5 的验收清单。
```

### 它最容易犯的三个错

| 坑 | 后果 | 对策在哪 |
|---|---|---|
| 想 `pip install hidapi / pywin32 / Pillow` | 直接破坏"零依赖"这个前提 | §0 硬约束 |
| 看到 `0x64` 就当成 100% | 把音量或档位读成电量 | §4.6 充电递增法 |
| 同时开 `--capture` 和 `--serve` | 两个进程抢同一个 HID 接口，数据忽有忽无 | §6 坑表 |

验收标准在 `AGENT_GUIDE.md` §9 Definition of Done —— 让它自己跑一遍再回话。

---

## 目录结构

```text
WirelessBattery/
├── my_devices.py       CLI 入口：参数分发 + 三种形态
├── wbc/
│   ├── proto.py        协议层：PROFILES 清单 + 各厂商读取器  ← 适配新设备主要改这里
│   ├── hid.py          HID 枚举、写通道、异步读（OVERLAPPED + CancelIoEx）
│   ├── win32.py        ctypes 封装：SetupAPI / User32 / GDI / Shell / COM
│   ├── tray.py         托盘宿主、SetTimer、Shell_NotifyIcon
│   ├── panel.py        简洁面板（debugui.py 是完整调试版）
│   └── …               桥 / 插件 / 图标 / 主题 / 自启 / 链接 / 配置 / 装配 / 调试开关
├── litemonitor/        插件样例
├── PROTOCOLS.md  AGENT_GUIDE.md  LITEMONITOR.md
└── LICENSE             MIT
```

运行时数据写在 `%LOCALAPPDATA%\WirelessBattery\`，不进项目目录，也不会提交到仓库。

---

## 文档

| 文档 | 写给谁 | 内容 |
|---|---|---|
| **README.md**（本文件） | 使用者 | 是什么、支持哪些设备、怎么跑、怎么让 AI 帮忙 |
| [`PROTOCOLS.md`](PROTOCOLS.md) | 想抄协议的人 | 六台设备的接口条件、帧格式、真实报文、解析代码、通用坑 |
| [`AGENT_GUIDE.md`](AGENT_GUIDE.md) | AI Agent | 硬约束、部署流程、适配九步法、验收清单、坑表、故障树 |
| [`LITEMONITOR.md`](LITEMONITOR.md) | 接任务栏的人 | 桥的字段、颜色阈值、充电显示、排障 |

只想用、不想改代码的话，看完本文件就够了。

---

## 几个常见问题

**读不到，但设备确实插着？** 说明这台设备的 VID:PID 还不在 `PROFILES` 里。跑 `--debug-ui`
或 `--status` 看它是谁，然后按上面的「适配你新买的外设」交给 AI。

**数据忽有忽无？** 几乎一定是两个进程在读同一个 HID 接口——检查是不是同时开了 `--serve`、
`--capture` 和托盘，或者上一轮进程没退干净。

**改了代码不生效？** Python 不热加载，重启进程。

---

## 许可

MIT。协议部分（`PROTOCOLS.md`）欢迎直接取用，注明出处即可。
