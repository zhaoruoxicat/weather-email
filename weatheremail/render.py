"""HTML 邮件渲染.

只发 HTML 单版本邮件（按用户要求，不做纯文本替代版本）。
所有样式内联，兼容 QQ / 163 / 微信邮件客户端的严格 CSS 过滤。

两种版式：
  * 预警版：顶部红色横幅 + 预警卡片列表
  * 普通版：蓝色横幅 + 次日天气简报
"""

from __future__ import annotations

import html
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from .alert import Alert, AlertResult
from .net import DailyForecast, WeatherNow

# 主题色（中国天气习惯：升温暖色、降温冷色）
COLOR_DANGER = "#c62828"
COLOR_DANGER_BG = "#fdecea"
COLOR_WARNING = "#e65100"
COLOR_WARNING_BG = "#fff4e5"
COLOR_NORMAL = "#1565c0"
COLOR_NORMAL_BG = "#e8f1fb"
COLOR_TEXT = "#2b2b2b"
COLOR_MUTED = "#7a7a7a"
COLOR_BORDER = "#e4e6eb"
COLOR_BG = "#f4f6f8"

WIND_DIR_CN = {
    "n": "北风", "nne": "北东北风", "ne": "东北风", "ene": "东东北风",
    "e": "东风", "ese": "东东南风", "se": "东南风", "sse": "南东南风",
    "s": "南风", "ssw": "南西南风", "sw": "西南风", "wsw": "西西南风",
    "w": "西风", "wnw": "西西北风", "nw": "西北风", "nnw": "北西北风",
    # 无固定风向
    "none": "风向不定", "vrb": "风向多变", "calm": "无风", "静风": "无风",
}


def _e(value: Any) -> str:
    """HTML 转义。"""
    if value is None or value == "":
        return "--"
    return html.escape(str(value))


def _wind_dir(text: str) -> str:
    """把风向统一转成中文。

    兼容两种来源：
      * 新版 v1：英文方位缩写（sw / ssw / nnw ...）
      * 旧版 v7：已经是中文（"西南风"），原样保留

    绝不能对已经是中文的值做英文查表后 fallback 回原值，
    也不能对英文缩写原样输出——那会把 "ssw" 直接印到邮件里。
    """
    if text is None or str(text).strip() == "":
        return "风向不定"
    raw = str(text).strip()
    key = raw.lower()

    # 命中英文缩写表
    if key in WIND_DIR_CN:
        return WIND_DIR_CN[key]

    # 已是中文（含"风"字）→ 原样返回
    if any("\u4e00" <= ch <= "\u9fff" for ch in raw):
        return raw

    # 其它情况：大写英文缩写也尝试匹配一次，仍失败则标记不定，不裸输出
    return WIND_DIR_CN.get(key, "风向不定")


def _wind_dir_v7(text: str) -> str:
    """旧版 v7 风向：通常已是中文，但同样兜住英文缩写。"""
    return _wind_dir(text)


def _wind_scale_text(scale: Any) -> str:
    """风级文本：空值不显示"级"，避免出现"夜间 级"。"""
    if scale is None or str(scale).strip() == "":
        return ""
    return f"{scale}级"


def _wind_text(tomorrow: "DailyForecast") -> str:
    """拼出「白天 西南风 2级 / 夜间 南风 1级」这样的风力描述。"""
    day_dir = _wind_dir(tomorrow.wind_dir_day)
    day_scale = _wind_scale_text(tomorrow.wind_scale_day)

    parts = [f"白天 {day_dir}" + (f" {day_scale}" if day_scale else "")]

    night_scale = _wind_scale_text(tomorrow.wind_scale_night)
    # 旧版 v7 没有夜间风向字段，只有夜间风级
    night_dir = ""
    if getattr(tomorrow, "wind_dir_night", ""):
        night_dir = _wind_dir(tomorrow.wind_dir_night)
    if night_scale or night_dir:
        seg = "夜间"
        if night_dir:
            seg += f" {night_dir}"
        if night_scale:
            seg += f" {night_scale}"
        parts.append(seg)

    return " / ".join(parts)


def _fmt(value: Optional[float], unit: str = "", digits: int = 0) -> str:
    if value is None:
        return "--"
    return f"{value:.{digits}f}{unit}"


