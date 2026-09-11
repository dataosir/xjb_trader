"""跟涨样本与通道对照 selftest 用例。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from tea.analysis import followthrough as ft_mod
from tea.reporting import weekly as weekly_mod

if TYPE_CHECKING:
    from tea.config.config_store import Config
    from tea.selftest import Suite


def check_winrate_seed_fields(t: Suite, cfg: Config) -> None:
    """硬闸原因与胜率明细随 record_seed 落盘。"""
    t.head("跟涨样本 · 胜率硬闸字段落盘")
    r = ft_mod.record_seed([{
        "code": "605006", "name": "山东玻纤", "price": 12.0, "track": "观察轨",
        "stage": "过热", "winrate_score": 5,
        "winrate_detail": "板块排名2≤3 +3；过热阶段 +1",
        "winrate_gate": "过热阶段 → 降级观察",
        "winrate_would_buy": True,
        "mode": "winrate",
    }], cfg, date="2026-09-11")
    t.eq("硬闸字段样本落盘", r, {"added": 1, "skipped": 0, "updated": 0})
    rec = next(x for x in ft_mod.load_records(cfg) if x.get("code") == "605006")
    t.eq("winrate_gate 落盘", rec.get("winrate_gate"), "过热阶段 → 降级观察")
    t.eq("winrate_detail 落盘", "板块排名2" in (rec.get("winrate_detail") or ""), True)
    t.eq("winrate_would_buy 落盘", rec.get("winrate_would_buy"), True)


def check_mode_channel_stats(t: Suite, cfg: Config) -> None:
    """rule vs winrate 分轨统计 + 周报段落。"""
    t.head("跟涨样本 · rule vs winrate 分轨")
    ft_mod.save_records([
        {"date": "2026-09-01", "code": "600101", "mode": "rule", "track": "可买",
         "result": "win", "next_chg": 4.0, "chg_t3": 2.0},
        {"date": "2026-09-02", "code": "600102", "mode": "rule", "track": "观察轨",
         "result": "loss", "next_chg": -1.0, "chg_t3": -0.5},
        {"date": "2026-09-03", "code": "605006", "mode": "winrate", "track": "观察轨",
         "winrate_score": 5, "winrate_gate": "过热阶段 → 降级观察",
         "winrate_would_buy": True, "result": "win", "next_chg": 3.5, "chg_t3": 1.0},
        {"date": "2026-09-04", "code": "605007", "mode": "winrate", "track": "可买",
         "winrate_score": 4, "winrate_would_buy": True,
         "result": None, "chg_t3": 0.5},
    ], cfg)
    st = ft_mod.mode_channel_stats(cfg)
    t.eq("rule 样本 2", st["rule"]["n"], 2)
    t.eq("winrate 样本 2", st["winrate"]["n"], 2)
    t.eq("rule T+1 胜 1", st["rule"]["t1_wins"], 1)
    t.eq("winrate 高分被闸挡 1", st["winrate"]["gate_blocked_n"], 1)
    t.ok("format_mode 含双通道", "规则(9分共振)" in ft_mod.format_mode_channel_stats(cfg))
    md = weekly_mod.render_md(cfg=cfg)
    t.ok("周报含分轨对照段", "rule vs winrate 通道对照" in md)
    t.ok("周报含硬闸 TOP", "过热阶段" in md)


def check_winrate_watch_full_persist(t: Suite, cfg: Config, _mk) -> None:
    """胜率选股观察轨全量保留（展示层截断，落盘不截断）。"""
    from tea.runtime.runner import _ft_entries

    t.head("胜率选股 · 观察轨全量落盘")
    passed = []
    for i in range(5):
        passed.append({
            "code": f"60010{i}", "name": f"观察票{i}",
            "quote": {"chg_pct": 4.0, "price": 10.0},
            "sector": {"name": "测试板", "rank": 2},
            "stage": {"stage": "发酵"},
            "identity": {"tier": "A", "score": 88},
            "ind": {"ma_bull": True},
        })
    cfg.set("seed.max_watch_output", 2)
    result = {
        "mode": "winrate", "winrate_threshold": 3,
        "sentiment": {"index": {}},
        "watch": passed,
        "watch_display_limit": 2,
        "buyable": [],
    }
    entries = _ft_entries(result)
    t.eq("落盘观察轨全量 5 条", len(entries), 5)
    t.eq("展示截断上限 2", result["watch_display_limit"], 2)
