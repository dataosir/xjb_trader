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
         "pick_sector_name": "玻纤制造", "pick_sector_rank": 2, "stage": "过热"},
        {"date": "2026-09-09", "code": "600101", "name": "明星电力",
         "mode": "rule", "track": "可买", "total_score": 7, "pass_threshold": 6,
         "sector_name": "电力", "sector_rank": 2, "stage": "发酵",
         "result": "win", "next_chg": 4.2, "chg_t3": 1.5},
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
    t.ok("默认不含完整周报", "======== 完整周报 ========" not in body)
    t.ok("指向本地报告", "reports/WEEKLY_test.md" in body)

    cfg.set("weekly_email.include_full_report", True)
    full = weekly_mod.email_body(wk, cfg)
    t.ok("include_full_report 附完整段", "======== 完整周报 ========" in full)
    t.ok("完整段含纪律自查", "纪律自查" in full)
    cfg.set("weekly_email.include_full_report", False)
