#!/usr/bin/env python3
"""生成两版邮件预览 HTML，方便直观查看邮件外观。

不联网，用示例数据渲染：
    samples/预警邮件示例.html
    samples/普通简报示例.html

用法： python3 tools/make-samples.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from weatheremail import alert, config, net, render  # noqa: E402

OUT = ROOT / "samples"
OUT.mkdir(exist_ok=True)

cfg = config.load_config()
cfg["location"]["name"] = "北京市"
NOW = datetime(2026, 10, 7, 16, 25)


def banner(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")


# ---------------------------------------------------------- 预警邮件
banner("生成预警邮件示例")

now_obj = net.WeatherNow(
    obs_time="2026-10-07T16:00+08:00",
    temp=6.4, feels_like=2.1, text="阴", code="104",
    wind_dir="n", wind_scale="5", wind_speed=9.8,
    humidity=72.0, precip=0.0, pressure=1021.3, vis=12.0, uv_index=1,
)

tomorrow = net.DailyForecast(
    fx_date="2026-10-08", temp_max=9.0, temp_min=-1.0,
    text_day="中雨转雨夹雪", text_night="小雪", code_day="313", code_night="400",
    wind_dir_day="n", wind_scale_day="7",
    wind_dir_night="ssw", wind_scale_night="5",
    humidity=88.0, precip=14.2, precip_probability=92.0,
    uv_index=1, sunrise="06:12", sunset="17:38",
)

temp_change = {
    "available": True, "prev_date": "2026-10-06", "prev_temp": 15.8,
    "delta": -9.4, "days_gap": 1,
}

result_warn = alert.evaluate(
    now=now_obj, tomorrow=tomorrow, today=None, temp_change=temp_change, cfg=cfg
)
print(f"  触发预警 {len(result_warn.alerts)} 条：")
for a in result_warn.alerts:
    print(f"    · [{a.level}] {a.title}")
print(f"  邮件主题：{render.render_subject(result_warn, cfg, NOW)}")

warn_html = render.render_email(result_warn, cfg, NOW)
warn_path = OUT / "预警邮件示例.html"
warn_path.write_text(warn_html, encoding="utf-8")
print(f"  已写入 {warn_path}  ({len(warn_html.encode('utf-8'))/1024:.1f} KB)")


# ---------------------------------------------------------- 普通简报
banner("生成普通简报邮件示例")

now_plain = net.WeatherNow(
    obs_time="2026-10-07T16:00+08:00",
    temp=19.2, feels_like=18.4, text="晴", code="100",
    wind_dir="sw", wind_scale="2", wind_speed=3.1,
    humidity=46.0, precip=0.0, pressure=1013.6, vis=25.0, uv_index=4,
)

tomorrow_plain = net.DailyForecast(
    fx_date="2026-10-08", temp_max=23.0, temp_min=11.0,
    text_day="晴", text_night="多云", code_day="100", code_night="101",
    wind_dir_day="ssw", wind_scale_day="2",
    wind_dir_night="nne", wind_scale_night="1",
    humidity=52.0, precip=0.0, precip_probability=0.0,
    uv_index=5, sunrise="06:13", sunset="17:37",
)

temp_plain = {
    "available": True, "prev_date": "2026-10-06", "prev_temp": 18.6,
    "delta": 0.6, "days_gap": 1,
}

result_plain = alert.evaluate(
    now=now_plain, tomorrow=tomorrow_plain, today=None,
    temp_change=temp_plain, cfg=cfg,
)
print(f"  触发预警 {len(result_plain.alerts)} 条（应为 0）")
print(f"  邮件主题：{render.render_subject(result_plain, cfg, NOW)}")

plain_html = render.render_email(result_plain, cfg, NOW)
plain_path = OUT / "普通简报示例.html"
plain_path.write_text(plain_html, encoding="utf-8")
print(f"  已写入 {plain_path}  ({len(plain_html.encode('utf-8'))/1024:.1f} KB)")

print("\n完成。用浏览器打开 samples/ 下的 HTML 即可查看邮件外观。")