def _weather_emoji(text: str, code: str = "") -> str:
    """给天气现象配个符号，纯装饰。"""
    text = str(text or "")
    if any(w in text for w in ("冰雹", "霰")):
        return "&#127783;&#65039;"   # 冰雹
    if any(w in text for w in ("雪",)):
        return "&#10052;&#65039;"     # 雪
    if any(w in text for w in ("雷",)):
        return "&#9928;&#65039;"      # 雷
    if any(w in text for w in ("雨",)):
        return "&#127782;&#65039;"    # 雨
    if any(w in text for w in ("雾", "霾", "尘", "沙")):
        return "&#127787;&#65039;"    # 雾
    if any(w in text for w in ("阴",)):
        return "&#9729;&#65039;"
    if any(w in text for w in ("多云", "晴间")):
        return "&#9925;"
    if "晴" in text:
        return "&#9728;&#65039;"
    return "&#127748;"


# ------------------------------------------------------------ 区块渲染


def _section_title(text: str) -> str:
    return (
        f'<div style="font-size:15px;font-weight:700;color:{COLOR_TEXT};'
        f'margin:0 0 10px 0;padding-left:9px;border-left:4px solid '
        f'{COLOR_NORMAL};">{_e(text)}</div>'
    )


def _kv_row(label: str, value: str) -> str:
    return (
        f'<tr>'
        f'<td style="padding:7px 0;font-size:13px;color:{COLOR_MUTED};'
        f'width:104px;vertical-align:top;">{label}</td>'
        f'<td style="padding:7px 0;font-size:14px;color:{COLOR_TEXT};'
        f'vertical-align:top;">{value}</td>'
        f'</tr>'
    )


def _render_now(now: Optional[WeatherNow]) -> str:
    if now is None:
        return ""
    obs = _e(now.obs_time)
    rows = [
        _kv_row(
            "当前温度",
            f'<strong style="font-size:19px;color:{COLOR_NORMAL};">'
            f'{_fmt(now.temp, " ℃", 1)}</strong>'
            f'<span style="color:{COLOR_MUTED};font-size:12px;">'
            f'（体感 {_fmt(now.feels_like, " ℃", 1)}）</span>',
        ),
        _kv_row(
            "天气现象",
            f'{_weather_emoji(now.text)} {_e(now.text)}',
        ),
        _kv_row(
            "风力风向",
            f'{_wind_dir(now.wind_dir)} {_e(now.wind_scale)}级'
            f'{"" if now.wind_speed is None else f"（{now.wind_speed:.1f} m/s）"}',
        ),
        _kv_row("相对湿度", f"{_fmt(now.humidity, ' %', 0)}"),
        _kv_row("降水量", f"{_fmt(now.precip, ' mm', 1)}"),
        _kv_row("能见度", f"{_fmt(now.vis, ' km', 1)}"),
        _kv_row("气压", f"{_fmt(now.pressure, ' hPa', 1)}"),
    ]
    if now.uv_index is not None:
        rows.append(_kv_row("紫外线", f"{now.uv_index} 级"))
    rows.append(
        _kv_row(
            "观测时间",
            f'<span style="color:{COLOR_MUTED};font-size:12px;">{obs}</span>',
        )
    )
    return (
        f'<div style="background:#ffffff;border:1px solid {COLOR_BORDER};'
        f'border-radius:8px;padding:16px 18px;margin-bottom:14px;">'
        f'{_section_title("实时天气")}'
        f'<table style="width:100%;border-collapse:collapse;">'
        f'{"".join(rows)}</table></div>'
    )


