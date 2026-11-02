# 无线外设电量读取协议汇编

> 🤖 **如果你是 AI Agent**：先读 `AGENT_GUIDE.md`（部署 + 适配新设备的操作手册），
> 本文用作协议查表。

几个 2.4G 无线外设的电量读取协议，全部来自**实测抓包**（不是猜测）。
每个设备给：接口定位条件、帧格式、真实报文样本、可复制的解析代码、边界情况。

面向"别的工程直接取用"：所有解析函数只依赖 `bytes`，不依赖本项目代码。

---

## 0. 速查表

| 设备 | VID:PID | 厂商/方案 | 交互方式 | 通道（usage） | 报文 | 电量位置 | 充电位 |
|---|---|---|---|---|---|---|---|
| ATK / VXE 无线鼠标 | `373B:1216` | Nordic | 请求-应答 | Input/Output 64B，`FF05` | Report ID `0x08`，64B | `resp[7]` | — |
| DARKRIM SHADOW 耳机 | `3654:6901` | 杰理 JL_RCSP | 请求-应答 | Output 65B | 首字节 `0x00`，65B | TLV `0x01` 首字节 | — |
| 雷柏 Rapoo VT 系列 | `24AE:1412` | Rapoo 私有 | **主动推送** | `FF00/0002` | Report `0x07`，13B，约 3s 一帧 | `frame[8]` | `frame[7]`：`0x02` 充电 / `0x01` 未充 |
| 迈从 MCHOSE X75 V2 | `3554:FA09` | CX 私有 | 请求-应答 | `FF02/0002` | Report `0x13`，20B | 数据帧第 6 字节 | 第 7 字节 `& 0x10` |
| 京东京造 无线鼠标 | `362D:D107` | Keychron Link-KM | 请求-应答 | `FFC1/0001` | Report `0xB3` 写 / `0xB4` 读，64B | 回应第 20 字节低 7 位 | 同字节 bit7：`1` 充电 / `0` 未充 |
| 飞智 Vader 4 Pro | `04B4:2412`（DInput） | Flydigi 经典协议 | 请求-应答 | `FFA0/0001` | Report `5` 写 / `4` 读，32B | 回应第 11 字节低 4 位（**0..5 档 ×20%**） | **未验证**：充电时该位仍是 0，只在置位时才报"充电中" |

判读优先级：**主动推送**最好（不用发指令，设备自己上报）；**请求-应答**次之（每次要发一条命令）；
有 **Feature Report** 的最省事（`HidD_GetFeature` 一次就拿到），但这几台都没有可用的 Feature。

---

## 1. 通用坑（踩过的，务必先看）

### 1.1 绝对不要在同一句柄上"阻塞 ReadFile + 并发 WriteFile"

实测会**直接杀掉进程**（连 Python 异常栈都没有）。正确做法：

- 写句柄：普通 `CreateFileW` + `WriteFile`
- 读句柄：**独立打开一次**，带 `FILE_FLAG_OVERLAPPED`，用事件 + 超时 + `CancelIoEx`

两个句柄指向同一个接口路径是允许的，Windows 不介意。

### 1.2 异步读超时后会"失聪"

`ReadFile` 以 `OVERLAPPED` 方式挂起后，如果等超时了，**必须重新发起一次 `ReadFile`**，
否则这个句柄从此再也收不到数据。轮询循环里只在"收到数据"时重新挂起，是经典错误——
表现为"用户明明在动设备，却一帧都收不到"。

```python
def poll(self, ms):
    """内部保证：无论有没有数据，退出时读请求都重新挂上了。"""
    data = self._wait_once(ms)
    self._begin_read()          # ← 关键，别漏
    return data
```

### 1.3 系统独占的顶层集合打不开

鼠标 / 键盘 / 笔 / 触摸屏这类顶层集合，`CreateFileW` 会被拒（`err=5`），
只有 `dwDesiredAccess = 0` 能打开——能读描述符，不能发指令，也收不到输入报文。

所以：**要发指令就得找厂商自定义的集合**（usage page `0xFF00`~`0xFFFF` 或 `0x008C` 这类）。

### 1.4 两个进程同时读同一个接口会互相抢报文

A 读到第 1、3、5 帧，B 读到第 2、4、6 帧。排查"数据忽有忽无"时先确认没有别的句柄开着
（包括自己上一轮的进程）。**抓包和探测不要并行**。

