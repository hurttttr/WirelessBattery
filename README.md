# WirelessBattery · 无线外设电量

Windows 上的无线鼠标 / 键盘 / 耳机 / 手柄电量小工具。托盘常驻，**只显示电量数字**，不装驱动、不装依赖、不写日志。

```text
┌──────────────────────────────┐
│  🖱  雷柏 Rapoo 无线鼠标      │
│      ████████████████░░  96% │
├──────────────────────────────┤
│  ⌨  迈从 X75 V2              │
│      ██████████████████ 100% │
└──────────────────────────────┘
```

> 鼠标电量靠厂商私有 HID 报文读出来，没有统一标准。本项目里的每一台设备
> 都是**实测抓包**逆出来的，协议细节全部公开在 [`PROTOCOLS.md`](PROTOCOLS.md)，
> 别的工程可以直接抄走解析函数（只依赖 `bytes`，不依赖本项目）。

---

## 为什么不用现成的

市面上能查电量的方案基本是三条路，各有各的坑：

| 方案 | 问题 |
|---|---|
| 罗技 G HUB / 雷云 / 各家驱动 | 只管自家设备，装完几百 MB，还常驻一堆服务 |
| `hidapi` / `pywin32` / `pystray` + Pillow | 能跑，但要 `pip install` 一堆轮子；打包后还挑 Python 版本 |
| 读系统 `HID` 的电池 usage（`0x06` 页） | 只有部分蓝牙设备实现了标准电量页，2.4G dongle 几乎全都没有 |

这个项目走的是第四条：**用 `ctypes` 直接调 SetupAPI + HID，零第三方依赖**。
代价是得自己解协议，好处是扔进任何一台装了 Python 的 Windows 上都能直接跑。

---

## 特性

- **零第三方依赖** —— 只用标准库 + `ctypes`，GUI 用 `tkinter`。不需要 `pip install` 任何东西。
- **三种形态** —— 一次读数（CLI）/ 托盘常驻 / 完整调试版，同一个入口切换。
- **托盘图标就是电量** —— 图标尺寸跟随系统 `SM_CXSMICON`（100% 缩放是 16px），自带点阵字体 + 超采样抗锯齿，
  16px 下数字依然认得出；多台设备各占一个槽位，相邻图标用圆角形状错开（`0.22` / `0.42`）方便区分。
- **低占用** —— `SetTimer` 驱动刷新（30 秒 ~ 30 分钟可调），不做忙等；空闲 75 秒实测 CPU 约 0.03 秒。
- **不写日志** —— 任何情况下不在项目目录产生 `.log`，调试信息只在 `--debug` 时打到控制台。
- **干净退出** —— 退出即销毁图标、杀定时器、释放单实例锁，不留僵尸进程。
- **开机自启不弹黑窗** —— 走「启动」文件夹的 `.lnk`，目标是 `pythonw.exe`。
- **桌面用链接而非复制** —— 目录 `symlink` → `junction` 兜底，文件走硬链接，改代码即生效。
- **可接 LiteMonitor** —— 内置 `127.0.0.1` 本地 HTTP 桥，把读数喂给只支持 HTTP 的宿主。

---

## 支持的设备

| 设备 | VID:PID | 方案 | 交互 | 报文 | 充电位 |
|---|---|---|---|---|---|
| 雷柏 Rapoo VT 系列 | `24AE:1412` | Rapoo 私有 | **主动推送** | Report `0x07`，13B，约 3s 一帧 | ✅ `frame[7]`：`0x02` 充电 / `0x01` 未充 |
| 迈从 MCHOSE X75 V2 | `3554:FA09` | CX 私有 | 请求-应答 | Report `0x13`，20B | ✅ 第 7 字节 `& 0x10` |
| 京东京造 无线鼠标 | `362D:D107` | Keychron Link-KM | 请求-应答 | Report `0xB3` 写 / `0xB4` 读，64B | ✅ 同字节 `bit7` |
| ATK / VXE 无线鼠标 | `373B:1216` | Nordic | 请求-应答 | Report ID `0x08`，64B | — |
| DARKRIM SHADOW 耳机 | `3654:6901` | 杰理 JL_RCSP | 请求-应答（TLV） | 首字节 `0x00`，65B | — |
| 飞智 Vader 4 Pro | `04B4:2412`（DInput） | Flydigi 经典 | 请求-应答 | Report `5` 写 / `4` 读，32B | ⚠️ 未验证 |

