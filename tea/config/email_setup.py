"""email_setup.py — 观察池邮件提醒邮箱配置向导（默认 163 SMTP）。

引导用户填写发件邮箱、SMTP 授权码、收件人，并可选发送测试邮件验证连通。
密码仅存 tea_config.json（gitignore），向导与日志均不打印明文。
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from tea.config.config_store import Config, load_config
from tea.core import notify, utils
from tea.phases import IO

WIZARD_VERSION = 1
MAX_RETRY = 3

MODE_SAVED = "saved"
MODE_ABORT = "abort"
MODE_TEST_ONLY = "test_only"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SMTP_163_STEPS = [
    "1. 浏览器打开 https://mail.163.com 并登录",
    "2. 设置 → POP3/SMTP/IMAP → 开启 SMTP 服务",
    "3. 按提示生成「授权码」（不是登录密码）",
    "4. 把授权码填到本向导（仅写入本地 tea_config.json）",
]


def _mask_secret(value: str) -> str:
    if not value:
        return "（未配置）"
    if len(value) <= 2:
        return "**"
    return value[0] + "*" * (len(value) - 2) + value[-1]


def _valid_email(addr: str) -> bool:
    return bool(_EMAIL_RE.match((addr or "").strip()))


def _parse_recipients(raw: str) -> List[str]:
    parts = [p.strip() for p in (raw or "").replace(";", ",").split(",")]
    return [p for p in parts if p]


def format_status(cfg: Config) -> str:
    """当前邮件配置摘要（密码打码）。"""
    ec = cfg.get("notify.email") or {}
    to = ec.get("to_addrs") or []
    lines = [
        "",
        "===== 当前邮件配置 =====",
        f"  观察提醒总开关 alert.enabled：{'开' if cfg.get('alert.enabled') else '关'}",
        f"  邮件通道 notify.email.enabled：{'开' if ec.get('enabled') else '关'}",
        f"  SMTP 服务器：{ec.get('smtp_host') or '—'}:{ec.get('smtp_port') or 465}",
        f"  发件邮箱 smtp_user：{ec.get('smtp_user') or '（未填）'}",
        f"  授权码 smtp_password：{_mask_secret(ec.get('smtp_password') or '')}",
        f"  发件人 from_addr：{ec.get('from_addr') or '（未填）'}",
        f"  收件人 to_addrs：{', '.join(to) if to else '（未填）'}",
        f"  配置完整：{'是' if notify.email_configured(cfg) else '否'}",
    ]
    return "\n".join(lines)


def _welcome(io: IO, cfg: Config) -> None:
    io.say("")
    io.say("=" * 56)
    io.say("观察池邮件提醒 · 邮箱配置向导（F16）")
    io.say("  用于盘中回踩就绪时发邮件提醒；引擎不自动下单。")
    io.say(f"  配置文件：{cfg.path}")
    io.say("=" * 56)
    io.say("")
    io.say("【163 邮箱授权码获取步骤】")
    for step in SMTP_163_STEPS:
        io.say(f"  {step}")
    io.say(format_status(cfg))


def _ask_email(io: IO, title: str, key: str, current: str,
               hint: str = "") -> Optional[str]:
    io.say("")
    io.say(f"  · {title}")
    if hint:
        io.say(f"    {hint}")
    for _ in range(MAX_RETRY):
        raw = io.ask("    输入邮箱", key, current or "")
        if raw is None:
            return None
        addr = str(raw).strip()
        if not addr:
            if current:
                return current
            io.say("    ✗ 邮箱不能为空")
        elif not _valid_email(addr):
            io.say("    ✗ 格式无效，示例：yourname@163.com")
        else:
            return addr
        if not io.interactive:
            return current or None
    io.say(f"    多次无效，采用当前值 {current or '—'}")
    return current or None


def _ask_auth_code(io: IO, current: str) -> Optional[str]:
    io.say("")
    io.say("  · SMTP 授权码　[notify.email.smtp_password]")
    io.say("    163 邮箱设置里生成的授权码，不是登录密码。")
    hint = "（已配置，回车保留）" if current else ""
    for _ in range(MAX_RETRY):
        raw = io.ask_secret(f"    输入授权码{hint}", "smtp_password",
                            default=current or None)
        if raw is None:
            return None
        code = str(raw).strip()
        if not code:
            if current:
                return current
            io.say("    ✗ 授权码不能为空（去 163 邮箱设置生成）")
        else:
            return code
        if not io.interactive:
            return current or None
    io.say("    多次无效，保留原授权码")
    return current or None


def _ask_recipients(io: IO, default_addr: str,
                    current: List[str]) -> Optional[List[str]]:
    io.say("")
    io.say("  · 收件邮箱　[notify.email.to_addrs]")
    io.say("    提醒发到哪个邮箱；多个用英文逗号分隔。")
    default_str = ", ".join(current) if current else default_addr
    for _ in range(MAX_RETRY):
        raw = io.ask("    输入收件人", "to_addrs", default_str)
        if raw is None:
            return None
        addrs = _parse_recipients(raw)
        if not addrs:
            io.say("    ✗ 至少填一个收件邮箱")
        elif any(not _valid_email(a) for a in addrs):
            io.say("    ✗ 存在无效邮箱，请检查格式")
        else:
            return addrs
        if not io.interactive:
            return current or [default_addr]
    io.say(f"    多次无效，采用 {default_str}")
    return current or [default_addr]


def _collect(io: IO, cfg: Config) -> Optional[Dict[str, Any]]:
    ec = cfg.get("notify.email") or {}
    cur_user = str(ec.get("smtp_user") or ec.get("from_addr") or "")
    cur_pwd = str(ec.get("smtp_password") or "")
    cur_to = list(ec.get("to_addrs") or [])

    user = _ask_email(io, "发件邮箱（163）　[notify.email.smtp_user]",
                      "smtp_user", cur_user,
                      "用于 SMTP 登录，一般填你的 163 邮箱地址。")
    if user is None:
        return None

    pwd = _ask_auth_code(io, cur_pwd)
    if pwd is None:
        return None

    same = io.ask_yes("    收件人与发件人相同？", "to_same_as_from",
                      default=not cur_to or cur_to == [cur_user or user])
    if same:
        recipients = [user]
    else:
        recipients = _ask_recipients(io, user, cur_to)
        if recipients is None:
            return None

    return {
        "smtp_user": user,
        "smtp_password": pwd,
        "from_addr": user,
        "to_addrs": recipients,
    }


def format_summary(values: Dict[str, Any]) -> str:
    to = values.get("to_addrs") or []
    lines = [
        "",
        "===== 即将写入 =====",
        f"  alert.enabled：开",
        f"  notify.email.enabled：开",
        f"  SMTP：smtp.163.com:465（SSL）",
        f"  发件邮箱：{values.get('smtp_user')}",
        f"  授权码：{_mask_secret(values.get('smtp_password') or '')}",
        f"  收件人：{', '.join(to)}",
    ]
    return "\n".join(lines)


def apply_values(values: Dict[str, Any], cfg: Config) -> str:
    """写入配置并开启提醒开关。"""
    cfg.set("alert.enabled", True)
    cfg.set("notify.email.enabled", True)
    cfg.set("notify.email.smtp_host", "smtp.163.com")
    cfg.set("notify.email.smtp_port", 465)
    cfg.set("notify.email.smtp_use_tls", True)
    cfg.set("notify.email.smtp_user", values["smtp_user"])
    cfg.set("notify.email.smtp_password", values["smtp_password"])
    cfg.set("notify.email.from_addr", values.get("from_addr") or values["smtp_user"])
    cfg.set("notify.email.to_addrs", list(values.get("to_addrs") or []))
    cfg.set("meta.email_wizard_version", WIZARD_VERSION)
    cfg.set("meta.email_wizard_at", utils.now().strftime("%Y-%m-%d %H:%M:%S"))
    return cfg.save()


def send_test_email(cfg: Config, io: Optional[IO] = None,
                    sender: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """发送测试邮件；sender 可注入 mock（selftest 用）。"""
    io = io or IO()
    if not notify.email_configured(cfg):
        return {"ok": False, "error": "邮件配置不完整，请先完成向导"}
    body = (
        "这是一封 TEA 观察池提醒测试邮件。\n\n"
        "若收到此信，说明 163 SMTP 配置正确。\n"
        "盘中满足回踩就绪等条件时，系统会发提醒；请手动 eval/run，引擎不自动下单。\n"
    )
    res = notify.send_email(cfg, subject="测试邮件", body=body, sender=sender)
    if res.get("ok"):
        io.say("  ✓ 测试邮件已发送，请查收收件箱（含垃圾箱）")
    else:
        err = str(res.get("error") or "")
        io.say(f"  ✗ 测试邮件发送失败：{err}")
        if "CERTIFICATE_VERIFY_FAILED" in err:
            io.say("    提示：macOS 可执行 pip3 install certifi，或确认 /etc/ssl/cert.pem 存在")
        elif "authentication failed" in err.lower() or "535" in err:
            io.say("    提示：请检查 163 授权码是否正确（不是登录密码）；可重新运行向导填写")
    return res


def run_wizard(cfg: Optional[Config] = None, io: Optional[IO] = None,
               test_only: bool = False,
               sender: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """跑邮箱配置向导。test_only=True 时仅测当前配置不发向导提问。"""
    cfg = cfg or load_config()
    io = io or IO()

    if test_only:
        io.say(format_status(cfg))
        res = send_test_email(cfg, io=io, sender=sender)
        return {"mode": MODE_TEST_ONLY, "saved": False,
                "test_ok": bool(res.get("ok")), "path": cfg.path}

    _welcome(io, cfg)
    collected = _collect(io, cfg)
    if collected is None:
        io.say("  已中断，配置未改动")
        return {"mode": MODE_ABORT, "saved": False, "values": {}, "path": cfg.path}

    io.say(format_summary(collected))
    raw = io.ask("确认写入配置 [Y/n]", "email_confirm", "y")
    if raw is None:
        io.say("  已中断，配置未改动")
        return {"mode": MODE_ABORT, "saved": False, "values": collected, "path": cfg.path}
    if str(raw).strip().lower() not in ("y", "yes", "1", "true", "是"):
        io.say("  未保存")
        return {"mode": MODE_ABORT, "saved": False, "values": collected, "path": cfg.path}

    path = apply_values(collected, cfg)
    io.say("")
    io.say(f"  ✓ 邮件配置已保存：{path}")

    test_ok = False
    if io.ask_yes("  现在发送一封测试邮件验证 SMTP？", "send_test", default=True):
        res = send_test_email(cfg, io=io, sender=sender)
        test_ok = bool(res.get("ok"))

    io.say("")
    io.say("  下一步：安装 launchd 定时扫描（若尚未安装）")
    io.say("    ./ops/install-launchd-watch-alert.sh")
    io.say("  手动试跑：tea watch-alert --force  或  ./ops/watch-alert-cron.sh")
    return {"mode": MODE_SAVED, "saved": True, "values": collected,
            "test_ok": test_ok, "path": path}
