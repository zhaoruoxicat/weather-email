"""预警判断：恶劣天气识别 + 大幅升降温识别.

判断对象是「明天的预报」，因为邮件的作用是提前告知次日天气，
让收件人有机会做准备。温度升降对比则基于实况记录（今天 vs 上次运行）。

天气现象既看文字描述，也看和风天气的图标代码，双保险。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .net import DailyForecast, WeatherNow

# ------------------------------------------------------------ 天气代码表
# 和风天气图标代码 -> 类别（新旧版代码一致）
# 参考 https://dev.qweather.com/docs/resource/icons/

_RAIN_CODES = {
    "300", "301", "302", "303", "304", "305", "306", "307", "308",
    "309", "310", "311", "312", "313", "314", "315", "316", "317",
    "318", "350", "351", "399",
}
_SNOW_CODES = {
    "400", "401", "402", "403", "404", "405", "406", "407", "408",
    "409", "410", "456", "457", "499",
}
_HAIL_CODES = {"404", "405", "406"}
_FOG_CODES = {"501", "502", "509", "510", "511", "512", "513", "514", "515"}

_RAIN_WORDS = ("雨", "雷阵雨", "阵雨", "毛毛雨")
_SNOW_WORDS = ("雪", "雨夹雪", "阵雪", "米雪")
_HAIL_WORDS = ("冰雹", "冰粒", "冻雨", "霰")
_FOG_WORDS = ("雾", "霾", "沙尘", "浮尘", "扬沙")
_WIND_WORDS = ("大风", "狂风", "飓风", "台风", "龙卷")


@dataclass
class Alert:
    """单条预警。"""

    kind: str          # severe / temp_drop / temp_rise
    level: str         # danger / warning
    title: str
    detail: str
    advice: str = ""

    @property
    def is_temp(self) -> bool:
        return self.kind in ("temp_drop", "temp_rise")


@dataclass
class AlertResult:
    """全部预警的判断结果。"""

    alerts: List[Alert]
    summary: str = ""
    tomorrow: Optional[DailyForecast] = None
    today: Optional[DailyForecast] = None
    now: Optional[WeatherNow] = None
    temp_change: Optional[Dict[str, Any]] = None

    @property
    def has_alert(self) -> bool:
        return bool(self.alerts)

    @property
    def highest_level(self) -> str:
        for level in ("danger", "warning"):
            if any(a.level == level for a in self.alerts):
                return level
        return "info"


# ------------------------------------------------------------ 工具


def _scale_value(text: str) -> Optional[int]:
    """从 '3-4' / '6' / '<3' 这类风级文本里取最大整数。"""
    if text is None:
        return None
    digits = []
    current = ""
    for ch in str(text):
        if ch.isdigit():
            current += ch
        else:
            if current:
                digits.append(int(current))
                current = ""
    if current:
        digits.append(int(current))
    return max(digits) if digits else None


def _classify(code: str, text: str) -> set:
    """判断某一天/某时段属于哪些天气类别。"""
    cats: set = set()
    code = str(code or "")
    text = str(text or "")

    if code in _HAIL_CODES or any(w in text for w in _HAIL_WORDS):
        cats.add("hail")
    if code in _SNOW_CODES or any(w in text for w in _SNOW_WORDS):
        cats.add("snow")
    if code in _RAIN_CODES or any(w in text for w in _RAIN_WORDS):
        cats.add("rain")
    if code in _FOG_CODES or any(w in text for w in _FOG_WORDS):
        cats.add("fog")
    if any(w in text for w in _WIND_WORDS):
        cats.add("wind")
    return cats


# ------------------------------------------------------------ 主判断


def evaluate(
    now: Optional[WeatherNow],
    tomorrow: Optional[DailyForecast],
    today: Optional[DailyForecast],
    temp_change: Optional[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> AlertResult:
    """产出全部预警。"""
    alerts: List[Alert] = []
    severe_cfg = cfg.get("severe", {})
    temp_cfg = cfg.get("temperature", {})

    # ---------------------------------------------- 恶劣天气（看明天）
    if severe_cfg.get("enabled", True) and tomorrow is not None:
        alerts.extend(_severe_alerts(tomorrow, severe_cfg))

    # ---------------------------------------------- 大范围升降温
    if temp_cfg.get("change_enabled", True) and temp_change:
        alerts.extend(
            _temperature_alerts(temp_change, temp_cfg, tomorrow=tomorrow)
        )

    # ---------------------------------------------- 摘要
    if alerts:
        titles = "、".join(a.title for a in alerts)
        summary = f"共 {len(alerts)} 项提醒：{titles}"
    else:
        summary = "未来 24 小时无恶劣天气，气温变化平稳"

    return AlertResult(
        alerts=alerts,
        summary=summary,
        tomorrow=tomorrow,
        today=today,
        now=now,
        temp_change=temp_change,
    )


def _severe_alerts(
    tomorrow: DailyForecast, severe_cfg: Dict[str, Any]
) -> List[Alert]:
    alerts: List[Alert] = []

    # 合并白天与夜间，白天用 day 代码，夜间用 night 代码
    day_cats = _classify(tomorrow.code_day, tomorrow.text_day)
    night_cats = _classify(tomorrow.code_night, tomorrow.text_night)
    cats = day_cats | night_cats

    def when(cat: str) -> str:
        in_day = cat in day_cats
        in_night = cat in night_cats
        if in_day and in_night:
            return "全天"
        if in_day:
            return "白天"
        return "夜间"

    text_all = f"{tomorrow.text_day}/{tomorrow.text_night}"

    # 冰雹优先级最高
    if "hail" in cats and severe_cfg.get("hail", True):
        alerts.append(
            Alert(
                kind="severe",
                level="danger",
                title="明天有冰雹/冻雨",
                detail=f"明日天气：{text_all}（{when('hail')}可能伴冰雹或冻雨）",
                advice="请提前收好车辆、遮盖货物与农作物，避免人员露天作业。",
            )
        )

    if "snow" in cats and severe_cfg.get("snow", True):
        alerts.append(
            Alert(
                kind="severe",
                level="warning",
                title="明天有降雪",
                detail=f"明日天气：{text_all}（{when('snow')}有雪或雨夹雪）",
                advice="注意道路结冰湿滑，出行请减速慢行，做好防寒保暖。",
            )
        )

    if "rain" in cats and severe_cfg.get("rain", True):
        # 若配置了降水概率门槛，则只在达到门槛时才提示
        threshold = severe_cfg.get("rain_probability")
        prob = tomorrow.precip_probability
        can_warn = True
        prob_note = ""
        if threshold is not None and prob is not None:
            if prob < float(threshold):
                can_warn = False
            else:
                prob_note = f"，降水概率 {prob:.0f}%"
        if can_warn:
            heavy = "雨" in text_all and any(
                w in text_all for w in ("暴雨", "大雨", "大暴雨", "雷阵雨")
            )
            alerts.append(
                Alert(
                    kind="severe",
                    level="danger" if heavy else "warning",
                    title="明天有降雨",
                    detail=(
                        f"明日天气：{text_all}（{when('rain')}有降水"
                        f"{prob_note}）"
                    ),
                    advice=(
                        "请携带雨具，注意低洼路段积水；露天货物建议提前苫盖。"
                        if not heavy
                        else "降雨较强，请避免外出，提前检查排水与货物遮盖。"
                    ),
                )
            )

    if "fog" in cats and severe_cfg.get("fog", True):
        alerts.append(
            Alert(
                kind="severe",
                level="warning",
                title="明天有雾/霾或沙尘",
                detail=f"明日天气：{text_all}（{when('fog')}能见度较低）",
                advice="能见度差，驾车请开启雾灯并保持车距，敏感人群减少外出。",
            )
        )

    # 大风：文字命中 或 风级达到门槛
    wind_enabled = severe_cfg.get("wind", True)
    if wind_enabled:
        threshold = int(severe_cfg.get("wind_scale", 6) or 6)
        day_scale = _scale_value(tomorrow.wind_scale_day)
        night_scale = _scale_value(tomorrow.wind_scale_night)
        max_scale = max(
            [v for v in (day_scale, night_scale) if v is not None] or [0]
        )
        if "wind" in cats or max_scale >= threshold:
            scale_note = (
                f"{max_scale} 级" if max_scale else tomorrow.wind_scale_day
            )
            alerts.append(
                Alert(
                    kind="severe",
                    level="warning" if max_scale < 8 else "danger",
                    title=f"明天有大风（{scale_note}）",
                    detail=(
                        f"明日天气：{text_all}；风力约 {scale_note}"
                        f"，风向 {tomorrow.wind_dir_day or '不定'}"
                    ),
                    advice="请固定广告牌、彩钢瓦与棚架，避免高空作业，注意行车侧风。",
                )
            )

    return alerts


def _temperature_alerts(
    change: Dict[str, Any],
    temp_cfg: Dict[str, Any],
    tomorrow: Optional[DailyForecast] = None,
) -> List[Alert]:
    alerts: List[Alert] = []
    if not change.get("available"):
        return alerts

    delta = change.get("delta")
    if delta is None:
        return alerts

    threshold = float(temp_cfg.get("change_threshold", 5.0) or 5.0)
    if abs(delta) < threshold:
        return alerts

    prev_date = change.get("prev_date", "上次记录")
    prev_temp = change.get("prev_temp")
    days_gap = change.get("days_gap", 1)
    gap_note = (
        "较上次同一时段"
        if days_gap <= 1
        else f"较 {days_gap} 天前（{prev_date}）"
    )

    if delta > 0:
        alerts.append(
            Alert(
                kind="temp_rise",
                level="warning",
                title=f"气温大幅回升 {delta:+.1f}℃",
                detail=(
                    f"当前实况温度较上次记录升高 {delta:.1f}℃"
                    f"（{prev_temp:.1f}℃ → {change.get('prev_temp', 0) + delta:.1f}℃）"
                    f"，{gap_note}，已超过 {threshold:.1f}℃ 阈值。"
                ),
                advice="升温明显，注意及时增减衣物；大棚与仓储需注意通风降温。",
            )
        )
    else:
        alerts.append(
            Alert(
                kind="temp_drop",
                level="danger" if abs(delta) >= threshold * 1.6 else "warning",
                title=f"气温大幅下降 {delta:+.1f}℃",
                detail=(
                    f"当前实况温度较上次记录下降 {abs(delta):.1f}℃"
                    f"（{prev_temp:.1f}℃ → {change.get('prev_temp', 0) + delta:.1f}℃）"
                    f"，{gap_note}，已超过 {threshold:.1f}℃ 阈值。"
                ),
                advice="降温剧烈，请及时添加衣物，注意防寒防冻，做好管道与设备保温。",
            )
        )
    return alerts


def tomorrow_highlight(tomorrow: Optional[DailyForecast]) -> str:
    """给邮件主题用的一句话概括。"""
    if tomorrow is None:
        return "明日天气"
    return (
        f"明日 {tomorrow.text_day} "
        f"{'--' if tomorrow.temp_min is None else f'{tomorrow.temp_min:.0f}'}"
        f"~{'--' if tomorrow.temp_max is None else f'{tomorrow.temp_max:.0f}'}℃"
    )
