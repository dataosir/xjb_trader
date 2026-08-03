"""身份判定：龙头 / 跟风 / 杂毛（满分 100，基准 50）。

维度：板块内排名、板块排名、个股 vs 板块涨幅、板块涨停跟涨、市值、均线。
热点板块（板块涨幅 ≥6%）用"相对跟涨"替代绝对涨幅惩罚。
flags ≥2 且非龙头 → 强制判杂毛。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from tea.config.config_store import Config, load_config
from tea.core import utils

TIER_LEADER = "龙头"
TIER_FOLLOW = "跟风"
TIER_ZAMAO = "杂毛"


def judge(quote: dict, sector: Optional[dict] = None, ind: Optional[dict] = None,
          cfg: Optional[Config] = None) -> dict:
    """返回 {score, tier, deltas, flags, reasons, rel_ratio, hot_sector}。"""
    cfg = cfg or load_config()
    c = lambda k, d=None: cfg.get(f"identity.{k}", d)
    sector = sector or {}
    ind = ind or {}

    score = float(c("base_score", 50.0))
    deltas: List[dict] = []
    flags: List[str] = []

    def add(item: str, delta: float, detail: str, flag: bool = False) -> None:
        nonlocal score
        score += delta
        deltas.append({"item": item, "delta": delta, "detail": detail})
        if flag:
            flags.append(item)

    chg = quote.get("chg_pct")
    cap = quote.get("cap_yi")
    sec_chg = sector.get("chg")
    sec_rank = sector.get("rank")
    rank_pct = sector.get("stock_rank_pct")
    sec_limit_ups = int(sector.get("limit_up_count") or 0)
    hot_sector = sec_chg is not None and sec_chg >= float(cfg.s("seed_hot_sector_chg", 6.0))

    # ① 板块内排名
    if rank_pct is not None:
        if rank_pct <= float(c("inner_top_pct", 0.20)):
            add("板块内排名", float(c("inner_top_delta", 20)), f"前 {rank_pct:.0%}")
        elif rank_pct <= float(c("inner_mid_pct", 0.35)):
            add("板块内排名", float(c("inner_mid_delta", 8)), f"前 {rank_pct:.0%}")
        elif rank_pct > float(c("inner_tail_pct", 0.50)):
            add("板块内排名", float(c("inner_tail_delta", -25)), f"后 {1 - rank_pct:.0%}（排名靠后）", flag=True)

    # ② 板块排名
    if sec_rank is not None:
        if sec_rank <= int(c("sector_rank_strong", 10)):
            add("板块排名", float(c("sector_rank_strong_delta", 15)), f"第 {sec_rank} 名 ≤10")
        elif sec_rank <= int(c("sector_rank_mid", 20)):
            add("板块排名", float(c("sector_rank_mid_delta", 8)), f"第 {sec_rank} 名 ≤20")
        elif sec_rank > int(c("sector_rank_weak", 30)):
            add("板块排名", float(c("sector_rank_weak_delta", -15)), f"第 {sec_rank} 名 >30（板块弱）", flag=True)

    # ③ 个股 vs 板块涨幅（热点板块走相对跟涨）
    rel_ratio = None
    if chg is not None and sec_chg is not None:
        if hot_sector and sec_chg > 0:
            rel_ratio = chg / sec_chg
            lo, hi = float(c("hot_rel_low", 0.42)), float(c("hot_rel_high", 0.90))
            if lo <= rel_ratio <= hi:
                add("相对跟涨", float(c("hot_rel_ok_delta", 18)),
                    f"极热板块 {sec_chg:.2f}%，相对系数 {rel_ratio:.2f} 达标")
            elif rel_ratio < lo:
                add("相对跟涨", float(c("hot_rel_lag_delta", -15)),
                    f"相对系数 {rel_ratio:.2f} < {lo}（相对落后）", flag=True)
            else:
                add("相对跟涨", float(c("hot_rel_over_delta", -10)),
                    f"相对系数 {rel_ratio:.2f} > {hi}（涨幅透支）", flag=True)
            # 热板块前排额外加成
            if (sec_rank is not None and sec_rank <= int(c("hot_front_sector_rank", 8))
                    and rank_pct is not None and rank_pct <= float(c("hot_front_inner_pct", 0.40))
                    and lo <= (rel_ratio or 0) <= hi):
                add("热板块前排", float(c("hot_front_delta", 12)),
                    f"板块第 {sec_rank} + 板块内前 {rank_pct:.0%}")
        else:
            diff = chg - sec_chg
            if diff >= float(c("vs_sector_strong_pct", 1.0)):
                add("强于板块", float(c("vs_sector_strong_delta", 10)),
                    f"个股 {chg:+.2f}% vs 板块 {sec_chg:+.2f}%")
            elif diff <= -float(c("vs_sector_weak_pct", 1.5)):
                add("弱于板块", float(c("vs_sector_weak_delta", -20)),
                    f"个股 {chg:+.2f}% vs 板块 {sec_chg:+.2f}%", flag=True)

    # ④ 板块涨停跟涨
    if sec_limit_ups > 0 and chg is not None:
        if chg >= float(c("limit_up_follow_chg", 7.0)):
            add("涨停跟涨", float(c("limit_up_follow_delta", 10)),
                f"板块 {sec_limit_ups} 家涨停，个股 {chg:.2f}% ≥7%")
        elif chg < float(c("limit_up_lag_chg", 5.0)):
            add("涨停跟涨", float(c("limit_up_lag_delta", -15)),
                f"板块 {sec_limit_ups} 家涨停但个股仅 {chg:.2f}%（跟不上）", flag=True)

    # ⑤ 市值
    if cap is not None:
        if float(c("cap_good_low", 50)) <= cap < float(c("cap_good_high", 200)):
            add("市值", float(c("cap_good_delta", 5)), f"{cap:.0f}亿 属 50~200亿")
        else:
            add("市值", float(c("cap_bad_delta", -10)), f"{cap:.0f}亿 不在 50~200亿", flag=True)

    # ⑥ 均线
    if ind:
        if ind.get("ma_bull"):
            add("均线", float(c("ma_bull_delta", 5)), "MA5≥MA10≥MA20 多头排列")
        elif (not ind.get("above_ma20")) and (chg is not None and chg < float(c("ma_weak_chg", 3.0))):
            add("均线", float(c("ma_weak_delta", -10)), "跌破 MA20 且涨幅弱", flag=True)

    score = utils.clamp(score, 0.0, 100.0)
    leader_th = float(cfg.s("identity_leader_threshold", 70))
    follow_th = float(cfg.s("identity_follow_threshold", 45))
    if score >= leader_th:
        tier = TIER_LEADER
    elif score >= follow_th:
        tier = TIER_FOLLOW
    else:
        tier = TIER_ZAMAO

    forced = False
    if len(flags) >= int(c("flags_force_zamao", 2)) and tier != TIER_LEADER:
        if tier != TIER_ZAMAO:
            forced = True
        tier = TIER_ZAMAO

    return {
        "score": round(score, 1),
        "tier": tier,
        "deltas": deltas,
        "flags": flags,
        "forced_zamao": forced,
        "rel_ratio": rel_ratio,
        "hot_sector": hot_sector,
        "sector_rank": sec_rank,
        "inner_rank": sector.get("stock_rank"),
        "inner_rank_pct": rank_pct,
        "sector_limit_ups": sec_limit_ups,
    }


def is_leader(identity: dict) -> bool:
    return (identity or {}).get("tier") == TIER_LEADER


def is_zamao(identity: dict) -> bool:
    return (identity or {}).get("tier") == TIER_ZAMAO


def pass_bump(identity: dict, cfg: Optional[Config] = None) -> int:
    """杂毛预警（warn 模式）：通过门槛 +1，不直接拒绝。"""
    cfg = cfg or load_config()
    if not is_zamao(identity):
        return 0
    if str(cfg.s("identity_zamao_mode", "warn")) != "warn":
        return 0
    return int(cfg.s("identity_zamao_pass_bump", 1))


def rejects_zamao(cfg: Optional[Config] = None) -> bool:
    """reject 模式下杂毛直接否决。"""
    cfg = cfg or load_config()
    return str(cfg.s("identity_zamao_mode", "warn")) == "reject"


def format_identity(identity: dict) -> str:
    lines = [f"身份 {identity['tier']}（{identity['score']} 分）"]
    for d in identity.get("deltas", []):
        lines.append(f"  {d['delta']:+.0f} {d['item']}：{d['detail']}")
    if identity.get("flags"):
        lines.append(f"  风险标记 {len(identity['flags'])} 项：{'、'.join(identity['flags'])}"
                     + ("（强制判杂毛）" if identity.get("forced_zamao") else ""))
    return "\n".join(lines)


def summarize(identity: Dict[str, Any]) -> str:
    return f"{identity.get('tier')}/{identity.get('score')}"