### 1.5 `WriteFile` 的长度必须等于 `OutputReportByteLength`

短一个字节就是 `err=87`（参数错误）。且**首字节是 Report ID**，HIDAPI 那种"自动补 ID"
的封装在这里不适用。

### 1.6 报文长度 ≠ 你想要的长度

描述符里声明的 `InputReportByteLength` 已经**含了 Report ID 那一个字节**。
比如"13 字节报文"指的是总共 13 字节，其中 `data[0]` 是 Report ID。

---

### 同一台设备可能有多套 VID:PID

手柄这类带"模式开关"的设备会**换模式就换 VID:PID**（飞智：XInput `045E:028E` /
DInput `04B4:2412` / Switch `057E:2000`）。所以：

- 判断"设备在不在线"不能只认一个 VID:PID —— 本工具用 `PROFILES` 里的 `also` 字段。
- **厂商通道只在特定模式下才存在**。看到 `out=0 feat=0` 的接口就别费劲了，
  命令根本发不出去，先解决模式问题。

## 2. ATK / VXE 无线鼠标（`373B:1216`，Nordic 方案）

**接口**：usage page `0xFF05`，Input 64 字节 / Output 64 字节，**没有 Feature Report**。

**请求**（64 字节，后面补 0）：

```
08 7D 72 02 00 01 07 01 00 00 ... 00
│  │  │  │  │  │  │  └─ 固定
│  │  │  │  │  │  └──── 0x07 = 要电量
│  │  │  │  │  └─────── 0x01
│  │  │  │  └────────── 0x00
│  │  │  └───────────── 0x02
│  │  └──────────────── 0x72 命令字
│  └─────────────────── 0x7D
└────────────────────── Report ID 0x08
```

**回复校验**：`resp[1] == 0x72` 且 `resp[5] == 0x07`，电量 = `resp[7]`。

```python
ATK_REQUEST = bytes([0x08, 0x7D, 0x72, 0x02, 0x00, 0x01, 0x07, 0x01])

def atk_build(out_len=64):
    return ATK_REQUEST + bytes(out_len - len(ATK_REQUEST))

def atk_parse(resp):
    """返回电量百分比；不是电量回复返回 None。"""
    if len(resp) < 8 or resp[1] != 0x72 or resp[5] != 0x07:
        return None
    pct = resp[7]
    return pct if 0 <= pct <= 100 else None
```

**收不到回复**：鼠标休眠了。提示用户"动一下鼠标"，或先发一次让设备唤醒。

---

## 3. DARKRIM SHADOW 耳机（`3654:6901`，杰理 JL_RCSP）

**接口**：65 字节 Output 报文，**无 Report ID**（首字节就是 `0x00`）。

### 帧格式

```
偏移  内容
0     0x00                    固定
1     0xFE                    固定
2     seq                     序号
3     0x10 | 0x11             0x10 查询 / 0x11 回复
4     0x00
5     0x02 | 0x03             0x02 查询 / 0x03 回复
6     分片号
7     more                    还有后续帧
8..9  len（小端 16 位）        TLV 载荷长度
10..  TLV 载荷
10+len..11+len  CRC（大端 16 位）
其余补 0
```

**CRC**：CRC16-CCITT-FALSE，`poly=0x1021, init=0xFFFF`，覆盖 `帧[1 : 10+len]`，
大端存在 `帧[10+len]`、`帧[11+len]`。

### TLV 类型

| 类型 | 含义 |
|---|---|
| `0x01` | 状态 / 电量（**首字节 = 电量百分比**） |
| `0x02` | 设备名（ASCII） |
| `0x03` | EQ |
| `0x04` | dongle 固件版本 |
| `0x05` | 耳机固件版本 |
| `0x06` | 状态码 |
| `0x0B` | 地址 |

**查询 = 把 TLV 长度置 0 发出去**。