几点说明：

- **只有插着的设备才出现在面板里**。一台都没插时退回显示默认的两台，免得面板空着让人以为程序坏了。
- 手柄这类带模式开关的设备**换模式就换 VID:PID**：XInput `045E:028E` 只有 15 字节摇杆报文、发不出指令，
  必须切到 DInput `04B4:2412` 才有厂商通道。两张卡都会出现，XInput 那张负责提示怎么切。
- 飞智手柄的**充电位在实测中没验证通过**（指示灯红灯充电时该位仍为 `0`）。代码只在置位时报「充电中」，
  否则给「未知」，**不谎报未充电**。

---

## 快速开始

### 环境要求

- Windows 10 / 11，**64 位 Python 3.9+**（多处按 x64 结构体布局写死，32 位跑不了）
- 需要 `tkinter`（官方安装包自带；某些 IDE 内置的 Python 是精简版，没有它）

```bash
python -c "import sys; print(sys.version); print('64bit' if sys.maxsize > 2**32 else '32bit')"
python -c "import tkinter; print('tk ok', tkinter.TkVersion)"
```

### 跑起来

```bash
git clone https://github.com/hurttttr/WirelessBattery.git
cd WirelessBattery

python my_devices.py --selftest    # 协议自检，不碰硬件
python my_devices.py --once        # 读一次真实设备
```

`--once` 的典型输出：

```text
雷柏 Rapoo 无线鼠标     (24AE:1412)   96%   设备推送 13B 报文 · 第 8 字节
迈从 X75 V2             (3554:FA09)  100%   0x05 查询 · 20B 帧第 6 字节
京东京造 无线鼠标       (362D:D107)   31% 充电中   0x06 查询 · 64B 帧第 20 字节
```

退出码：全部成功 `0`，有设备读取失败 `2`。没插设备时打印「未发现接收器」，属正常。

---

## 命令行

```text
python my_devices.py [选项]
```

| 选项 | 说明 |
|---|---|
| `--once` | 读一次并打印电量，不进 GUI |
| `--tray` / 无参数 | 托盘常驻（默认） |
| `--panel` | 托盘常驻，并立即显示简洁面板 |
| `--no-tray` | 不建托盘图标（配合 `--panel`） |
| `--debug-ui` | 完整调试版：列设备、转储报文、探测、标定 |
| `--interval 秒` | 刷新间隔，30 ~ 1800（默认 60） |
| `--debug` | 调试信息打到控制台 |
| `--install` / `--uninstall` | 创建 / 移除开机自启 + 桌面链接 |
| `--make-links` | 只创建桌面链接 |
| `--status` | 设备 / 自启 / 链接 / 数据目录 / 日志残留，一次看全 |
| `--selftest` | 不依赖硬件的协议自检 |
| `--capture VID:PID --seconds N` | 抓该设备的输入报文，自动标出变化字节 |
| `--watch VID:PID` | 监听推送型设备的电量（如 `24AE:1412`） |
| `--serve` / `--only-serve` | 启动本地 HTTP 桥（可与 `--tray` 同进程） |
| `--port N` / `--serve-ttl N` | 桥端口（默认 `18763`）/ 读数缓存（默认 10 秒） |
| `--make-plugin` | 按当前插着的设备生成 LiteMonitor 插件 JSON |

### 三种形态

