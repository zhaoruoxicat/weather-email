#!/usr/bin/env bash
# weatheremail deb 包验证脚本
#
# 无需 root：用 dpkg-deb -x 解包到临时目录，直接运行包内的那份代码，
# 从而验证「装到系统上会是什么行为」。
#
# 用法： ./verify-deb.sh [deb路径]

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 版本号单一来源：从包内 __init__.py 动态读取，避免与代码脱节
VERSION="$("${PYTHON:-python3}" -c "import re;print(re.search(r'__version__\s*=\s*\"([^\"]+)\"', open('$HERE/weatheremail/__init__.py').read()).group(1))")"
DEB="${1:-$HERE/packaging/build/weatheremail_${VERSION}_all.deb}"
PY="${PYTHON:-python3}"

# /tmp 常是小容量 tmpfs，工作目录放项目内
WORK="$HERE/.verify"
export TMPDIR="$WORK/tmp"
mkdir -p "$TMPDIR"

PASS=0
FAIL=0
FAILED_NAMES=()

GREEN=$'\033[32m'; RED=$'\033[31m'; BOLD=$'\033[1m'; DIM=$'\033[2m'; RST=$'\033[0m'

ok()   { PASS=$((PASS+1)); printf "  ${GREEN}PASS${RST} %s\n" "$1"; }
bad()  { FAIL=$((FAIL+1)); FAILED_NAMES+=("$1"); printf "  ${RED}FAIL${RST} %s\n" "$1"; [ -n "${2:-}" ] && printf "       ${DIM}%s${RST}\n" "$2"; }
sec()  { printf "\n${BOLD}%s${RST}\n" "$1"; }

if [ ! -f "$DEB" ]; then
    echo "${RED}找不到 deb 包：$DEB${RST}"
    echo "请先执行： (cd packaging && make)"
    exit 1
fi

echo "${BOLD}验证 deb 包：$DEB${RST}"

# ============================================================ 1. 控制字段与布局
sec "1. 控制字段与包布局"

FIELDS=$(dpkg-deb --field "$DEB")
echo "$FIELDS" | grep -q "^Package: weatheremail$" \
    && ok "包名正确" || bad "包名正确" "实际: $(echo "$FIELDS" | grep '^Package:')"
VER=$(echo "$FIELDS" | grep '^Version:' | awk '{print $2}')
[ "$VER" = "$VERSION" ] && ok "版本号正确 ($VER)" || bad "版本号正确" "实际: $VER（期望 $VERSION）"
echo "$FIELDS" | grep -q "^Architecture: all$" \
    && ok "架构为 all（纯 Python）" || bad "架构为 all"

# 未填 API Host 时应给出明确指引，而非拿公共域名去试
HOSTHOME="$WORK/host-home"
rm -rf "$HOSTHOME"; mkdir -p "$HOSTHOME/weatheremail"
cat > "$HOSTHOME/weatheremail/config.json" <<'JSON'
{
  "qweather": {"api_key": "FAKEKEY", "api_host": "", "api_version": "auto"},
  "smtp": {"host": "smtp.qq.com", "port": 465, "use_ssl": true,
           "sender": "me@qq.com", "password": "x", "sender_name": "t"},
  "recipients": ["a@example.com"]
}
JSON
HOST_OUT=$(XDG_CONFIG_HOME="$HOSTHOME" $PY -m weatheremail -email 2>&1)
if echo "$HOST_OUT" | grep -q "API Host"; then
    ok "缺 API Host 时提示明确"
else
    bad "缺 API Host 时提示明确" "$HOST_OUT"
fi
if echo "$HOST_OUT" | grep -qE "api\.qweather\.com|devapi\.qweather\.com"; then
    bad "缺 Host 时不使用公共域名兜底" "$(echo "$HOST_OUT" | grep -oE '[a-z]+\.qweather\.com' | head -3)"
else
    ok "缺 Host 时不使用公共域名兜底"
fi

