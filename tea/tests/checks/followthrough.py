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
    ], cfg, merge_disk=False)
    st = ft_mod.mode_channel_stats(cfg)
    t.eq("rule 样本 2", st["rule"]["n"], 2)
    t.eq("winrate 样本 2", st["winrate"]["n"], 2)
    t.eq("rule T+1 胜 1", st["rule"]["t1_wins"], 1)
    t.eq("winrate 高分被闸挡 1", st["winrate"]["gate_blocked_n"], 1)
    t.ok("format_mode 含双通道", "规则(9分共振)" in ft_mod.format_mode_channel_stats(cfg))
    md = weekly_mod.render_md(cfg=cfg)
    t.ok("周报含分轨对照段", "rule vs winrate 通道对照" in md)
    t.ok("周报含硬闸 TOP", "过热阶段" in md)


def check_gate_blocked_recent(t: Suite, cfg: Config) -> None:
    """硬闸挡下高分样本列表 + 近失纳入后验遗漏。"""
    from tea.reporting import retrospective as retro_mod

    t.head("跟涨样本 · 硬闸挡下高分列表")
    ft_mod.save_records([
        {"date": "2026-10-01", "code": "600201", "name": "A", "mode": "winrate",
         "winrate_score": 5, "winrate_gate": "突破阶段 → 降级观察",
         "winrate_would_buy": True, "sector_rank": 2, "chg_t3": 4.5},
        {"date": "2026-10-02", "code": "600202", "name": "B", "mode": "winrate",
         "winrate_score": 4, "winrate_gate": "rank超限 → 观察",
         "winrate_would_buy": True, "sector_rank": 4, "chg_t3": -1.0},
    ], cfg)
    rows = ft_mod.gate_blocked_recent(cfg, since_ymd="2026-10-01", limit=5)
    t.eq("挡下样本 2 条", len(rows), 2)
    t.eq("T+3 最高排前", rows[0].get("code"), "600201")
    txt = ft_mod.format_gate_blocked_recent(cfg, since_ymd="2026-10-01", limit=2)
    t.ok("格式化含代码", "600201" in txt)
    wk = weekly_mod.collect(7, cfg)
    wk["since"] = "2026-10-01"
    md = weekly_mod.render_md(wk, cfg)
    t.ok("周报含挡下个案表", "硬闸挡下高分个案" in md)
    intro = retro_mod.generate_retrospective("2026-10-01", "2026-10-02", cfg)
    t.ok("后验说明含近失轨", "观察轨/近失" in intro or "近失" in intro.split("## 遗漏关注", 1)[0])


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


class _TradingDayMarket:
    """只给一个代码返回真实交易日 K 线，用来验证周末样本顺延。"""

    def __init__(self, rows):
        self.rows = rows

    def get_klines(self, code: str, limit: int = 120):
        return list(self.rows)


def check_backfill_roll_forward(t: Suite, cfg: Config) -> None:
    """周末样本顺延到下一交易日回填；版本号让二次回填幂等。"""
    t.head("跟涨样本 · 周末顺延回填")
    from tea.analysis import followthrough as ft

    rows = [
        {"date": "2026-08-07", "open": 9.9, "close": 10.0, "high": 10.1, "low": 9.8},
        {"date": "2026-08-10", "open": 10.5, "close": 11.0, "high": 11.2, "low": 10.3},
        {"date": "2026-08-11", "open": 11.2, "close": 12.0, "high": 12.1, "low": 11.0},
        {"date": "2026-08-12", "open": 12.1, "close": 13.0, "high": 13.2, "low": 12.0},
        {"date": "2026-08-13", "open": 13.1, "close": 14.0, "high": 14.2, "low": 13.0},
        {"date": "2026-08-14", "open": 14.1, "close": 15.0, "high": 15.2, "low": 14.0},
        {"date": "2026-08-17", "open": 15.1, "close": 16.0, "high": 16.2, "low": 15.0},
    ]
    orig = ft.load_records(cfg)
    try:
        ft.save_records([{
            "date": "2026-08-08", "code": "600451", "name": "周末样本",
            "chg_pct": 7.41, "close": 10.0, "data_status": ft.STATUS_LEGACY,
            "next_chg": None, "result": None,
        }], cfg, merge_disk=False)
        upd = ft.update_results(_TradingDayMarket(rows), cfg)
        t.eq("周末样本已回填", upd.get("updated"), 1)
        rec = next(r for r in ft.load_records(cfg) if r.get("code") == "600451")
        t.eq("顺延到周一", rec.get("backfill_date"), "2026-08-10")
        t.eq("选票基准取周一收盘", rec.get("pick_close"), 11.0)
        t.eq("顺延来源标注", rec.get("pick_price_basis"), "rolled")
        t.ok("顺延说明可读", "顺延" in (rec.get("backfill_note") or ""))
        t.eq("T+1 从周一起算", rec.get("next_chg"), 9.09)
        t.eq("T+2 从周一起算", rec.get("chg_t2"), 18.18)
        t.eq("T+3 从周一起算", rec.get("chg_t3"), 27.27)
        t.eq("失效标记保留", rec.get("data_status"), ft.STATUS_LEGACY)
        t.eq("回填版本落盘", rec.get("backfill_ver"), ft.BACKFILL_VER)
        t.eq("顺延后不再 pending", ft.pending_backfill(cfg), 0)
        t.eq("各周期缺口归零", ft.pending_by_horizon(cfg),
             {"next_chg": 0, "chg_t2": 0, "chg_t3": 0, "chg_t5": 0})
        upd2 = ft.update_results(_TradingDayMarket(rows), cfg)
        t.eq("同版本二次回填不重复抓 K 线", upd2.get("updated"), 0)
    finally:
        ft.save_records(orig, cfg, merge_disk=False)