```python
def crc16_ccitt_false(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc

def jl_frame(seq, payload=b"", flag=0x10, opcode=0x02, total=65):
    n = len(payload)
    head = bytes([0x00, 0xFE, seq & 0xFF, flag, 0x00, opcode, 0x00, 0x00,
                  n & 0xFF, (n >> 8) & 0xFF])
    body = head + payload
    crc = crc16_ccitt_false(body[1:])
    body += bytes([(crc >> 8) & 0xFF, crc & 0xFF])
    return body + bytes(total - len(body))

def jl_parse(buf):
    """返回 {'tlv': {type: value_bytes}, 'crc_ok': bool}；不像 JL 帧返回 None。"""
    if len(buf) < 13 or buf[0] != 0x00 or buf[1] != 0xFE:
        return None
    n = buf[8] | (buf[9] << 8)
    if 10 + n + 2 > len(buf):
        return None
    crc = (buf[10 + n] << 8) | buf[11 + n]
    ok = crc == crc16_ccitt_false(buf[1:10 + n])
    tlv, i, end = {}, 10, 10 + n
    while i < end:
        t = buf[i]; i += 1
        if i + 2 <= end and (buf[i] | (buf[i + 1] << 8)) <= end - (i + 2):
            ln = buf[i] | (buf[i + 1] << 8); i += 2
        else:
            ln = end - i                       # 兼容"类型+值"的短写法
        tlv[t] = buf[i:i + ln]; i += ln
    return {"tlv": tlv, "crc_ok": ok}
```

**注意**：这个协议有两种 TLV 写法（带 2 字节长度 / 只有值），解析要都兼容。
标定实现里对查询也做了两种写法的自动回退。

**没回复**：耳机没开机 / 没和 dongle 配对，dongle 不会回任何数据。

---

## 4. 雷柏 Rapoo VT 系列（`24AE:1412`）

**接口**：`mi_01&col09`，usage `FF00/0002`，Report ID `0x07`，**13 字节输入报文**。
**主动推送，约每 3 秒一帧**，不需要发任何命令。

```
07 10 04 80 0C 80 0C [7] [8] 00 84 84 00
│  └──┬──┘ └──┬──┘  │   │
│     │       │     │   └── 电量百分比（0x5D=93 … 0x64=100）
│     │       │     └────── 充电状态：0x02 充电中 / 0x01 未充电
│     │       └──────────── DPI Y（0x0C80 = 3200）
│     └──────────────────── DPI X
└────────────────────────── Report ID 0x07
```

**充电位是"拔线对比"定的**：其余字节全不变，只有 `[7]` 从 `0x02` 变 `0x01`。
（拔掉瞬间会短暂出现 `0x00`，是过渡态。）

```python
RAPOO_LEVEL_INDEX = 8
RAPOO_CHARGE_INDEX = 7
RAPOO_CHARGE_MASK = 0x02
RAPOO_REPORT_LEN = 13

def rapoo_parse(frame):
    """返回 (电量, 是否充电)；长度不对或电量越界返回 None。"""
    if len(frame) != RAPOO_REPORT_LEN:
        return None
    pct = frame[RAPOO_LEVEL_INDEX]
    if not 0 <= pct <= 100:
        return None
    return pct, bool(frame[RAPOO_CHARGE_INDEX] & RAPOO_CHARGE_MASK)
```

**在线判定**：帧间隔约 3 秒，超过 15 秒没帧就算离线（休眠 / 关机）。

**旁证**：开源 Linux 内核模块 `hid_rapoo_vt3` 的
`RAPOO_BATTERY_REPORT_SIZE=13` / `RAPOO_BATTERY_CAPACITY_INDEX=8` 与本表一致；
区别是该模块只读电量，不判充电。

---

## 5. 迈从 MCHOSE X75 V2（`3554:FA09`，CX 方案）

**接口**：`mi_01&col01`，usage `FF02/0002`，Report ID `0x13`，**Input / Output 都是 20 字节**。

### 帧结构（20 字节定长）

| 偏移 | 含义 |
|---|---|
| `[0]` | `0x13` Report ID |
| `[1]` | 命令 / 响应码 |
| `[2]` `[3]` | 固定 `0x01` `0x00`（回应里） |
| `[4]` | N，载荷长度 |
| `[5 : 5+N]` | 载荷 |
| `[19]` | 校验 = **前 19 字节求和取低 8 位**（不是 CRC） |

### 查询命令 `0x05`

发一条只有 `[0]=0x13`、`[1]=0x05` 的 20 字节报文（共 20 字节，短了报 `err=87`）。
设备**必回两帧**：

```
13 05 01 00 0A 03 00 00 00 01 C6 00 00 10 00 00 00 00 00 FD     ← 应答（[4]=0x0A=载荷长 10）
13 0A 01 00 04 05 64 01 00 00 00 00 00 00 00 00 00 00 00 8C     ← 数据帧
               └┬─┘
                └ 载荷[0]=0x05（"这是 0x05 的答复"）载荷[1]=电量 载荷[2]=标志位 载荷[3]=0
```