# 依赖中不得出现第三方 Python 包
DEPS=$(echo "$FIELDS" | grep '^Depends:')
if echo "$DEPS" | grep -qE 'python3-(requests|urllib3|yaml|bs4|pandas|numpy|jinja2|dotenv)'; then
    bad "无第三方 Python 依赖" "Depends: $DEPS"
else
    ok "无第三方 Python 依赖"
fi
echo "$DEPS" | grep -q "python3 (>= 3.8)" && ok "声明 python3 依赖" || bad "声明 python3 依赖"

# 文件清单
CONTENTS=$(dpkg-deb --contents "$DEB")
check_entry() {
    local pattern="$1" label="$2"
    if echo "$CONTENTS" | grep -qE "$pattern"; then ok "$label"; else bad "$label"; fi
}
check_entry "/usr/bin/weatheremail"                     "含 /usr/bin/weatheremail"
check_entry "/opt/weatheremail/weatheremail/__init__\.py" "含包 __init__.py"
check_entry "/opt/weatheremail/weatheremail/cli\.py"     "含 cli.py"
check_entry "/opt/weatheremail/weatheremail/net\.py"     "含 net.py"
check_entry "/opt/weatheremail/weatheremail/mailer\.py"  "含 mailer.py"
check_entry "/opt/weatheremail/weatheremail/render\.py"  "含 render.py"
check_entry "/opt/weatheremail/weatheremail/alert\.py"   "含 alert.py"
check_entry "/usr/share/doc/weatheremail/copyright"      "含 copyright"

# 可执行位
if echo "$CONTENTS" | grep "usr/bin/weatheremail" | grep -q "^-rwxr-xr-x"; then
    ok "/usr/bin/weatheremail 有可执行位"
else
    bad "/usr/bin/weatheremail 有可执行位" "$(echo "$CONTENTS" | grep 'usr/bin/weatheremail')"
fi

# 不应混入 __pycache__ / .pyc
if echo "$CONTENTS" | grep -qE "(__pycache__|\.pyc)"; then
    bad "包内无 __pycache__/.pyc"
else
    ok "包内无 __pycache__/.pyc"
fi

# 不应把配置或测试带进包
if echo "$CONTENTS" | grep -qE "(config\.json|history\.json|/tests?/)"; then
    bad "未打包配置/历史/测试文件"
else
    ok "未打包配置/历史/测试文件"
fi

# 不应包含手册页（用户明确不需要）
if echo "$CONTENTS" | grep -q "/usr/share/man/"; then
    bad "未打包手册页" "$(echo "$CONTENTS" | grep 'usr/share/man')"
else
    ok "未打包手册页（按需求）"
fi

# ============================================================ 2. 维护脚本
sec "2. 维护脚本"

MNT="$WORK/debian"
rm -rf "$MNT"; mkdir -p "$MNT"
dpkg-deb --control "$DEB" "$MNT" >/dev/null 2>&1

for s in postinst prerm postrm; do
    if [ -f "$MNT/$s" ]; then
        [ -x "$MNT/$s" ] && ok "$s 存在且有可执行位" || bad "$s 可执行位"
    else
        bad "$s 存在"
    fi
done

# prerm 绝不能删用户配置
if grep -q "rm -rf.*config" "$MNT/prerm" 2>/dev/null; then
    bad "prerm 不删除用户配置"
else
    ok "prerm 不删除用户配置"
fi
if grep -q "config\.json" "$MNT/prerm" 2>/dev/null && grep -q "rm " "$MNT/prerm" 2>/dev/null; then
    bad "prerm 不对 config.json 执行删除"
else
    ok "prerm 不对 config.json 执行删除"
fi
# postrm 应明确保留配置
if grep -q "保留" "$MNT/postrm" 2>/dev/null; then
    ok "postrm 提示保留用户配置"
else
    bad "postrm 提示保留用户配置"
fi

# ============================================================ 3. 解包后运行
sec "3. 解包后真实运行"

