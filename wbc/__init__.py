# -*- coding: utf-8 -*-
"""无线外设电量小工具 —— 纯标准库 + ctypes 实现。

包内模块：
    win32      Win32/COM 的 ctypes 封装（结构与函数原型）
    hid        HID 设备枚举与安全的读写通道
    proto      设备协议（ATK Nordic / 杰理 JL_RCSP / 兜底链）
    icons      托盘图标与 .ico 的纯 Python 光栅化
    tray       通知区域宿主窗口（SetTimer + Shell_NotifyIcon）
    panel      简洁面板（两张卡片）
    debugui    完整调试版
    autostart  启动文件夹 .lnk（COM IShellLinkW）
    links      桌面符号链接 / junction / 硬链接
    settings   运行时配置与手动标定
    app        进程装配（单实例、线程模型、退出）
"""

__version__ = "1.0.0"
APP_NAME = "无线外设电量"
APP_ID = "WirelessBattery"
