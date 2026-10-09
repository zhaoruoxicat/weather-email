"""统一管理配置、历史记录与输出文件的存放位置.

运行位置分两种：
  * 源码树里运行 → 文件落在项目目录内（开发调试方便，不污染系统）
  * 安装到 /opt/weatheremail 后运行 → 文件落在用户的 ~/.config/weatheremail
"""

from __future__ import annotations

import os
from pathlib import Path

APPNAME = "weatheremail"


def _pkg_parent() -> Path:
    """返回包目录的上一级（源码树根目录 或 /opt/weatheremail）。"""
    return Path(__file__).resolve().parent.parent


def is_installed() -> bool:
    """判断当前是否以已安装形态运行。"""
    parent = str(_pkg_parent())
    return parent.startswith("/opt/") or parent.startswith("/usr/lib/")


def data_dir() -> Path:
    """返回可写的数据目录，必要时创建。

    已安装：~/.config/weatheremail（或 $XDG_CONFIG_HOME/weatheremail）
    源码树：<项目根>/data
    """
    if is_installed():
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            Path.home(), ".config"
        )
        path = Path(base) / APPNAME
    else:
        path = _pkg_parent() / "data"
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def config_path() -> Path:
    return data_dir() / "config.json"


def history_path() -> Path:
    return data_dir() / "history.json"


def log_path() -> Path:
    return data_dir() / "weatheremail.log"


def preview_dir() -> Path:
    path = data_dir() / "preview"
    path.mkdir(parents=True, exist_ok=True)
    return path


def binary_path() -> str:
    """定位可执行入口，返回可直接调用的命令字符串。"""
    import shutil

    candidate = Path("/usr/bin") / APPNAME
    if candidate.is_file():
        return str(candidate)
    found = shutil.which(APPNAME)
    if found:
        return found
    # 源码树里的启动脚本
    local = _pkg_parent() / "packaging" / APPNAME
    if local.is_file():
        return str(local)
    return APPNAME


def systemd_dir() -> Path:
    """返回用户级 systemd 单元目录（用户可写）。"""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        Path.home(), ".config"
    )
    return Path(base) / "systemd" / "user"
