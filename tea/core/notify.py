"""邮件通知（标准库 smtplib，零第三方依赖）。

默认 SMTP 为 163 邮箱（smtp.163.com:465 SSL）。密码仅存 tea_config.json。
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Callable, Dict, List, Optional, Union

from tea.config.config_store import Config, load_config

# 测试注入：selftest 可替换为内存 sender，避免真实 SMTP
_TEST_SENDER: Optional[Callable[..., Dict[str, Any]]] = None


def _email_cfg(cfg: Config) -> dict:
    return cfg.get("notify.email") or {}


def email_configured(cfg: Optional[Config] = None) -> bool:
    """邮件通道已启用且必填项齐全。"""
    cfg = cfg or load_config()
    ec = _email_cfg(cfg)
    if not cfg.get("alert.enabled") or not ec.get("enabled"):
        return False
    if not ec.get("smtp_host") or not ec.get("smtp_user"):
        return False
    if not ec.get("smtp_password") or not ec.get("from_addr"):
        return False
    to = ec.get("to_addrs") or []
    return bool(to)


def send_email(cfg: Optional[Config] = None, subject: str = "", body: str = "",
               to_addrs: Optional[List[str]] = None,
               sender: Optional[Callable[..., Dict[str, Any]]] = None) -> Dict[str, Any]:
    """发送纯文本邮件。成功返回 ``{ok: True}``；失败 ``{ok: False, error: ...}``。"""
    cfg = cfg or load_config()
    ec = _email_cfg(cfg)
    recipients = list(to_addrs or ec.get("to_addrs") or [])
    if not recipients:
        return {"ok": False, "error": "无收件人"}
    host = ec.get("smtp_host") or ""
    port = int(ec.get("smtp_port") or 465)
    user = ec.get("smtp_user") or ""
    password = ec.get("smtp_password") or ""
    from_addr = ec.get("from_addr") or user
    if not (host and user and password and from_addr):
        return {"ok": False, "error": "SMTP 配置不完整"}

    prefix = str(ec.get("subject_prefix") or "[TEA观察]")
    full_subject = f"{prefix} {subject}".strip()

    fn = sender or _TEST_SENDER or _smtp_send
    try:
        fn(host=host, port=port, use_tls=bool(ec.get("smtp_use_tls", True)),
           user=user, password=password, from_addr=from_addr,
           to_addrs=recipients, subject=full_subject, body=body)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


def _smtp_send(host: str, port: int, use_tls: bool, user: str, password: str,
               from_addr: str, to_addrs: List[str], subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(body)

    if use_tls and port == 465:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
        return

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        if use_tls:
            ctx = ssl.create_default_context()
            smtp.starttls(context=ctx)
        smtp.login(user, password)
        smtp.send_message(msg)


def set_test_sender(fn: Optional[Callable[..., Dict[str, Any]]]) -> None:
    """selftest 专用：注入 mock sender。"""
    global _TEST_SENDER
    _TEST_SENDER = fn
