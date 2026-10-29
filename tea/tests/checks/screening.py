"""筛选与 scan_details selftest 用例。"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from tea.screening import screener as screener_mod
from tea.screening.scan_anchor import ANCHOR_WINRATE, sidecar_scan_details_name

if TYPE_CHECKING:
    from tea.config.config_store import Config
    from tea.selftest import Suite


def check_winrate_sidecar_scan_details(t: Suite, cfg: Config, _mk) -> None:
    """胜率通道写 sidecar scan_details，不污染主文件。"""
    from tea.core import utils

    t.head("胜率选股 · scan_details sidecar")
    sc = screener_mod.Screener(cfg, _mk)
    today = utils.today_str()
    scan_id = "wr-test-scan-001"
    result = {
        "at": f"{today} 15:08",
        "scan_id": scan_id,
        "verdict": "pending",
        "tier": None,
        "notes": ["胜率评分门槛 3 分"],
        "sectors": [{"name": "玻纤制造", "rank": 2}],
        "candidates": [{"code": "605006", "veto": {"passed": False}}],
        "buyable": [],
        "watch": [{"code": "605006"}],
        "near_miss": [],
        "candidates_n": 1,
        "veto_passed_n": 1,
    }
    canonical = os.path.join(cfg.data_dir(), f"scan_details_{today}.json")
    if os.path.isfile(canonical):
        os.remove(canonical)
    sc._write_scan_log(result, scan_anchor=ANCHOR_WINRATE)
    side_name = sidecar_scan_details_name(today, ANCHOR_WINRATE, scan_id)
    side_path = os.path.join(cfg.data_dir(), side_name)
    t.ok("sidecar 文件已写入", os.path.isfile(side_path))
    t.ok("主 scan_details 未写入", not os.path.isfile(canonical))
    if os.path.isfile(side_path):
        payload = utils.read_json(side_path)
        t.eq("sidecar scan_anchor", payload.get("scan_anchor"), ANCHOR_WINRATE)
        t.eq("sidecar timestamp", payload.get("timestamp"), f"{today} 15:08")
        t.eq("sidecar 含 VETO 候选", len(payload.get("candidates") or []), 1)


def check_winrate_result_display(t: Suite, cfg: Config) -> None:
    """胜率选股控制台：操作建议与种子一致，胜率分 X/7，T+3 获利概率。"""
    from tea.screening import preflight, seed_report

    t.head("胜率选股 · 控制台展示")
    watch_ev = {
        "code": "002842", "name": "翔鹭钨业", "sector_name": "钨",
        "sector": {"name": "钨", "rank": 1},
        "quote": {"price": 12.5, "chg_pct": 7.12},
        "identity": {"tier": "龙头", "score": 88},
        "stage": {"stage": "突破"},
        "winrate_score": 3,
        "winrate_detail": "板块排名1≤3 +3；突破阶段 -1；身份分88∈85-93 +1",
        "winrate_gate": "突破阶段（历史胜率 6%）→ 降级观察",
        "winrate_would_buy": True,
    }
    preflight.apply_winrate_t3(watch_ev, cfg)
    result = {
        "at": "2026-09-15 11:30",
        "winrate_threshold": 3,
        "winrate_max": 7,
        "sectors": [{"name": "钨", "rank": 1}],
        "buyable": [],
        "watch": [watch_ev],
        "watch_display_limit": 3,
        "notes": [],
    }
    text = seed_report.format_winrate_result(result, cfg)
    t.ok("含观察操作建议", "[观察]" in text or "观察" in text)
    t.ok("胜率分 3/7 格式", "3/7" in text and "门槛≥3" in text)
    t.ok("T+3 获利展示", "T+3获利" in text and "门槛≥35%" in text)
    t.ok("分制说明展示", "分制说明" in text and "共振" in text and "T+3获利" in text)
    t.ok("硬闸原因展示", "突破阶段" in text)
    t.ok("归因明细展示", "板块排名1≤3 +3" in text)
    t.ok("T+3 因子明细", "综合" in text and "非收益承诺" in text)


def check_winrate_t3_prob(t: Suite, cfg: Config) -> None:
    """T+3 获利概率：几何均值 + 硬闸 + 落盘字段。"""
    from tea.analysis import followthrough as ft
    from tea.screening import preflight, screener as screener_mod

    t.head("胜率选股 · T+3 获利概率")
    ev_break = {
        "sector": {"rank": 3},
        "stage": {"stage": "突破"},
        "quote": {"chg_pct": 5.34},
    }
    t3 = ft.estimate_t3_up_prob(ev_break, cfg)
    t.ok("突破票有 T+3 概率", t3.get("prob") is not None)
    t.ok("突破票概率偏低", (t3.get("prob") or 1) < float(cfg.get("winrate.t3_prob.min_prob", 0.35)))
    t.ok("明细含因子", "板块" in t3.get("detail", "") and "综合" in t3.get("detail", ""))

    preflight.apply_winrate_t3(ev_break, cfg)
    t.ok("写入 winrate_t3_prob", ev_break.get("winrate_t3_prob") is not None)

    sc = screener_mod.Screener(cfg)
    note = sc._winrate_gate({
        "stage": {"stage": "萌芽"},
        "sector": {"rank": 2, "bk": "BK001", "name": "测试"},
        "pick_sector_bk": "BK001",
        "pick_sector_name": "测试",
        "pick_sector_rank": 2,
        "quote": {"chg_pct": 4.2},
        "winrate_score": 5,
        "verdict": preflight.VERDICT_PASS,
    })
    t.ok("萌芽前排可过 T+3 闸", note is None)

    low = sc._winrate_gate({
        "stage": {"stage": "萌芽"},
        "sector": {"rank": 3, "bk": "BK001", "name": "测试"},
        "pick_sector_bk": "BK001",
        "pick_sector_name": "测试",
        "pick_sector_rank": 3,
        "quote": {"chg_pct": 7.8},
        "winrate_score": 5,
        "winrate_t3_prob": 0.30,
        "winrate_t3_min": 0.35,
        "verdict": preflight.VERDICT_PASS,
    })
    t.ok("低 T+3 概率硬闸", low is not None and "T+3获利概率" in low)


def check_winrate_email(t: Suite, cfg: Config) -> None:
    """胜率选股有候选时发邮件；无候选/去重/开关守卫。"""
    from tea.screening import seed_report

    t.head("胜率选股 · 邮件通知")
    sent: list = []

    def _fake_sender(**kw):
        sent.append(kw)

    result = {
        "at": "2026-09-15 12:18",
        "scan_id": "wr-mail-test",
        "verdict": "pending",
        "winrate_threshold": 3,
        "winrate_max": 7,
        "sectors": [{"name": "风电设备", "rank": 3}],
        "buyable": [],
        "watch": [{
            "code": "603092", "name": "德力佳", "sector_name": "风电设备",
            "quote": {"price": 43.19, "chg_pct": 5.34},
            "identity": {"tier": "龙头", "score": 88},
            "stage": {"stage": "突破"},
            "total_score": 4, "pass_threshold": 7,
            "winrate_score": 2,
            "winrate_detail": "板块排名3≤3 +3；突破阶段 -1",
            "winrate_gate": "突破阶段（历史胜率 6%）→ 降级观察",
            "triggers": ["共振分补足 3 分（门槛 7）"],
        }],
        "watch_display_limit": 3,
        "notes": ["胜率评分门槛 3 分"],
    }

    cfg.set("winrate.email.enabled", True)
    cfg.set("winrate.email.dedupe_per_day", False)
    cfg.set("notify.email.enabled", True)
    cfg.set("notify.email.smtp_host", "smtp.163.com")
    cfg.set("notify.email.smtp_user", "test@163.com")
    cfg.set("notify.email.smtp_password", "secret")
    cfg.set("notify.email.from_addr", "test@163.com")
    cfg.set("notify.email.to_addrs", ["recv@163.com"])

    empty = seed_report.send_winrate_email({"buyable": [], "watch": []}, cfg)
    t.eq("无候选跳过", empty.get("skip"), "empty")

    cfg.set("winrate.email.enabled", False)
    off = seed_report.send_winrate_email(result, cfg)
    t.eq("开关关跳过", off.get("skip"), "winrate_email_disabled")
    cfg.set("winrate.email.enabled", True)

    cfg.set("notify.email.enabled", False)
    no_smtp = seed_report.send_winrate_email(result, cfg)
    t.eq("SMTP 未就绪", no_smtp.get("skip"), "email_not_configured")
    cfg.set("notify.email.enabled", True)

    subj = seed_report.winrate_email_subject(result, cfg)
    t.ok("主题含观察", "观察1只" in subj and "603092" in subj)

    body = seed_report.winrate_email_body(result, cfg)
    t.ok("正文无 ANSI", "\033[" not in body)
    t.ok("正文含操作建议", "【观察】" in body and "603092" in body)
    t.ok("正文含关键分", "胜率" in body and "共振" in body)
    t.ok("正文精简无冗长说明", "分制说明" not in body and "读分" not in body
         and "winrate_detail" not in body and "盘面：" not in body)
    t.ok("正文不含归因明细", "板块排名3≤3" not in body)
    t.ok("正文含总结", "总结：" in body)
    t.ok("正文 demo 格式", "涨幅5.34%" in body and "门槛≥3" in body)

    res = seed_report.send_winrate_email(result, cfg, sender=_fake_sender)
    t.ok("发送成功", res.get("ok") is True)
    t.eq("假 sender 被调用", len(sent), 1)
    t.ok("主题前缀", "[TEA胜率]" in sent[0].get("subject", ""))

    cfg.set("winrate.email.dedupe_per_day", True)
    seed_report.mark_winrate_email_sent(result, cfg)
    dup = seed_report.send_winrate_email(result, cfg, sender=_fake_sender)
    t.eq("同股同日去重", dup.get("skip"), "all_codes_already_sent")
    t.eq("去重不再发", len(sent), 1)

    result2 = dict(result)
    result2["watch"] = [{
        "code": "002842", "name": "翔鹭钨业", "sector_name": "钨",
        "quote": {"price": 12.5, "chg_pct": 7.12},
        "identity": {"tier": "龙头", "score": 88},
        "stage": {"stage": "突破"},
        "total_score": 3, "pass_threshold": 7,
        "winrate_score": 3,
        "winrate_detail": "板块排名1≤3 +3；突破阶段 -1",
        "winrate_gate": "突破阶段（历史胜率 6%）→ 降级观察",
    }]
    new_res = seed_report.send_winrate_email(result2, cfg, sender=_fake_sender)
    t.ok("新股票仍发", new_res.get("ok") is True)
    t.eq("新股票发信次数", len(sent), 2)
    t.ok("新股票主题", "002842" in (sent[-1].get("subject") or ""))


def check_seed_email(t: Suite, cfg: Config) -> None:
    """种子扫描完成后发邮件；精简摘要 / 去重 / 开关守卫。"""
    from tea.screening import seed_report
    from tea.screening.screener import VERDICT_PENDING, VERDICT_TRADEABLE

    t.head("种子扫描 · 邮件通知")
    sent: list = []

    def _fake_sender(**kw):
        sent.append(kw)

    result = {
        "at": "2026-09-15 14:30",
        "scan_id": "seed-mail-test",
        "verdict": VERDICT_PENDING,
        "sentiment": {"score": 37, "stance": "空仓", "cycle": "禁止新开"},
        "sectors": [{"name": "风电设备", "rank": 3}],
        "buyable": [],
        "watch": [{
            "code": "603092", "name": "德力佳",
            "quote": {"price": 43.19, "chg_pct": 5.34},
            "stage": {"stage": "突破"},
            "total_score": 4, "pass_threshold": 7,
            "winrate_score": 2,
            "winrate_t3_prob": 0.33,
            "winrate_t3_min": 0.35,
            "winrate_gate": "突破阶段（历史胜率 6%）→ 降级观察",
        }],
        "eve": [],
    }

    cfg.set("seed.email.enabled", True)
    cfg.set("seed.email.dedupe_per_day", False)
    cfg.set("seed.email.send_on_empty", False)
    cfg.set("notify.email.enabled", True)
    cfg.set("notify.email.smtp_host", "smtp.163.com")
    cfg.set("notify.email.smtp_user", "test@163.com")
    cfg.set("notify.email.smtp_password", "secret")
    cfg.set("notify.email.from_addr", "test@163.com")
    cfg.set("notify.email.to_addrs", ["recv@163.com"])

    empty = seed_report.send_seed_email(
        {"verdict": VERDICT_PENDING, "buyable": [], "watch": [], "eve": []}, cfg)
    t.eq("空仓且 send_on_empty=false 跳过", empty.get("skip"), "empty")

    cfg.set("seed.email.send_on_empty", True)
    empty2 = seed_report.send_seed_email(
        {"verdict": VERDICT_PENDING, "buyable": [], "watch": [], "eve": []},
        cfg, sender=_fake_sender)
    t.ok("空仓仍发", empty2.get("ok") is True)
    t.eq("空仓发信次数", len(sent), 1)

    cfg.set("seed.email.enabled", False)
    off = seed_report.send_seed_email(result, cfg)
    t.eq("开关关跳过", off.get("skip"), "seed_email_disabled")
    cfg.set("seed.email.enabled", True)

    subj = seed_report.seed_email_subject(result, cfg)
    t.ok("主题含观察", "观察1只" in subj and "603092" in subj)

    body = seed_report.seed_email_body(result, cfg)
    t.ok("正文无 ANSI", "\033[" not in body)
    t.ok("正文含操作建议", "【观察】" in body and "603092" in body)
    t.ok("正文含关键分", "共振" in body and "胜率" in body and "T+3" in body)
    t.ok("正文精简", "分制说明" not in body and "触发：" not in body and "裁决：" not in body)
    t.ok("正文含总结", "总结：" in body)
    t.ok("正文 demo 格式", "涨幅5.34%" in body and "共振4/" in body)

    buyable_result = dict(result)
    buyable_result["verdict"] = VERDICT_TRADEABLE
    buyable_result["buyable"] = [{
        "code": "600001", "name": "测试票",
        "quote": {"price": 10.0, "chg_pct": 4.2},
        "stage": {"stage": "萌芽"},
        "total_score": 7, "pass_threshold": 6,
        "winrate_score": 4,
        "winrate_t3_prob": 0.55,
        "winrate_t3_min": 0.35,
    }]
    buy_subj = seed_report.seed_email_subject(buyable_result, cfg)
    t.ok("可买主题", "可买1只" in buy_subj and "600001" in buy_subj)

    cfg.set("seed.email.dedupe_per_day", True)
    seed_report.mark_seed_email_sent(cfg)
    dup = seed_report.send_seed_email(result, cfg, sender=_fake_sender)
    t.eq("同日去重", dup.get("skip"), "already_sent")
    t.eq("去重不再发", len(sent), 1)

    res = seed_report.send_seed_email(result, cfg, force=True, sender=_fake_sender)
    t.ok("force 可再发", res.get("ok") is True)
    t.ok("主题前缀", "[TEA种子]" in sent[-1].get("subject", ""))


def check_scan_at_persist(t: Suite, cfg: Config) -> None:
    """scan_at 随 record_seed 落盘（来自扫描 result.at）。"""
    from tea.analysis import followthrough as ft_mod
    from tea.runtime.runner import _ft_entries

    t.head("种子样本 · scan_at 落盘")
    result = {
        "at": "2026-09-11 14:32",
        "mode": "winrate",
        "winrate_threshold": 3,
        "sentiment": {"index": {}},
        "watch": [{
            "code": "605099", "name": "时点票",
            "quote": {"chg_pct": 6.6, "price": 12.0},
            "sector": {"name": "玻纤制造", "rank": 2},
            "stage": {"stage": "过热"},
            "winrate_score": 5,
            "track": "观察轨",
        }],
        "buyable": [],
    }
    entries = _ft_entries(result)
    t.eq("scan_at 带入 entries", entries[0].get("scan_at"), "2026-09-11 14:32")
    r = ft_mod.record_seed(entries, cfg, date="2026-09-11")
    t.eq("scan_at 样本落盘", r, {"added": 1, "skipped": 0, "updated": 0})
    rec = next(x for x in ft_mod.load_records(cfg) if x.get("code") == "605099")
    t.eq("scan_at 字段", rec.get("scan_at"), "2026-09-11 14:32")


def check_scheduled_winrate_scan(t: Suite, cfg: Config, mk) -> None:
    """winrate-scan launchd 调度：registry / plist / 守卫 / 日去重。"""
    import os

    from tea.config import schedules as sched_mod
    from tea.phases import IO
    from tea.runtime import runner as runner_mod

    t.head("胜率影子 · scheduled winrate-scan")

    t.ok("winrate.scheduled_enabled 默认开", cfg.get("winrate.scheduled_enabled") is True)
    t.eq("scheduler.winrate_scan 默认 14:35",
         sched_mod.format_hm(cfg.get("scheduler.winrate_scan.hour"),
                             cfg.get("scheduler.winrate_scan.minute")), "14:35")
    t.ok("launchd list 含 winrate_scan", "winrate_scan:" in sched_mod.trigger_summary(cfg))
    plist = sched_mod.render_plist("winrate_scan", cfg.data_dir(), python_exe="python3", cfg=cfg)
    t.ok("winrate plist 含 14:35",
         "<key>Hour</key><integer>14</integer>" in plist
         and "<key>Minute</key><integer>35</integer>" in plist
         and "com.tea.winrate-scan" in plist)
    t.ok("winrate plist 直调 python -m tea winrate-scan --scheduled",
         "<string>winrate-scan</string>" in plist
         and "<string>--scheduled</string>" in plist
         and "winrate-scan-cron.sh" not in plist)
    t.ok("winrate 日录类别", "winrate" in __import__("tea.core.logger", fromlist=["DAILY_CATEGORIES"]).DAILY_CATEGORIES)

    cfg.set("winrate.scheduled_enabled", False)
    skip = runner_mod.scheduled_winrate_scan(cfg=cfg, force=False)
    t.eq("关 scheduled 跳过", skip.get("skip"), "scheduled_disabled")
    cfg.set("winrate.scheduled_enabled", True)

    state_path = cfg.data_file("winrate_scheduled_state_file")
    if os.path.exists(state_path):
        os.remove(state_path)
    done = runner_mod.scheduled_winrate_scan(cfg=cfg, market=mk,
                                             io=IO(interactive=False, quiet=True), force=True)
    t.ok("force winrate 无 skip", "skip" not in done)
    t.ok("winrate state 已写入", os.path.exists(state_path))
    t.ok("winrate state 记录今日", bool(__import__("tea.core.utils", fromlist=["read_json"]).read_json(state_path, default={}).get("last_date")))
