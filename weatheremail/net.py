"""和风天气 API 客户端（新旧两版接口自动兼容）.

新版（2024 起）
    GET {api_host}/weather/v1/current/{lat}/{lon}
    GET {api_host}/weather/v1/daily/{lat}/{lon}
    认证：Header  X-QW-Api-Key: <key>   或  query  ?key=<key>
    每个账号有专属 API Host，形如 abcdefg.re.qweatherapi.com

旧版
    GET https://devapi.qweather.com/v7/weather/now?location=<lon,lat>&key=<key>
    GET https://devapi.qweather.com/v7/weather/3d?location=<lon,lat>&key=<key>

两版返回的 JSON 结构差异很大，本模块把两者归一化成内部统一的
WeatherNow / DailyForecast 数据结构，上层逻辑无需关心版本。

注意：文件名刻意不叫 http.py，避免遮蔽标准库的 http 包。
"""

from __future__ import annotations

import gzip
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

# 注意：API Host 一律由用户在程序设置里填写，不在此处硬编码任何默认域名。
# 和风天气新版控制台为每个账号分配专属 Host（形如 abc.re.qweatherapi.com），
# 使用公共域名必然返回 403 Invalid Host，因此不再内置任何公共域名候选。
LEGACY_HOST = "devapi.qweather.com"   # 仅供旧版 v7 接口拼接路径使用

USER_AGENT = "weatheremail/1.0 (+https://dev.qweather.com)"
TIMEOUT = 15


class WeatherError(RuntimeError):
    """天气接口调用失败。"""


# ----------------------------------------------------------- 数据结构


@dataclass
class WeatherNow:
    """实时天气（归一化后）。"""

    obs_time: str = ""
    temp: Optional[float] = None
    feels_like: Optional[float] = None
    text: str = ""
    code: str = ""
    wind_dir: str = ""
    wind_scale: str = ""
    wind_speed: Optional[float] = None
    humidity: Optional[float] = None      # 百分比
    precip: Optional[float] = None        # mm
    pressure: Optional[float] = None      # hPa
    vis: Optional[float] = None           # km
    uv_index: Optional[int] = None
    raw_source: str = ""


@dataclass
class DailyForecast:
    """某一天的预报（归一化后）。"""

    fx_date: str = ""
    temp_max: Optional[float] = None
    temp_min: Optional[float] = None
    text_day: str = ""
    text_night: str = ""
    code_day: str = ""
    code_night: str = ""
    wind_dir_day: str = ""
    wind_scale_day: str = ""
    wind_speed_day: Optional[float] = None
    wind_dir_night: str = ""
    wind_scale_night: str = ""
    humidity: Optional[float] = None
    precip: Optional[float] = None
    precip_probability: Optional[float] = None   # 百分比
    uv_index: Optional[int] = None
    sunrise: str = ""
    sunset: str = ""

    @property
    def temp_range(self) -> str:
        lo = "--" if self.temp_min is None else f"{self.temp_min:.0f}"
        hi = "--" if self.temp_max is None else f"{self.temp_max:.0f}"
        return f"{lo} ~ {hi}℃"

    @property
    def weekday(self) -> str:
        names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        try:
            d = date.fromisoformat(self.fx_date)
            return names[d.weekday()]
        except (ValueError, TypeError):
            return ""


@dataclass
class WeatherReport:
    """一次抓取的完整结果。"""

    now: Optional[WeatherNow] = None
    daily: List[DailyForecast] = field(default_factory=list)
    api_version: str = ""
    location_name: str = ""
    fetched_at: datetime = field(default_factory=datetime.now)
    errors: List[str] = field(default_factory=list)

    def today(self) -> Optional[DailyForecast]:
        if not self.daily:
            return None
        today = date.today().isoformat()
        for item in self.daily:
            if item.fx_date == today:
                return item
        return self.daily[0]

    def tomorrow(self) -> Optional[DailyForecast]:
        """取明天。优先按日期精确匹配，其次按顺序取第二个。"""
        if not self.daily:
            return None
        from datetime import timedelta

        target = (date.today() + timedelta(days=1)).isoformat()
        for item in self.daily:
            if item.fx_date == target:
                return item
        if len(self.daily) > 1:
            return self.daily[1]
        return self.daily[0]


# ----------------------------------------------------------- 网络层


