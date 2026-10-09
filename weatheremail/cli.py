"""交互式终端设置界面 + 主流程编排.

无参数运行 weatheremail   → 进入设置菜单
weatheremail -email       → 抓天气、记温度、判断预警、发邮件
其余辅助参数见 -help。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import __appname__, __version__
from .alert import AlertResult, evaluate
from .config import load_config, mask_secret, save_config, validate
from .history import compare_with_previous, history_tail, record_today
from .mailer import MailError, send_mail
from .net import QWeatherClient, WeatherError, WeatherReport
from .paths import config_path, preview_dir
from .render import render_email, render_subject

# ------------------------------------------------------------ 终端样式
# 非 TTY（重定向到文件）时自动关闭颜色

_TTY = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    if not _TTY or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(t: str) -> str:
    return _c("1", t)


def cyan(t: str) -> str:
    return _c("36", t)


def green(t: str) -> str:
    return _c("32", t)


def yellow(t: str) -> str:
    return _c("33", t)


def red(t: str) -> str:
    return _c("31", t)


def dim(t: str) -> str:
    return _c("2", t)


def hr(width: int = 62) -> str:
    return dim("-" * width)


# ------------------------------------------------------------ 输入辅助

_TRUTHY = ("y", "yes", "1", "是", "true", "t", "on")
_FALSY = ("n", "no", "0", "否", "false", "f", "off")


def _input(prompt: str, default: str = "") -> str:
    """读取一行输入，允许默认值。"""
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return raw or default


def _input_secret(prompt: str, current: str = "") -> str:
    """读取敏感输入，回车保留原值。"""
    shown = mask_secret(current) if current else "(未设置)"
    suffix = f" [当前 {shown}，回车保留]"
    try:
        raw = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return current
    if not raw:
        return current
    # 允许输入 "-" 显式清空
    if raw == "-":
        return ""
    return raw


def _input_int(prompt: str, default: int) -> int:
    raw = _input(prompt, str(default))
    try:
        return int(raw)
    except ValueError:
        print(yellow(f"  「{raw}」不是整数，保留原值 {default}"))
        return default


def _input_float(prompt: str, default: float) -> float:
    raw = _input(prompt, f"{default:g}")
    try:
        return float(raw)
    except ValueError:
        print(yellow(f"  「{raw}」不是数字，保留原值 {default:g}"))
        return default


def _input_bool(prompt: str, default: bool) -> bool:
    d = "Y/n" if default else "y/N"
    try:
        raw = input(f"{prompt} [{d}]: ").strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    print(yellow("  未识别，保留原值"))
    return default


def _input_list(prompt: str, current: List[str]) -> List[str]:
    """编辑邮箱列表，逗号或分号分隔，回车保留，'-' 清空。"""
    shown = "，".join(current) if current else "(空)"
    try:
        raw = input(f"{prompt} [当前 {shown}]（逗号分隔，回车保留，- 清空）: ")
    except EOFError:
        return current
    raw = raw.strip()
    if not raw:
        return list(current)
    if raw == "-":
        return []
    parts = raw.replace("；", ",").replace(";", ",").replace("，", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


# ------------------------------------------------------------ 设置菜单


def _print_settings(cfg: Dict[str, Any]) -> None:
    smtp = cfg["smtp"]
    qw = cfg["qweather"]
    sev = cfg["severe"]
    tmp = cfg["temperature"]
    loc = cfg["location"]

    def onoff(v: bool) -> str:
        return green("开") if v else dim("关")

    print()
    print(bold(cyan(f"  {loc['name']} 天气预警设置")))
    print(hr())
    print(bold("  [1] 推送位置"))
    print(f"      地名       : {loc.get('name')}")
    print(f"      经纬度     : {loc.get('lat')}, {loc.get('lon')}")
    print(bold("  [2] 和风天气"))
    print(f"      API KEY    : {mask_secret(qw.get('api_key', ''))}")
    host_display = qw.get("api_host") or red("(未设置，必填)")
    print(f"      API Host   : {host_display}")
    ver = qw.get("api_version", "auto")
    ver_label = {"auto": "自动", "v1": "新版 v1", "v7": "旧版 v7"}.get(ver, ver)
    print(f"      接口版本   : {ver_label}")
    print(bold("  [3] 邮件发送 (SMTP)"))
    print(f"      服务器     : {smtp.get('host') or dim('(未设置)')}:{smtp.get('port')}")
    print(f"      加密方式   : {'SSL' if smtp.get('use_ssl') else 'STARTTLS/明文'}")
    print(f"      发件人     : {smtp.get('sender') or dim('(未设置)')}")
    print(f"      发件人名称 : {smtp.get('sender_name') or dim('(空)')}")
    print(f"      密码/授权码: {mask_secret(smtp.get('password', ''))}")
    print(bold("  [4] 收件人"))
    if cfg["recipients"]:
        for i, addr in enumerate(cfg["recipients"], 1):
            print(f"      {i}. {addr}")
    else:
        print(f"      {dim('(未设置，至少需要 1 个)')}")
    print(f"      邮件主题前缀: {cfg.get('subject_prefix')}")
    print(bold("  [5] 恶劣天气预警"))
    print(f"      总开关     : {onoff(sev.get('enabled', True))}")
    print(f"      雨  {onoff(sev.get('rain', True))}   "
          f"雪  {onoff(sev.get('snow', True))}   "
          f"冰雹 {onoff(sev.get('hail', True))}   "
          f"雾霾 {onoff(sev.get('fog', True))}   "
          f"大风 {onoff(sev.get('wind', True))}")
    print(f"      大风判定   : 达到 {sev.get('wind_scale')} 级")
    print(f"      降水概率门槛: {sev.get('rain_probability')} %（低于此值不提示降雨）")
    print(bold("  [6] 温度变化预警"))
    print(f"      总开关     : {onoff(tmp.get('change_enabled', True))}")
    print(f"      升降阈值   : {tmp.get('change_threshold'):g} ℃")
    print(f"      记录历史   : {onoff(tmp.get('record_history', True))}")
    print(bold("  [7] 发送策略"))
    print(f"      无预警也发 : {onoff(cfg['send'].get('always_send', True))}")
    print(hr())
    print("  [8] 立即抓取并发送一封测试邮件   [9] 仅预览邮件（不发信）")
    print("  [h] 查看温度历史   [s] 保存并退出   [q] 不保存退出")
    print()


def _edit_smtp(cfg: Dict[str, Any]) -> None:
    smtp = cfg["smtp"]
    print()
    print(bold("  ── 邮件发送设置 ──"))
    print(dim("  提示：\n"
              "    QQ邮箱   smtp.qq.com      端口 465  SSL   密码填「授权码」\n"
              "    163邮箱  smtp.163.com     端口 465  SSL   密码填「授权码」\n"
              "    阿里企业 smtp.qiye.aliyun.com 端口 465 SSL\n"
              "    Gmail    smtp.gmail.com   端口 587  TLS   密码填「应用专用密码」"))
    smtp["host"] = _input("  SMTP 服务器", smtp.get("host", ""))
    smtp["port"] = _input_int("  端口", int(smtp.get("port", 465)))
    smtp["use_ssl"] = _input_bool("  使用 SSL 加密", bool(smtp.get("use_ssl", True)))
    if smtp.get("port") == 587:
        smtp["use_ssl"] = False
    smtp["sender"] = _input("  发件人邮箱", smtp.get("sender", ""))
    smtp["sender_name"] = _input("  发件人显示名称", smtp.get("sender_name", "天气邮件预警"))
    smtp["password"] = _input_secret("  SMTP 密码/授权码", smtp.get("password", ""))
    print(green("  已更新邮件设置（尚未保存，返回主菜单后按 s 保存）"))


def _edit_recipients(cfg: Dict[str, Any]) -> None:
    print()
    print(bold("  ── 收件人设置 ──"))
    cfg["recipients"] = _input_list("  收件人邮箱", cfg.get("recipients", []))
    cfg["subject_prefix"] = _input(
        "  邮件主题前缀", cfg.get("subject_prefix", "【天气邮件】")
    )
    print(green(f"  当前收件人 {len(cfg['recipients'])} 个"))


def _edit_severe(cfg: Dict[str, Any]) -> None:
    sev = cfg["severe"]
    print()
    print(bold("  ── 恶劣天气预警设置 ──"))
    print(dim("  选择需要发送预警的天气类型（全部关闭则只发普通简报）"))
    sev["enabled"] = _input_bool("  恶劣天气预警总开关", bool(sev.get("enabled", True)))
    sev["rain"] = _input_bool("  降雨预警", bool(sev.get("rain", True)))
    sev["snow"] = _input_bool("  降雪预警", bool(sev.get("snow", True)))
    sev["hail"] = _input_bool("  冰雹/冻雨预警", bool(sev.get("hail", True)))
    sev["fog"] = _input_bool("  雾霾/沙尘预警", bool(sev.get("fog", True)))
    sev["wind"] = _input_bool("  大风预警", bool(sev.get("wind", True)))
    if sev.get("wind"):
        sev["wind_scale"] = _input_int(
            "  大风判定风力（蒲福风级）", int(sev.get("wind_scale", 6))
        )
    sev["rain_probability"] = _input_int(
        "  降雨提示的降水概率门槛(%)", int(sev.get("rain_probability", 30))
    )
    print(green("  已更新恶劣天气设置"))


def _edit_temperature(cfg: Dict[str, Any]) -> None:
    tmp = cfg["temperature"]
    print()
    print(bold("  ── 温度变化预警设置 ──"))
    print(dim("  程序会把每次运行的温度记录下来，下次运行时对比。\n"
              "  当前温度与上次记录相差超过阈值时发送升/降温预警。"))
    tmp["change_enabled"] = _input_bool(
        "  温度变化预警总开关", bool(tmp.get("change_enabled", True))
    )
    tmp["change_threshold"] = _input_float(
        "  触发预警的温差（℃）", float(tmp.get("change_threshold", 5.0))
    )
    tmp["record_history"] = _input_bool(
        "  记录温度历史（用于下次对比）", bool(tmp.get("record_history", True))
    )
    print(green("  已更新温度预警设置"))


def _edit_qweather(cfg: Dict[str, Any]) -> None:
    qw = cfg["qweather"]
    print()
    print(bold("  ── 和风天气设置 ──"))
    print(dim("  API KEY 在 https://console.qweather.com/project 创建凭据后获取。\n"
              "  API Host 在 https://console.qweather.com/setting 查看，\n"
              "  是和风天气为你的账号分配的专属域名，形如 abcdefg.re.qweatherapi.com，\n"
              "  必须填写，否则无法请求天气数据。"))
    qw["api_key"] = _input_secret("  API KEY", qw.get("api_key", ""))
    qw["api_host"] = _input(
        "  API Host（必填）", qw.get("api_host", "")
    )
    if not qw.get("api_host"):
        print(yellow("  提醒：未填写 API Host，天气功能将无法使用。"))
    cur = qw.get("api_version", "auto")
    print("  接口版本  1=自动  2=新版 v1  3=旧版 v7")
    choice = _input("  请选择", {"auto": "1", "v1": "2", "v7": "3"}.get(cur, "1"))
    qw["api_version"] = {"1": "auto", "2": "v1", "3": "v7"}.get(choice, "auto")
    print(green("  已更新和风天气设置"))


def _input_lat(prompt: str, default: str) -> str:
    """读取纬度：-90 ~ 90。"""
    raw = _input(prompt, default).strip()
    try:
        val = float(raw)
    except ValueError:
        print(yellow(f"  「{raw}」不是数字，保留原值 {default}"))
        return default
    if not -90.0 <= val <= 90.0:
        print(yellow(f"  纬度需在 -90 ~ 90 之间，保留原值 {default}"))
        return default
    return raw


def _input_lon(prompt: str, default: str) -> str:
    """读取经度：-180 ~ 180。"""
    raw = _input(prompt, default).strip()
    try:
        val = float(raw)
    except ValueError:
        print(yellow(f"  「{raw}」不是数字，保留原值 {default}"))
        return default
    if not -180.0 <= val <= 180.0:
        print(yellow(f"  经度需在 -180 ~ 180 之间，保留原值 {default}"))
        return default
    return raw


def _edit_location(cfg: Dict[str, Any]) -> None:
    """编辑推送位置（地名 + 经纬度）。"""
    loc = cfg.setdefault("location", {})
    print()
    print(bold("  ── 推送位置设置 ──"))
    print(dim("  填写你所在位置的经纬度，程序按此坐标获取天气。\n"
              "  经纬度可用地图 App（长按地点取点）或\n"
              "  https://www.latlong.net 等在线工具查询。\n"
              "  纬度范围 -90 ~ 90（北纬为正），经度范围 -180 ~ 180（东经为正）。"))
    loc["name"] = _input(
        "  地名（显示在邮件标题，如「北京市朝阳区」）",
        loc.get("name", "北京市"),
    )
    loc["lat"] = _input_lat("  纬度", loc.get("lat", "39.90"))
    loc["lon"] = _input_lon("  经度", loc.get("lon", "116.41"))
    print(green(
        f"  已更新位置：{loc.get('name')}  "
        f"({loc.get('lat')}, {loc.get('lon')})"
    ))


def _edit_send(cfg: Dict[str, Any]) -> None:
    print()
    print(bold("  ── 发送策略 ──"))
    cfg["send"]["always_send"] = _input_bool(
        "  没有预警时也发送普通天气简报邮件",
        bool(cfg["send"].get("always_send", True)),
    )
    print(green("  已更新发送策略"))


# ------------------------------------------------------------ 抓取流程


def _load_api_key(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """若 API KEY 为空，提示用户现场填写。"""
    if cfg["qweather"].get("api_key"):
        return cfg
    print(yellow("\n  尚未配置和风天气 API KEY，无法获取天气数据。"))
    key = _input_secret("  API KEY", "")
    if key:
        cfg["qweather"]["api_key"] = key
        host = _input("  API Host（新版控制台专属域名，可留空）", "")
        if host:
            cfg["qweather"]["api_host"] = host
        save_config(cfg)
        print(green("  已保存"))
    return cfg


def fetch_and_evaluate(
    cfg: Dict[str, Any], verbose: bool = True
) -> AlertResult:
    """抓取天气并产出预警结果。"""
    loc = cfg["location"]
    client = QWeatherClient(
        api_key=cfg["qweather"].get("api_key", ""),
        api_host=cfg["qweather"].get("api_host", ""),
        api_version=cfg["qweather"].get("api_version", "auto"),
        location_name=loc.get("name", "北京市"),
    )
    if verbose:
        print(dim(f"  正在获取 {loc.get('name')} 天气…"))
    report: WeatherReport = client.fetch(loc["lat"], loc["lon"])

    now_temp = report.now.temp if report.now else None

    # 先算温度变化（用旧历史），再写入今天的记录
    temp_change = compare_with_previous(now_temp)

    if cfg["temperature"].get("record_history", True) and now_temp is not None:
        tmr = report.tomorrow()
        record_today(
            now_temp,
            source=(
                "obs" if report.now and report.now.temp is not None else "forecast"
            ),
            forecast_max=(tmr.temp_max if tmr else None),
            forecast_min=(tmr.temp_min if tmr else None),
        )

    result = evaluate(
        now=report.now,
        tomorrow=report.tomorrow(),
        today=report.today(),
        temp_change=temp_change,
        cfg=cfg,
    )
    if verbose:
        ver = report.api_version
        print(dim(f"  数据来源：和风天气 {ver} 接口"))
        for err in report.errors:
            print(dim(f"  · {err}"))
    return result


def _print_result(result: AlertResult) -> None:
    print()
    if result.has_alert:
        level = result.highest_level
        head = "  ⚠  触发预警" if level == "danger" else "  !  需要关注"
        print(red(head) if level == "danger" else yellow(head))
        for item in result.alerts:
            tag = red("  [紧急]") if item.level == "danger" else yellow("  [注意]")
            print(f"{tag} {bold(item.title)}")
            print(f"         {dim(item.detail)}")
    else:
        print(green("  ✓  无恶劣天气，气温平稳"))
    tmr = result.tomorrow
    if tmr:
        print(
            f"\n  明日天气：{tmr.fx_date} {tmr.weekday}  "
            f"{tmr.text_day} / {tmr.text_night}  {tmr.temp_range}"
        )
    if result.now and result.now.temp is not None:
        print(f"  当前实况：{result.now.temp:.1f}℃  {result.now.text}")
    print()


def do_send(cfg: Dict[str, Any], dry_run: bool = False) -> int:
    """执行完整的「抓取 → 判断 → 发信」流程。"""
    errors, warnings = validate(cfg)
    for w in warnings:
        print(yellow(f"  提醒：{w}"))
    if errors:
        print(red("\n  配置不完整，无法发送："))
        for e in errors:
            print(red(f"    · {e}"))
        print(dim("\n  请运行 weatheremail 进入设置补全。\n"))
        return 2

    try:
        result = fetch_and_evaluate(cfg)
    except WeatherError as exc:
        print(red(f"\n  获取天气失败：{exc}\n"))
        return 3

    _print_result(result)

    # 无预警且设置为不发普通简报时提前结束
    if not result.has_alert and not cfg["send"].get("always_send", True):
        print(dim("  无预警，且已设置不发送普通简报，本次不发信。\n"))
        return 0

    subject = render_subject(result, cfg)
    html_body = render_email(result, cfg)

    if dry_run:
        path = preview_dir() / (
            "preview-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".html"
        )
        path.write_text(html_body, encoding="utf-8")
        print(green(f"  已生成预览文件（未发信）：{path}"))
        print(dim(f"  邮件主题：{subject}"))
        print(dim(f"  正文大小：{len(html_body.encode('utf-8')) / 1024:.1f} KB\n"))
        return 0

    try:
        info = send_mail(cfg, subject, html_body)
    except MailError as exc:
        print(red(f"\n  发送邮件失败：{exc}\n"))
        return 4

    size_kb = info["size"] / 1024
    print(green("  ✓ 邮件已发送"))
    print(dim(f"      收件人：{', '.join(info['recipients'])}"))
    print(dim(f"      主题　：{info['subject']}"))
    print(dim(f"      大小　：{size_kb:.1f} KB  ({info['host']}:{info['port']})\n"))
    return 0


def show_history() -> None:
    rows = history_tail(14)
    print()
    print(bold("  ── 温度历史（最近 14 条）──"))
    if not rows:
        print(dim("  （暂无记录，执行一次天气抓取后会写入）"))
        print()
        return
    for row in rows:
        temp = row["temp"]
        temp_s = f"{temp:6.1f} ℃" if isinstance(temp, (int, float)) else "   --   "
        print(f"  {row['date']}   {temp_s}   {dim(row['recorded_at'])}")
    print()
    print(dim(f"  数据文件：{config_path().parent / 'history.json'}"))
    print()


# ------------------------------------------------------------ 菜单主循环


def settings_menu(cfg: Dict[str, Any]) -> int:
    """交互式设置主循环。"""
    dirty = False
    while True:
        _print_settings(cfg)
        if dirty:
            print(yellow("  * 有未保存的修改"))
        try:
            choice = input("  请选择操作: ").strip().lower()
        except EOFError:
            print()
            break

        if not choice:
            continue
        if choice == "q":
            if dirty and not _input_bool("  有未保存修改，确认放弃？", False):
                continue
            print(dim("  已退出，未保存修改。"))
            return 0
        if choice == "s":
            save_config(cfg)
            print(green(f"  已保存到 {config_path()}"))
            errors, warnings = validate(cfg)
            for w in warnings:
                print(yellow(f"  提醒：{w}"))
            if errors:
                print(yellow("  尚缺以下必填项（不影响保存）："))
                for e in errors:
                    print(yellow(f"    · {e}"))
            dirty = False
            continue
        if choice == "1":
            _edit_location(cfg); dirty = True
        elif choice == "2":
            _edit_qweather(cfg); dirty = True
        elif choice == "3":
            _edit_smtp(cfg); dirty = True
        elif choice == "4":
            _edit_recipients(cfg); dirty = True
        elif choice == "5":
            _edit_severe(cfg); dirty = True
        elif choice == "6":
            _edit_temperature(cfg); dirty = True
        elif choice == "7":
            _edit_send(cfg); dirty = True
        elif choice == "8":
            save_config(cfg); dirty = False
            if _input_bool("  将真实发送一封邮件到收件人，确认继续？", False):
                do_send(cfg, dry_run=False)
            else:
                print(dim("  已取消"))
        elif choice == "9":
            draft = dict(cfg)
            save_config(draft); dirty = False
            do_send(cfg, dry_run=True)
        elif choice in ("h", "history"):
            show_history()
        elif choice in ("help", "?"):
            _print_settings(cfg)
        else:
            print(yellow(f"  无法识别的选项「{choice}」"))
    return 0


# ------------------------------------------------------------ CLI 入口


USAGE = f"""\
{__appname__} {__version__} — 天气邮件推送程序