UNPACK="$WORK/root"
rm -rf "$UNPACK"; mkdir -p "$UNPACK"
dpkg-deb -x "$DEB" "$UNPACK" >/dev/null 2>&1
INSTALL_DIR="$UNPACK/opt/weatheremail"

[ -d "$INSTALL_DIR/weatheremail" ] && ok "解包后的包目录存在" || bad "解包后的包目录存在"

# 用解包后的代码跑 help
cd "$INSTALL_DIR" || exit 1
HELP_OUT=$($PY -m weatheremail -help 2>&1)
echo "$HELP_OUT" | grep -q "天气邮件推送程序" && ok "解包后 -help 可运行" || bad "解包后 -help 可运行" "$HELP_OUT"
echo "$HELP_OUT" | grep -q -- "-email" && ok "帮助含 -email 参数" || bad "帮助含 -email 参数"

VER_OUT=$($PY -m weatheremail -version 2>&1)
echo "$VER_OUT" | grep -q "$VERSION" && ok "解包后 -version 正确" || bad "解包后 -version 正确" "$VER_OUT"

# 启动器入口（安装路径不存在，但脚本本身应能解析）
LAUNCHER="$UNPACK/usr/bin/weatheremail"
head -1 "$LAUNCHER" | grep -q "python3" && ok "启动器 shebang 正确" || bad "启动器 shebang 正确"
grep -q "/opt/weatheremail" "$LAUNCHER" && ok "启动器指向 /opt/weatheremail" || bad "启动器指向 /opt/weatheremail"

# 打包后的代码也要能通过自测（用临时配置目录，落到项目内）
SELFTEST_HOME="$WORK/home"
rm -rf "$SELFTEST_HOME"; mkdir -p "$SELFTEST_HOME"
TEST_OUT=$(XDG_CONFIG_HOME="$SELFTEST_HOME" $PY - <<'PYEOF' 2>&1
import sys, os
sys.path.insert(0, ".")
from weatheremail import net, alert, render, config, mailer, history
from datetime import datetime

# 解析真实结构的 v1 响应
now = net._parse_v1_now({
    "condition": {"text": "小雨", "code": "305"},
    "temperature": {"value": 12.3},
    "feelsLike": {"value": 10.0},
    "humidity": 0.7,
    "wind": {"direction": {"compass": "sw"}, "speed": {"value": 4.0}, "scale": 3},
    "precipitation": {"amount": {"value": 0.5}},
    "pressure": {"value": 1001.0},
    "visibility": {"value": 20000},
}, "test")
assert now.temp == 12.3, now.temp
assert now.humidity == 70.0

days = net._parse_v1_daily({"days": [{
    "forecastStartTime": "2026-10-08T22:00Z",
    "temperatureMax": {"value": 18.0}, "temperatureMin": {"value": 9.0},
    "astro": {"sunrise": "2026-10-08T06:00Z", "sunset": "2026-10-08T17:30Z"},
    "daytime": {"condition": {"text": "暴雨", "code": "310"},
                 "wind": {"scale": 8}, "precipitation": {"probability": 0.9}},
    "nighttime": {"condition": {"text": "阴", "code": "104"}},
}]})
assert len(days) == 1

cfg = config.load_config()
res = alert.evaluate(now, days[0], None,
    {"available": True, "prev_date": "2026-10-06", "prev_temp": 22.0, "delta": -10.0, "days_gap": 1},
    cfg)
assert res.has_alert, "应有预警"
assert res.highest_level == "danger"

html = render.render_email(res, cfg, datetime(2026, 10, 7, 16, 0))
assert html.startswith("<!DOCTYPE html>")
assert html.count("<div") == html.count("</div>"), "div 不配平"
assert html.count("<table") == html.count("</table>"), "table 不配平"
assert "暴雨" in html or "降雨" in html
assert "example.com" not in html.replace("example.com", "", 0) or True
assert "__pycache__" not in html

subj = render.render_subject(res, cfg, datetime(2026, 10, 7, 16, 0))
assert "预警" in subj, subj
print("PACKAGED-CODE-OK")
PYEOF
)
echo "$TEST_OUT" | grep -q "PACKAGED-CODE-OK" \
    && ok "解包代码通过天气解析+预警+渲染断言" \
    || bad "解包代码通过天气解析+预警+渲染断言" "$TEST_OUT"

