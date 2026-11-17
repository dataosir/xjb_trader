"""周报与邮件 selftest 用例。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from tea.analysis import followthrough as ft_mod
from tea.reporting import weekly as weekly_mod

if TYPE_CHECKING:
    from tea.config.config_store import Config
    from tea.selftest import Suite


def check_email_digest(t: Suite, cfg: Config) -> None:
    """周报邮件精简摘要：含核心段、默认不含完整 Markdown。"""
    t.head("周报 · 邮件精简摘要")
    ft_mod.save_records([
        {"date": "2026-09-10", "code": "605006", "name": "山东玻纤",
         "mode": "winrate", "track": "观察轨", "winrate_score": 5,
         "winrate_gate": "过热阶段 → 降级观察", "winrate_would_buy": True,
         "pick_sector_name": "玻纤制造", "pick_sector_rank": 2, "stage": "过热",
         "tier": "热点降级档", "sector_chg": 3.2},
        {"date": "2026-09-09", "code": "600101", "name": "明星电力",
         "mode": "rule", "track": "可买", "total_score": 7, "pass_threshold": 6,
         "sector_name": "电力", "sector_rank": 2, "stage": "发酵",
         "tier": "严格档", "sector_chg": 4.6,
         "result": "win", "next_chg": 4.2, "chg_t3": 1.5, "chg_t5": 2.0},
    ], cfg)
    wk = {
        "since": "2026-09-08", "until": "2026-09-11",
        "days": ["2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"],
        "empty_days": 2, "forced_n": 0,
        "range": {
            "avg_sentiment": 51.0,
            "total_buyable": 1, "total_evaluations": 3,
            "verdicts": {"EMPTY": 2, "BUYABLE": 1},
            "reasons": {"姿态防守": 3, "胜率分不足": 2},
            "digests": [{
                "date": "2026-09-10", "sentiment_score": 51.0, "stance": "防守",
                "verdict": "EMPTY", "buyable_n": 0, "watch_n": 1,
                "top_reasons": {"过热硬闸": 1},
                "sectors": [{"name": "玻纤制造", "chg": 4.6, "rank": 2, "limit_up_count": 2}],
            }],
        },
        "week_perf": {"n": 0}, "week_trades": [],
        "watch_items": [], "decisions": {},
        "t3_attribution": ft_mod.t3_attribution(cfg),
        "mode_channels": ft_mod.mode_channel_stats(cfg),
    }
    body = weekly_mod.email_body(wk, cfg, report_path="reports/WEEKLY_test.md")
    t.ok("含核心一览", "核心一览" in body)
    t.ok("含空仓日对比", "空仓日对比" in body)
    t.ok("含本周主线", "本周主线" in body)
    t.ok("含本周选股", "605006" in body and "600101" in body)
    t.ok("含样本积累", "样本积累" in body)
    t.ok("含质量确认段", "质量确认" in body and "双确认" in body and "无确认" in body)
    t.ok("默认不含完整周报", "======== 完整周报 ========" not in body)
    t.ok("指向本地报告", "reports/WEEKLY_test.md" in body)

    cfg.set("weekly_email.include_full_report", True)
    full = weekly_mod.email_body(wk, cfg)
    t.ok("include_full_report 附完整段", "======== 完整周报 ========" in full)
    t.ok("完整段含纪律自查", "纪律自查" in full)
    cfg.set("weekly_email.include_full_report", False)


def check_quality_reports(t: Suite, cfg: Config) -> None:
    """质量确认（档位+板块）：落盘、日报、周报与邮件只读展示。"""
    from tea.analysis import shadow_lab as sl
    from tea.reporting import ops_summary as ops_mod
    from tea.screening import seed_report

    t.head("报告 · 质量确认")

    q2 = sl.quality_parts("严格档", 4.2)
    q1 = sl.quality_parts("热点降级档", 4.2)
    q0 = sl.quality_parts("严格档", 3.2)
    unknown = sl.quality_parts("严格档", None)
    t.eq("严格档+板块≥4 = 2", q2["score"], 2)
    t.eq("热点降级+板块≥4 = 1", q1["score"], 1)
    t.eq("严格档+板块<4 = 1", q0["score"], 1)
    t.ok("缺板块 = 未知", unknown["score"] is None and unknown["label"] == "未知")

    orig = ft_mod.load_records(cfg)
    try:
        ft_mod.save_records([], cfg, merge_disk=False)
        added = ft_mod.record_seed([
            {"code": "600101", "name": "明星电力", "tier": "严格档",
             "sector_rank": 2, "sector_chg": 4.6, "chg_pct": 4.2,
             "stage": "发酵", "mode": "winrate", "track": "可买",
             "winrate_score": 5},
            {"code": "600102", "name": "单确认票", "tier": "严格档",
             "sector_rank": 3, "sector_chg": 3.2, "chg_pct": 4.0,
             "stage": "萌芽"},
            {"code": "600103", "name": "无确认票", "tier": "热点降级档",
             "sector_rank": 4, "sector_chg": 3.1, "chg_pct": 5.1,
             "stage": "突破"},
        ], cfg, date="2026-09-10")
        t.eq("质量样本落盘", added.get("added"), 3)
        recs = ft_mod.load_records(cfg)
        first = next(r for r in recs if r.get("code") == "600101")
        t.eq("质量分落盘", first.get("shadow_lab_quality"), 2)
        for r in recs:
            if r.get("code") == "600101":
                r.update(next_chg=4.2, chg_t3=5.0, chg_t5=6.0)
            elif r.get("code") == "600102":
                r.update(next_chg=1.0, chg_t3=-1.0, chg_t5=0.5)
            else:
                r.update(next_chg=2.0, chg_t3=2.0, chg_t5=-2.0)
        ft_mod.save_records(recs, cfg, merge_disk=False)

        st = sl.quality_stats(cfg, since="2026-09-10", until="2026-09-10")
        t.eq("双确认 1 条", st["buckets"][2]["n"], 1)
        t.eq("单确认 1 条", st["buckets"][1]["n"], 1)
        t.eq("无确认 1 条", st["buckets"][0]["n"], 1)
        t.eq("双确认 T+3 1/1", st["buckets"][2]["t3"]["up"], 1)

        daily = "\n".join(sl.quality_daily_lines(cfg, "2026-09-10"))
        t.ok("日报质量段含股票", "今日 双确认 1" in daily and "600101" in daily)

        md = "\n".join(sl.quality_weekly_markdown(cfg, "2026-09-10", "2026-09-10"))
        t.ok("周报质量表", "T+3均涨" in md and "双确认" in md)
        t.ok("周报质量股票", "600101" in md and "600103" in md and "T+3" in md)

        compare = sl.format_quality_compare(cfg)
        t.ok("复盘质量摘要", "质量确认" in compare and "600101" in compare)

        wk = {"since": "2026-09-10", "until": "2026-09-10",
              "days": ["2026-09-10"], "empty_days": 0, "forced_n": 0,
              "range": {}, "week_perf": {}, "week_trades": [],
              "watch_items": [], "decisions": {}, "t3_attribution": {},
              "mode_channels": {}}
        body = weekly_mod.email_body(wk, cfg)
        t.ok("周报邮件质量段", "质量确认" in body and "600101" in body)
        t.ok("周报邮件含 T+3", "T+3" in body)

        status = ops_mod.collect_daily_status("2026-09-10", cfg, stderr_res={})
        ops_body = ops_mod.format_summary_text(status, cfg)
        t.ok("运维摘要质量段", "今日质量确认" in ops_body and "600101" in ops_body)

        line = seed_report.compact_email_stock_line(
            {"code": "600101", "name": "明星电力", "tier_label": "严格档",
             "sector": {"name": "电力", "rank": 2, "chg": 4.6},
             "quote": {"chg_pct": 4.2}}, "观察", cfg)
        t.ok("日邮件行质量标签", "质量2/2" in line)
        console = seed_report.format_winrate_result({
            "at": "2026-09-10 14:30", "winrate_threshold": 3,
            "winrate_max": 7, "sectors": [{"name": "电力", "rank": 2}],
            "buyable": [], "watch": [{
                "code": "600101", "name": "明星电力", "tier_label": "严格档",
                "sector": {"name": "电力", "rank": 2, "chg": 4.6},
                "quote": {"price": 10.0, "chg_pct": 4.2},
                "stage": {"stage": "发酵"}, "winrate_score": 2,
                "winrate_t3_prob": 0.4, "winrate_t3_min": 0.35,
            }], "watch_display_limit": 3, "notes": [],
        }, cfg)
        t.ok("日控制台质量列", "质量 2/2" in console)
    finally:
        ft_mod.save_records(orig, cfg, merge_disk=False)