- **电量 = 数据帧第 6 字节**（`payload[1]`）
- **充电位 = 第 7 字节 bit4**：插线 `0x10` / 拔线 `0x01`，切换瞬间会路过 `0x00`

### 命令地图（`0x00`–`0x2F` 全扫过）

| 命令 | 结果 |
|---|---|
| `0x01` `0x02` `0x03` `0x04` `0x09` | 空应答（只回校验和） |
| **`0x05`** | **回两帧，带电量/充电状态** |
| `0x0A` | 不单独响应，只作为 `0x05` 的第二帧出现 |
| 其余 | 无响应 |

### 有个 `0x07` 事件帧，别当电量

```
13 0A 01 00 04 07 00 00 00 ... 29
```

载荷首字节是 `0x07` 而不是 `0x05`，**必须按 `payload[0] == 0x05` 过滤**。
早期实现取"最后一帧"，正好被它污染，表现为电量在 100 和 0 之间乱跳。

```python
KB_REPORT_ID = 0x13
KB_LEN = 20
KB_CMD_STATUS = 0x05
KB_RESP_DATA = 0x0A
KB_CHARGE_BIT = 0x10

def kb_build(cmd, total=KB_LEN):
    body = bytearray(total)
    body[0] = KB_REPORT_ID
    body[1] = cmd
    body[-1] = sum(body[:-1]) & 0xFF
    return bytes(body)

def kb_parse_frame(frame):
    """返回 (电量, 是否充电, 标志字节)；不是状态数据帧返回 None。"""
    if not frame or len(frame) < 8 or frame[0] != KB_REPORT_ID:
        return None
    n = frame[4]
    if n < 1 or n > len(frame) - 6:
        return None
    payload = frame[5:5 + n]
    if frame[1] != KB_RESP_DATA or len(payload) < 2 or payload[0] != KB_CMD_STATUS:
        return None
    pct = payload[1]
    if not 0 <= pct <= 100:
        return None
    flags = payload[2] if len(payload) > 2 else 0
    return pct, bool(flags & KB_CHARGE_BIT), flags
```

**注意**：设备闲置 30 分钟会断开 2.4G 连接（官方说明），此后查询无回复，按任意键重连。

---

## 6. 京东京造 无线鼠标（`362D:D107`，Keychron Link-KM 方案）

固件自报名字是 **"Keychron Link-KM"**、版本 `0.1.3`（京东京造 K 系列是 Keychron 代工，
所以 VID 是 Keychron 家族的 `362D`）。这是个键鼠复合 dongle：除了鼠标集合，
还带一套键盘集合，以及三条厂商通道。

**接口**：`mi_01&col02`，usage `FFC1/0001`，**64 字节输入 / 输出**，
`0xB3` 写、`0xB4` 读。

### 帧结构（64 字节）

```
写：[0]=0xB3  [1]=命令   其余补 0
读：[0]=0xB4（或 0xB6）  [1]=命令回显  [2]=?  [3:]=载荷
不支持的命令统一回 0xE4：B4/B6 E4 <arg> <cmd>
```

### 查询命令 `0x06`（状态）

```
写： B3 06 00 00 ... 00                                  (64 字节)
读： B4 06 00 21 21 01 20 03 40 06 60 09 80 0C A0 0F 15 05 04 0A 9C 00 ...
                  └────────────┬────────────┘                        │
                     6 档 DPI（LE16：800/1600/…/4000）          电量在这里
```

**第 20 字节是一个"标志 + 数值"的组合**，靠插拔充电线对比定死的：

```
插着充电线： B4 06 00 … 0A 9C 00 …      ← 0x9C，最高位 1
拔掉充电线： B4 06 00 … 0A 1C 00 …      ← 0x1C，最高位 0
```

- **bit7 = 充电中**（`1` 充电 / `0` 未充电）
- **低 7 位 = 电量百分比**（`0x1C` = 28%）

这个偏移与 Keychron 开源内核模块 `keychron_battery.c` 的
`KEYCHRON_BATTERY_OFFSET = 20` 一致，但它是直接判 `data[20] <= 100`——
那是没有 bit7 标志的机型，所以这里必须 `raw & 0x7F` 才通用，否则充电时会读到 156。

