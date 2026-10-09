"""温度历史记录.

每次运行把当天的实况温度写入 history.json，供后续运行对比「较昨日」
的温升/温降幅度。同时兼作「程序是否已运行过」的凭据。

文件结构：
    {
      "entries": {
        "2026-10-07": {"temp": 18.5, "recorded_at": "2026-10-07T08:30:00",
                       "source": "obs", "forecast_max": 22.0,
                       "forecast_min": 11.0}
      }
    }
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .paths import history_path

MAX_ENTRIES = 400  # 约一年


def _empty() -> Dict[str, Any]:
    return {"entries": {}}


def load_history() -> Dict[str, Any]:
    path = history_path()
    if not path.is_file():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return _empty()
    return data


def save_history(data: Dict[str, Any]) -> None:
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = data.get("entries", {})
    if len(entries) > MAX_ENTRIES:
        keep = sorted(entries.keys())[-MAX_ENTRIES:]
        data["entries"] = {k: entries[k] for k in keep}

    fd, tmp_name = tempfile.mkstemp(
        prefix=".history-", suffix=".json", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def record_today(
    temp: Optional[float],
    source: str = "obs",
    forecast_max: Optional[float] = None,
    forecast_min: Optional[float] = None,
    day: Optional[date] = None,
) -> Dict[str, Any]:
    """记录今天的温度，返回更新后的历史数据。"""
    day = day or date.today()
    data = load_history()
    data["entries"][day.isoformat()] = {
        "temp": temp,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "forecast_max": forecast_max,
        "forecast_min": forecast_min,
    }
    save_history(data)
    return data


def get_entry(day: date) -> Optional[Dict[str, Any]]:
    return load_history()["entries"].get(day.isoformat())


def previous_entry(
    within_days: int = 7,
) -> Tuple[Optional[date], Optional[Dict[str, Any]]]:
    """找出今天之前最近的一条记录（最多回溯 within_days 天）。"""
    entries = load_history()["entries"]
    today = date.today()
    for offset in range(1, within_days + 1):
        target = today - timedelta(days=offset)
        item = entries.get(target.isoformat())
        if item and item.get("temp") is not None:
            return target, item
    return None, None


def compare_with_previous(
    current_temp: Optional[float],
) -> Dict[str, Any]:
    """比较当前温度与最近一次历史记录。

    返回 dict：
        {
          "available": bool,        # 是否有可比数据
          "prev_date": "YYYY-MM-DD" 或 None,
          "prev_temp": float 或 None,
          "delta": float 或 None,   # 正值表示升温
          "days_gap": int,           # 相隔天数
        }
    """
    result: Dict[str, Any] = {
        "available": False,
        "prev_date": None,
        "prev_temp": None,
        "delta": None,
        "days_gap": 0,
    }
    if current_temp is None:
        return result

    prev_day, prev = previous_entry()
    if prev is None or prev_day is None:
        return result

    prev_temp = prev.get("temp")
    if prev_temp is None:
        return result

    result.update(
        {
            "available": True,
            "prev_date": prev_day.isoformat(),
            "prev_temp": float(prev_temp),
            "delta": round(float(current_temp) - float(prev_temp), 1),
            "days_gap": (date.today() - prev_day).days,
        }
    )
    return result


def history_tail(limit: int = 10) -> List[Dict[str, Any]]:
    """返回最近若干条记录，用于 -status 展示。"""
    entries = load_history()["entries"]
    rows = []
    for key in sorted(entries.keys(), reverse=True)[:limit]:
        item = entries[key]
        rows.append(
            {
                "date": key,
                "temp": item.get("temp"),
                "recorded_at": item.get("recorded_at", ""),
            }
        )
    return rows
