"""notify_setup.py — 观察池提醒通道配置向导（macOS 弹窗 + Bark Push + 邮件入口）。

邮件 SMTP 仍走 ``tea setup-email``；本向导负责即时 Push 通道。
"""
from __future__ import annotations

import platform
from typing import Any, Dict, Optional

from tea.config.config_store import Config, load_config
from tea.core import notify
from tea.phases import IO

WIZARD_VERSION = 1

BARK_STEPS = [
    "1. iPhone 安装 Bark App（App Store 搜「Bark」）",
    "2. 打开 App → 复制设备 Key（一串字母数字）",
    "3. 把 Key 填到本向导（仅存本地 tea_config.json）",
    "4. 可选：自建服务器用户改 notify.bark.server",
]


def _mask_key(value: str) -> str:
    if not value:
        return "（未配置）"
    if len(value) <= 6:
        return value[:2] + "****"
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def format_status(cfg: Config) -> str:
    """当前通知通道摘要。"""
    ec = cfg.get("notify.email") or {}
    mc = cfg.get("notify.macos") or {}
    bc = cfg.get("notify.bark") or {}
    chs = notify.active_channels(cfg)
    lines = [
        "",
        "===== 当前提醒通道 =====",
        f"  alert.enabled：{'开' if cfg.get('alert.enabled') else '关'}",
        f"  活跃通道：{', '.join(chs) if chs else '（无）'}",
        f"  macOS 弹窗：{'开' if mc.get('enabled', True) else '关'}"
        + ("（仅 macOS 有效）" if platform.system() == "Darwin" else "（当前非 macOS）"),
        f"  Bark Push：{'开' if bc.get('enabled') else '关'}  key={_mask_key(bc.get('key') or '')}",
        f"  邮件 SMTP：{'开' if ec.get('enabled') else '关'}"
        f"（完整度：{'是' if notify.smtp_ready(cfg) else '否'}）",
        "  邮件配置：运行 tea setup-email",
    ]
    return "\n".join(lines)


def _welcome(io: IO, cfg: Config) -> None:
    io.say("")
    io.say("=" * 56)
    io.say("观察池提醒 · 通知通道配置（F18）")
    io.say("  盘中回踩就绪时推送提醒；引擎不自动下单。")
    io.say(f"  配置文件：{cfg.path}")
    io.say("=" * 56)
    io.say(format_status(cfg))
    io.say("")


def run_wizard(cfg: Optional[Config] = None, io: Optional[IO] = None,
               test_only: bool = False) -> Dict[str, Any]:
    """交互式配置 macOS / Bark 通道；可选测试推送。"""
    cfg = cfg or load_config()
    io = io or IO()
    out: Dict[str, Any] = {"saved": False, "test_ok": False}

    if test_only:
        return _test_push(cfg, io)

    _welcome(io, cfg)

    # macOS
    if platform.system() == "Darwin":
        cur = bool((cfg.get("notify.macos") or {}).get("enabled", True))
        ans = io.ask("启用 macOS 本地弹窗？", "macos_enable",
                     "y" if cur else "n")
        enable_macos = _parse_yn(ans or "", default=cur)
        cfg.set("notify.macos.enabled", enable_macos)
    else:
        io.say("  当前非 macOS，跳过本地弹窗配置。")
        cfg.set("notify.macos.enabled", False)

    # Bark
    io.say("")
    io.say("【Bark Push 配置步骤】")
    for step in BARK_STEPS:
        io.say(f"  {step}")
    bc = cfg.get("notify.bark") or {}
    cur_bark = bool(bc.get("enabled"))
    ans_b = io.ask("启用 Bark Push？", "bark_enable", "y" if cur_bark else "n")
    enable_bark = _parse_yn(ans_b or "", default=cur_bark)
    cfg.set("notify.bark.enabled", enable_bark)

    if enable_bark:
        cur_key = (bc.get("key") or "").strip()
        key_raw = io.ask("Bark 设备 Key", "bark_key", cur_key)
        key = (key_raw or "").strip()
        if key:
            cfg.set("notify.bark.key", key)
        elif not cur_key:
            io.say("  ✗ 未填 Key，Bark 通道将不可用。")
            cfg.set("notify.bark.enabled", False)

    cfg.set("alert.enabled", True)
    cfg.save()
    out["saved"] = True
    io.say("")
    io.say(f"  ✓ 通知配置已保存：{cfg.path}")
    io.say(format_status(cfg))

    test_ans = io.ask("发送测试推送？", "send_test", "y")
    if (test_ans or "y").strip().lower() in ("", "y", "yes"):
        test_res = _test_push(cfg, io)
        out["test_ok"] = test_res.get("test_ok", False)

    io.say("")
    io.say("  邮件周报仍走 tea setup-email；观察提醒 launchd 已装则无需重装。")
    io.say("  手动试跑：tea watch-alert --force")
    return out


def _test_push(cfg: Config, io: IO) -> Dict[str, Any]:
    body = (
        "这是一封 TEA 观察池提醒测试。\n"
        "若收到此消息，说明通知通道配置正确。\n"
        "引擎不自动下单，请手动 eval/run。"
    )
    res = notify.send_alert(
        cfg,
        subject="测试推送",
        body=body,
    )
    out: Dict[str, Any] = {"test_ok": bool(res.get("ok")), "channels": res.get("channels")}
    if res.get("ok"):
        ok_ch = [k for k, v in (res.get("channels") or {}).items() if v.get("ok")]
        io.say(f"  ✓ 测试推送成功：{', '.join(ok_ch)}")
    else:
        io.say("  ✗ 测试推送失败：")
        for ch, detail in (res.get("channels") or {}).items():
            if not detail.get("ok"):
                io.say(f"    {ch}: {detail.get('error')}")
        if not res.get("channels"):
            io.say(f"    {res.get('error')}")
        io.say("  提示：邮件通道请运行 tea setup-email；Bark 需有效 Key。")
    return out


def _parse_yn(raw: str, default: bool) -> bool:
    s = (raw or "").strip().lower()
    if not s:
        return default
    if s in ("y", "yes", "是", "开", "true", "1"):
        return True
    if s in ("n", "no", "否", "关", "false", "0"):
        return False
    return default
