# -*- coding: utf-8 -*-
"""桌面入口：不复制脚本，只放链接。

目录链接：优先 os.symlink（需要管理员或开发者模式），
          降级用 junction（mklink /J，普通用户即可创建）。
文件链接：优先 os.symlink，降级 os.link（硬链接），
          跨卷时硬链接必然失败（桌面在 C:、项目在 D: 很常见），
          这时再退一步放一个 .lnk 快捷方式——仍然不是副本。
"""

import os
import subprocess

from . import logx
from .logx import dbg

DIR_LINK_NAME = "无线外设电量"
FILE_LINK_NAME = "电量托盘.py"

CREATE_NO_WINDOW = 0x08000000


class LinkResult(object):
    __slots__ = ("ok", "kind", "path", "target", "message")

    def __init__(self, ok, kind, path, target, message=""):
        self.ok = ok
        self.kind = kind
        self.path = path
        self.target = target
        self.message = message

    def __repr__(self):
        return "<%s %s %s -> %s %s>" % (
            "OK" if self.ok else "FAIL", self.kind, self.path, self.target, self.message)


def _same_target(link, target):
    try:
        return os.path.normcase(os.path.realpath(link)) == os.path.normcase(os.path.realpath(target))
    except Exception:
        return False


def _mklink_junction(link, target):
    """mklink /J —— 不需要管理员权限，但只支持目录、只支持本地卷。"""
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", link, target],
        capture_output=True, text=True, encoding="mbcs", errors="replace",
        creationflags=CREATE_NO_WINDOW)
    return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def make_dir_link(link_path, target_dir):
    target_dir = os.path.abspath(target_dir)
    if os.path.exists(link_path) or os.path.islink(link_path):
        if _same_target(link_path, target_dir):
            return LinkResult(True, "existing", link_path, target_dir, "已存在且指向正确")
        return LinkResult(False, "conflict", link_path, target_dir, "同名文件/目录已存在，未覆盖")

    os.makedirs(os.path.dirname(link_path), exist_ok=True)
    try:
        os.symlink(target_dir, link_path, target_is_directory=True)
        return LinkResult(True, "symlink", link_path, target_dir)
    except OSError as e:
        dbg("目录符号链接失败：%s，降级 junction" % e)

    ok, out = _mklink_junction(link_path, target_dir)
    if ok:
        return LinkResult(True, "junction", link_path, target_dir)
    return LinkResult(False, "junction", link_path, target_dir, out.strip() or "mklink 失败")


def make_file_link(link_path, target_file):
    target_file = os.path.abspath(target_file)
    if os.path.exists(link_path) or os.path.islink(link_path):
        if _same_target(link_path, target_file):
            return LinkResult(True, "existing", link_path, target_file, "已存在且指向正确")
        return LinkResult(False, "conflict", link_path, target_file, "同名文件已存在，未覆盖")

    os.makedirs(os.path.dirname(link_path), exist_ok=True)
    try:
        os.symlink(target_file, link_path)
        return LinkResult(True, "symlink", link_path, target_file)
    except OSError as e:
        dbg("文件符号链接失败：%s，降级硬链接" % e)

    try:
        os.link(target_file, link_path)
        return LinkResult(True, "hardlink", link_path, target_file)
    except OSError as e:
        dbg("硬链接失败（多半是跨卷）：%s，降级 .lnk" % e)

    # 跨卷兜底：放一个快捷方式，仍然不是脚本副本
    try:
        from . import autostart, settings
        lnk = link_path + ".lnk"
        if os.path.exists(lnk):
            os.remove(lnk)
        autostart.create_shortcut(
            lnk,
            target=autostart.python_executable(windowed=True),
            arguments='"%s"' % target_file,
            workdir=os.path.dirname(target_file),
            description="无线外设电量面板",
            icon_path=settings.app_icon_path(),
            icon_index=0,
        )
        return LinkResult(True, "shortcut", lnk, target_file,
                          "硬链接跨卷不可用，已改用快捷方式")
    except Exception as e:
        return LinkResult(False, "shortcut", link_path, target_file, str(e))


def make_desktop_links(project_dir, script_path, desktop=None):
    """在桌面建两个链接：指向项目目录的目录链接 + 指向启动脚本的文件链接。"""
    from . import win32 as wx
    desktop = desktop or wx.desktop_dir()
    results = []
    results.append(make_dir_link(os.path.join(desktop, DIR_LINK_NAME), project_dir))
    results.append(make_file_link(os.path.join(desktop, FILE_LINK_NAME), script_path))
    return results


def remove_desktop_links(desktop=None):
    from . import win32 as wx
    desktop = desktop or wx.desktop_dir()
    removed = []
    for name in (DIR_LINK_NAME, FILE_LINK_NAME, FILE_LINK_NAME + ".lnk"):
        p = os.path.join(desktop, name)
        if not (os.path.exists(p) or os.path.islink(p)):
            continue
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                os.rmdir(p)          # junction 用 rmdir，不会删到目标内容
            else:
                os.remove(p)
            removed.append(p)
        except OSError as e:
            logx.warn("删除失败 %s: %s" % (p, e))
    return removed