整帧对比还发现：**除了 bit7，插拔充电线不改变任何其他字节**，所以这个标志位是可靠的。

```python
KCH_CMD_ID = 0xB3
KCH_RESP_ID = 0xB4
KCH_CMD_STATUS = 0x06
KCH_BATTERY_OFFSET = 20
KCH_LEN = 64

def kch_build(cmd, total=KCH_LEN):
    body = bytearray(total)
    body[0] = KCH_CMD_ID
    body[1] = cmd & 0xFF
    return bytes(body)

def kch_parse(frame):
    """返回 (电量, 是否充电, 原始帧)；不是状态回应返回 None。"""
    if not frame or len(frame) <= KCH_BATTERY_OFFSET:
        return None
    if frame[0] != KCH_RESP_ID or frame[1] != KCH_CMD_STATUS:
        return None
    raw = frame[KCH_BATTERY_OFFSET]
    pct = raw & 0x7F
    if pct > 100:
        return None
    return pct, bool(raw & 0x80), bytes(frame)
```

### 命令地图（`0x00`–`0xFF` 全扫）

| 命令 | 回应 | 含义 |
|---|---|---|
| `0x02` | `B6 02 02 00 2D 36 07 D1 02 01 01` | VID `0x362D` / PID `0xD107` |
| `0x03` | `B6 03 01 2D 36 0F D2 01 00` | 版本 / 序列相关 |
| `0x04` | `B4 04 06 "0.1.3"` + TLV `0x11` = 设备名 | 固件版本 + 名称 |
| `0x05` | `B6 05 00 <6 档 DPI> … 电量` + TLV `0x11` = 设备名 | 设置快照 |
| **`0x06`** | **`B4 06 00 <6 档 DPI> … <电量\|充电位>`** | **状态（取电量/充电用这个）** |
| `0x61` `0x62` `0x63` | `B4 6x 00 00 …` | 目前全 0，用途未知 |
| 其余 | `B4/B6 E4 <arg> <cmd>` | 不支持 |

### 另外两条通道（本机实测无响应）

- `mi_01&col01`，usage `008C/0001`，33 字节，`0xB1` 读 / `0xB2` 写 —— 发 `0xB2` 命令无任何回包。
- `mi_03`，usage `FF60/0061`，33 字节 —— 这是 VIA/QMK raw HID 的 usage，但按 VIA 协议
  （`0x01` 取版本 / `0x02` 取 keyboard id）问都没有回应，不是标准 VIA。

> 备注：`FF60/0061` 出现在鼠标 dongle 上容易误导人，它长得像 VIA 但实际上不说话。

---

## 7. 飞智 Flydigi Vader 4 Pro（`04B4:2412`，经典协议）

### 先决条件：必须切到 DInput 模式

同一把手柄在不同模式下枚举成**完全不同的设备**：

| 模式 | VID:PID | 接口 | 厂商通道 |
|---|---|---|---|
| **XInput** | `045E:028E` | 1 个（15 字节纯摇杆报文，`out=0 feat=0`） | **无** —— 命令发不出去 |
| **DInput** | `04B4:2412` | 4 个（gamepad / mouse / 2×vendor） | **接口 2，usage `0xFFA0`** |

切换方式（官方说明书）：**长按 `FN` + `A` 三秒 → DInput（指示灯变蓝）**；
`FN` + `X` 三秒 → 回 XInput（白灯）。切换后 USB 会重新枚举。

> `FN` 就是手柄上那个 `O` 形 logo 键。Switch 模式是 `FN + B`，对应 `057E:2000`。

2.4G 接收器也枚举成 `045E:028E`（产品串 "Flydigi VADER3"，4 个接口），
接口 0 用 XInput 通道（`A5 <cmd> …` 15 字节）。只有描述符真给得出输出报表时
本工具才会尝试这条路径（`match_gamepad()` 里判 `output_len >= 15`）。

### 通道

- 输出：report id **5**，`05 <cmd> <args…>`，**补零到 12 字节**（只发 5 字节会被静默忽略）
- 输入：report id **4**，31 字节，载荷从偏移 3 开始

### 设备信息（取电量用这个）

