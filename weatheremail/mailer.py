"""SMTP 邮件发送.

只发送 HTML 单版本邮件（按需求不做纯文本替代版本）。
支持 SSL(465) 与 STARTTLS(587/25) 两种方式，自动按端口判断。
"""

from __future__ import annotations

import smtplib
import socket
import ssl
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from typing import Any, Dict, List, Optional

TIMEOUT = 30


class MailError(RuntimeError):
    """发送邮件失败。"""


def _split_sender(sender: str) -> str:
    """从 '名字 <a@b.com>' 或 'a@b.com' 中取出邮箱地址。"""
    sender = (sender or "").strip()
    if "<" in sender and ">" in sender:
        return sender[sender.rindex("<") + 1 : sender.rindex(">")].strip()
    return sender


def build_message(
    subject: str,
    html_body: str,
    sender: str,
    sender_name: str,
    recipients: List[str],
) -> MIMEText:
    """构造 HTML 邮件。"""
    mail = MIMEText(html_body, "html", "utf-8")
    mail["Subject"] = Header(subject, "utf-8")
    addr = _split_sender(sender)
    if sender_name:
        mail["From"] = formataddr((str(Header(sender_name, "utf-8")), addr))
    else:
        mail["From"] = addr
    mail["To"] = ", ".join(recipients)
    mail["Date"] = formatdate(localtime=True)
    mail["Message-ID"] = make_msgid(domain="weatheremail")
    mail["X-Mailer"] = "weatheremail"
    return mail


def send_mail(
    cfg: Dict[str, Any],
    subject: str,
    html_body: str,
    recipients: Optional[List[str]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """发送邮件，返回发送结果摘要。

    dry_run=True 时只构造邮件不实际投递，用于预览与测试。
    """
    smtp_cfg = cfg.get("smtp", {})
    host = (smtp_cfg.get("host") or "").strip()
    port = int(smtp_cfg.get("port") or 465)
    password = smtp_cfg.get("password") or ""
    sender = (smtp_cfg.get("sender") or "").strip()
    sender_name = smtp_cfg.get("sender_name") or ""
    to_list = list(recipients or cfg.get("recipients") or [])

    if not host:
        raise MailError("SMTP 服务器地址未配置")
    if not sender:
        raise MailError("发件人邮箱未配置")
    if not to_list:
        raise MailError("收件人列表为空")

    message = build_message(subject, html_body, sender, sender_name, to_list)
    raw = message.as_string()
    size = len(raw.encode("utf-8"))

    if dry_run:
        return {
            "sent": False,
            "dry_run": True,
            "host": host,
            "port": port,
            "recipients": to_list,
            "subject": subject,
            "size": size,
        }

    use_ssl = bool(smtp_cfg.get("use_ssl", True))
    # 常见约定：465 走 SSL，587/25 走 STARTTLS
    if port == 465:
        use_ssl = True

    context = ssl.create_default_context()
    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(
                host, port, timeout=TIMEOUT, context=context
            )
        else:
            server = smtplib.SMTP(host, port, timeout=TIMEOUT)
            server.ehlo()
            if server.has_extn("starttls"):
                server.starttls(context=context)
                server.ehlo()
    except (socket.timeout,) as exc:
        raise MailError(f"连接 SMTP 服务器超时（{host}:{port}）") from exc
    except (socket.gaierror,) as exc:
        raise MailError(f"无法解析 SMTP 服务器地址：{host}") from exc
    except (ConnectionRefusedError, OSError) as exc:
        raise MailError(
            f"无法连接 SMTP 服务器 {host}:{port} —— {exc}"
        ) from exc

    try:
        if password:
            login_user = _split_sender(sender)
            try:
                server.login(login_user, password)
            except smtplib.SMTPAuthenticationError as exc:
                raise MailError(
                    "SMTP 认证失败：请检查发件人邮箱与授权码是否正确。"
                    "注意多数邮箱需要使用「授权码」而不是登录密码。"
                ) from exc
        server.sendmail(_split_sender(sender), to_list, raw)
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailError(f"收件人被拒绝：{exc.recipients}") from exc
    except smtplib.SMTPSenderRefused as exc:
        raise MailError(f"发件人被拒绝：{exc.smtp_error}") from exc
    except smtplib.SMTPDataError as exc:
        raise MailError(f"邮件内容被服务器拒绝：{exc.smtp_error}") from exc
    except smtplib.SMTPException as exc:
        raise MailError(f"SMTP 发送失败：{exc}") from exc
    finally:
        try:
            server.quit()
        except Exception:
            try:
                server.close()
            except Exception:
                pass

    return {
        "sent": True,
        "dry_run": False,
        "host": host,
        "port": port,
        "recipients": to_list,
        "subject": subject,
        "size": size,
    }