def _render_tomorrow(tomorrow: Optional[DailyForecast]) -> str:
    if tomorrow is None:
        return ""
    rows = [
        _kv_row(
            "日期",
            f'{_e(tomorrow.fx_date)} '
            f'<span style="color:{COLOR_MUTED};font-size:12px;">'
            f'{_e(tomorrow.weekday)}</span>',
        ),
        _kv_row(
            "天气",
            f'{_weather_emoji(tomorrow.text_day, tomorrow.code_day)} '
            f'白天 {_e(tomorrow.text_day)} / 夜间 {_e(tomorrow.text_night)}',
        ),
        _kv_row(
            "气温",
            f'<strong style="font-size:17px;color:{COLOR_NORMAL};">'
            f'{_e(tomorrow.temp_range)}</strong>',
        ),
        _kv_row("风力", _wind_text(tomorrow)),
        _kv_row("相对湿度", f"{_fmt(tomorrow.humidity, ' %', 0)}"),
        _kv_row("降水量", f"{_fmt(tomorrow.precip, ' mm', 1)}"),
    ]
    if tomorrow.precip_probability is not None:
        rows.append(
            _kv_row("降水概率", f"{tomorrow.precip_probability:.0f} %")
        )
    if tomorrow.sunrise or tomorrow.sunset:
        rows.append(
            _kv_row(
                "日出 / 日落",
                f"{_e(tomorrow.sunrise)} / {_e(tomorrow.sunset)}",
            )
        )
    if tomorrow.uv_index is not None:
        rows.append(_kv_row("紫外线", f"{tomorrow.uv_index} 级"))

    return (
        f'<div style="background:#ffffff;border:1px solid {COLOR_BORDER};'
        f'border-radius:8px;padding:16px 18px;margin-bottom:14px;">'
        f'{_section_title("明日天气预报")}'
        f'<table style="width:100%;border-collapse:collapse;">'
        f'{"".join(rows)}</table></div>'
    )


def _render_temp_change(
    change: Optional[Dict[str, Any]], current_temp: Optional[float] = None
) -> str:
    if not change or not change.get("available"):
        return ""
    delta = change.get("delta")
    prev_temp = change.get("prev_temp")
    day = change.get("prev_date", "")
    gap = change.get("days_gap", 1)

    if delta is None:
        return ""

    if delta > 0:
        color, arrow, word = COLOR_DANGER, "&#9650;", "回升"
    elif delta < 0:
        color, arrow, word = COLOR_NORMAL, "&#9660;", "下降"
    else:
        color, arrow, word = COLOR_MUTED, "&#8212;", "持平"

    gap_note = "较上次记录" if gap <= 1 else f"较 {gap} 天前（{_e(day)}）"
    return (
        f'<div style="background:#ffffff;border:1px solid {COLOR_BORDER};'
        f'border-radius:8px;padding:16px 18px;margin-bottom:14px;">'
        f'{_section_title("气温变化")}'
        f'<table style="width:100%;border-collapse:collapse;">'
        f'{_kv_row("上次记录", f"{_fmt(prev_temp, chr(8451), 1)}（{_e(day)}）")}'
        f'{_kv_row("本次温度", f"{_fmt(current_temp, chr(8451), 1)}")}'
        f'</table>'
        f'<div style="margin-top:12px;padding:11px 13px;background:{COLOR_BG};'
        f'border-radius:6px;font-size:14px;color:{COLOR_TEXT};">'
        f'<span style="color:{color};font-weight:700;font-size:16px;">'
        f'{arrow} {abs(delta):.1f}℃</span>'
        f'<span style="color:{COLOR_MUTED};font-size:12px;"> '
        f'气温{word}，{gap_note}</span></div></div>'
    )


def _render_alerts(alerts: List[Alert]) -> str:
    if not alerts:
        return ""
    cards = []
    for item in alerts:
        if item.level == "danger":
            color, bg = COLOR_DANGER, COLOR_DANGER_BG
            badge = "紧急"
        else:
            color, bg = COLOR_WARNING, COLOR_WARNING_BG
            badge = "注意"
        advice = (
            f'<div style="margin-top:9px;padding:9px 11px;background:#ffffff;'
            f'border-radius:5px;font-size:13px;color:{COLOR_TEXT};'
            f'border-left:3px solid {color};">'
            f'<strong>建议：</strong>{_e(item.advice)}</div>'
            if item.advice
            else ""
        )
        cards.append(
            f'<div style="background:{bg};border-radius:7px;padding:14px 16px;'
            f'margin-bottom:10px;border:1px solid {color}33;">'
            f'<div style="font-size:15px;font-weight:700;color:{color};'
            f'margin-bottom:6px;">'
            f'<span style="display:inline-block;background:{color};color:#fff;'
            f'font-size:11px;padding:2px 7px;border-radius:9px;margin-right:7px;'
            f'vertical-align:middle;">{badge}</span>{_e(item.title)}</div>'
            f'<div style="font-size:13px;color:{COLOR_TEXT};line-height:1.65;">'
            f'{_e(item.detail)}</div>{advice}</div>'
        )
    return (
        f'<div style="margin-bottom:14px;">'
        f'{_section_title("预警提示")}'
        f'{"".join(cards)}</div>'
    )