| 形态 | 命令 | 适用 |
|---|---|---|
| 简洁面板 | `--panel` | 想看一眼就关掉，卡片式，圆角进度条 |
| 托盘常驻 | `--tray` | 日常使用：图标本身就是电量数字 |
| 完整调试版 | `--debug-ui` | 适配新设备、排查「为什么读不到」 |

**托盘交互**：左键点图标 = 立刻刷新；右键 / 双击 = 打开简洁面板。

### 开机自启

```bash
python my_devices.py --install          # 自启 + 桌面链接
python my_devices.py --install --serve  # 顺便把 HTTP 桥写进启动项
python my_devices.py --status           # 核对
python my_devices.py --uninstall        # 撤销
```

自启走「启动」文件夹的 `.lnk`（用 `ctypes` 调 COM `IShellLinkW` 创建，不依赖 `pywin32`），
目标是 `pythonw.exe`，所以**不会弹黑窗**。`--status` 会显示它实际选中的解释器路径——
如果机器上装了多个 Python，这一步能立刻看出用的是哪个。

---

## 接 LiteMonitor

LiteMonitor 的插件系统是**纯声明式 JSON**，整个 `src/Plugins/` 目录没有任何 `Process.Start`，
只有 `HttpClient`。而电量必须靠 ctypes 发厂商指令才能拿到，JSON 表达不了。

所以接法是：**本工具当数据源，开一个本地 HTTP 桥，插件只负责取数和显示。**

```
┌──────────────┐   HID 厂商指令   ┌─────────────────────┐
│ 鼠标/键盘/耳机│ ◄──────────────► │ my_devices.py       │
└──────────────┘   （ctypes）     │  --tray --serve     │
                                  │  ↓ 127.0.0.1:18763  │
┌──────────────┐   HTTP GET       │  /battery → JSON    │
│  LiteMonitor │ ◄────────────────┤                     │
│  JSON 插件   │                  └─────────────────────┘
└──────────────┘
```

```bash
python my_devices.py --tray --serve   # 托盘和桥必须在同一个进程
python my_devices.py --make-plugin    # 按现场设备生成插件
```

> ⚠️ **同一时刻只能有一个进程读 HID**。两个进程读同一批接口会互相抢报文，
> 表现为「数据忽有忽无」。所以桥和托盘必须在**同一个进程**里。

细节（JSON 字段、颜色阈值、充电显示、排障）见 [`LITEMONITOR.md`](LITEMONITOR.md)。

---

## 目录结构

```text
WirelessBattery/
├── my_devices.py          CLI 入口、参数分发、文字排版
├── wbc/
│   ├── __init__.py        版本号与 APP_NAME / APP_ID
│   ├── app.py             进程装配（单实例、线程模型、退出）
│   ├── proto.py           协议层：PROFILES 清单 + 各厂商读取器 + read_all + 自检
│   ├── hid.py             HID 枚举、HidController（写）、OverlappedReader（异步读）
│   ├── win32.py           ctypes 封装：SetupAPI / User32 / GDI / Shell / COM
│   ├── tray.py            托盘宿主窗口、SetTimer、Shell_NotifyIcon
│   ├── icons.py           纯 Python 光栅化点阵数字 → HICON
│   ├── panel.py           简洁面板（卡片 + 圆角进度条）
│   ├── debugui.py         完整调试版
│   ├── httpserve.py       本地 HTTP 桥（127.0.0.1:18763/battery）
│   ├── lmplugin.py        按现场设备生成 LiteMonitor 插件 JSON
│   ├── autostart.py       启动文件夹 .lnk（COM IShellLinkW）
│   ├── links.py           桌面链接（symlink → junction → 硬链接）
│   ├── settings.py        settings.json / calibration.json
│   ├── theme.py           DPI 缩放、字体、配色
│   └── logx.py            调试输出开关
├── litemonitor/           LiteMonitor 插件样例
├── README.md              门面与使用手册（本文件）
├── PROTOCOLS.md           六台设备的协议汇编 + 逆向方法论
├── AGENT_GUIDE.md         给 AI Agent 的部署 / 适配手册
├── LITEMONITOR.md         LiteMonitor 桥与插件接入手册
└── LICENSE                MIT
```

