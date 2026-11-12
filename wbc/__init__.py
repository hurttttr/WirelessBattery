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

import os
import sys

__version__ = "1.0.3"
APP_NAME = "无线外设电量"
APP_ID = "WirelessBattery"


# ---------------------------------------------------------------- 运行形态
#
# 源码运行和 PyInstaller 打包后跑，路径语义完全不同，所有涉及"往哪写文件"
# 的地方都必须按这里判断，不能用 __file__ 硬推：
#
#   源码   __file__ = <项目>/my_devices.py         写文件 → 项目根目录
#   onedir __file__ = <安装目录>/_internal/xxx.pyc  写文件 → exe 所在目录
#   onefile __file__ = %TEMP%/_MEIxxxxx/xxx.pyc    写文件 → exe 所在目录
#                              ^ 退出即删，往这里写等于丢文件
#

def frozen():
    """是否被 PyInstaller 之类的打包器冻结运行。"""
    return bool(getattr(sys, "frozen", False))


def app_dir():
    """程序所在目录：用户看得见、可写的地方。

    源码运行是项目根目录；打包后是 exe 所在目录。
    凡是"要留下痕迹"的写操作都往这里放，别用 __file__ 推——
    onefile 下 __file__ 在 %TEMP%\\_MEIxxxxx\\，退出就被删了。
    """
    if frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