# ------------------------------------------------------------ 主渲染


def render_email(
    result: AlertResult,
    cfg: Dict[str, Any],
    fetched_at: Optional[datetime] = None,
) -> str:
    """渲染整封 HTML 邮件正文。"""
    fetched_at = fetched_at or datetime.now()
    location = cfg.get("location", {}).get("name", "北京市")
    has_alert = result.has_alert

    if has_alert:
        banner_bg = COLOR_DANGER
        banner_title = "天气预警"
        banner_sub = result.summary
    else:
        banner_bg = COLOR_NORMAL
        banner_title = "明日天气简报"
        banner_sub = result.summary

    date_note = fetched_at.strftime("%Y年%m月%d日 %H:%M")

    parts = [
        '<!DOCTYPE html>',
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{_e(location)}天气{'预警' if has_alert else '简报'}</title>",
        "</head>",
        f'<body style="margin:0;padding:0;background:{COLOR_BG};'
        f'-webkit-text-size-adjust:100%;">',
        # 外层容器
        f'<div style="max-width:620px;margin:0 auto;padding:18px 14px;'
        f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\','
        f'\'PingFang SC\',\'Hiragino Sans GB\',\'Microsoft YaHei\','
        f'\'Helvetica Neue\',Arial,sans-serif;">',
        # 顶部横幅
        f'<div style="background:{banner_bg};border-radius:9px 9px 0 0;'
        f'padding:20px 22px;color:#ffffff;">'
        f'<div style="font-size:20px;font-weight:700;letter-spacing:1px;">'
        f'{_e(location)} · {banner_title}</div>'
        f'<div style="font-size:13px;opacity:0.92;margin-top:7px;'
        f'line-height:1.5;">{_e(banner_sub)}</div>'
        f'<div style="font-size:12px;opacity:0.78;margin-top:9px;">'
        f'生成时间 {date_note}</div>'
        f'</div>',
        # 内容主体
        f'<div style="background:{COLOR_BG};padding:16px 0 4px 0;">',
    ]

    if has_alert:
        parts.append(_render_alerts(result.alerts))
    parts.append(_render_tomorrow(result.tomorrow))
    parts.append(_render_now(result.now))
    if cfg.get("temperature", {}).get("change_enabled", True):
        current_temp = result.now.temp if result.now else None
        parts.append(_render_temp_change(result.temp_change, current_temp))

    # 页脚
    api_note = result.now.raw_source if result.now else ""
    parts.extend(
        [
            "</div>",
            f'<div style="border-radius:0 0 9px 9px;background:#ffffff;'
            f'border:1px solid {COLOR_BORDER};border-top:none;'
            f'padding:14px 18px;font-size:11px;color:{COLOR_MUTED};'
            f'line-height:1.7;">'
            f'本邮件由 weatheremail 程序自动发送，数据来源：和风天气'
            f'{"（" + _e(api_note) + "）" if api_note else ""}。<br>'
            f'如需调整预警条件或收件人，请在终端运行 '
            f'<code style="background:{COLOR_BG};padding:1px 5px;'
            f'border-radius:3px;">weatheremail</code> 进入设置。<br>'
            f'本邮件为自动通知，请勿直接回复。</div>',
            "</div>",
            "</body></html>",
        ]
    )
    return "".join(parts)


def render_subject(
    result: AlertResult, cfg: Dict[str, Any], fetched_at: Optional[datetime] = None
) -> str:
    """构造邮件主题。"""
    fetched_at = fetched_at or datetime.now()
    prefix = cfg.get("subject_prefix", "【天气邮件】")
    location = cfg.get("location", {}).get("name", "北京市")
    day = fetched_at.strftime("%m-%d")

    if not result.has_alert:
        from .alert import tomorrow_highlight

        return f"{prefix}{day} {tomorrow_highlight(result.tomorrow)}"

    if result.highest_level == "danger":
        tag = "【预警】"
    else:
        tag = "【提醒】"
    # 主题里只放最重要的 2 条，避免过长
    titles = "、".join(a.title for a in result.alerts[:2])
    if len(result.alerts) > 2:
        titles += f" 等 {len(result.alerts)} 项"
    return f"{prefix}{tag}{location}{titles}"
