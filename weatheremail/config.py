"""配置的读写与交互式编辑.

配置文件为 JSON，落在 data_dir()/config.json，权限锁定 600。
支持环境变量覆盖敏感项，便于不想把密码落盘的用户：

    WEATHEREMAIL_SMTP_PASS    覆盖 SMTP 密码
    WEATHEREMAIL_API_KEY      覆盖和风天气 API KEY
"""

from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Dict

from . import (
    DEFAULT_LAT,
    DEFAULT_LOCATION_NAME,
    DEFAULT_LON,
)
from .paths import config_path

# ---------------------------------------------------------------- 默认配置

DEFAULT_CONFIG: Dict[str, Any] = {
    "location": {
        "name": DEFAULT_LOCATION_NAME,
        "lat": DEFAULT_LAT,
        "lon": DEFAULT_LON,
    },
    # 和风天气
    "qweather": {
        "api_key": "",
        # API Host 必须由用户在设置界面填写（不写死在代码里）。
        # 在 https://console.qweather.com/setting 查看，为账号专属域名，
        # 形如 abcdefg.re.qweatherapi.com。
        "api_host": "",
        # "auto" / "v1"(新版) / "v7"(旧版)
        "api_version": "auto",
    },
    # 邮件
    "smtp": {
        "host": "smtp.qq.com",
        "port": 465,
        "use_ssl": True,
        # "sender@example.com" 或 "天气邮件 <sender@example.com>"
        "sender": "",
        "password": "",
        "sender_name": "天气邮件预警",
    },
    "recipients": [],
    "subject_prefix": "【天气邮件】",
    # 恶劣天气开关：值为 True 表示该天气出现时发预警
    "severe": {
        "enabled": True,
        "rain": True,       # 雨（含雷阵雨）
        "snow": True,       # 雪 / 雨夹雪
        "hail": True,       # 冰雹 / 冰粒
        "fog": True,        # 雾 / 霾 / 沙尘
        "wind": True,       # 大风
        "wind_scale": 6,    # 风力达到蒲福风级几级算大风
        "rain_probability": 30,  # 降水概率(%)达到多少才提示
    },
    # 温度变化阈值
    "temperature": {
        "change_enabled": True,
        # 触发「大范围升温/降温」通知的温差（摄氏度）
        "change_threshold": 5.0,
        "record_history": True,
    },
    "send": {
        # 无预警时也发普通简报邮件
        "always_send": True,
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """把 override 合并进 base 的副本，保证新增默认字段不会丢失。"""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> Dict[str, Any]:
    """读取配置；文件不存在或损坏时返回默认配置。"""
    path = config_path()
    raw: Dict[str, Any] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = {}
    cfg = _deep_merge(DEFAULT_CONFIG, raw)
    _apply_env_overrides(cfg)
    return cfg


def _apply_env_overrides(cfg: Dict[str, Any]) -> None:
    """环境变量优先级高于配置文件。"""
    pw = os.environ.get("WEATHEREMAIL_SMTP_PASS")
    if pw:
        cfg["smtp"]["password"] = pw
    key = os.environ.get("WEATHEREMAIL_API_KEY")
    if key:
        cfg["qweather"]["api_key"] = key


def save_config(cfg: Dict[str, Any]) -> Path:
    """原子写入配置文件，并锁定为 600 权限。"""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # 写入前剥掉被环境变量覆盖的临时值，避免把环境变量固化进文件
    stored = copy.deepcopy(cfg)
    if os.environ.get("WEATHEREMAIL_SMTP_PASS"):
        stored["smtp"]["password"] = stored["smtp"].get("password", "")
    if os.environ.get("WEATHEREMAIL_API_KEY"):
        stored["qweather"]["api_key"] = stored["qweather"].get("api_key", "")

    fd, tmp_name = tempfile.mkstemp(
        prefix=".config-", suffix=".json", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(stored, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)  # 600
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def mask_secret(value: str, keep: int = 4) -> str:
    """把密钥渲染成可安全展示的形式。"""
    if not value:
        return "(未设置)"
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * max(4, len(value) - keep)


# ------------------------------------------------------------ 校验


def _check_coord(value: Any, low: float, high: float) -> bool:
    """判断经纬度字符串是否在合法范围内。"""
    try:
        num = float(str(value).strip())
    except (TypeError, ValueError):
        return False
    return low <= num <= high


def validate(cfg: Dict[str, Any]) -> "tuple[list[str], list[str]]":
    """返回 (errors, warnings)。

    硬性必填：API KEY、API Host、发件人、SMTP 地址、至少一个收件人。
    软性提醒：配了用户名/密码只配一半之类。
    """
    errors: list[str] = []
    warnings: list[str] = []

    loc = cfg.get("location", {})
    if not _check_coord(loc.get("lat"), -90.0, 90.0):
        errors.append("推送位置纬度无效（需在 -90 ~ 90 之间，请在设置第 [1] 项填写）")
    if not _check_coord(loc.get("lon"), -180.0, 180.0):
        errors.append("推送位置经度无效（需在 -180 ~ 180 之间，请在设置第 [1] 项填写）")

    if not cfg["qweather"].get("api_key"):
        errors.append("和风天气 API KEY 未填写（请在设置中填写）")
    if not cfg["qweather"].get("api_host"):
        errors.append(
            "和风天气 API Host 未填写"
            "（在 https://console.qweather.com/setting 查看，必须填写）"
        )

    smtp = cfg.get("smtp", {})
    if not smtp.get("host"):
        errors.append("SMTP 服务器地址未填写")
    if not smtp.get("sender"):
        errors.append("发件人邮箱未填写")
    if not cfg.get("recipients"):
        errors.append("收件人列表为空（请至少填写一个收件邮箱）")

    if smtp.get("password") and not smtp.get("sender"):
        warnings.append("已填写 SMTP 密码但未填写发件人邮箱，认证可能失败")

    return errors, warnings
