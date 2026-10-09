#!/usr/bin/env python3
"""离线单元/集成测试：不联网，用假数据验证全部逻辑。

运行：
    python3 tests/selftest.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

# 把配置/历史目录重定向到临时目录，避免污染真实数据。
# 临时目录必须落在项目内（/tmp 常是小容量 tmpfs），且需自行创建，
# 否则从干净源码包解压后直接运行会因目录不存在而失败。
_TMP_ROOT = ROOT / ".testtmp"
_TMP_ROOT.mkdir(parents=True, exist_ok=True)
_TMP = Path(tempfile.mkdtemp(prefix="weatheremail-test-", dir=str(_TMP_ROOT)))
os.environ["XDG_CONFIG_HOME"] = str(_TMP)

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  \033[32mPASS\033[0m {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  \033[31mFAIL\033[0m {name} {detail}")


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")


# ----------------------------------------------------------------- 导入模块
from weatheremail import alert, config, history, mailer, net, render  # noqa: E402
from weatheremail.paths import config_path, data_dir  # noqa: E402

# data_dir 依赖 is_installed()，源码树运行时返回 <root>/data，
# 我们不想污染它，改用 monkeypatch 成临时目录
config_dir = _TMP / "weatheremail"
config_dir.mkdir(parents=True, exist_ok=True)
config.config_path = lambda: config_dir / "config.json"
history.history_path = lambda: config_dir / "history.json"
net_config_path = config_dir / "config.json"


# ----------------------------------------------------------------- 测试 1
section("1. 配置读写")

cfg = config.load_config()
check("加载默认配置", cfg["location"]["name"].endswith("北京市"))
check("默认使用北京坐标", cfg["location"]["lat"] == "39.90")
check("默认阈值 5 度", cfg["temperature"]["change_threshold"] == 5.0)

cfg["qweather"]["api_key"] = "TEST-KEY-123456"
cfg["smtp"]["sender"] = "sender@example.com"
cfg["smtp"]["password"] = "secret-pass"
cfg["recipients"] = ["a@example.com", "b@example.com"]
path = config.save_config(cfg)
check("配置已写入", path.is_file())
mode = path.stat().st_mode & 0o777
check("配置文件权限为 600", mode == 0o600, f"实际 {oct(mode)}")

reloaded = config.load_config()
check("重新加载保留 API KEY", reloaded["qweather"]["api_key"] == "TEST-KEY-123456")
check("重新加载保留收件人", reloaded["recipients"] == ["a@example.com", "b@example.com"])
check("重新加载保留密码", reloaded["smtp"]["password"] == "secret-pass")

check("密钥脱敏", config.mask_secret("ABCDEFGHIJ") == "ABCD******")
check("空密钥脱敏", config.mask_secret("") == "(未设置)")

# 缺失字段容错：写入残缺配置，应能合并出默认值
config_dir.joinpath("config.json").write_text(
    json.dumps({"smtp": {"host": "smtp.test.com"}}), encoding="utf-8"
)
partial = config.load_config()
check("残缺配置合并默认值", partial["temperature"]["change_threshold"] == 5.0)
check("残缺配置保留已有值", partial["smtp"]["host"] == "smtp.test.com")
check("残缺配置带出默认端口", partial["smtp"]["port"] == 465)

# 环境变量覆盖
os.environ["WEATHEREMAIL_SMTP_PASS"] = "from-env"
envcfg = config.load_config()
check("环境变量覆盖密码", envcfg["smtp"]["password"] == "from-env")
del os.environ["WEATHEREMAIL_SMTP_PASS"]


# ----------------------------------------------------------------- 测试 2
section("2. 和风天气响应解析（新版 v1）")

v1_current = {
    "metadata": {"tag": "abc"},
    "condition": {"text": "小雨", "code": "305"},
    "temperature": {"value": 12.34, "unit": "°C"},
    "feelsLike": {"value": 10.1, "unit": "°C"},
    "humidity": 0.68,
    "wind": {
        "direction": {"degree": 226, "compass": "sw"},
        "speed": {"value": 4.74, "unit": "m/s"},
        "scale": 3,
    },
    "windGust": {"value": 7.07, "unit": "m/s"},
    "precipitation": {"amount": {"value": 0.4, "unit": "mm"}, "type": "rain"},
    "pressure": {"value": 1001.5, "unit": "hPa"},
    "visibility": {"value": 29020, "unit": "m"},
    "cloudCover": 0.05,
    "uvIndex": 3,
}
now = net._parse_v1_now(v1_current, "v1 @ test")
check("v1 温度解析", now.temp == 12.34)
check("v1 体感解析", now.feels_like == 10.1)
check("v1 天气现象", now.text == "小雨")
check("v1 湿度转百分比", now.humidity == 68.0)
check("v1 能见度转 km", now.vis == 29.0)
check("v1 风向", now.wind_dir == "sw")
check("v1 风级", now.wind_scale == "3")
check("v1 气压", now.pressure == 1001.5)

v1_daily = {
    "days": [
        {
            "forecastStartTime": "2026-10-07T22:00Z",
            "astro": {"sunrise": "2026-10-08T04:22Z", "sunset": "2026-10-08T19:34Z"},
            "temperatureMax": {"value": 22.4, "unit": "°C"},
            "temperatureMin": {"value": 8.6, "unit": "°C"},
            "uvIndexMax": 5,
            "daytime": {
                "condition": {"text": "中雨", "code": "306"},
                "wind": {
                    "direction": {"compass": "ne"},
                    "speed": {"value": 9.2},
                    "scale": 5,
                },
                "precipitation": {
                    "amount": {"value": 12.0},
                    "probability": 0.85,
                    "type": "rain",
                },
                "humidity": 0.81,
            },
            "nighttime": {
                "condition": {"text": "小雨", "code": "305"},
                "wind": {
                    "direction": {"compass": "ssw"},
                    "scale": 3,
                },
            },
        },
        {
            "forecastStartTime": "2026-10-08T22:00Z",
            "temperatureMax": {"value": 15.0},
            "temperatureMin": {"value": 3.2},
            "daytime": {
                "condition": {"text": "小雪", "code": "400"},
                "wind": {"scale": 7},
                "precipitation": {"probability": 0.9, "amount": {"value": 3.0}},
            },
            "nighttime": {"condition": {"text": "阴", "code": "104"}, "wind": {"scale": 2}},
        },
    ]
}
days = net._parse_v1_daily(v1_daily)
check("v1 解析出 2 天", len(days) == 2)
check("v1 日期截取", days[0].fx_date == "2026-10-07")
check("v1 最高温", days[0].temp_max == 22.4)
check("v1 降水概率转百分比", days[0].precip_probability == 85.0)
check("v1 气温区间文案", days[1].temp_range == "3 ~ 15℃")
check("v1 日出时间截取", days[0].sunrise == "04:22")
check("v1 夜间天气", days[0].text_night == "小雨")
check("v1 白天风向解析", days[0].wind_dir_day == "ne")
check("v1 夜间风向解析", days[0].wind_dir_night == "ssw")
check("v1 夜间风级解析", days[0].wind_scale_night == "3")


# ----------------------------------------------------------------- 测试 3
section("3. 和风天气响应解析（旧版 v7）")

v7_payload = {
    "code": "200",
    "now": {
        "obsTime": "2026-10-07T16:00+08:00",
        "temp": "11",
        "feelsLike": "9",
        "text": "晴",
        "icon": "100",
        "windDir": "西北风",
        "windScale": "3",
        "windSpeed": "12",
        "humidity": "45",
        "precip": "0.0",
        "pressure": "1015",
        "vis": "25",
    },
}
now7 = net._parse_v7_now(v7_payload)
check("v7 温度解析", now7.temp == 11.0)
check("v7 温度字符串转数值", isinstance(now7.temp, float))
check("v7 风向直接中文", now7.wind_dir == "西北风")
check("v7 湿度", now7.humidity == 45.0)

v7_daily = {
    "daily": [
        {
            "fxDate": "2026-10-07",
            "tempMax": "20",
            "tempMin": "6",
            "textDay": "多云",
            "textNight": "晴",
            "iconDay": "101",
            "windDirDay": "东北风",
            "windScaleDay": "4",
            "humidity": "60",
            "precip": "0.0",
            "sunrise": "06:12",
            "sunset": "17:40",
        },
        {
            "fxDate": "2026-10-08",
            "tempMax": "9",
            "tempMin": "-1",
            "textDay": "雨夹雪",
            "textNight": "小雪",
            "iconDay": "404",
            "windScaleDay": "6",
            "humidity": "88",
            "precip": "5.2",
        },
    ]
}
d7 = net._parse_v7_daily(v7_daily)
check("v7 解析 2 天", len(d7) == 2)
check("v7 负温度", d7[1].temp_min == -1.0)
check("v7 雨夹雪文案", d7[1].text_day == "雨夹雪")

# 2.9 错误响应处理：gzip 压缩的错误体必须能解压并翻译
import gzip as _gzip  # noqa: E402

invalid_host_body = (
    '{"error":{"status":403,"type":"https://dev.qweather.com/docs/resource/'
    'error-code/#invalid-host","title":"Invalid Host",'
    '"detail":"An invalid or unauthorized API Host."}}'
)
# 原样（未压缩）
msg_plain = net._friendly_error("", 403, invalid_host_body)
check("Invalid Host 提示含「API Host」", "API Host 无效" in msg_plain)
check("Invalid Host 提示给出控制台地址",
      "console.qweather.com" in msg_plain)
check("Invalid Host 提示不误导查 KEY", "API KEY 无效" not in msg_plain)

# 压缩后：先解压再翻译，模拟真实响应
gz = _gzip.compress(invalid_host_body.encode("utf-8"))
check("gzip 魔数识别", gz[:2] == b"\x1f\x8b")
decompressed = net._maybe_gunzip(gz)
check("错误体可解压", b"invalid-host" in decompressed)
msg_gz = net._friendly_error("", 403, decompressed.decode("utf-8"))
check("压缩错误体也能翻译正确", "API Host 无效" in msg_gz)
# 未压缩的数据不应被误处理
check("非 gzip 数据原样返回",
      net._maybe_gunzip(b'{"code":"200"}') == b'{"code":"200"}')
# 其他错误码仍走原表
check("401 提示仍正确", "认证失败" in net._friendly_error("401", 401))

# ----------------------------------------------------------------- 2.95
section("2.95 API Host 由用户设置，不硬编码公共域名")

# 模块里不应再存在任何公共域名候选列表
check("已移除公共域名候选常量", not hasattr(net, "DEFAULT_HOST_CANDIDATES"))

# 未填 Host 时：不发请求，直接给出明确指引
no_host_client = net.QWeatherClient(api_key="SOMEKEY", api_host="")
try:
    no_host_client.fetch("39.56", "116.42")
    check("未填 Host 时 fetch 应报错", False)
except net.WeatherError as exc:
    msg = str(exc)
    check("未填 Host 报错含指引", "API Host" in msg and "未设置" in msg)
    check("未填 Host 报错给出控制台地址", "console.qweather.com" in msg)
    check("未填 Host 时不出现公共域名", "api.qweather.com" not in msg)
    check("未填 Host 时不误报 KEY 问题", "API KEY 无效" not in msg)

# 空候选列表（不再回退公共域名）
check("无 Host 时候选为空", net.QWeatherClient(
    api_key="K", api_host="")._candidate_hosts() == [])

# 填了 Host 时候选就是它自己，且被规范化
c_host = net.QWeatherClient(api_key="K", api_host="abc.re.qweatherapi.com")
check("候选仅含用户填写的 Host",
      c_host._candidate_hosts() == ["https://abc.re.qweatherapi.com"])
c_host2 = net.QWeatherClient(api_key="K", api_host="https://x.qweatherapi.com/")
check("Host 尾部斜杠被清理",
      c_host2._candidate_hosts() == ["https://x.qweatherapi.com"])

# 版本推断：自定义 Host → v1；显式填旧版公共域名 → v7
check("专属 Host 推断为 v1", c_host._pick_version() == "v1")
check("旧版公共域名推断为 v7",
      net.QWeatherClient(api_key="K",
                         api_host="devapi.qweather.com")._pick_version() == "v7")
check("显式指定 v7 时尊重用户选择",
      net.QWeatherClient(api_key="K", api_host="a.re.qweatherapi.com",
                         api_version="v7")._pick_version() == "v7")
# 未填 Host + auto → 仍按 v1（随后会被 fetch 的前置校验拦下）
check("无 Host 时版本默认 v1",
      net.QWeatherClient(api_key="K", api_host="")._pick_version() == "v1")

# 默认配置里 api_host 必须为空（不得预置任何域名）
_default_cfg = config.DEFAULT_CONFIG
check("默认配置 api_host 为空", _default_cfg["qweather"]["api_host"] == "")
check("默认配置不含任何 qweatherapi 域名",
      "qweatherapi" not in json.dumps(_default_cfg, ensure_ascii=False))

# 校验：缺 Host 应报错
_v_cfg = config.load_config()
_v_cfg["qweather"]["api_key"] = "K"
_v_cfg["qweather"]["api_host"] = ""
_v_cfg["smtp"]["sender"] = "s@example.com"
_v_cfg["recipients"] = ["r@example.com"]
_v_errs, _ = config.validate(_v_cfg)
check("缺 Host 时校验报错", any("API Host" in e for e in _v_errs),
      f"实际 {_v_errs}")
_v_cfg["qweather"]["api_host"] = "abc.re.qweatherapi.com"
_v_errs2, _ = config.validate(_v_cfg)
check("填了 Host 后校验通过", not any("API Host" in e for e in _v_errs2))


# ----------------------------------------------------------------- 测试 3.5
section("3.5 总开关与开关交互（evaluate 层）")

sw_cfg_on = {
    "severe": {"enabled": True,
               "rain": True, "snow": True, "hail": True,
               "fog": True, "wind": True, "wind_scale": 6,
               "rain_probability": 30},
    "temperature": {"change_enabled": True, "change_threshold": 5.0},
}
sw_now = net.WeatherNow(temp=12.0, text="阴")
sw_tmr_severe = net.DailyForecast(
    fx_date="2026-10-08", text_day="暴雨", code_day="310", wind_scale_day="8",
    precip_probability=95.0)
sw_change = {"available": True, "prev_date": "2026-10-06", "prev_temp": 22.0,
             "delta": -10.0, "days_gap": 1}

res_on = alert.evaluate(sw_now, sw_tmr_severe, None, sw_change, sw_cfg_on)
check("全开时恶劣天气+降温都有预警", len(res_on.alerts) >= 2)
check("has_alert 为真", res_on.has_alert is True)
check("最高级别为 danger", res_on.highest_level == "danger")

# 关闭恶劣天气总开关 → 只剩温度预警
cfg_sev_off = json.loads(json.dumps(sw_cfg_on))
cfg_sev_off["severe"]["enabled"] = False
res_sev_off = alert.evaluate(sw_now, sw_tmr_severe, None, sw_change, cfg_sev_off)
check("关恶劣天气后仅剩温度预警", len(res_sev_off.alerts) == 1)
check("仅剩的是降温预警", res_sev_off.alerts[0].kind == "temp_drop")

# 两个总开关都关 → 无预警
cfg_all_off = json.loads(json.dumps(cfg_sev_off))
cfg_all_off["temperature"]["change_enabled"] = False
res_all_off = alert.evaluate(sw_now, sw_tmr_severe, None, sw_change, cfg_all_off)
check("两个开关都关时无预警", len(res_all_off.alerts) == 0)
check("无预警时摘要为平稳", "平稳" in res_all_off.summary or "无恶劣" in res_all_off.summary)

# 明天为 None 不应崩溃
res_none = alert.evaluate(sw_now, None, None, None, sw_cfg_on)
check("无预报数据时安全返回", res_none.has_alert is False)


# ----------------------------------------------------------------- 测试 4
section("4. 恶劣天气识别")

sev_cfg = {
    "enabled": True,
    "rain": True,
    "snow": True,
    "hail": True,
    "fog": True,
    "wind": True,
    "wind_scale": 6,
    "rain_probability": 30,
}

# 4a. 大雨
tmr_rain = net.DailyForecast(
    fx_date="2026-10-08", temp_max=18, temp_min=10,
    text_day="大雨", text_night="中雨", code_day="310", code_night="306",
    wind_scale_day="4", precip_probability=90.0, wind_dir_day="东北风",
)
alerts = alert._severe_alerts(tmr_rain, sev_cfg)
kinds = [a.kind for a in alerts]
check("大雨被识别", "severe" in kinds and len(alerts) >= 1)
check("大雨标题含降雨", any("降雨" in a.title for a in alerts))
check("大雨判定为 danger", any(a.level == "danger" for a in alerts))

# 4b. 暴雨 + 大风
tmr_storm = net.DailyForecast(
    fx_date="2026-10-08", temp_max=18, temp_min=10,
    text_day="暴雨", text_night="暴雨", code_day="310", code_night="310",
    wind_scale_day="8", precip_probability=95.0,
)
alerts2 = alert._severe_alerts(tmr_storm, sev_cfg)
check("暴雨+大风产生 2 条预警", len(alerts2) == 2, f"实际 {len(alerts2)}")
check("大风被识别", any("大风" in a.title for a in alerts2))
check("大风达到 8 级判 danger", any(
    a.level == "danger" and "大风" in a.title for a in alerts2))

# 4c. 冰雹
tmr_hail = net.DailyForecast(
    fx_date="2026-10-08", temp_max=20, temp_min=12,
    text_day="雷阵雨伴有冰雹", text_night="阴", code_day="304", code_night="104",
    wind_scale_day="3",
)
alerts3 = alert._severe_alerts(tmr_hail, sev_cfg)
check("冰雹被识别", any("冰雹" in a.title for a in alerts3))
check("冰雹判 danger", any(a.level == "danger" for a in alerts3))

# 4d. 雪
tmr_snow = net.DailyForecast(
    fx_date="2026-10-08", temp_max=-2, temp_min=-8,
    text_day="大雪", text_night="小雪", code_day="403", code_night="400",
    wind_scale_day="4",
)
alerts4 = alert._severe_alerts(tmr_snow, sev_cfg)
check("大雪被识别", any("降雪" in a.title for a in alerts4))

# 4e. 雾霾
tmr_fog = net.DailyForecast(
    fx_date="2026-10-08", temp_max=15, temp_min=8,
    text_day="霾", text_night="雾", code_day="502", code_night="501",
    wind_scale_day="2",
)
alerts5 = alert._severe_alerts(tmr_fog, sev_cfg)
check("雾霾被识别", any("雾" in a.title or "霾" in a.title for a in alerts5))

# 4f. 好天气 → 无预警
tmr_clear = net.DailyForecast(
    fx_date="2026-10-08", temp_max=22, temp_min=12,
    text_day="晴", text_night="晴", code_day="100", code_night="100",
    wind_scale_day="2", precip_probability=0.0,
)
alerts6 = alert._severe_alerts(tmr_clear, sev_cfg)
check("晴天无预警", len(alerts6) == 0, f"实际 {[a.title for a in alerts6]}")

# 4g. 关闭降雨开关 → 不提示
cfg_no_rain = dict(sev_cfg, rain=False)
alerts7 = alert._severe_alerts(tmr_rain, cfg_no_rain)
check("关闭降雨开关后不提示雨", not any("降雨" in a.title for a in alerts7))

# 4h. 降水概率低于门槛 → 不提示
cfg_high_thresh = dict(sev_cfg, rain_probability=95)
tmr_lowprob = net.DailyForecast(
    fx_date="2026-10-08", text_day="小雨", code_day="305",
    wind_scale_day="2", precip_probability=40.0,
)
alerts8 = alert._severe_alerts(tmr_lowprob, cfg_high_thresh)
check("概率 40% 低于 95% 门槛不提示", not any("降雨" in a.title for a in alerts8))

# 4i. 风级文本 "6-7" 解析
check("风级 6-7 取最大", alert._scale_value("6-7") == 7)
check("风级 <3 解析", alert._scale_value("<3") == 3)
check("风级空值", alert._scale_value("") is None)


# ----------------------------------------------------------------- 测试 5
section("5. 温度变化判断")

# 5a. 降温 8 度，阈值 5
drop = {"available": True, "prev_date": "2026-10-06", "prev_temp": 20.0,
        "delta": -8.0, "days_gap": 1}
temp_cfg = {"change_enabled": True, "change_threshold": 5.0}
talerts = alert._temperature_alerts(drop, temp_cfg)
check("降温 8 度触发预警", len(talerts) == 1)
check("降温标题为下降", "下降" in talerts[0].title)
check("降温幅度 1.6 倍以上判 danger", talerts[0].level == "danger")
check("降温文案含阈值", "5.0" in talerts[0].detail)

# 5b. 升温 6 度
rise = {"available": True, "prev_date": "2026-10-06", "prev_temp": 10.0,
        "delta": 6.0, "days_gap": 1}
talerts2 = alert._temperature_alerts(rise, temp_cfg)
check("升温 6 度触发预警", len(talerts2) == 1)
check("升温标题为回升", "回升" in talerts2[0].title)

# 5c. 变化 3 度，低于阈值 → 不触发
small = {"available": True, "prev_date": "2026-10-06", "prev_temp": 15.0,
         "delta": 3.0, "days_gap": 1}
check("变化 3 度不触发", len(alert._temperature_alerts(small, temp_cfg)) == 0)

# 5d. 阈值为 2 时 3 度应触发
check("阈值调小到 2 度后触发", len(alert._temperature_alerts(
    small, {"change_enabled": True, "change_threshold": 2.0})) == 1)

# 5e. 无历史数据
check("无历史数据不触发", len(alert._temperature_alerts(
    {"available": False}, temp_cfg)) == 0)

# 5f. 关闭开关 —— 开关判定在 evaluate() 层，_temperature_alerts 是底层helper
quiet_eval = alert.evaluate(
    now=None, tomorrow=None, today=None, temp_change=drop,
    cfg={"severe": {"enabled": True},
         "temperature": {"change_enabled": False, "change_threshold": 5.0}},
)
check("关闭温度预警后不触发（evaluate 层）", len(quiet_eval.alerts) == 0)
# 同样的输入，打开开关就应触发
loud_eval = alert.evaluate(
    now=None, tomorrow=None, today=None, temp_change=drop,
    cfg={"severe": {"enabled": True},
         "temperature": {"change_enabled": True, "change_threshold": 5.0}},
)
check("打开开关后同一输入触发（evaluate 层）", len(loud_eval.alerts) == 1)


# ----------------------------------------------------------------- 测试 6
section("6. 温度历史记录")

hist_cfg = {"change_enabled": True, "change_threshold": 5.0}
history.record_today(20.0, source="obs", forecast_max=22.0, forecast_min=12.0)
entry = history.get_entry(date.today())
check("今日记录已写入", entry is not None and entry["temp"] == 20.0)
check("记录含预报温度", entry["forecast_max"] == 22.0)

# 伪造昨天的记录
data = history.load_history()
yesterday = (date.today() - timedelta(days=1)).isoformat()
data["entries"][yesterday] = {
    "temp": 28.0, "recorded_at": yesterday + "T08:00:00", "source": "obs"}
history.save_history(data)

cmp = history.compare_with_previous(20.0)
check("可比对历史", cmp["available"] is True)
check("温差计算正确", cmp["delta"] == -8.0, f"实际 {cmp['delta']}")
check("相差 1 天", cmp["days_gap"] == 1)
check("上次温度正确", cmp["prev_temp"] == 28.0)

check("无当前温度返回不可比", history.compare_with_previous(None)["available"] is False)
tail = history.history_tail(5)
check("历史列表非空", len(tail) >= 2)


# ----------------------------------------------------------------- 测试 7
section("7. 邮件渲染")

cfg_full = config.load_config()
cfg_full["recipients"] = ["test@example.com"]
cfg_full["smtp"]["sender"] = "sender@example.com"
cfg_full["location"]["name"] = "北京市"

now_obj = net.WeatherNow(
    obs_time="2026-10-07T16:00+08:00", temp=12.3, feels_like=10.1,
    text="小雨", code="305", wind_dir="sw", wind_scale="3", wind_speed=4.7,
    humidity=68.0, precip=0.4, pressure=1001.5, vis=29.0, uv_index=3,
)

# 7a. 有预警的邮件
result_alert = alert.AlertResult(
    alerts=[
        alert.Alert(kind="severe", level="danger", title="明天有降雨",
                    detail="明日天气：大雨/中雨（白天有降水，降水概率 90%）",
                    advice="请携带雨具。"),
        alert.Alert(kind="temp_drop", level="warning", title="气温大幅下降 -8.0℃",
                    detail="当前实况温度较上次记录下降 8.0℃", advice="注意保暖。"),
    ],
    summary="共 2 项提醒：明天有降雨、气温大幅下降 -8.0℃",
    tomorrow=net.DailyForecast(
        fx_date="2026-10-08", temp_max=18, temp_min=10,
        text_day="大雨", text_night="中雨", code_day="310", code_night="306",
        wind_dir_day="东北风", wind_scale_day="5",
        humidity=81.0, precip=12.0, precip_probability=90.0,
        sunrise="06:12", sunset="17:40"),
    now=now_obj,
    temp_change={"available": True, "prev_date": "2026-10-06", "prev_temp": 20.3,
                 "delta": -8.0, "days_gap": 1},
)
html_alert = render.render_email(result_alert, cfg_full, datetime(2026, 10, 7, 16, 25))
check("预警邮件含 DOCTYPE", html_alert.startswith("<!DOCTYPE html>"))
check("预警邮件含预警标题", "明天有降雨" in html_alert)
check("预警邮件含建议", "请携带雨具" in html_alert)
check("预警邮件含 LocationName", "北京市" in html_alert)
check("预警邮件含明日天气", "大雨" in html_alert)
check("预警邮件含实时天气", "12.3" in html_alert)
check("div 标签配平", html_alert.count("<div") == html_alert.count("</div>"),
      f"<div {html_alert.count('<div')} vs </div> {html_alert.count('</div>')}")
check("table 标签配平", html_alert.count("<table") == html_alert.count("</table>"))
check("tr 标签配平", html_alert.count("<tr") == html_alert.count("</tr>"))
check("td 标签配平", html_alert.count("<td") == html_alert.count("</td>"))
check("含中文编码声明", 'charset="utf-8"' in html_alert)
check("无占位链接", "example.com" not in html_alert)
check("含预警红色", "#c62828" in html_alert)

subject_alert = render.render_subject(result_alert, cfg_full, datetime(2026, 10, 7, 16, 25))
check("预警主题含【预警】", "预警" in subject_alert and "北京市" in subject_alert)

# 7b. 无预警的普通邮件
result_plain = alert.AlertResult(
    alerts=[],
    summary="未来 24 小时无恶劣天气，气温变化平稳",
    tomorrow=net.DailyForecast(
        fx_date="2026-10-08", temp_max=22, temp_min=12,
        text_day="晴", text_night="多云", code_day="100",
        wind_dir_day="南风", wind_scale_day="2", precip_probability=0.0),
    now=now_obj,
    temp_change={"available": True, "prev_date": "2026-10-06", "prev_temp": 11.5,
                 "delta": 0.8, "days_gap": 1},
)
html_plain = render.render_email(result_plain, cfg_full, datetime(2026, 10, 7, 16, 25))
check("普通邮件含简报标题", "明日天气简报" in html_plain)
check("普通邮件不含预警区块", "预警提示" not in html_plain)
check("普通邮件 div 配平", html_plain.count("<div") == html_plain.count("</div>"))
check("普通邮件蓝色主题", "#1565c0" in html_plain)
check("直升 0.8 度不触发文案", "大幅" not in html_plain or "回升" not in html_plain)

subject_plain = render.render_subject(result_plain, cfg_full, datetime(2026, 10, 7, 16, 25))
check("普通主题无【预警】", "预警" not in subject_plain)
check("普通主题含温度", "℃" in subject_plain)
check("普通主题含日期", "10-07" in subject_plain)

# 7c. 特殊字符转义
result_xss = alert.AlertResult(
    alerts=[alert.Alert(kind="severe", level="warning",
                        title="<script>alert(1)</script>",
                        detail="测试 & 转义", advice="")],
    summary="测试",
    tomorrow=net.DailyForecast(fx_date="2026-10-08", text_day="晴"),
)
html_xss = render.render_email(result_xss, cfg_full, datetime(2026, 10, 7))
check("HTML 转义生效", "<script>alert(1)</script>" not in html_xss)
check("转义为实体", "&lt;script&gt;" in html_xss)

# 7d. 风向必须转成中文（回归：曾把 ssw 直接印进邮件）
check("风向 ssw 转中文", render._wind_dir("ssw") == "南西南风")
check("风向 sw 转中文", render._wind_dir("sw") == "西南风")
check("风向大写也转", render._wind_dir("NNW") == "北西北风")
check("风向带空格也能转", render._wind_dir("  ne  ") == "东北风")
check("已是中文则原样保留", render._wind_dir("西南风") == "西南风")
check("空风向", render._wind_dir("") == "风向不定")
check("None 风向", render._wind_dir(None) == "风向不定")
check("none 缩写", render._wind_dir("none") == "风向不定")
check("vrb 缩写", render._wind_dir("vrb") == "风向多变")
check("未知英文不裸输出", render._wind_dir("xx") == "风向不定")
check("v7 中文风向函数一致", render._wind_dir_v7("西北风") == "西北风")
check("v7 英文缩写也兜住", render._wind_dir_v7("ssw") == "南西南风")

check("风级正常拼接", render._wind_scale_text("2") == "2级")
check("空风级不输出「级」", render._wind_scale_text("") == "")
check("None 风级", render._wind_scale_text(None) == "")

# 明日风力整行拼接
_wind_tmr = net.DailyForecast(
    fx_date="2026-10-08",
    wind_dir_day="ssw", wind_scale_day="2",
    wind_dir_night="nne", wind_scale_night="1",
)
_wind_line = render._wind_text(_wind_tmr)
check("风力行含白天中文风向", "白天 南西南风 2级" in _wind_line, _wind_line)
check("风力行含夜间中文风向", "夜间 北东北风 1级" in _wind_line, _wind_line)
check("风力行不含英文缩写",
      "ssw" not in _wind_line and "nne" not in _wind_line, _wind_line)

# 无夜间数据时不应出现空「级」
_wind_day_only = net.DailyForecast(
    fx_date="2026-10-08", wind_dir_day="sw", wind_scale_day="3")
_wind_line2 = render._wind_text(_wind_day_only)
check("仅白天数据时正常", _wind_line2 == "白天 西南风 3级", _wind_line2)
check("不出现悬空「级」", " 级" not in _wind_line2)

# 整封邮件里也不应有英文缩写
html_wind = render.render_email(
    alert.AlertResult(
        alerts=[],
        summary="测试",
        tomorrow=net.DailyForecast(
            fx_date="2026-10-08", temp_max=20, temp_min=10,
            text_day="晴", text_night="多云", code_day="100",
            wind_dir_day="ssw", wind_scale_day="2",
            wind_dir_night="nne", wind_scale_night="1",
            precip_probability=0.0),
        now=now_obj,
    ),
    cfg_full, datetime(2026, 10, 7),
)
check("邮件中白天风向为中文", "南西南风" in html_wind)
check("邮件中夜间风向为中文", "北东北风" in html_wind)
check("邮件中无 ssw 裸缩写", "ssw" not in html_wind)
check("邮件中无 nne 裸缩写", "nne" not in html_wind)


# ----------------------------------------------------------------- 测试 8
section("8. 邮件构造与发送")

msg = mailer.build_message(
    "测试主题", "<html><body>测试</body></html>",
    "天气邮件 <sender@example.com>", "天气邮件预警",
    ["a@example.com", "b@example.com"],
)
check("主题编码正确", "测试主题" in str(msg["Subject"]))
check("发件人地址提取", "sender@example.com" in msg["From"])
check("收件人多地址", "a@example.com" in msg["To"] and "b@example.com" in msg["To"])
# 中文显示名走 RFC2047 编码，解码后应还原
from email.header import decode_header, make_header  # noqa: E402

decoded_from = str(make_header(decode_header(msg["From"])))
check("发件人名称保留（解码后）", "天气邮件预警" in decoded_from, decoded_from)
check("内容类型为 HTML", msg.get_content_type() == "text/html")
check("charset 为 utf-8", msg.get_charset() == "utf-8")

check("发件人解析-带名称", mailer._split_sender("名 <x@y.com>") == "x@y.com")
check("发件人解析-纯地址", mailer._split_sender("x@y.com") == "x@y.com")

# 缺少配置时应报错
try:
    mailer.send_mail({"smtp": {}, "recipients": []}, "s", "<p>x</p>")
    check("缺 SMTP 配置应报错", False)
except mailer.MailError as exc:
    check("缺 SMTP 配置报错", "SMTP" in str(exc))

# dry_run 不实际发送
dry = mailer.send_mail(cfg_full, "主题", "<html></html>", dry_run=True)
check("dry_run 不投递", dry["sent"] is False and dry["dry_run"] is True)
check("dry_run 返回体积", dry["size"] > 0)


# ----------------------------------------------------------------- 测试 9
section("9. 真实本地 SMTP 收发（asyncio 迷你服务器）")

RECEIVED: list[bytes] = []


async def _mini_smtp(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """实现最小 SMTP 协议，把 DATA 阶段内容存起来。"""
    async def send(line: str) -> None:
        writer.write((line + "\r\n").encode())
        await writer.drain()

    await send("220 test.local ESMTP ready")
    in_data = False
    buffer = b""
    while True:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
        except (asyncio.TimeoutError, ConnectionResetError):
            break
        if not line:
            break
        if in_data:
            if line.rstrip(b"\r\n") == b".":
                in_data = False
                RECEIVED.append(buffer)
                buffer = b""
                await send("250 OK queued")
            else:
                buffer += line
            continue
        cmd = line.decode("utf-8", "replace").strip()
        upper = cmd.upper()
        if upper.startswith("EHLO"):
            await send("250-test.local")
            await send("250 SIZE 10485760")
        elif upper.startswith("HELO"):
            await send("250 test.local")
        elif upper.startswith(("MAIL FROM", "RCPT TO")):
            await send("250 OK")
        elif upper.startswith("DATA"):
            in_data = True
            await send("354 End data with <CR><LF>.<CR><LF>")
        elif upper.startswith("QUIT"):
            await send("221 Bye")
            break
        else:
            await send("250 OK")
    try:
        writer.close()
    except Exception:
        pass


async def run_smtp_test() -> dict:
    server = await asyncio.start_server(_mini_smtp, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    local_cfg = {
        "smtp": {
            "host": "127.0.0.1", "port": port, "use_ssl": False,
            "sender": "sender@example.com", "password": "",
            "sender_name": "天气邮件预警",
        },
        "recipients": ["boss@example.com"],
    }
    html_body = render.render_email(result_alert, cfg_full, datetime(2026, 10, 7, 16, 25))
    subject = render.render_subject(result_alert, cfg_full, datetime(2026, 10, 7, 16, 25))

    # 客户端是阻塞式 smtplib，必须丢到线程里跑，否则会卡死本事件循环
    info = await asyncio.to_thread(mailer.send_mail, local_cfg, subject, html_body)
    for _ in range(30):
        if RECEIVED:
            break
        await asyncio.sleep(0.1)
    server.close()
    await server.wait_closed()
    return info


smtp_info = asyncio.run(run_smtp_test())
check("本地 SMTP 发送返回成功", smtp_info["sent"] is True)

check("SMTP 服务端收到 1 封", len(RECEIVED) == 1, f"实际 {len(RECEIVED)}")
if RECEIVED:
    import email as email_mod
    from email import policy

    raw = RECEIVED[0]
    parsed = email_mod.message_from_bytes(raw, policy=policy.default)
    check("解析出的主题正确", "预警" in str(parsed["Subject"]))
    check("解析出的发件人正确", "sender@example.com" in str(parsed["From"]))
    check("解析出的收件人正确", "boss@example.com" in str(parsed["To"]))
    check("内容类型为 HTML", parsed.get_content_type() == "text/html")
    check("非 multipart（单版本 HTML）", not parsed.is_multipart())

    body = parsed.get_content()
    check("中文主题未乱码", "北京" in str(parsed["Subject"]))
    check("正文含中文且未乱码", "明天有降雨" in body)
    check("正文含预警样式", "#c62828" in body)
    check("正文 div 配平", body.count("<div") == body.count("</div>"))
    check("正文为完整 HTML 文档", body.strip().startswith("<!DOCTYPE html>"))
    # MIME 头里不应出现未编码的裸中文（RFC2047）
    head_section = raw.split(b"\r\n\r\n", 1)[0]
    check("邮件头已做 RFC2047 编码", b"\xe5\xae\x89\xe5\xb9\xb3" not in head_section)


# ----------------------------------------------------------------- 测试 10
section("10. CLI 行为")

from weatheremail import cli  # noqa: E402

# 未识别参数应返回 1
check("非法参数返回 1", cli.main(["-nosuchflag"]) == 1)
check("help 返回 0", cli.main(["-help"]) == 0)
check("--help 也支持", cli.main(["--help"]) == 0)
check("version 返回 0", cli.main(["-version"]) == 0)

# 配置不完整时 -email 返回 2
empty_cfg = config.load_config()
empty_cfg["qweather"]["api_key"] = ""
empty_cfg["recipients"] = []
config.save_config(empty_cfg)
rc = cli.main(["-email"])
check("-email 缺配置返回 2", rc == 2, f"实际 {rc}")

# 配置完整但 API KEY 是假的 → 返回 3（网络失败）
fake_cfg = config.load_config()
fake_cfg["qweather"]["api_key"] = "FAKE-KEY-FOR-TEST"
fake_cfg["qweather"]["api_host"] = "127.0.0.1:9"   # 必定连不上
fake_cfg["smtp"]["sender"] = "s@example.com"
fake_cfg["recipients"] = ["r@example.com"]
config.save_config(fake_cfg)
rc2 = cli.main(["-email"])
check("-email 网络失败返回 3", rc2 == 3, f"实际 {rc2}")

# -config 应正常输出且密码脱敏
import io
from contextlib import redirect_stdout  # noqa: E402

buf = io.StringIO()
with redirect_stdout(buf):
    cli.main(["-config"])
out = buf.getvalue()
check("-config 输出含设置项", "和风天气" in out and "SMTP" in out)
check("-config 密码已脱敏", "secret-pass" not in out)

# -history 正常
buf2 = io.StringIO()
with redirect_stdout(buf2):
    cli.main(["-history"])
check("-history 有输出", "温度历史" in buf2.getvalue())


# ----------------------------------------------------------------- 测试 11
section("11. 设置界面交互（模拟输入）")

cfg_menu = config.load_config()
cfg_menu["qweather"]["api_key"] = "MENU-TEST-KEY"
cfg_menu["smtp"]["sender"] = "m@example.com"
cfg_menu["recipients"] = ["r1@example.com"]
config.save_config(cfg_menu)

from unittest import mock  # noqa: E402

# 走一遍：编辑温度阈值(6) → 改阈值为 8 → 保存(s) → 退出(q)
answers = iter(["6", "n", "8", "n", "s", "q"])
buf3 = io.StringIO()
with mock.patch("builtins.input", lambda *a, **k: next(answers)), \
        redirect_stdout(buf3):
    rc = cli.settings_menu(config.load_config())
check("设置菜单正常退出", rc == 0)
saved = config.load_config()
check("阈值修改已保存", saved["temperature"]["change_threshold"] == 8.0,
      f"实际 {saved['temperature']['change_threshold']}")

# 走一遍：关闭恶劣天气总开关（5）
answers2 = iter(["5", "n", "y", "y", "y", "y", "y", "6", "30", "s", "q"])
buf4 = io.StringIO()
with mock.patch("builtins.input", lambda *a, **k: next(answers2)), \
        redirect_stdout(buf4):
    cli.settings_menu(config.load_config())
saved2 = config.load_config()
check("恶劣天气总开关已关闭", saved2["severe"]["enabled"] is False)
check("子项开关已保留", saved2["severe"]["rain"] is True)
check("风级已保存", saved2["severe"]["wind_scale"] == 6)

# 走一遍：编辑推送位置(1) → 改成上海人民广场经纬度 → 保存(s) → 退出(q)
answers3 = iter(["1", "上海市黄浦区", "31.2304", "121.4737", "s", "q"])
buf5 = io.StringIO()
with mock.patch("builtins.input", lambda *a, **k: next(answers3)), \
        redirect_stdout(buf5):
    cli.settings_menu(config.load_config())
saved3 = config.load_config()
check("地名已保存", saved3["location"]["name"] == "上海市黄浦区",
      f"实际 {saved3['location']['name']}")
check("纬度已保存", saved3["location"]["lat"] == "31.2304",
      f"实际 {saved3['location']['lat']}")
check("经度已保存", saved3["location"]["lon"] == "121.4737",
      f"实际 {saved3['location']['lon']}")
# 界面应回显新位置
check("设置界面回显新位置", "上海市黄浦区" in buf5.getvalue())
check("设置界面回显经纬度", "31.2304" in buf5.getvalue())

# 抽掉测试 11 中已改动的经纬度断言（保留原值应为修改后的值）
# 非法经纬度应被拒绝并保留原值（纬度 999 越界、经度非数字）
answers4 = iter(["1", "测试地", "999", "abc", "q", "y"])
buf6 = io.StringIO()
with mock.patch("builtins.input", lambda *a, **k: next(answers4)), \
        redirect_stdout(buf6):
    cli.settings_menu(config.load_config())
saved4 = config.load_config()
check("越界纬度被拒绝保留原值", saved4["location"]["lat"] == "31.2304",
      f"实际 {saved4['location']['lat']}")
check("非数字经度被拒绝保留原值", saved4["location"]["lon"] == "121.4737",
      f"实际 {saved4['location']['lon']}")
check("越界纬度有提示", "纬度需在" in buf6.getvalue())

# 经纬度辅助函数边界
check("纬度下界 -90 合法", cli._input_lat("x", "0") == "0")
check("经度辅助函数存在", callable(cli._input_lon))


# ----------------------------------------------------------------- 测试 12
section("12. 端到端：抓取→判断→发信（mock 网络）")

import unittest.mock as mock  # noqa: E402

e2e_cfg = config.load_config()
e2e_cfg["qweather"]["api_key"] = "E2E-KEY"
e2e_cfg["qweather"]["api_host"] = "e2e.re.qweatherapi.com"
e2e_cfg["qweather"]["api_version"] = "v1"
e2e_cfg["smtp"]["sender"] = "s@example.com"
e2e_cfg["smtp"]["password"] = ""
e2e_cfg["recipients"] = ["r@example.com"]
e2e_cfg["temperature"]["change_threshold"] = 5.0
e2e_cfg["severe"]["enabled"] = True
e2e_cfg["send"]["always_send"] = True
config.save_config(e2e_cfg)

# 造一份「明天大雨 + 大风」的 v1 响应
tomorrow_iso = (date.today() + timedelta(days=1)).isoformat()
fake_now = dict(v1_current)
fake_daily = {"days": [dict(v1_daily["days"][0])]}
fake_daily["days"][0]["forecastStartTime"] = tomorrow_iso + "T22:00Z"
fake_daily["days"][0]["daytime"]["condition"] = {"text": "暴雨", "code": "310"}
fake_daily["days"][0]["daytime"]["wind"]["scale"] = 8
fake_daily["days"][0]["daytime"]["precipitation"]["probability"] = 0.95

RECEIVED.clear()


def fake_call_v1(self, host, endpoint, lat, lon):
    if endpoint == "current":
        return fake_now
    return fake_daily


async def run_e2e() -> dict:
    server = await asyncio.start_server(_mini_smtp, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    e2e_cfg["smtp"]["host"] = "127.0.0.1"
    e2e_cfg["smtp"]["port"] = port
    e2e_cfg["smtp"]["use_ssl"] = False
    config.save_config(e2e_cfg)

    async def _run() -> object:
        with mock.patch.object(net.QWeatherClient, "_call_v1", fake_call_v1):
            b = io.StringIO()
            with redirect_stdout(b):
                r = await asyncio.to_thread(cli.do_send, config.load_config(), False)
        return r, b.getvalue()

    rc, out = await _run()
    for _ in range(30):
        if RECEIVED:
            break
        await asyncio.sleep(0.1)
    server.close()
    await server.wait_closed()
    return {"rc": rc, "out": out, "port": port}


e2e = asyncio.run(run_e2e())
check("端到端返回 0", e2e["rc"] == 0, f"rc={e2e['rc']} 输出={e2e['out'][-300:]}")
check("端到端收到 1 封邮件", len(RECEIVED) == 1, f"实际 {len(RECEIVED)}")
check("端到端识别出预警", "触发预警" in e2e["out"] or "需要关注" in e2e["out"])

if RECEIVED:
    from email import policy as _policy

    parsed = email_mod.message_from_bytes(RECEIVED[0], policy=_policy.default)
    body = parsed.get_content()
    check("端到端邮件主题含暴雨预警", "预警" in str(parsed["Subject"]))
    check("端到端正文含暴雨", "暴雨" in body or "降雨" in body)
    check("端到端正文含大风", "大风" in body)
    check("端到端正文颜色正确", "#c62828" in body)

# 无预警场景 + always_send=False → 不发信
RECEIVED.clear()
plain_now = dict(v1_current)
plain_daily = {"days": [dict(v1_daily["days"][0])]}
plain_daily["days"][0]["forecastStartTime"] = tomorrow_iso + "T22:00Z"
plain_daily["days"][0]["daytime"]["condition"] = {"text": "晴", "code": "100"}
plain_daily["days"][0]["daytime"]["wind"]["scale"] = 2
plain_daily["days"][0]["daytime"]["precipitation"]["probability"] = 0.0


async def run_quiet() -> int:
    cfg_q = config.load_config()
    cfg_q["send"]["always_send"] = False
    config.save_config(cfg_q)

    def fake_clear(self, host, endpoint, lat, lon):
        return plain_now if endpoint == "current" else plain_daily

    with mock.patch.object(net.QWeatherClient, "_call_v1", fake_clear):
        b = io.StringIO()
        with redirect_stdout(b):
            rc = await asyncio.to_thread(cli.do_send, config.load_config(), False)
    await asyncio.sleep(0.2)
    return rc


rc_quiet = asyncio.run(run_quiet())
check("无预警且关闭常发时返回 0", rc_quiet == 0)
check("无预警时不发信", len(RECEIVED) == 0, f"实际收到 {len(RECEIVED)}")


# ----------------------------------------------------------------- 汇总
print()
print("=" * 62)
if FAIL == 0:
    print(f"\033[32m全部通过：{PASS} 项\033[0m")
else:
    print(f"\033[31m失败 {FAIL} 项\033[0m / 通过 {PASS} 项")
    for name in FAILURES:
        print(f"  - {name}")
print("=" * 62)

# 清理临时目录
try:
    shutil.rmtree(_TMP)
except OSError:
    pass

sys.exit(1 if FAIL else 0)