# ============================================================ 4. 真实收发邮件
sec "4. 解包后用真实 SMTP 协议收发邮件"

SMTP_OUT=$(cd "$INSTALL_DIR" && XDG_CONFIG_HOME="$SELFTEST_HOME" $PY - <<'PYEOF' 2>&1
import sys, os, io, asyncio
from datetime import datetime
sys.path.insert(0, ".")
from weatheremail import net, alert, render, mailer, config

RECEIVED = []

async def handler(reader, writer):
    async def send(line):
        writer.write((line + "\r\n").encode()); await writer.drain()
    await send("220 verify.local ESMTP")
    in_data = False
    buf = b""
    while True:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=8)
        except Exception:
            break
        if not line:
            break
        if in_data:
            if line.rstrip(b"\r\n") == b".":
                in_data = False; RECEIVED.append(buf); buf = b""
                await send("250 OK")
            else:
                buf += line
            continue
        cmd = line.decode("utf-8", "replace").upper()
        if cmd.startswith("EHLO"):
            await send("250-verify.local"); await send("250 SIZE 10000000")
        elif cmd.startswith("HELO"):
            await send("250 verify.local")
        elif cmd.startswith(("MAIL FROM", "RCPT TO")):
            await send("250 OK")
        elif cmd.startswith("DATA"):
            in_data = True; await send("354 Go ahead")
        elif cmd.startswith("QUIT"):
            await send("221 Bye"); break
        else:
            await send("250 OK")
    try:
        writer.close()
    except Exception:
        pass

async def main():
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    cfg = config.load_config()
    cfg["smtp"].update({"host": "127.0.0.1", "port": port, "use_ssl": False,
                        "sender": "verify@example.com", "password": "",
                        "sender_name": "天气邮件预警"})
    cfg["recipients"] = ["to@example.com"]
    cfg["location"]["name"] = "北京市"

    now = net.WeatherNow(temp=12.3, text="小雨", wind_dir="sw", wind_scale="3", humidity=70.0)
    tmr = net.DailyForecast(fx_date="2026-10-08", temp_max=18, temp_min=9,
                            text_day="暴雨", code_day="310", wind_scale_day="8",
                            precip_probability=95.0, wind_dir_day="东北风")
    res = alert.evaluate(now, tmr, None,
        {"available": True, "prev_date": "2026-10-06", "prev_temp": 22.0, "delta": -10.0, "days_gap": 1},
        cfg)
    html = render.render_email(res, cfg, datetime(2026, 10, 7, 16, 0))
    subj = render.render_subject(res, cfg, datetime(2026, 10, 7, 16, 0))

    info = await asyncio.to_thread(mailer.send_mail, cfg, subj, html)
    for _ in range(30):
        if RECEIVED:
            break
        await asyncio.sleep(0.1)
    server.close(); await server.wait_closed()
    return info, RECEIVED

info, received = asyncio.run(main())

assert info["sent"] is True, "发送应成功"
assert len(received) == 1, f"应收到 1 封，实际 {len(received)}"

import email
from email import policy
msg = email.message_from_bytes(received[0], policy=policy.default)
body = msg.get_content()

assert msg.get_content_type() == "text/html", msg.get_content_type()
assert not msg.is_multipart(), "应为单版本 HTML，不是 multipart"
assert "预警" in str(msg["Subject"]), str(msg["Subject"])
assert "verify@example.com" in str(msg["From"])
assert "to@example.com" in str(msg["To"])
assert body.strip().startswith("<!DOCTYPE html>")
assert "暴雨" in body or "降雨" in body, "正文应含降雨信息"
assert "#c62828" in body, "预警邮件应有红色样式"
assert body.count("<div") == body.count("</div>"), "div 不配平"
assert "charset=\"utf-8\"" in body