```
TX: 05 EC 00 00 00 00 00 00 00 00 00 00 00 …（补零到输出报表长度，本机 32 字节）
RX: 04 FF .. <devId> .. <MAC 4B> <fw 2B> <battery> <cpu> <conn> <motion> .. <cmdEcho>
                                            ↑         ↑                        ↑
                                      第 11 字节  第 13 字节(1=有线)      第 15 字节 = 0xEC
```

本机实抓（2026-11-01，接收器无线连接，16 进制）：

```
04 FF F0 55 00 68 92 90 38 33 69 04 02 00 01 EC 00 00 5A 00 …
      ↑     ↑  └── MAC ──┘ └fw─┘ ↑  ↑  ↑     ↑
   头部   devId=0x55       电量=4  │conn=0 回显 0xEC
                                cpu=2
```

**最大的坑**：这个接口每秒推上千帧实时状态（`04 FE …`，1000Hz 轮询率），
命令回应 `04 FF …` 混在里面**只出现一次**。只打印前几帧的话根本看不到它——
必须按"byte[1] == 0xFF"过滤，否则会以为设备没回复。

### 电量是「0..5 档」，不是百分比

```python
level    = battery & 0x0F      # 0..5，每档 20%
charging = (battery >> 4) == 1 # 高 4 位为 1 表示充电中
percent  = level * 20
```

这是飞智自己的口径（`Battery = (b >> 4 == 1) ? 6 : (b & 0xF)`，充电时用一个专门的图标 6）。
**命令集里不存在 1% 粒度的值**——不要试图把它换算成更精细的百分比。

> ⚠️ 但这一位**在 2.4G 无线连接下不会被置起来**：插着充电头充电时它仍然是 0。
> 详见本节末尾「边界情况」，那里有逐字节对比和替代方案。

两个必须卡死的校验（我第一版只判了低 4 位，结果把 MAC 地址里的 `0x33` 当成了 3 档）：

```python
def _fd_level(raw):
    hi, level = raw >> 4, raw & 0x0F
    if hi not in (0, 1) or level > 5:   # 高 4 位只可能是 0（未充）或 1（充电）
        return None
    return level, bool(hi)
```

### XInput 通道（备用）

```
TX: A5 <cmd> <args…> <crc>     15 字节，crc = 前 14 字节求和取低 8 位
RX: 载荷在 64 字节输入报文的偏移 14 处：r[14]==0xA5 → r[15]=cmd，r[16..]=payload
    电量在 r[23]，连接方式在 r[25]
```

本机实测：有线 XInput 的报文只有 15 字节、没有输出报表，这条路径用不上。

### 边界情况

- **固件在 2.4G 下不下发充电位**。文档口径是"高 4 位 == 1 = 充电中"，但实测（2026-11-02）：
  插着充电线（指示灯红灯常亮）和拔线两种状态下，`0xEC` 回包**逐字节相同**——

  ```
  充电中：04 FF F0 55 00 68 92 90 38 33 69 04 02 00 01 EC 00 00 73 …
  未充电：04 FF F0 55 00 68 92 90 38 33 69 04 02 00 01 EC 00 00 E8 …
  ```

  40 秒采样里只有第 18 字节（滚动计数）在变，电量位恒为 `0x04`（档位 4 = 80%）。
  换别的路也没有：`04 FE` 实时帧的第 11–15 字节是陀螺仪/加速度计（见 `protocol.md` §9），
  不含电量；`0x01`（新协议信息）、`0x11`（接收器信息）、`0x20`、`0xA0` 在本机全部无回应。
  **用充电头给手柄充电时，PC 侧拿不到任何信号**——这不是软件没实现，是物理上断开的。

  所以 `read_gamepad()` 按三条来源依次降级（`_fd_charge_state()`）：

  | 优先级 | 判据 | 显示 | 来源标记 |
  |---:|---|---|---|
  | 1 | 充电位真的置起来 | 充电中 | `flag` |
  | 2 | `conn == 1`（有线接电脑） | 充电中 | `wired` |
  | 3 | 40 分钟窗口内电量涨过一档 | 充电中（推断） | `trend` |
  | 4 | 电量在掉 | 未充电 | `trend` |
  | 5 | 都没有 | 什么都不显示 | — |

  `trend` 的代价是延迟：一档 20%，电池 800mAh 充满约 2 小时，所以**约 20~25 分钟才涨一格**，
  刚插上线是看不出来的。好处是只要真在充就一定会命中，而且误报几乎不可能——
  电量只会因为充电而上涨。拔线后最多 40 分钟（窗口过期）回落成未知。
