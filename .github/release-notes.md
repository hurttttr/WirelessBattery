## 下载哪个？

两个包功能完全一样，只是打包方式不同，都**不需要安装 Python**。

| | 目录版 | 单文件版 |
|---|---|---|
| 文件名 | `WirelessBattery-win64.zip` | `WirelessBattery-win64-onefile.zip` |
| 解压后 | `WirelessBattery\` 文件夹（exe + `_internal\`） | 就一个 `WirelessBattery.exe` |
| 启动速度 | 快，约 0.2 秒 | 慢一点，约 0.4 秒（每次要先解压到临时目录） |
| 杀软误报 | 较少 | 偶有（自解压结构容易被判成可疑） |
| 适合 | 装在自己电脑上长期用、要开机自启 | 拷给别人、放 U 盘、临时用一次 |

> 目录版解压后**整个文件夹一起放着**，别只把 exe 拖出来——它离不开旁边的 `_internal\`。

## 用法

双击 `WirelessBattery.exe`，托盘出现电量图标。**不弹窗是正常的**，它就是个托盘工具。

- **左键**点图标 → 立刻刷新读数
- **右键或双击**图标 → 打开面板

Win11 默认把新图标收进溢出区（任务栏右侧的 `^`），去那里找；也可以在
「设置 → 个性化 → 任务栏 → 其他系统托盘图标」里把它打开。

开机自启 + 桌面链接：

```bat
WirelessBattery.exe --install
```

其它常用命令（在 cmd 里跑，会有输出）：

```bat
WirelessBattery.exe --once       :: 读一次，直接打印电量
WirelessBattery.exe --status     :: 设备 / 自启 / 链接 状态
WirelessBattery.exe --selftest   :: 不依赖硬件的协议自检
WirelessBattery.exe --uninstall  :: 撤销自启和桌面链接
```

## 说明

- 只支持 **Windows 10 / 11，64 位**
- 没插受支持的 2.4G 外设时，会退回显示默认两台设备，此时数值没有意义
- 读不到电量 = 这台设备还没适配。协议逆向方法和已有设备的报文都在仓库的
  `PROTOCOLS.md` 与 `AGENT_GUIDE.md` 里，可以照着自己加
