"""通知通道（标准库：smtplib / urllib / subprocess，零第三方依赖）。

- email：163 SMTP 默认（F16/F17）
- macos：osascript 本地弹窗（F18，仅 Darwin）
- bark：HTTP Push（F18，iPhone Bark App）
"""
from __future__ import annotations

import json
import platform
import smtplib
import ssl
import subprocess
import urllib.error
import urllib.request
from email.message import EmailMessage
from typing import Any, Callable, Dict, List, Optional

from tea.config.config_store import Config, load_config

CHANNEL_EMAIL = "email"
CHANNEL_MACOS = "macos"
CHANNEL_BARK = "bark"
ALL_CHANNELS = (CHANNEL_EMAIL, CHANNEL_MACOS, CHANNEL_BARK)

# 测试注入：selftest 可替换 sender，避免真实 SMTP / osascript / HTTP
_TEST_SENDER: Optional[Callable[..., None]] = None
_TEST_MACOS: Optional[Callable[..., None]] = None
_TEST_BARK: Optional[Callable[..., None]] = None


def _email_cfg(cfg: Config) -> dict:
    return cfg.get("notify.email") or {}


def _macos_cfg(cfg: Config) -> dict:
    return cfg.get("notify.macos") or {}


def _bark_cfg(cfg: Config) -> dict:
    return cfg.get("notify.bark") or {}


def smtp_ready(cfg: Optional[Config] = None) -> bool:
    """SMTP 必填项齐全（不检查 alert / weekly 业务开关）。"""
    cfg = cfg or load_config()
    ec = _email_cfg(cfg)
    if not ec.get("enabled"):
        return False
    if not ec.get("smtp_host") or not ec.get("smtp_user"):
        return False
    if not ec.get("smtp_password") or not ec.get("from_addr"):
        return False
    to = ec.get("to_addrs") or []
    return bool(to)


def macos_ready(cfg: Optional[Config] = None) -> bool:
    """macOS 本地通知可用（Darwin + 开关开）。"""
    if platform.system() != "Darwin":
        return False
    cfg = cfg or load_config()
    mc = _macos_cfg(cfg)
    return bool(mc.get("enabled", True))


def bark_ready(cfg: Optional[Config] = None) -> bool:
    """Bark Push 必填项齐全。"""
    cfg = cfg or load_config()
    bc = _bark_cfg(cfg)
    if not bc.get("enabled"):
        return False
    return bool((bc.get("key") or "").strip())


def active_channels(cfg: Optional[Config] = None) -> List[str]:
    """当前 alert 会尝试的通道列表（显式 alert.channels 或按各通道 enabled+ready 推断）。"""
    cfg = cfg or load_config()
    explicit = cfg.get("alert.channels")
    if explicit:
        return [str(c) for c in explicit if c in ALL_CHANNELS]
    out: List[str] = []
    if smtp_ready(cfg):
        out.append(CHANNEL_EMAIL)
    if macos_ready(cfg):
        out.append(CHANNEL_MACOS)
    if bark_ready(cfg):
        out.append(CHANNEL_BARK)
    return out


def alert_notify_ready(cfg: Optional[Config] = None) -> bool:
    """观察池提醒（F16/F18）：alert 开启且至少一个通知通道就绪。"""
    cfg = cfg or load_config()
    if not cfg.get("alert.enabled"):
        return False
    return bool(active_channels(cfg))


def email_configured(cfg: Optional[Config] = None) -> bool:
    """兼容旧名：等同 alert_notify_ready。"""
    return alert_notify_ready(cfg)


def send_email(cfg: Optional[Config] = None, subject: str = "", body: str = "",
               to_addrs: Optional[List[str]] = None,
               subject_prefix: Optional[str] = None,
               sender: Optional[Callable[..., None]] = None) -> Dict[str, Any]:
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

    prefix = str(subject_prefix or ec.get("subject_prefix") or "[TEA观察]")
    full_subject = f"{prefix} {subject}".strip()

    fn = sender or _TEST_SENDER or _smtp_send
    try:
        fn(host=host, port=port, use_tls=bool(ec.get("smtp_use_tls", True)),
           user=user, password=password, from_addr=from_addr,
           to_addrs=recipients, subject=full_subject, body=body)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


