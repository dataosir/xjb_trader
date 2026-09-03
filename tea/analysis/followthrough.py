"""跟涨经验胜率 + 观察触发条件。

从历史种子记录（seed_records.jsonl）按 阶段×档位×轨道 聚合 T+1 胜率
（T+1 涨幅 ≥3% 计为赢），样本 ≥8 才有效；用于种子排序与半仓系数调整。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from tea.config.config_store import Config, load_config
from tea.core import utils
from tea.data import Market

KEY_SEP = "|"


def records_path(cfg: Optional[Config] = None) -> str:
    return (cfg or load_config()).data_file("seed_records_file")


def load_records(cfg: Optional[Config] = None) -> List[dict]:
    return utils.read_jsonl(records_path(cfg))


# 轨道优先级：同一天同一只票按「更接近可成交」的轨道保留。早盘先以观察/启动待定
# 落盘、午后升级为「可买」时，若仍按 (date, code) 只记首条，会把「可买」样本漏掉，
# 导致可买轨道的跟涨胜率被系统性低估。
_TRACK_PRIORITY = {
    "可买": 5,
    "启动待定轨": 4,
    "萌芽观察轨": 3,
    "观察轨": 2,
    "前夕观察轨": 1,
    "趋势轨": 0,
}


def _track_rank(track: Optional[str]) -> int:
    return _TRACK_PRIORITY.get(track, -1)


# ------------------------------------------------------------------ 影子对照标签（不驱动计划）

def compute_shadow_tag(stage: Optional[str], sector_rank: Any = None,
                       pick_sector_rank: Any = None) -> Optional[str]:
    """影子对照桶标签：萌芽 ∪（非突破 ∧ rank≤3）。

    只用于落盘对照 T+3>0，**不写计划、不改可买**。优先用入选板块
    ``pick_sector_rank``，缺则退回预审 ``sector_rank``。
    """
    tags: List[str] = []
    if stage == "萌芽":
        tags.append("萌芽")
    rank = pick_sector_rank if pick_sector_rank is not None else sector_rank
    try:
        rank_i = int(rank) if rank is not None else None
    except (TypeError, ValueError):
        rank_i = None
    if stage and stage != "突破" and rank_i is not None and rank_i <= 3:
        tags.append("前三非突破")
    return "|".join(tags) if tags else None


def resolve_shadow_tag(rec: dict) -> Optional[str]:
    """读已落盘标签；缺则按阶段/排名重算（兼容闸门前旧样本）。"""
    raw = rec.get("shadow_tag")
    if raw:
        return str(raw)
    return compute_shadow_tag(rec.get("stage"), rec.get("sector_rank"),
                              rec.get("pick_sector_rank"))


def record_seed(entries: List[dict], cfg: Optional[Config] = None,
                date: Optional[str] = None) -> dict:
    """落盘当日种子记录（供次日回填 T+1 结果），按 (date, code) 去重。

    seed-plan 一天可能跑多次，同一标的会被重复 append，导致 T+1 样本翻倍、胜率
    失真。这里以 (date, code) 去重，但同一天内同一标的的轨道可能升级（观察→启动
    待定→可买），只记首条会把「可买」漏掉——所以按轨道优先级升级保留更高者。
    返回 {"added": n, "skipped": m, "updated": k}。
    """
    cfg = cfg or load_config()
    d = date or utils.today_str()
    recs = load_records(cfg)
    by_key = {(r.get("date"), r.get("code")): i
              for i, r in enumerate(recs) if r.get("date") and r.get("code")}
    added = skipped = updated = 0
    changed = False
    for e in entries:
        code = e.get("code")
        if not code:
            skipped += 1
            continue
        key = (d, code)
        rec = {
            "date": d, "code": code, "name": e.get("name"),
            "stage": e.get("stage"), "tier": e.get("tier"), "track": e.get("track"),
            "chg_pct": e.get("chg_pct"), "total_score": e.get("total_score"),
            "pass_threshold": e.get("pass_threshold"),
            "identity_tier": e.get("identity_tier"), "identity_score": e.get("identity_score"),
            "sector_name": e.get("sector_name"), "sector_rank": e.get("sector_rank"),
            "sector_chg": e.get("sector_chg"),
            "scoring_dims": e.get("scoring_dims"),
            "market_score": e.get("market_score"), "market_cycle": e.get("market_cycle"),
            "market_stance": e.get("market_stance"), "market_ma20_above": e.get("market_ma20_above"),
            "market_idx_chg": e.get("market_idx_chg"),
            "bias_ma20": e.get("bias_ma20"),
            "bb_mid": e.get("bb_mid"),
            "bb_upper": e.get("bb_upper"),
            "bb_lower": e.get("bb_lower"),
            "bb_pct_b": e.get("bb_pct_b"),
            "bb_bandwidth": e.get("bb_bandwidth"),
            "atr_pct": e.get("atr_pct"),
            "vol_ratio": e.get("vol_ratio"), "turnover": e.get("turnover"),
            "intraday": e.get("intraday"),
            "ma_bull": e.get("ma_bull"), "above_ma20": e.get("above_ma20"),
            "amount_yi": e.get("amount_yi"), "cap_yi": e.get("cap_yi"),
            "rank_pct": e.get("rank_pct"), "odds": e.get("odds"),
            "sl_pct": e.get("sl_pct"), "tp_pct": e.get("tp_pct"),
            "veto_labels": e.get("veto_labels"),
            "lowbuy": bool(e.get("lowbuy")),
            "winrate_score": e.get("winrate_score"),
            "mode": e.get("mode", "rule"),
            "pick_sector_bk": e.get("pick_sector_bk"),
            "pick_sector_name": e.get("pick_sector_name"),
            "pick_sector_rank": e.get("pick_sector_rank"),
            "shadow_tag": (e.get("shadow_tag")
                           or compute_shadow_tag(
                               e.get("stage"), e.get("sector_rank"),
                               e.get("pick_sector_rank"))),
            "close": e.get("price"), "next_chg": None, "result": None,
            "chg_t2": None, "chg_t3": None, "chg_t5": None,
        }
        if key not in by_key:
            recs.append(rec)
            by_key[key] = len(recs) - 1
            added += 1
            changed = True
            continue
        idx = by_key[key]
        old = recs[idx]
        if _track_rank(rec.get("track")) > _track_rank(old.get("track")):
            # 用更高优先级轨道的新快照覆盖，但保留已回填的 T+1 结果（若有）。
            rec["next_chg"] = old.get("next_chg")
            rec["result"] = old.get("result")
            recs[idx] = rec
            updated += 1
            changed = True
        else:
            skipped += 1
    if changed:
        save_records(recs, cfg)
    return {"added": added, "skipped": skipped, "updated": updated}


def save_records(records: List[dict], cfg: Optional[Config] = None) -> str:
    """整体重写（回填 T+1 结果后使用，原子写）。"""
    cfg = cfg or load_config()
    lines = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    return utils.atomic_write(records_path(cfg), lines + ("\n" if lines else ""))


def dedupe_records(cfg: Optional[Config] = None) -> dict:
    """历史跟涨样本按 (date, code) 去重（保留首条），返回 {before, after, removed}。

    去重只对新写入生效（见 record_seed），已落盘的历史重复需要这里一次性清理，
    否则 update_results / aggregate 仍会被历史重复污染跟涨胜率。原子写，安全。
    """
    cfg = cfg or load_config()
    recs = load_records(cfg)
    seen = set()
    kept: List[dict] = []
    for r in recs:
        key = (r.get("date"), r.get("code"))
        if key in seen:
            continue
        seen.add(key)
        kept.append(r)
    removed = len(recs) - len(kept)
    if removed:
        save_records(kept, cfg)
    return {"before": len(recs), "after": len(kept), "removed": removed}


# ------------------------------------------------------------------ T+1 回填

def update_results(market: Optional[Market] = None, cfg: Optional[Config] = None,
                   io: Any = None) -> dict:
    """回填 T+1/T+2/T+3/T+5 多周期结果：用日 K 找记录日之后的第 N 个交易日涨幅。

    「选了 3 天全跌」这类复盘只靠 T+1 看不出来，所以一次把 T+1~T+5 都算好落盘。
    result（win/loss）仍按 T+1 ≥ win_chg_pct 判定，保持既有跟涨胜率口径不变。

    io 传入时每约 10% 报一次进度：几十条 K 线逐条抓会沉默很久，屏上像卡死，
    报个数让人知道还在跑。
    """
    cfg = cfg or load_config()
    recs = load_records(cfg)
    if not recs:
        return {"updated": 0, "pending": 0, "total": 0}
    mk = market or Market(cfg)
    win_th = float(cfg.get("followthrough.win_chg_pct", 3.0))
    today = utils.today_str()
    horizons = ((1, "next_chg"), (2, "chg_t2"), (3, "chg_t3"), (5, "chg_t5"))

    pending = sum(1 for r in recs if r.get("code") and r.get("date") == today)
    needs = [r for r in recs if r.get("code") and r.get("date") != today
             and (r.get("result") is None or r.get("chg_t5") is None)]
    total = len(needs)
    step = max(1, total // 10) if total else 1
    updated = 0
    for done, r in enumerate(needs, 1):
        try:
            kl = mk.get_klines(r["code"], limit=30)
        except Exception:
            pending += 1
            continue
        idx = next((i for i, k in enumerate(kl) if k.get("date") == r.get("date")), None)
        if idx is None:
            pending += 1
            continue
        base = kl[idx].get("close")
        if not base:
            pending += 1
            continue
        for off, field in horizons:
            if idx + off < len(kl) and kl[idx + off].get("close"):
                r[field] = round((kl[idx + off]["close"] - base) / base * 100.0, 2)
        if r.get("next_chg") is not None:
            r["result"] = "win" if r["next_chg"] >= win_th else "loss"
        updated += 1
        if io is not None and (done % step == 0 or done == total):
            io.say(f"  ⏳ 回填 {done}/{total} 条...")
    if updated:
        save_records(recs, cfg)
    return {"updated": updated, "pending": pending, "total": len(recs)}


def pending_backfill(cfg: Optional[Config] = None) -> int:
    """未回填 T+1 的历史记录数（不含今日：今日的样本本就要等下一个交易日）。

    用于种子扫描收尾提示——若历史样本一直没人跑 review，跟涨胜率模块就是零样本，
    白白积累了一周数据。返回 0 表示都已回填（或没有历史记录）。
    """
    cfg = cfg or load_config()
    today = utils.today_str()
    return sum(1 for r in load_records(cfg)
               if r.get("result") is None and r.get("code") and r.get("date") != today)


# ------------------------------------------------------------------ 聚合

def key_of(stage: Optional[str], tier: Optional[str], track: Optional[str]) -> str:
    return KEY_SEP.join([stage or "-", tier or "-", track or "-"])


def aggregate(cfg: Optional[Config] = None) -> Dict[str, dict]:
    """按 阶段×档位×轨道 聚合 T+1 胜率 + T+1/T+3 平均涨幅。"""
    cfg = cfg or load_config()
    min_n = int(cfg.get("followthrough.min_samples", 8))
    out: Dict[str, dict] = {}
    for r in load_records(cfg):
        if r.get("result") not in ("win", "loss"):
            continue
        k = key_of(r.get("stage"), r.get("tier"), r.get("track"))
        slot = out.setdefault(k, {"n": 0, "wins": 0, "stage": r.get("stage"),
                                  "tier": r.get("tier"), "track": r.get("track"),
                                  "avg_next_chg": [], "avg_chg_t3": []})
        slot["n"] += 1
        slot["wins"] += 1 if r["result"] == "win" else 0
        if r.get("next_chg") is not None:
            slot["avg_next_chg"].append(float(r["next_chg"]))
        if r.get("chg_t3") is not None:
            slot["avg_chg_t3"].append(float(r["chg_t3"]))
    for k, v in out.items():
        v["rate"] = v["wins"] / v["n"] if v["n"] else None
        v["valid"] = v["n"] >= min_n
        v["avg_next_chg"] = utils.mean(v["avg_next_chg"])
        v["avg_chg_t3"] = utils.mean(v["avg_chg_t3"])
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
                     f" 次日均涨 {utils.pct(v.get('avg_next_chg'))}"
                     f"　T+3均涨 {utils.pct(v.get('avg_chg_t3'))}")
    return "\n".join(lines)


# ------------------------------------------------------------------ 阶段 B 样本盘点

def stage_b_bucket_stats(cfg: Optional[Config] = None, min_samples: int = 30) -> dict:
    """阶段 B 经验先验的样本盘点。

    按「板块排名桶 × 阶段 × 档位 × 身份分桶」聚合已回填的 T+1 样本，返回最大桶的
    样本数与是否达标——阶段 B（经验胜率先验）要求单个交叉桶 ≥ min_samples 才可信。
    """
    cfg = cfg or load_config()
    buckets: Dict[str, dict] = {}
    for r in load_records(cfg):
        if r.get("result") not in ("win", "loss"):
            continue
        rank = r.get("sector_rank")
        if rank is None:
            continue
        rank_bucket = ("1-3" if rank <= 3 else "4-5" if rank <= 5
                       else "6-8" if rank <= 8 else ">8")
        isc = r.get("identity_score")
        id_bucket = ("<75" if isc is None or isc < 75 else "75-85" if isc < 85
                     else "85-93" if isc < 93 else "93+")
        key = " / ".join([rank_bucket, r.get("stage") or "-", r.get("tier") or "-", id_bucket])
        slot = buckets.setdefault(key, {"n": 0, "wins": 0})
        slot["n"] += 1
        slot["wins"] += 1 if r["result"] == "win" else 0
    if not buckets:
        return {"max_n": 0, "max_bucket": None, "max_rate": None,
                "threshold": min_samples, "ready": False, "bucket_count": 0}
    max_key = max(buckets, key=lambda k: buckets[k]["n"])
    slot = buckets[max_key]
    return {
        "max_n": slot["n"], "max_bucket": max_key,
        "max_rate": slot["wins"] / slot["n"],
        "threshold": min_samples, "ready": slot["n"] >= min_samples,
        "bucket_count": len(buckets),
    }


def format_stage_b_status(cfg: Optional[Config] = None, min_samples: int = 30) -> str:
    """阶段 B 触发条件提示：review 时打印最大桶样本数，判断何时能上线经验先验。"""
    st = stage_b_bucket_stats(cfg, min_samples)
    if st["max_n"] == 0:
        return "阶段 B 经验先验：暂无回填样本（先跑 seed-plan 攒样本，再跑 review 回填）"
    if st["ready"]:
        return (f"阶段 B 经验先验：最大桶样本 {st['max_n']} ≥ {st['threshold']} 已达标，"
                f"可开始实现（{st['max_bucket']}，胜率 {st['max_rate']:.0%}）")
    return (f"阶段 B 经验先验：最大桶样本 {st['max_n']}/{st['threshold']}"
            f"（{st['max_bucket']}，胜率 {st['max_rate']:.0%}），"
            f"还差 {st['threshold'] - st['max_n']} 条达标")


def lowbuy_sample_stats(cfg: Optional[Config] = None) -> dict:
    """低吸（启动前夕）样本计数：seed_records 里 lowbuy=True 的样本，含回填进度。"""
    cfg = cfg or load_config()
    records = load_records(cfg)
    lowbuy = [r for r in records if r.get("lowbuy")]
    backfilled = [r for r in lowbuy if r.get("result") in ("win", "loss")]
    return {
        "total": len(lowbuy),
        "backfilled": len(backfilled),
        "wins": sum(1 for r in backfilled if r["result"] == "win"),
    }


def format_lowbuy_status(cfg: Optional[Config] = None) -> str:
    """低吸样本进度提示：review 时打印，判断何时能进入低吸验证（≥30 条）。"""
    st = lowbuy_sample_stats(cfg)
    if st["total"] == 0:
        return "低吸样本：0 条（跑 seed-plan 积累启动前夕候选，再跑 review 回填）"
    if st["backfilled"] == 0:
        return f"低吸样本：累计 {st['total']} 条，尚无回填（跑 review 回填 T+N）"
    return (f"低吸样本：累计 {st['total']} 条，已回填 {st['backfilled']} 条"
            f"（胜 {st['wins']}/{st['backfilled']}），目标 ≥30 条进入验证")


# ------------------------------------------------------------------ 样本缺口看板 + 影子桶 T+3

def sample_gap_stats(cfg: Optional[Config] = None) -> dict:
    """样本缺口看板：待 T+1 / 待 T+3、低吸、因子覆盖、新闸门后可买。

    只读统计，不改策略。今日未回填不算进「历史待 T+1」。
    """
    cfg = cfg or load_config()
    today = utils.today_str()
    gate = str(cfg.get("followthrough.p0_gate_date", "2026-08-26") or "2026-08-26")
    recs = load_records(cfg)
    pending_t1 = pending_t3 = today_waiting = 0
    factor_ok = 0
    post_buyable = 0
    post_buyable_t3 = 0
    for r in recs:
        if not r.get("code"):
            continue
        d = r.get("date") or ""
        if d == today:
            today_waiting += 1
        elif r.get("result") is None:
            pending_t1 += 1
        elif r.get("chg_t3") is None:
            pending_t3 += 1
        if r.get("bias_ma20") is not None:
            factor_ok += 1
        if r.get("track") == "可买" and d >= gate:
            post_buyable += 1
            if r.get("chg_t3") is not None:
                post_buyable_t3 += 1
    lb = lowbuy_sample_stats(cfg)
    return {
        "total": len(recs),
        "pending_t1": pending_t1,
        "pending_t3": pending_t3,
        "today_waiting": today_waiting,
        "lowbuy_total": lb["total"],
        "lowbuy_backfilled": lb["backfilled"],
        "factor_ok": factor_ok,
        "factor_pct": (factor_ok / len(recs)) if recs else 0.0,
        "p0_gate_date": gate,
        "post_gate_buyable": post_buyable,
        "post_gate_buyable_t3": post_buyable_t3,
    }


def format_sample_gap(cfg: Optional[Config] = None) -> str:
    """一行可读的样本缺口摘要（review / followthrough 打印）。"""
    st = sample_gap_stats(cfg)
    factor = f"{st['factor_ok']}/{st['total']}"
    if st["total"]:
        factor += f"（{st['factor_pct']:.0%}）"
    lines = [
        "===== 样本缺口看板 =====",
        (f"  累计 {st['total']} 条｜待 T+1 {st['pending_t1']}｜"
         f"待 T+3 {st['pending_t3']}｜今日待下一交易日 {st['today_waiting']}"),
        (f"  低吸 {st['lowbuy_total']} 条（已回填 {st['lowbuy_backfilled']}）｜"
         f"因子字段齐全 {factor}"),
        (f"  新闸门后（≥{st['p0_gate_date']}）可买 {st['post_gate_buyable']} 条"
         f"（其中已有 T+3 {st['post_gate_buyable_t3']}）"),
    ]
    return "\n".join(lines)


def shadow_t3_stats(cfg: Optional[Config] = None) -> dict:
    """影子桶「萌芽∪前三非突破」的 T+3>0 对照（不驱动计划）。

    验收门槛默认 t3_up_target=0.60；样本不足时 ready=False。
    """
    cfg = cfg or load_config()
    target = float(cfg.get("followthrough.t3_up_target", 0.60))
    min_n = int(cfg.get("followthrough.shadow_min_samples", 15))
    tagged: List[dict] = []
    for r in load_records(cfg):
        tag = resolve_shadow_tag(r)
        if not tag:
            continue
        tagged.append({**r, "_shadow_tag": tag})
    with_t3 = [r for r in tagged if r.get("chg_t3") is not None]
    t3_up = sum(1 for r in with_t3 if float(r["chg_t3"]) > 0)
    rate = (t3_up / len(with_t3)) if with_t3 else None
    # 分标签粗看（同一条可属多标签，分母按标签各自计）
    by_tag: Dict[str, dict] = {}
    for r in with_t3:
        for part in str(r["_shadow_tag"]).split("|"):
            if not part:
                continue
            slot = by_tag.setdefault(part, {"n": 0, "up": 0})
            slot["n"] += 1
            if float(r["chg_t3"]) > 0:
                slot["up"] += 1
    return {
        "n_tagged": len(tagged),
        "n_t3": len(with_t3),
        "t3_up": t3_up,
        "t3_up_rate": rate,
        "target": target,
        "min_samples": min_n,
        "ready": bool(rate is not None and len(with_t3) >= min_n and rate >= target),
        "by_tag": by_tag,
    }


def format_shadow_status(cfg: Optional[Config] = None) -> str:
    """影子桶 T+3 对照文案：对照验收门槛，不暗示可买。"""
    st = shadow_t3_stats(cfg)
    if st["n_tagged"] == 0:
        return ("影子桶（萌芽∪前三非突破）：0 条（跑 seed-plan 落盘 shadow_tag；"
                "只对照不驱动计划）")
    if st["n_t3"] == 0:
        return (f"影子桶：已标 {st['n_tagged']} 条，尚无 T+3 回填"
                f"（目标 T+3>0 ≥{st['target']:.0%}，至少 {st['min_samples']} 条）")
    rate = st["t3_up_rate"] or 0.0
    flag = "达标" if st["ready"] else (
        "样本不足" if st["n_t3"] < st["min_samples"] else "未达门槛")
    parts = []
    for name, slot in sorted(st["by_tag"].items(), key=lambda x: -x[1]["n"]):
        rr = slot["up"] / slot["n"] if slot["n"] else 0.0
        parts.append(f"{name} {slot['up']}/{slot['n']}={rr:.0%}")
    detail = "；".join(parts) if parts else "—"
    return (f"影子桶 T+3>0：{st['t3_up']}/{st['n_t3']} = {rate:.0%}"
            f"（目标 ≥{st['target']:.0%}，{flag}；已标 {st['n_tagged']}）"
            f"　{detail}")