def _maybe_gunzip(body: bytes, encoding: str = "") -> bytes:
    """按需解压 gzip。错误响应体常常也是压缩的，不能只看响应头。"""
    if encoding == "gzip" or body[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(body)
        except OSError:
            return body
    return body


def _http_get(url: str, headers: Dict[str, str]) -> Dict[str, Any]:
    """发起 GET 请求并解析 JSON（自动处理 gzip）。"""
    request = urllib.request.Request(url, headers=headers, method="GET")
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(
            request, timeout=TIMEOUT, context=context
        ) as resp:
            body = _maybe_gunzip(
                resp.read(), (resp.headers.get("Content-Encoding") or "").lower()
            )
    except urllib.error.HTTPError as exc:  # 4xx/5xx 仍可能带 JSON 错误体
        raw = exc.read() if hasattr(exc, "read") else b""
        # 关键：错误体同样可能被 gzip 压缩（和风天气 403 即如此），
        # 不解压就会解析失败，错误原因被吞掉。
        body = _maybe_gunzip(
            raw, (exc.headers.get("Content-Encoding") or "").lower()
            if exc.headers
            else ""
        )
        text = body.decode("utf-8", "replace")
        try:
            payload = json.loads(text)
        except Exception:
            raise WeatherError(
                f"HTTP {exc.code} 请求失败：{url}"
            ) from exc
        code = str(payload.get("code", ""))
        raise WeatherError(_friendly_error(code, exc.code, text)) from exc
    except urllib.error.URLError as exc:
        raise WeatherError(f"网络请求失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise WeatherError("网络请求超时，请检查网络连接") from exc

    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WeatherError("返回内容不是合法 JSON，可能被网络中间层拦截") from exc


def _friendly_error(code: str, http_code: int = 0, body: str = "") -> str:
    """把和风天气错误码翻译成中文提示。"""
    # 新版接口的 Invalid Host 错误：HTTP 403 但 code 字段缺失，
    # 错误详情在 error.detail 里，必须单独识别，否则会误导用户去查 KEY。
    if "invalid-host" in body or "Invalid Host" in body:
        return (
            "API Host 无效（Invalid Host）：当前使用的域名不属于你的账号。\n"
            "      请登录 https://console.qweather.com/setting 查看「API Host」，\n"
            "      把它填入设置的第 [1] 项（形如 abcdefg.re.qweatherapi.com）。"
        )

    table = {
        "204": "该地区暂无数据（和风天气免费版不含海外数据）",
        "400": "请求参数错误，请检查坐标或 API Host 是否正确",
        "401": "认证失败：API KEY 无效，或 API Host / 认证方式不匹配",
        "402": "超过每日访问次数限额，或账号余额不足",
        "403": (
            "无访问权限：API Host 与账号不匹配（最常见），"
            "或该项目未绑定此 API"
        ),
        "404": "请求的资源不存在，可能是 API Host 填错",
        "429": "请求过于频繁，已被限流",
        "500": "和风天气服务端内部错误，请稍后重试",
    }
    if code in table:
        return f"和风天气返回错误 code={code}：{table[code]}"
    if http_code:
        return f"和风天气返回 HTTP {http_code}（code={code or '未知'}）"
    return f"和风天气返回未知错误 code={code}"


def _num(value: Any) -> Optional[float]:
    """尽量把值转成 float，失败返回 None。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------- 新版 v1 解析


def _parse_v1_now(payload: Dict[str, Any], source: str) -> WeatherNow:
    cond = payload.get("condition") or {}
    wind = payload.get("wind") or {}
    direction = wind.get("direction") or {}
    speed = wind.get("speed") or {}
    precip = payload.get("precipitation") or {}
    amount = precip.get("amount") or {}
    pressure = payload.get("pressure") or {}
    visibility = payload.get("visibility") or {}
    humidity = _num(payload.get("humidity"))

    return WeatherNow(
        obs_time=str(payload.get("obsTime") or ""),
        temp=_num((payload.get("temperature") or {}).get("value")),
        feels_like=_num((payload.get("feelsLike") or {}).get("value")),
        text=str(cond.get("text") or ""),
        code=str(cond.get("code") or ""),
        wind_dir=str(direction.get("compass") or ""),
        wind_scale=str(wind.get("scale") if wind.get("scale") is not None else ""),
        wind_speed=_num(speed.get("value")),
        humidity=None if humidity is None else round(humidity * 100, 1),
        precip=_num(amount.get("value")),
        pressure=_num(pressure.get("value")),
        vis=(
            None
            if _num(visibility.get("value")) is None
            else round(_num(visibility.get("value")) / 1000.0, 1)
        ),
        uv_index=payload.get("uvIndex"),
        raw_source=source,
    )


def _parse_v1_daily(payload: Dict[str, Any]) -> List[DailyForecast]:
    out: List[DailyForecast] = []
    for day in payload.get("days") or []:
        astro = day.get("astro") or {}
        daytime = day.get("daytime") or {}
        nighttime = day.get("nighttime") or {}
        d_cond = daytime.get("condition") or {}
        n_cond = nighttime.get("condition") or {}
        d_wind = daytime.get("wind") or {}
        n_wind = nighttime.get("wind") or {}
        d_dir = (d_wind.get("direction") or {}).get("compass") or ""
        d_speed = _num((d_wind.get("speed") or {}).get("value"))
        precip = daytime.get("precipitation") or {}
        amount = precip.get("amount") or {}
        probability = _num(precip.get("probability"))
        humidity = _num(daytime.get("humidity"))

        start = str(day.get("forecastStartTime") or "")
        fx_date = start[:10] if start else ""
        out.append(
            DailyForecast(
                fx_date=fx_date,
                temp_max=_num((day.get("temperatureMax") or {}).get("value")),
                temp_min=_num((day.get("temperatureMin") or {}).get("value")),
                text_day=str(d_cond.get("text") or ""),
                text_night=str(n_cond.get("text") or ""),
                code_day=str(d_cond.get("code") or ""),
                code_night=str(n_cond.get("code") or ""),
                wind_dir_day=str(d_dir),
                wind_scale_day=str(
                    d_wind.get("scale") if d_wind.get("scale") is not None else ""
                ),
                wind_speed_day=d_speed,
                wind_dir_night=str(
                    (n_wind.get("direction") or {}).get("compass") or ""
                ),
                wind_scale_night=str(
                    n_wind.get("scale") if n_wind.get("scale") is not None else ""
                ),
                humidity=None if humidity is None else round(humidity * 100, 1),
                precip=_num(amount.get("value")),
                precip_probability=(
                    None if probability is None else round(probability * 100, 1)
                ),
                uv_index=day.get("uvIndexMax"),
                sunrise=str(astro.get("sunrise") or "")[11:16],
                sunset=str(astro.get("sunset") or "")[11:16],
            )
        )
    return out


# ----------------------------------------------------------- 旧版 v7 解析


def _parse_v7_now(payload: Dict[str, Any]) -> WeatherNow:
    now = payload.get("now") or {}
    return WeatherNow(
        obs_time=str(now.get("obsTime") or ""),
        temp=_num(now.get("temp")),
        feels_like=_num(now.get("feelsLike")),
        text=str(now.get("text") or ""),
        code=str(now.get("icon") or ""),
        wind_dir=str(now.get("windDir") or ""),
        wind_scale=str(now.get("windScale") or ""),
        wind_speed=_num(now.get("windSpeed")),
        humidity=_num(now.get("humidity")),
        precip=_num(now.get("precip")),
        pressure=_num(now.get("pressure")),
        vis=_num(now.get("vis")),
        raw_source="v7",
    )


def _parse_v7_daily(payload: Dict[str, Any]) -> List[DailyForecast]:
    out: List[DailyForecast] = []
    for day in payload.get("daily") or []:
        out.append(
            DailyForecast(
                fx_date=str(day.get("fxDate") or ""),
                temp_max=_num(day.get("tempMax")),
                temp_min=_num(day.get("tempMin")),
                text_day=str(day.get("textDay") or ""),
                text_night=str(day.get("textNight") or ""),
                code_day=str(day.get("iconDay") or ""),
                code_night=str(day.get("iconNight") or ""),
                wind_dir_day=str(day.get("windDirDay") or ""),
                wind_scale_day=str(day.get("windScaleDay") or ""),
                wind_speed_day=_num(day.get("windSpeedDay")),
                wind_scale_night=str(day.get("windScaleNight") or ""),
                humidity=_num(day.get("humidity")),
                precip=_num(day.get("precip")),
                precip_probability=None,
                uv_index=None,
                sunrise=str(day.get("sunrise") or ""),
                sunset=str(day.get("sunset") or ""),
            )
        )
    return out


# ----------------------------------------------------------- 客户端


class QWeatherClient:
    """按配置自动选择新版 / 旧版接口的客户端。"""

    def __init__(
        self,
        api_key: str,
        api_host: str = "",
        api_version: str = "auto",
        location_name: str = "",
    ) -> None:
        if not api_key:
            raise WeatherError("未配置和风天气 API KEY")
        self.api_key = api_key.strip()
        self.api_host = (api_host or "").strip().rstrip("/")
        self.api_version = api_version or "auto"
        self.location_name = location_name
        self._resolved_version = ""

    # -------------------------------------------------- 路径拼接

    def _normalize_host(self, host: str) -> str:
        host = host.strip().rstrip("/")
        if not host:
            return ""
        if not host.startswith("http"):
            host = "https://" + host
        return host

    def _v1_url(self, host: str, endpoint: str, lat: str, lon: str) -> str:
        return f"{self._normalize_host(host)}/weather/v1/{endpoint}/{lat}/{lon}"

    def _v7_url(self, endpoint: str, lat: str, lon: str) -> str:
        # 旧版要求 location 为 "经度,纬度"
        location = f"{lon},{lat}"
        query = urllib.parse.urlencode(
            {"location": location, "key": self.api_key, "lang": "zh", "unit": "m"}
        )
        return f"https://{LEGACY_HOST}/v7/weather/{endpoint}?{query}"

    # -------------------------------------------------- 请求

    def _call_v1(
        self, host: str, endpoint: str, lat: str, lon: str
    ) -> Dict[str, Any]:
        url = self._v1_url(host, endpoint, lat, lon)
        headers = {
            "X-QW-Api-Key": self.api_key,
            "Accept-Encoding": "gzip",
            "User-Agent": USER_AGENT,
        }
        return _http_get(url, headers)

    def _call_v7(self, endpoint: str, lat: str, lon: str) -> Dict[str, Any]:
        url = self._v7_url(endpoint, lat, lon)
        headers = {
            "Accept-Encoding": "gzip",
            "User-Agent": USER_AGENT,
        }
        return _http_get(url, headers)

    def _candidate_hosts(self) -> List[str]:
        """返回要尝试的 API Host —— 只使用用户在设置里填写的那个。

        不再回退到任何内置公共域名：和风天气为每个账号分配专属 Host，
        用公共域名只会得到 403 Invalid Host，徒增误导性报错。
        """
        if not self.api_host:
            return []
        return [self._normalize_host(self.api_host)]

    def _pick_version(self) -> str:
        """确定使用哪个 API 版本。"""
        if self.api_version in ("v1", "v7"):
            return self.api_version
        # auto：仅当用户明确填了旧版公共域名时才走 v7，否则一律按新版 v1。
        # （新版 v1 API Host 是账号专属域名，形如 abc.re.qweatherapi.com）
        if self.api_host and "devapi.qweather.com" in self.api_host.lower():
            return "v7"
        return "v1"

    # -------------------------------------------------- 抓取

    def fetch(self, lat: str, lon: str) -> WeatherReport:
        """抓取实况 + 逐日预报，失败时抛出 WeatherError。"""
        version = self._pick_version()
        errors: List[str] = []

        # 未填 API Host 时直接给出明确指引，不再拿公共域名去试。
        if not self.api_host:
            raise WeatherError(
                "未设置「API Host」，无法请求天气数据。\n"
                "  和风天气为每个账号分配专属 API Host，请登录\n"
                "    https://console.qweather.com/setting\n"
                "  复制其中的 API Host（形如 abcdefg.re.qweatherapi.com），\n"
                "  然后在 weatheremail 设置界面第 [1] 项填写并保存。"
            )

        if version == "v1":
            for host in self._candidate_hosts():
                try:
                    now_payload = self._call_v1(host, "current", lat, lon)
                    daily_payload = self._call_v1(host, "daily", lat, lon)
                except WeatherError as exc:
                    errors.append(f"{host}: {exc}")
                    continue
                self._resolved_version = "v1"
                self._resolved_host = host
                return WeatherReport(
                    now=_parse_v1_now(now_payload, f"v1 @ {host}"),
                    daily=_parse_v1_daily(daily_payload),
                    api_version="v1",
                    location_name=self.location_name,
                    errors=errors,
                )
            # v1 失败，回退旧版试试（仅当用户显式选择或 Host 指向旧版时才有意义）
            try:
                report = self._fetch_v7(lat, lon)
                report.errors = errors + report.errors
                return report
            except WeatherError as exc:
                errors.append(f"v7 回退失败: {exc}")
        else:
            try:
                report = self._fetch_v7(lat, lon)
                report.errors = errors + report.errors
                return report
            except WeatherError as exc:
                errors.append(f"v7: {exc}")

        raise WeatherError(
            "无法获取天气数据。已尝试的接口：\n  " + "\n  ".join(errors)
        )

    def _fetch_v7(self, lat: str, lon: str) -> WeatherReport:
        now_payload = self._call_v7("now", lat, lon)
        daily_payload = self._call_v7("3d", lat, lon)
        self._resolved_version = "v7"
        return WeatherReport(
            now=_parse_v7_now(now_payload),
            daily=_parse_v7_daily(daily_payload),
            api_version="v7",
            location_name=self.location_name,
        )

    @property
    def resolved_version(self) -> str:
        return self._resolved_version or self._pick_version()