运行时数据（`settings.json` / `calibration.json`）写在
`%LOCALAPPDATA%\WirelessBattery\`，**不进项目目录，也不会提交到仓库**。

---

## 适配自己的设备

核心只有两步：**找到厂商通道**，**定位电量字节**。

1. 先跑 `--debug-ui` 看设备在不在；不在就用 `python -c` 调 `wbc.hid.enumerate_interfaces(full=True)` 列出所有接口，
   找 `usage_page` 落在 `0xFF00~0xFFFF`（或 `0x008C` / `0xFF60`）的那个——那就是厂商通道。
2. 抓包：`--capture VID:PID --seconds 120`，期间正常使用设备。输出会自动标出 `<< 变化` 的字节。
3. 抓不到 → 是「查询型」，改用 `--ping` 或调试版里的命令字扫描（**参数全填 0**，只变命令字）。
4. **定位电量字节的唯一可靠方法**：让设备进入充电，每 20 秒采样一次，持续 5~10 分钟——
   **每 20 秒递增 1 的那个字节就是电量**。别看到 `0x64`(100) 就认定是电量，它可能是音量、亮度或档位。
5. 在 `wbc/proto.py` 的 `PROFILES` 里加一条，接上对应的读取器，然后跑 `--selftest` 和 `--once` 自证。

完整流程、九步检查表、踩过的坑（路径偏移、异步读失聪、`err=87`、系统独占集合……）见
[`AGENT_GUIDE.md`](AGENT_GUIDE.md)；协议查表见 [`PROTOCOLS.md`](PROTOCOLS.md)。

---

## 设计约束

这些是刻意为之的取舍，不是没做完：

| 约束 | 原因 |
|---|---|
| 零第三方依赖 | 目标机器可能是临时环境，装不了轮子；也让代码可以被直接拷贝走 |
| 不写日志文件 | 工具类程序不该在用户目录里留垃圾；出问题用 `--debug` 现场看 |
| 开机自启走 `.lnk` | 注册表 `Run` 键改一个参数要重写整条命令；`.lnk` 直观、可被用户手动检查 |
| 桌面用链接 | 改代码即生效，不用反复覆盖；`symlink` 失败自动降级 `junction` / 硬链接 |
| 充电位不确定就报「未知」 | 宁可少一条信息，也不报错误的「未充电」 |
| 托盘和桥同进程 | Windows 的 HID 句柄不共享，两个进程读同一接口会互相抢报文 |

---

## 常见问题

**Q：`--once` 说「未发现接收器」，但设备确实插着？**
A：说明这台设备的 VID:PID 还不在 `PROFILES` 里。用 `--debug-ui` 或上面的 enumerate 一行命令看看它是谁，
然后按[适配自己的设备](#适配自己的设备)加一条。

**Q：读出来的电量一闪一闪，有时有有时没有？**
A：几乎一定是**两个进程在抢同一个接口**。检查是不是同时开了 `--serve`、`--capture` 和托盘，
或者上一轮进程没退干净（`--status` 会提示）。

**Q：托盘图标是空白 / 数字看不清？**
A：图标只有 `SM_CXSMICON` 那么大（100% 缩放时是 16px），笔画本来就细。两个方向：把显示缩放调到 150% 以上，
系统会给图标更多像素，数字明显更清楚；图标「根本找不到」则多半是 Win11 把它收进了溢出区（`^`），
拖出来即可，程序改不了这个行为。如果 `NIM_ADD` 本身失败，加 `--debug` 能看到错误码。

**Q：改了 `--serve` 的代码为什么不生效？**
A：Python 不热加载，重启进程。

---

## 文档地图

仓库根目录一共四份 Markdown，**读者不一样，不用通读**。先认准自己是谁：

| 文档 | 写给谁 | 用途 | 什么时候读 |
|---|---|---|---|
| [`README.md`](README.md) | 使用者 / 刚接触项目的人 | **门面与使用手册**：这是什么、支持哪些设备、怎么跑、有哪些命令 | 第一次打开这个仓库 |
| [`PROTOCOLS.md`](PROTOCOLS.md) | 想抄协议的人、要逆自己设备的人 | **协议查表**：六台设备的接口条件、帧格式、真实报文、可复制的解析代码 | 要在别的工程里读这些设备，或自己动手抓一台新的 |
| [`AGENT_GUIDE.md`](AGENT_GUIDE.md) | AI Agent | **操作手册**：硬约束、环境自检、部署顺序、九步适配法、验收清单、坑表、故障树 | Agent 要独立完成"部署跑通"或"适配新设备" |
| [`LITEMONITOR.md`](LITEMONITOR.md) | 接 LiteMonitor 的人 | **接入手册**：为什么只能走本地 HTTP 桥、JSON 字段、颜色阈值、充电显示 | 想把电量显示在任务栏上 |

### 各文档讲什么

**`README.md`（本文件）— 门面**

为什么不用现成方案（驱动 / hidapi / 标准 HID 电池页三条路的坑）· 九条特性 · 设备速查表 ·
三种形态与托盘交互 · 完整 CLI 选项 · 开机自启与桌面链接 · 五步适配速览 · 设计约束 · FAQ

**`PROTOCOLS.md`（12 节）— 协议查表**

- **§0 速查表**：六台设备的 VID:PID、方案、交互模型、通道、报文、电量位、充电位一屏看完
- **§1 通用坑**：六条踩过的坑 + 一条"同一设备可能有多套 VID:PID"的提醒。
  最要命的是「同一句柄上阻塞读 + 并发写会直接杀掉进程」和「异步读超时后不重新挂起就永久失聪」
- **§2–§7 一台设备一节**：接口定位条件 → 帧格式 → **真实报文原文** → 解析代码 → 命令地图 → 边界情况。
  解析函数只依赖 `bytes`，可以整段抄到别的工程
- **§8 兜底链**（换设备时按顺序试）、**§9 逆向方法论**、**§10 x64 HID 结构体两个陷阱**、**§11 参考实现**

**`AGENT_GUIDE.md`（10 节）— 操作手册**

- **§0 硬约束**（违反即失败）、**§1 环境自检**（必须 64 位 Python + 有 tkinter）
- **§2 项目结构表**（20 个文件各改哪里，含文档）+ `PROFILES` 的数据结构约定
- **§3 部署三步**：跑起来 → 装常驻 → 接 LiteMonitor，每步带期望输出
- **§4 适配新设备九步**：问三件事 → 枚举接口 → 判交互模型 → 被动抓包 → 主动探测 →
  定位电量字节 → 定位充电位 → 落地 5 处代码 → 收尾
- **§5 验收清单**（6 条，含托盘启停自测脚本）、**§6 已知坑**（14 条，症状 → 原因 → 规避）、
  **§7 故障树**、**§8 汇报模板**、**§9 Definition of Done**

**`LITEMONITOR.md`（8 节）— 接入手册**

- **§1** 为什么做不成纯 JSON 插件（LiteMonitor 源码里没有 `Process.Start`，只有 `HttpClient`）
- **§2 三步接入**：起本地接口 → 生成并安装插件 → 加监控项
- **§3** 接口 JSON 的每个字段、**§4** 充电时能显示什么 / 不能显示什么、**§5** 为什么要缓存
- **§6** 换成自己的设备、**§7** FAQ、**§8** 只读性说明（这个桥不会写任何东西）

> 三份技术文档分工明确：`AGENT_GUIDE.md` 管流程、`PROTOCOLS.md` 查协议、`LITEMONITOR.md` 管接入。
> 只想用、不想改代码的话，看完本文件就够了。

---

## 许可

MIT。协议部分（`PROTOCOLS.md`）欢迎直接取用，注明出处即可。