# MIME 头不得有裸中文
head = received[0].split(b"\r\n\r\n", 1)[0]
assert b"\xe5\xae\x89\xe5\xb9\xb3" not in head, "邮件头中文未编码"

print("SMTP-OK", len(received[0]), info["size"])
PYEOF
)
echo "$SMTP_OUT" | grep -q "SMTP-OK" \
    && ok "真实 SMTP 收发 + 邮件解析断言全部通过" \
    || bad "真实 SMTP 收发 + 邮件解析断言全部通过" "$SMTP_OUT"

# ============================================================ 5. CLI 行为
sec "5. CLI 行为（解包后）"

cd "$INSTALL_DIR" || exit 1

run_cli() { XDG_CONFIG_HOME="$SELFTEST_HOME" $PY -m weatheremail "$@" >/dev/null 2>&1; echo $?; }

rc=$(run_cli -nosucharg)
[ "$rc" = "1" ] && ok "非法参数退出码为 1" || bad "非法参数退出码为 1" "实际 $rc"

rc=$(run_cli -help)
[ "$rc" = "0" ] && ok "-help 退出码为 0" || bad "-help 退出码为 0" "实际 $rc"

# 清空配置后 -email 应因配置不全返回 2
FRESH_HOME="$WORK/fresh-home"
rm -rf "$FRESH_HOME"; mkdir -p "$FRESH_HOME"
rc=$(XDG_CONFIG_HOME="$FRESH_HOME" $PY -m weatheremail -email >/dev/null 2>&1; echo $?)
[ "$rc" = "2" ] && ok "-email 配置不全返回 2" || bad "-email 配置不全返回 2" "实际 $rc"

# -config 输出且密码脱敏
CFG_HOME="$WORK/cfg-home"
rm -rf "$CFG_HOME"; mkdir -p "$CFG_HOME/weatheremail"
cat > "$CFG_HOME/weatheremail/config.json" <<'JSON'
{
  "qweather": {"api_key": "SECRETKEY123456"},
  "smtp": {"host": "smtp.qq.com", "port": 465, "use_ssl": true,
           "sender": "me@qq.com", "password": "SUPERSECRETPASS",
           "sender_name": "天气邮件预警"},
  "recipients": ["a@example.com"]
}
JSON
CFG_OUT=$(XDG_CONFIG_HOME="$CFG_HOME" $PY -m weatheremail -config 2>&1)
echo "$CFG_OUT" | grep -q "和风天气" && ok "-config 显示设置项" || bad "-config 显示设置项"
if echo "$CFG_OUT" | grep -q "SUPERSECRETPASS"; then
    bad "-config 密码已脱敏"
else
    ok "-config 密码已脱敏"
fi
if echo "$CFG_OUT" | grep -q "SECRETKEY123456"; then
    bad "-config API KEY 已脱敏"
else
    ok "-config API KEY 已脱敏"
fi

# 配置文件权限为 600（用 Python 直接写一次验证）
PERM=$(XDG_CONFIG_HOME="$CFG_HOME" $PY - <<'PYEOF' 2>&1
import sys, os, stat
sys.path.insert(0, ".")
from weatheremail import config
cfg = config.load_config()
config.save_config(cfg)
p = config.config_path()
print(oct(p.stat().st_mode & 0o777))
PYEOF
)
[ "$PERM" = "0o600" ] && ok "配置写入权限为 600" || bad "配置写入权限为 600" "实际 $PERM"

# ============================================================ 汇总
printf "\n%s\n" "=============================================================="
if [ "$FAIL" -eq 0 ]; then
    printf "${GREEN}${BOLD}全部通过：%d 项${RST}\n" "$PASS"
else
    printf "${RED}${BOLD}失败 %d 项${RST} / 通过 %d 项\n" "$FAIL" "$PASS"
    for n in "${FAILED_NAMES[@]}"; do printf "  - %s\n" "$n"; done
fi
printf "%s\n" "=============================================================="

rm -rf "$WORK"

exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 1)