def _escape_applescript(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace('"', '\\"')


def send_macos_notification(title: str, body: str, sound: str = "default",
                            sender: Optional[Callable[..., None]] = None) -> Dict[str, Any]:
    """macOS 右上角弹窗（osascript display notification）。"""
    if platform.system() != "Darwin":
        return {"ok": False, "error": "非 macOS 环境"}
    fn = sender or _TEST_MACOS or _macos_send
    try:
        fn(title=title, body=body, sound=sound or "default")
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


def send_bark(cfg: Optional[Config] = None, title: str = "", body: str = "",
              sender: Optional[Callable[..., None]] = None) -> Dict[str, Any]:
    """Bark HTTP Push（POST JSON 到 server/key）。"""
    cfg = cfg or load_config()
    bc = _bark_cfg(cfg)
    key = (bc.get("key") or "").strip()
    if not key:
        return {"ok": False, "error": "Bark key 未配置"}
    server = str(bc.get("server") or "https://api.day.app").rstrip("/")
    group = str(bc.get("group") or "TEA观察")
    fn = sender or _TEST_BARK or _bark_send
    try:
        fn(server=server, key=key, title=title, body=body, group=group)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


def send_alert(cfg: Optional[Config] = None, subject: str = "", body: str = "",
               channels: Optional[List[str]] = None,
               email_sender: Optional[Callable[..., None]] = None,
               macos_sender: Optional[Callable[..., None]] = None,
               bark_sender: Optional[Callable[..., None]] = None) -> Dict[str, Any]:
    """向 alert 活跃通道发送提醒。任一通道成功即 ``ok: True``。"""
    cfg = cfg or load_config()
    chs = channels or active_channels(cfg)
    if not chs:
        return {"ok": False, "error": "无可用通知通道", "channels": {}}

    results: Dict[str, Dict[str, Any]] = {}
    for ch in chs:
        if ch == CHANNEL_EMAIL:
            results[ch] = send_email(cfg, subject=subject, body=body, sender=email_sender)
        elif ch == CHANNEL_MACOS:
            mc = _macos_cfg(cfg)
            results[ch] = send_macos_notification(
                title=subject, body=body,
                sound=str(mc.get("sound") or "default"),
                sender=macos_sender,
            )
        elif ch == CHANNEL_BARK:
            results[ch] = send_bark(cfg, title=subject, body=body, sender=bark_sender)
        else:
            results[ch] = {"ok": False, "error": f"未知通道 {ch}"}

    ok = any(r.get("ok") for r in results.values())
    return {"ok": ok, "channels": results}


def _ca_bundle_paths() -> List[str]:
    """候选 CA bundle 路径（按优先级）。macOS 自带 Python 常缺默认 CA 链。"""
    paths: List[str] = []
    try:
        import certifi
        paths.append(certifi.where())
    except ImportError:
        pass
    paths.extend([
        "/etc/ssl/cert.pem",
        "/private/etc/ssl/cert.pem",
    ])
    return paths


def _ssl_context() -> ssl.SSLContext:
    """构建 SMTP TLS 上下文；依次尝试 certifi / 系统 CA bundle。"""
    for cafile in _ca_bundle_paths():
        try:
            return ssl.create_default_context(cafile=cafile)
        except (OSError, ssl.SSLError):
            continue
    return ssl.create_default_context()


def _smtp_send(host: str, port: int, use_tls: bool, user: str, password: str,
               from_addr: str, to_addrs: List[str], subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(body)

    if use_tls and port == 465:
        ctx = _ssl_context()
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
        return

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        if use_tls:
            smtp.starttls(context=_ssl_context())
        smtp.login(user, password)
        smtp.send_message(msg)


def _macos_send(title: str, body: str, sound: str) -> None:
    script = (
        f'display notification "{_escape_applescript(body)}" '
        f'with title "{_escape_applescript(title)}" '
        f'sound name "{_escape_applescript(sound)}"'
    )
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise RuntimeError(err)


def _bark_send(server: str, key: str, title: str, body: str, group: str) -> None:
    url = f"{server}/{key}"
    payload = json.dumps({
        "title": title,
        "body": body,
        "group": group,
        "icon": "https://day.app/assets/images/avatar.jpg",
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc


def set_test_sender(fn: Optional[Callable[..., None]]) -> None:
    """selftest 专用：注入 mock email sender。"""
    global _TEST_SENDER
    _TEST_SENDER = fn


def set_test_macos(fn: Optional[Callable[..., None]]) -> None:
    global _TEST_MACOS
    _TEST_MACOS = fn


def set_test_bark(fn: Optional[Callable[..., None]]) -> None:
    global _TEST_BARK
    _TEST_BARK = fn