- **`conn`（第 13 字节）只在"接电脑"时才是 1**：说明书说 1 = 有线，本机插着充电头充电时
  该位仍是 0，所以它反映的是"USB 主机连接"而不是"有电"。也正因为它代表有线，
  一旦为 1 就可以直接判定在充电（上表第 2 条）。
- **刚上电时电量字节读出来是 0**，要持续重发几秒才变成真实值
  （飞智空间站的做法是每秒重发、最多 30 秒）。本工具每次刷新会重试 3 轮。
- **接收器大约每两次请求超时一次**（固件行为），所以 `read_gamepad()` 默认重试 3 次再判定失败。
- **插上充电线后手柄可能整块不应答**（射频链路掉线或直接关机），此时连电量都读不到，
  面板会显示"无回复 · 手柄可能已关机或没连上接收器"。拔线后按一下 Home 键即可恢复。

### 参考

- `ericzuobin/flydigi-space-station-mac` → `docs/protocol.md`（Apex 4 / k2 经典协议，§1 USB modes、§3 命令表）
- `mkaliaha/openflydigi` → `docs/device-settings.md` §"Battery: a 0..5 level"（档位口径）
- `dantmnf/Vader4ProReader`（DInput 32 字节状态报文）—— 它明确写了 **Battery Level: 无**

---

## 8. 兜底链（换设备时按顺序试）

1. **标准 HID 电量字段**：`UsagePage 0x0006 / Usage 0x0020`（Battery Strength），
   `HidD_GetFeature` 直接读。有的话最省事。
2. **厂商协议表**：已知 VID 对应的命令字（本表第 2~5 节）。
3. **罗技 HID++ 2.0**：Feature `0x1000`（Battery Voltage）/ `0x1004`（Unified Battery）/ `0x1001`。
4. **被动监听 Input Report**：挂 30 秒，看有没有规律性的定长帧（很多 dongle 是推送式）。
5. **手动标定**：把 Report ID / 电量字节下标 / 倍率存成 JSON，人工确认一次。

---

## 9. 方法论：怎么把一台新设备逆出来

顺序很重要，每一步都能排除掉大量可能：

1. **枚举全部 HID 接口**，把 `usage_page`、`input_len` / `output_len` / `feature_len`、
   `can_write` 列成表。厂商通道一定是 `0xFF00` 以上（或 `0x8C`、`0xFF60` 这类特殊页）。
2. **读描述符的 Value Caps**：直接告诉你有没有标准电量字段、有哪些 Report ID。
   （`HidP_GetValueCaps` + `HidP_GetButtonCaps`，后者能列出所有声明的 Report ID。）
3. **先被动监听 60~120 秒**，用户正常操作设备。有推送就直接省掉逆向。
4. **再试 Feature Report**：按描述符声明的 Report ID 和长度去 `HidD_GetFeature`，
   长度错了会报 `err=87`、没实现会报 `err=31`。
5. **最后才是盲发命令**：从 `0x00` 扫到 `0x2F`，参数全填 0（避免改到设置），
   看有没有回包。有回包再看校验规则（求和 / CRC / 无）。
6. **定位电量字节**：让设备进入充电，**每 20 秒采样一次**——电量每 20 秒涨 1% 的那个字节就是它。
7. **定位充电位**：**拔掉充电线**，对比前后帧，只有一位变的就是充电标志。
   这个"对比法"是唯一可靠的办法，不要靠猜 bit。

> 0x64 = 100 这种值特别容易误判（可能是个音量、亮度、档位）。一定要用"充电时递增"验证。

---

## 10. 附：HID 结构体在 x64 下的两个陷阱

1. `SP_DEVICE_INTERFACE_DETAIL_DATA_W` 的 `cbSize` 在 x64 下要写 **8**，
   但**路径字符串从偏移 4 开始**（不是 8）。按常见写法用偏移 8 会丢掉开头的 `\?\`，
   `CreateFileW` 直接失败。
2. `CreateIconIndirect` 把位图当**底朝下**读。要做托盘图标的话，行序必须倒着写。

---

## 11. 参考实现

本项目 `wbc/proto.py` 是以上协议的完整实现（纯标准库 + ctypes，零第三方依赖），
包含：设备自动发现、协议回退、推送监听线程、超时与重试、自检用例。