def check_shadow_lab(t: Suite, cfg: Config) -> None:
    """影子指标：派生字段落盘、每桶 n/30 进度、前后半段稳定性。"""
    t.head("跟涨样本 · 影子指标实验")
    from tea.analysis import shadow_lab as sl

    fields = sl.compute_fields({
        "sector_rank": 4, "tier": "热点降级档", "mode": "rule",
        "winrate_score": 4, "bb_bandwidth": 30,
        "scoring_dims": [{"name": "板块强度", "score": 2}],
    }, cfg)
    t.eq("板块强度派生", fields["shadow_lab_sector"], 2)
    t.eq("坏组合标记", fields["shadow_lab_bad_combo"], True)
    t.eq("跨通道共识标记", fields["shadow_lab_wr_consensus"], True)
    t.eq("布林带宽过热", fields["shadow_lab_bbw"], "hot")
    fields2 = sl.compute_fields({
        "sector_rank": 3, "tier": "严格档", "mode": "rule",
        "winrate_score": 3.5, "bb_bandwidth": 15,
        "scoring_dims": [{"name": "板块强度", "score": 1}],
    }, cfg)
    t.eq("非坏组合", fields2["shadow_lab_bad_combo"], False)
    t.eq("非共识", fields2["shadow_lab_wr_consensus"], False)
    t.eq("布林带宽压缩", fields2["shadow_lab_bbw"], "squeeze")

    orig = ft_mod.load_records(cfg)
    old_min = cfg.get("shadow_lab.min_samples", 30)
    old_half = cfg.get("shadow_lab.half_min_samples", 5)
    try:
        r = ft_mod.record_seed([{
            "code": "600471", "name": "影子字段", "price": 10.0,
            "sector_rank": 4, "tier": "热点降级档", "mode": "rule",
            "winrate_score": 4, "bb_bandwidth": 30,
            "scoring_dims": [{"name": "板块强度", "score": 2}],
        }], cfg, date="2026-09-01")
        t.eq("影子字段样本落盘", r["added"], 1)
        rec = next(x for x in ft_mod.load_records(cfg) if x.get("code") == "600471")
        t.eq("影子字段随 record_seed 落盘", rec.get("shadow_lab_bad_combo"), True)
        t.eq("影子字段板块强度落盘", rec.get("shadow_lab_sector"), 2)

        # 8 条样本：4 条强板块全胜，4 条弱板块前半胜/后半败；用于验证前后半段。
        rows = []
        for i, (d, t3) in enumerate([("2026-09-01", 2.0), ("2026-09-03", 3.0),
                                     ("2026-09-05", 4.0), ("2026-09-07", 5.0)]):
            rows.append({
                "date": d, "code": f"60047{i+1}", "data_status": ft_mod.STATUS_VALID,
                "sector_rank": 2, "tier": "严格档", "mode": "rule",
                "winrate_score": 5, "bb_bandwidth": 30, "chg_t3": t3,
                "chg_t2": t3 / 2, "scoring_dims": [{"name": "板块强度", "score": 2}],
            })
        for i, (d, t3, tier) in enumerate([
                ("2026-09-02", 1.0, "热点降级档"), ("2026-09-04", 2.0, "严格档"),
                ("2026-09-06", -1.0, "热点降级档"), ("2026-09-08", -2.0, "严格档")]):
            rows.append({
                "date": d, "code": f"60048{i+1}", "data_status": ft_mod.STATUS_VALID,
                "sector_rank": 5, "tier": tier, "mode": "rule",
                "winrate_score": 3, "bb_bandwidth": 15, "chg_t3": t3,
                "chg_t2": t3 / 2, "scoring_dims": [{"name": "板块强度", "score": 1}],
            })
        cfg.set("shadow_lab.min_samples", 4)
        cfg.set("shadow_lab.half_min_samples", 2)
        ft_mod.save_records(rows, cfg, merge_disk=False)
        st = sl.shadow_lab_stats(cfg)
        t.eq("影子实验样本数", st["pool_n"], 8)
        sector = {b["label"]: b for d in st["dimensions"] if d["key"] == "sector"
                  for b in d["buckets"]}
        t.eq("板块强度2 样本", sector["2"]["t3"]["n"], 4)
        t.eq("板块强度2 全胜", sector["2"]["t3"]["up"], 4)
        t.eq("板块强度2 达标稳定", sector["2"]["status"], "达标·稳定")
        t.eq("板块强度1 前后不一致", sector["1"]["status"], "达标·不稳")
        bad = {b["label"]: b for d in st["dimensions"] if d["key"] == "bad_combo"
               for b in d["buckets"]}
        t.eq("坏组合进度不足", "观察中" in bad["是"]["status"], True)
        txt = sl.format_shadow_lab(cfg)
        t.ok("看板含前后半段", "前半" in txt and "后半" in txt)
        t.ok("看板含样本进度", "/4" in txt and "才讨论进闸" in txt)
        t.ok("周报表格含影子维度", any("板块强度" in ln for ln in sl.shadow_lab_markdown(cfg)))
        t.ok("摘要含桶数", "影子指标" in sl.shadow_lab_summary(cfg))
    finally:
        cfg.set("shadow_lab.min_samples", old_min)
        cfg.set("shadow_lab.half_min_samples", old_half)
        ft_mod.save_records(orig, cfg, merge_disk=False)


