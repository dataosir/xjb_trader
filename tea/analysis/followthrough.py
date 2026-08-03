"""跟涨经验胜率 + 观察触发条件。

从历史种子记录（seed_records.jsonl）按 阶段×档位×轨道 聚合 T+1 胜率
（T+1 涨幅 ≥3% 计为赢），样本 ≥8 才有效；用于种子排序与半仓系数调整。
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from tea.config.config_store import Config, load_config
from tea.core import utils
from tea.data import Market

KEY_SEP = "|"


def records_path(cfg: Optional[Config] = None) -> str:
    return (cfg or load_config()).data_file("seed_records_file")


def load_records(cfg: Optional[Config] = None) -> List[dict]:
    return utils.read_jsonl(records_path(cfg))


def record_seed(entries: List[dict], cfg: Optional[Config] = None,
                date: Optional[str] = None) -> int:
    """落盘当日种子记录（供次日回填 T+1 结果）。"""
    cfg = cfg or load_config()
    path = records_path(cfg)
    d = date or utils.today_str()
    n = 0
    for e in entries:
        utils.append_jsonl(path, {
            "date": d, "code": e.get("code"), "name": e.get("name"),
            "stage": e.get("stage"), "tier": e.get("tier"), "track": e.get("track"),
            "chg_pct": e.get("chg_pct"), "total_score": e.get("total_score"),
            "identity_tier": e.get("identity_tier"), "identity_score": e.get("identity_score"),
            "sector_name": e.get("sector_name"), "sector_rank": e.get("sector_rank"),
            "close": e.get("price"), "next_chg": None, "result": None,
        })
        n += 1
    return n


def save_records(records: List[dict], cfg: Optional[Config] = None) -> str:
    """整体重写（回填 T+1 结果后使用，原子写）。"""
    cfg = cfg or load_config()
    lines = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    return utils.atomic_write(records_path(cfg), lines + ("\n" if lines else ""))


# ------------------------------------------------------------------ T+1 回填

def update_results(market: Optional[Market] = None, cfg: Optional[Config] = None) -> dict:
    """回填 T+1 结果：用日 K 找记录日之后第一个交易日的涨幅。"""
    cfg = cfg or load_config()
    recs = load_records(cfg)
    if not recs:
        return {"updated": 0, "pending": 0}
    mk = market or Market(cfg)
    win_th = float(cfg.get("followthrough.win_chg_pct", 3.0))
    updated = pending = 0
    for r in recs:
        if r.get("result") is not None or not r.get("code"):
            continue
        if r.get("date") == utils.today_str():
            pending += 1
            continue
        try:
            kl = mk.get_klines(r["code"], limit=30)
        except Exception:
            pending += 1
            continue
        idx = next((i for i, k in enumerate(kl) if k.get("date") == r.get("date")), None)
        if idx is None or idx + 1 >= len(kl):
            pending += 1
            continue
        base, nxt = kl[idx].get("close"), kl[idx + 1].get("close")
        if not base or not nxt:
            pending += 1
            continue
        chg = (nxt - base) / base * 100.0
        r["next_chg"] = round(chg, 2)
        r["result"] = "win" if chg >= win_th else "loss"
        updated += 1
    if updated:
        save_records(recs, cfg)
    return {"updated": updated, "pending": pending, "total": len(recs)}


# ------------------------------------------------------------------ 聚合

def key_of(stage: Optional[str], tier: Optional[str], track: Optional[str]) -> str:
    return KEY_SEP.join([stage or "-", tier or "-", track or "-"])


def aggregate(cfg: Optional[Config] = None) -> Dict[str, dict]:
    """按 阶段×档位×轨道 聚合 T+1 胜率。"""
    cfg = cfg or load_config()
    min_n = int(cfg.get("followthrough.min_samples", 8))
    out: Dict[str, dict] = {}
    for r in load_records(cfg):
        if r.get("result") not in ("win", "loss"):
            continue
        k = key_of(r.get("stage"), r.get("tier"), r.get("track"))
        slot = out.setdefault(k, {"n": 0, "wins": 0, "stage": r.get("stage"),
                                  "tier": r.get("tier"), "track": r.get("track"),
                                  "avg_next_chg": []})
        slot["n"] += 1
        slot["wins"] += 1 if r["result"] == "win" else 0
        if r.get("next_chg") is not None:
            slot["avg_next_chg"].append(float(r["next_chg"]))
    for k, v in out.items():
        v["rate"] = v["wins"] / v["n"] if v["n"] else None
        v["valid"] = v["n"] >= min_n
        v["avg_next_chg"] = utils.mean(v["avg_next_chg"])
    return out


def score_for(stage: Optional[str], tier: Optional[str], track: Optional[str] = None,
              cfg: Optional[Config] = None) -> Tuple[Optional[float], int]:
    """跟涨分（= 经验 T+1 胜率），样本不足返回 (None, n)。"""
    cfg = cfg or load_config()
    agg = aggregate(cfg)
    slot = agg.get(key_of(stage, tier, track))
    if slot and slot.get("valid"):
        return slot["rate"], slot["n"]
    # 退化：忽略轨道再聚合
    tot_n = tot_w = 0
    for k, v in agg.items():
        s, t, _ = k.split(KEY_SEP)
        if s == (stage or "-") and t == (tier or "-"):
            tot_n += v["n"]
            tot_w += v["wins"]
    if tot_n >= int(cfg.get("followthrough.min_samples", 8)):
        return tot_w / tot_n, tot_n
    return None, (slot or {}).get("n", 0)


def mult_for(score: Optional[float], cfg: Optional[Config] = None) -> float:
    """跟涨仓位乘数：≥0.45→1.0；≥0.30→0.90；<0.30→0.75；无数据→默认。"""
    cfg = cfg or load_config()
    c = lambda k, d: float(cfg.get(f"followthrough.{k}", d))
    if score is None:
        return c("default_mult", 1.0)
    if score >= c("score_high", 0.45):
        return c("mult_high", 1.0)
    if score >= c("score_mid", 0.30):
        return c("mult_mid", 0.90)
    return c("mult_low", 0.75)


def evaluate(stage: Optional[str], tier: Optional[str], track: Optional[str] = None,
             cfg: Optional[Config] = None) -> dict:
    cfg = cfg or load_config()
    score, n = score_for(stage, tier, track, cfg)
    mult = mult_for(score, cfg)
    return {
        "stage": stage, "tier": tier, "track": track,
        "score": round(score, 4) if score is not None else None,
        "samples": n, "mult": mult,
        "note": (f"跟涨分 {score:.0%}（{n} 样本）→ 乘数 {mult}" if score is not None
                 else f"跟涨样本不足（{n}/{cfg.get('followthrough.min_samples', 8)}）→ 乘数 {mult}"),
    }


# ------------------------------------------------------------------ 观察触发

def trigger_conditions(ev: dict, cfg: Optional[Config] = None) -> List[str]:
    """生成观察轨触发条件（回踩就绪判定的人类可读版本）。"""
    cfg = cfg or load_config()
    conds: List[str] = []
    lo = float(cfg.get("seed.strict_min_chg", 3.0))
    hi = float(cfg.get("seed.strict_max_chg", 5.5))
    intr = float(cfg.get("watch.pullback_max_intraday", 0.65))
    drop = float(cfg.get("watch.pullback_min_drop_pct", 3.0))
    chg = (ev.get("quote") or {}).get("chg_pct")
    vt = ev.get("veto") or {}
    soft = [i["name"] for i in vt.get("soft", [])]

    if chg is not None and chg < lo:
        conds.append(f"涨入严格窗 {lo}~{hi}%")
    if "intraday_high" in soft or "chase_high" in soft:
        conds.append(f"分时回落至 ≤{intr:.0%}")
    if "bias_ma20" in soft:
        conds.append(f"乖离收敛（回撤 ≥{drop:.0f}% 或贴近 MA20）")
    if "near_limit_up" in soft:
        conds.append("次日不封板且回踩不破 5 日线")
    if ev.get("gap", 0) and ev.get("gap") > 0:
        conds.append(f"共振分补足 {ev['gap']} 分（门槛 {ev.get('pass_threshold')}）")
    if not conds:
        conds.append(f"回踩 ≥{drop:.0f}% 且分时 ≤{intr:.0%} 后复评")
    return conds


def format_followthrough(cfg: Optional[Config] = None) -> str:
    agg = aggregate(cfg)
    if not agg:
        return "跟涨经验：暂无样本"
    lines = ["===== 跟涨经验胜率（阶段×档位×轨道）====="]
    for k, v in sorted(agg.items(), key=lambda x: -x[1]["n"]):
        rate = f"{v['rate']:.0%}" if v.get("rate") is not None else "—"
        lines.append(f"  {k.replace(KEY_SEP, ' / ')}: {v['wins']}/{v['n']} = {rate}"
                     f"{'（有效）' if v.get('valid') else '（样本不足）'}"
                     f" 次日均涨 {utils.pct(v.get('avg_next_chg'))}")
    return "\n".join(lines)