用法：
  {__appname__}                 进入交互式设置界面
  {__appname__} -email          获取天气并发送天气邮件
  {__appname__} -preview        只抓取并生成邮件预览，不发送
  {__appname__} -test           发送一封配置测试邮件（非天气内容）
  {__appname__} -config         显示当前设置
  {__appname__} -history        查看温度历史记录
  {__appname__} -help           显示本帮助

说明：
  程序在 {config_path().parent}
  下保存配置与温度历史。无预警时发送普通天气简报，
  出现恶劣天气或大幅升降温时发送预警邮件。
"""


def _cmd_test(cfg: Dict[str, Any]) -> int:
    """发送配置测试邮件。"""
    errors, warnings = validate(cfg)
    for w in warnings:
        print(yellow(f"  提醒：{w}"))
    if errors:
        print(red("\n  配置不完整，无法发送测试邮件："))
        for e in errors:
            print(red(f"    · {e}"))
        print()
        return 2

    subject = f"{cfg.get('subject_prefix', '【天气邮件】')}配置测试邮件"
    body = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        "<title>weatheremail 配置测试</title></head>"
        '<body style="margin:0;padding:24px;background:#f4f6f8;'
        'font-family:-apple-system,\'PingFang SC\',\'Microsoft YaHei\',sans-serif;">'
        '<div style="max-width:560px;margin:0 auto;background:#fff;'
        'border-radius:9px;overflow:hidden;border:1px solid #e4e6eb;">'
        '<div style="background:#1565c0;color:#fff;padding:18px 22px;'
        'font-size:18px;font-weight:700;">weatheremail 配置测试</div>'
        '<div style="padding:20px 22px;color:#2b2b2b;font-size:14px;'
        'line-height:1.8;">'
        "如果你收到了这封邮件，说明 SMTP 配置正确，天气预警通知可以正常投递。"
        f'<div style="margin-top:14px;padding:12px 14px;background:#f4f6f8;'
        f'border-radius:6px;font-size:13px;color:#7a7a7a;">'
        f"收件人：{', '.join(cfg.get('recipients', []))}<br>"
        f"发件人：{cfg['smtp'].get('sender', '')}<br>"
        f"SMTP：{cfg['smtp'].get('host')}:{cfg['smtp'].get('port')}</div>"
        "</div></div></body></html>"
    )
    try:
        info = send_mail(cfg, subject, body)
    except MailError as exc:
        print(red(f"\n  发送测试邮件失败：{exc}\n"))
        return 4
    print(green("  ✓ 测试邮件已发送"))
    print(dim(f"      收件人：{', '.join(info['recipients'])}"))
    print(dim(f"      服务器：{info['host']}:{info['port']}\n"))
    return 0


def _cmd_show_config(cfg: Dict[str, Any]) -> int:
    _print_settings(cfg)
    errors, warnings = validate(cfg)
    for w in warnings:
        print(yellow(f"  提醒：{w}"))
    if errors:
        print(yellow("  尚缺必填项："))
        for e in errors:
            print(yellow(f"    · {e}"))
    print()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # 无参数 → 设置界面
    if not argv:
        cfg = load_config()
        # 首次运行时给个引导
        if not config_path().is_file():
            print()
            print(bold(cyan(f"  欢迎使用 {__appname__} — 天气邮件推送程序")))
            print(dim("  首次运行，请先完成必要设置：API KEY、SMTP、收件人。"))
        return settings_menu(cfg)

    # 参数归一化：同时支持 -email 与 --email
    arg = argv[0]
    flag = arg.lstrip("-").lower()
    extra = argv[1:]

    if flag in ("help", "h", "?"):
        print(USAGE)
        return 0

    cfg = load_config()

    if flag == "email":
        return do_send(cfg, dry_run=False)
    if flag in ("preview", "dry-run", "dryrun"):
        return do_send(cfg, dry_run=True)
    if flag in ("test", "test-mail", "testmail"):
        return _cmd_test(cfg)
    if flag == "config":
        return _cmd_show_config(cfg)
    if flag == "history":
        show_history()
        return 0
    if flag in ("version", "v"):
        print(f"{__appname__} {__version__}")
        return 0
    if flag == "settings":
        return settings_menu(cfg)

    print(red(f"  无法识别的参数：{arg}"))
    print()
    print(USAGE)
    return 1