def check_data_quality_exclusion(t: Suite, cfg: Config) -> None:
    """degraded 样本不进胜率归因，样本看板单独提示。"""
    t.head("跟涨样本 · 降级数据排除")
    from tea.analysis import followthrough as ft

    orig = ft.load_records(cfg)
    try:
        ft.save_records([
            {"date": "2026-09-01", "code": "600461", "name": "正常",
             "data_status": ft.STATUS_VALID, "data_quality": ft.QUALITY_OK,
             "result": "win", "next_chg": 4.0, "result_t2": "win",
             "chg_t2": 2.0, "result_t3": "win", "chg_t3": 1.0},
            {"date": "2026-09-02", "code": "600462", "name": "缺数",
             "data_status": ft.STATUS_VALID, "data_quality": ft.QUALITY_DEGRADED,
             "result": "win", "next_chg": 9.0, "result_t2": "win",
             "chg_t2": 8.0, "result_t3": "win", "chg_t3": 7.0},
        ], cfg, merge_disk=False)
        h = ft.data_health(cfg)
        t.eq("降级样本计数", h["degraded"], 1)
        t.eq("可用样本排除降级", h["usable"], 1)
        t.eq("归因只吃正常样本", len(ft.load_valid_records(cfg)), 1)
        hr = ft.horizon_winrates(cfg)
        t.eq("T+2 样本排除降级", hr["chg_t2"]["n"], 1)
        t.ok("T+2 胜率带置信区间", isinstance(hr["chg_t2"].get("ci"), tuple))
        txt = ft.format_sample_gap(cfg)
        t.ok("缺口看板含 T+2", "待 T+2" in txt)
        t.ok("缺口看板含降级", "降级数据 1 条" in txt)
        t.ok("周期摘要含 T+2/T+3", "T+2>0" in ft.format_horizon_winrates(cfg)
             and "T+3>0" in ft.format_horizon_winrates(cfg))
    finally:
        ft.save_records(orig, cfg, merge_disk=False)
