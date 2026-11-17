"""影子指标实验：只记录、分桶和前后半段对照，不参与计划与闸门。

新增指标先在这里跑 n≥min_samples 与前后半段稳定性；达标且方向一致后，才讨论
是否进闸。字段从现有 seed 记录派生，旧样本没有落盘字段时按同口径即时重算。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from tea.config.config_store import Config, load_config
from tea.core import utils

_SECTOR_DIM = "板块强度"

#: 质量确认影子分：只用于展示与迭代，不进评分/排序/闸门。
QUALITY_MAX = 2
QUALITY_SECTOR_CHG_MIN = 4.0
QUALITY_TIERS = ("严格档", "强势跟涨档")
_QUALITY_LABELS = {2: "双确认", 1: "单确认", 0: "无确认"}


def _dim_score(entry: dict, name: str) -> Optional[int]:
    for d in entry.get("scoring_dims") or []:
        if d.get("name") == name:
            try:
                return int(d.get("score"))
            except (TypeError, ValueError):
                return None
    return None


def _sector_chg_of(entry: dict) -> Optional[float]:
    v = entry.get("sector_chg")
    if v is None:
        v = (entry.get("sector") or {}).get("chg")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def quality_parts(tier: Any, sector_chg: Any) -> dict:
    """档位 × 板块涨幅的质量确认分（0–2，纯函数）。

    严格档/强势跟涨档 +1，板块涨幅 ≥4% +1；只做展示和样本分层，不进闸门。
    任一分量缺失时返回 ``score=None``，避免缺失被当成 0 分拉低统计。
    """
    t = str(tier).strip() if tier else ""
    try:
        c = float(sector_chg) if sector_chg is not None else None
    except (TypeError, ValueError):
        c = None
    parts: List[str] = []
    if t in QUALITY_TIERS:
        parts.append(f"档位{t} +1")
    elif t == "热点降级档":
        parts.append("热点降级档 0")
    if not t or c is None:
        missing = "档位" if not t else "板块涨幅"
        return {"score": None, "max": QUALITY_MAX, "label": "未知",
                "detail": "；".join(parts + [f"{missing}缺失"]), "parts": parts}
    score = 1 if t in QUALITY_TIERS else 0
    if c >= QUALITY_SECTOR_CHG_MIN:
        score += 1
        parts.append(f"板块{c:.1f}%≥{QUALITY_SECTOR_CHG_MIN:.0f} +1")
    else:
        parts.append(f"板块{c:.1f}%<{QUALITY_SECTOR_CHG_MIN:.0f} 0")
    return {"score": score, "max": QUALITY_MAX,
            "label": _QUALITY_LABELS.get(score, "未知"),
            "detail": "；".join(parts), "parts": parts}


def quality_of(entry: dict) -> dict:
    """优先读取落盘质量分；旧样本按 tier / sector_chg 即时重算。"""
    stored = entry.get("shadow_lab_quality")
    if stored is not None:
        try:
            score = int(stored)
        except (TypeError, ValueError):
            score = None
        if score in _QUALITY_LABELS:
            return {"score": score, "max": QUALITY_MAX,
                    "label": _QUALITY_LABELS[score],
                    "detail": entry.get("shadow_lab_quality_detail") or "",
                    "parts": []}
    return quality_parts(entry.get("tier") or entry.get("tier_label"),
                         _sector_chg_of(entry))


def quality_tag(entry: dict) -> str:
    q = quality_of(entry)
    if q["score"] is None:
        return ""
    return f"质量{q['score']}/{q['max']}"


def quality_cell(entry: dict) -> str:
    """表格/控制台短格式：``2/2``；未知为 ``—``。"""
    q = quality_of(entry)
    return f"{q['score']}/{q['max']}" if q["score"] is not None else "—"


def compute_fields(entry: dict, cfg: Optional[Config] = None) -> dict:
    """按扫描条目派生影子字段；只读，不改 entry。"""
    cfg = cfg or load_config()
    rank = entry.get("sector_rank")
    tier = entry.get("tier")
    bad_combo: Optional[bool] = None
    if rank is not None and tier:
        try:
            bad_combo = 4 <= int(rank) <= 5 and tier == "热点降级档"
        except (TypeError, ValueError):
            bad_combo = None

    wr_consensus: Optional[bool] = None
    ws = entry.get("winrate_score")
    if (entry.get("mode") or "rule") == "rule" and ws is not None:
        try:
            wr_consensus = float(ws) >= float(
                cfg.get("shadow_lab.wr_consensus_score", 4.0))
        except (TypeError, ValueError):
            wr_consensus = None

    bbw_state: Optional[str] = None
    bbw = entry.get("bb_bandwidth")
    if bbw is not None:
        try:
            x = float(bbw)
            if x <= float(cfg.get("shadow_lab.bbw_squeeze", 20.0)):
                bbw_state = "squeeze"
            elif x > float(cfg.get("shadow_lab.bbw_hot", 25.0)):
                bbw_state = "hot"
            else:
                bbw_state = "normal"
        except (TypeError, ValueError):
            bbw_state = None

    quality = quality_parts(entry.get("tier") or entry.get("tier_label"),
                            _sector_chg_of(entry))
    return {
        "shadow_lab_sector": _dim_score(entry, _SECTOR_DIM),
        "shadow_lab_bad_combo": bad_combo,
        "shadow_lab_wr_consensus": wr_consensus,
        "shadow_lab_bbw": bbw_state,
        "shadow_lab_quality": quality["score"],
        "shadow_lab_quality_detail": quality["detail"],
    }


def resolve_fields(rec: dict, cfg: Optional[Config] = None) -> dict:
    """读取落盘字段；旧样本缺字段时按扫描字段即时重算。"""
    computed = compute_fields(rec, cfg)
    return {
        "sector": (rec["shadow_lab_sector"] if "shadow_lab_sector" in rec
                   else computed["shadow_lab_sector"]),
        "bad_combo": (rec["shadow_lab_bad_combo"] if "shadow_lab_bad_combo" in rec
                      else computed["shadow_lab_bad_combo"]),
        "wr_consensus": (rec["shadow_lab_wr_consensus"] if "shadow_lab_wr_consensus" in rec
                         else computed["shadow_lab_wr_consensus"]),
        "bbw": (rec["shadow_lab_bbw"] if "shadow_lab_bbw" in rec
                else computed["shadow_lab_bbw"]),
    }


# ------------------------------------------------------------------ V2 胜率影子分

WINRATE_V2_MAX = 4
#: V2 影子分观察阈值；只用于对照统计，不接入闸门。
V2_SHADOW_THRESHOLD = 2


def winrate_v2_parts(rank: Any, chg: Any) -> dict:
    """按板块排名与涨幅算 V2 影子分（纯函数，供打分与历史样本重算共用）。"""
    parts: List[str] = []
    score = 0
    try:
        r = int(rank) if rank is not None else None
    except (TypeError, ValueError):
        r = None
    if r is not None:
        if r <= 3:
            score += 3
            parts.append(f"板块排名{r}≤3 +3")
        elif r <= 5:
            score -= 1
            parts.append(f"板块排名{r}∈4-5 -1")
        elif r <= 8:
            parts.append(f"板块排名{r}∈6-8 0")
        else:
            score -= 2
            parts.append(f"板块排名{r}>8 -2")
    try:
        c = float(chg) if chg is not None else None
    except (TypeError, ValueError):
        c = None
    if c is not None and 3.0 <= c <= 5.0:
        score += 1
        parts.append(f"涨幅{c:.1f}%∈3-5 +1")
    return {
        "score": score,
        "parts": parts,
        "detail": "；".join(parts) if parts else "无 V2 影子信号",
        "max": WINRATE_V2_MAX,
    }


def resolve_winrate_v2(rec: dict) -> dict:
    """优先用落盘 V2；旧样本按 sector_rank / chg_pct 即时重算。"""
    stored = rec.get("winrate_score_v2")
    if stored is not None:
        try:
            return {"score": int(stored),
                    "detail": rec.get("winrate_score_v2_detail"),
                    "source": "stored"}
        except (TypeError, ValueError):
            pass
    out = winrate_v2_parts(rec.get("sector_rank"), rec.get("chg_pct"))
    out["source"] = "recomputed"
    return out


_Buckets = Tuple[Tuple[str, Callable[[dict], bool]], ...]

_DIMENSIONS: Tuple[Tuple[str, str, _Buckets], ...] = (
    ("sector", "板块强度", (
        ("2", lambda f: f["sector"] == 2),
        ("1", lambda f: f["sector"] == 1),
        ("0", lambda f: f["sector"] == 0),
    )),
    ("bad_combo", "排名4-5+热点降级", (
        ("是", lambda f: f["bad_combo"] is True),
        ("否", lambda f: f["bad_combo"] is False),
    )),
    ("wr_consensus", "规则+胜率共识", (
        ("共识", lambda f: f["wr_consensus"] is True),
        ("非共识", lambda f: f["wr_consensus"] is False),
    )),
    ("bbw", "布林带宽", (
        ("过热", lambda f: f["bbw"] == "hot"),
        ("正常", lambda f: f["bbw"] == "normal"),
        ("压缩", lambda f: f["bbw"] == "squeeze"),
    )),
)


def _horizon(rows: List[dict], field: str) -> dict:
    vals = [float(r[field]) for r in rows if r.get(field) is not None]
    if not vals:
        return {"n": 0, "up": 0, "rate": None, "avg": None}
    up = sum(1 for v in vals if v > 0)
    return {"n": len(vals), "up": up, "rate": up / len(vals),
            "avg": sum(vals) / len(vals)}


def _slot(rows: List[dict]) -> dict:
    return {"t2": _horizon(rows, "chg_t2"),
            "t3": _horizon(rows, "chg_t3"),
            "t5": _horizon(rows, "chg_t5")}


def _split_date(pool: List[dict], dates: List[str]) -> Optional[str]:
    """按样本数取最接近对半的日期分界（同日样本不拆开）。"""
    if not dates:
        return None
    if len(dates) == 1:
        return dates[0]
    counts: dict = {}
    for r in pool:
        counts[r["date"]] = counts.get(r["date"], 0) + 1
    half = len(pool) / 2
    cum = 0
    best_idx, best_gap = 1, None
    for i, d in enumerate(dates[:-1], 1):
        cum += counts[d]
        gap = abs(cum - half)
        if best_gap is None or gap < best_gap:
            best_idx, best_gap = i, gap
    return dates[best_idx]


def shadow_lab_stats(cfg: Optional[Config] = None) -> dict:
    """按影子维度聚合 T+2/T+3/T+5，并按样本数对半切前后段。"""
    cfg = cfg or load_config()
    from tea.analysis.followthrough import load_valid_records

    min_n = int(cfg.get("shadow_lab.min_samples", 30))
    half_min = int(cfg.get("shadow_lab.half_min_samples", 5))
    max_gap = float(cfg.get("shadow_lab.stability_max_gap", 0.20))

    pool = [r for r in load_valid_records(cfg)
            if r.get("chg_t3") is not None and r.get("date")]
    pool.sort(key=lambda r: (r.get("date") or "", r.get("code") or ""))
    dates = sorted({r["date"] for r in pool})
    split_date = _split_date(pool, dates)
    pre = [r for r in pool if split_date and r["date"] < split_date]
    post = [r for r in pool if split_date and r["date"] >= split_date]
    pre_keys = {(r.get("date"), r.get("code")) for r in pre}

    dimensions: List[dict] = []
    for key, title, buckets in _DIMENSIONS:
        rows: List[dict] = []
        for label, pred in buckets:
            selected = [r for r in pool
                        if pred(resolve_fields(r, cfg))]
            pre_sel = [r for r in selected
                       if (r.get("date"), r.get("code")) in pre_keys]
            post_sel = [r for r in selected
                        if (r.get("date"), r.get("code")) not in pre_keys]
            slot = _slot(selected)
            pre_slot = _slot(pre_sel)
            post_slot = _slot(post_sel)
            t3 = slot["t3"]
            stable: Optional[bool] = None
            if (pre_slot["t3"]["n"] >= half_min and post_slot["t3"]["n"] >= half_min
                    and pre_slot["t3"]["rate"] is not None
                    and post_slot["t3"]["rate"] is not None):
                stable = abs(pre_slot["t3"]["rate"] - post_slot["t3"]["rate"]) <= max_gap
            if t3["n"] == 0:
                status = "无样本"
            elif t3["n"] < min_n:
                status = f"观察中 {t3['n']}/{min_n}"
            elif stable is None:
                status = "达标·半段不足"
            elif stable:
                status = "达标·稳定"
            else:
                status = "达标·不稳"
            rows.append({
                "label": label,
                "t2": slot["t2"], "t3": t3, "t5": slot["t5"],
                "pre": pre_slot, "post": post_slot,
                "ready": t3["n"] >= min_n,
                "stable": stable,
                "status": status,
            })
        dimensions.append({"key": key, "title": title, "buckets": rows})

    return {
        "pool_n": len(pool),
        "pre_n": len(pre),
        "post_n": len(post),
        "split_date": split_date,
        "min_samples": min_n,
        "half_min_samples": half_min,
        "stability_max_gap": max_gap,
        "dimensions": dimensions,
    }


def _fmt_rate(slot: dict) -> str:
    return f"{slot['rate']:.1%}" if slot.get("rate") is not None else "—"


def _fmt_half(slot: dict) -> str:
    return f"{_fmt_rate(slot)}({slot['n']})"


def format_shadow_lab(cfg: Optional[Config] = None) -> str:
    """复盘/CLI 可读看板：每桶样本进度 + T+2/T+3 + 前后半段。"""
    st = shadow_lab_stats(cfg)
    if not st["pool_n"]:
        return "影子指标实验：暂无 T+3 回填样本（先跑 review）"
    lines = [
        "===== 影子指标实验（只记录，不进闸）=====",
        (f"  T+3 样本 {st['pool_n']}｜每桶目标 ≥{st['min_samples']}｜"
         f"前后半段 {st['pre_n']}/{st['post_n']}（分界 {st['split_date']}）"),
    ]
    for dim in st["dimensions"]:
        lines.append(f"  {dim['title']}：")
        for b in dim["buckets"]:
            lines.append(
                f"    {b['label']:<5s} n={b['t3']['n']:2d}/{st['min_samples']}  "
                f"T+2 {_fmt_rate(b['t2']):>6s}  T+3 {_fmt_rate(b['t3']):>6s}  "
                f"前半 {_fmt_half(b['pre']['t3']):>10s}｜"
                f"后半 {_fmt_half(b['post']['t3']):>10s}  {b['status']}")
    lines.append(
        f"  判定：n≥{st['min_samples']} 且前后半段差≤"
        f"{st['stability_max_gap']:.0%} 才讨论进闸；当前只影响报告。")
    return "\n".join(lines)


def shadow_lab_summary(cfg: Optional[Config] = None) -> str:
    """周报/邮件一行摘要。"""
    st = shadow_lab_stats(cfg)
    buckets = [b for dim in st["dimensions"] for b in dim["buckets"]]
    if not buckets:
        return "影子指标：暂无 T+3 样本"
    ready = sum(1 for b in buckets if b["ready"])
    stable = sum(1 for b in buckets if b["ready"] and b["stable"] is True)
    top = max(buckets, key=lambda b: b["t3"]["n"])
    return (f"影子指标：{len(buckets)} 桶｜达 n≥{st['min_samples']} {ready} 个｜"
            f"前后一致 {stable} 个｜最大 {top['label']} n={top['t3']['n']}")


def shadow_lab_markdown(cfg: Optional[Config] = None) -> List[str]:
    """周报 Markdown 表格行。"""
    st = shadow_lab_stats(cfg)
    if not st["pool_n"]:
        return ["暂无 T+3 回填样本。"]
    lines = [
        (f"样本 {st['pool_n']}｜目标每桶 ≥{st['min_samples']}｜"
         f"前半 {st['pre_n']} / 后半 {st['post_n']}（分界 {st['split_date']}）"),
        "",
        "| 维度 | 桶 | n | T+2 | T+3 | 前半段 | 后半段 | 状态 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for dim in st["dimensions"]:
        for b in dim["buckets"]:
            lines.append(
                f"| {dim['title']} | {b['label']} | {b['t3']['n']} | "
                f"{_fmt_rate(b['t2'])} | {_fmt_rate(b['t3'])} | "
                f"{_fmt_half(b['pre']['t3'])} | {_fmt_half(b['post']['t3'])} | "
                f"{b['status']} |")
    return lines


# ------------------------------------------------------------------ V1 vs V2 对照

def _hit(rows: List[dict], field: str, lo: float) -> dict:
    vals = [float(r[field]) for r in rows if r.get(field) is not None]
    if not vals:
        return {"n": 0, "up": 0, "rate": None}
    up = sum(1 for v in vals if v >= lo)
    return {"n": len(vals), "up": up, "rate": up / len(vals)}


def winrate_v2_stats(cfg: Optional[Config] = None) -> dict:
    """V1/V2 影子分对照；旧样本缺 V2 时按 sector_rank/chg_pct 即时重算。"""
    cfg = cfg or load_config()
    from tea.analysis.followthrough import load_valid_records

    rows: List[dict] = []
    for r in load_valid_records(cfg):
        if r.get("chg_t3") is None:
            continue
        if r.get("sector_rank") is None and r.get("winrate_score_v2") is None:
            continue
        v2 = resolve_winrate_v2(r)
        rows.append({"rec": r, "v1": r.get("winrate_score"),
                     "v2": v2["score"], "source": v2["source"]})

    def group(pred) -> dict:
        sel = [x["rec"] for x in rows if pred(x)]
        return {"n": len(sel),
                "t1": _hit(sel, "next_chg", 3.0),
                "t3": _horizon(sel, "chg_t3"),
                "t5": _horizon(sel, "chg_t5")}

    buckets = {}
    for s in sorted({x["v2"] for x in rows}):
        buckets[s] = group(lambda x, s=s: x["v2"] == s)

    return {
        "n": len(rows),
        "stored": sum(1 for x in rows if x["source"] == "stored"),
        "recomputed": sum(1 for x in rows if x["source"] == "recomputed"),
        "v1_ge3": group(lambda x: x["v1"] is not None and x["v1"] >= 3),
        "v1_lt3": group(lambda x: x["v1"] is not None and x["v1"] < 3),
        "v2_ge": group(lambda x: x["v2"] >= V2_SHADOW_THRESHOLD),
        "v2_lt": group(lambda x: x["v2"] < V2_SHADOW_THRESHOLD),
        "buckets": buckets,
    }


def _fmt_slot(slot: dict) -> str:
    return (f"{slot['rate']:.0%}({slot['n']})"
            if slot.get("n") and slot.get("rate") is not None else "—")


def format_winrate_v2_compare(cfg: Optional[Config] = None) -> str:
    """复盘/CLI 可读的 V1 vs V2 影子分对照。"""
    st = winrate_v2_stats(cfg)
    if not st["n"]:
        return "V1/V2 胜率影子分：暂无 T+3 样本"
    lines = [
        "===== V1/V2 胜率影子分对照（只读，不进闸）=====",
        (f"  T+3 样本 {st['n']}｜V2 已落盘 {st['stored']}｜"
         f"即时重算 {st['recomputed']}（旧样本按 sector_rank/chg_pct）"),
    ]
    for label, key in (("V1≥3", "v1_ge3"), ("V1<3", "v1_lt3"),
                       (f"V2≥{V2_SHADOW_THRESHOLD}", "v2_ge"),
                       (f"V2<{V2_SHADOW_THRESHOLD}", "v2_lt")):
        g = st[key]
        lines.append(
            f"  {label:<5s} n={g['n']:2d}  T+1≥3% {_fmt_slot(g['t1']):>8s}  "
            f"T+3>0 {_fmt_slot(g['t3']):>8s}  T+5>0 {_fmt_slot(g['t5']):>8s}")
    parts = [f"{s}:{b['t3']['up']}/{b['t3']['n']}"
             for s, b in st["buckets"].items() if b["t3"]["n"]]
    lines.append("  V2 分桶 T+3>0：" + "；".join(parts))
    return "\n".join(lines)


# ------------------------------------------------------------------ 质量确认（展示层）

def quality_stats(cfg: Optional[Config] = None, since: Optional[str] = None,
                  until: Optional[str] = None, records: Optional[List[dict]] = None) -> dict:
    """按质量分档汇总 T+1≥3%、T+3/T+5 与均涨；只读。"""
    cfg = cfg or load_config()
    if records is None:
        from tea.analysis.followthrough import load_valid_records
        records = load_valid_records(cfg)
    pool = [
        r for r in records
        if (not since or (r.get("date") or "") >= since)
        and (not until or (r.get("date") or "") <= until)
    ]
    buckets: Dict[int, List[dict]] = {2: [], 1: [], 0: []}
    unknown = 0
    for r in pool:
        q = quality_of(r)
        if q["score"] is None:
            unknown += 1
            continue
        buckets[q["score"]].append(r)
    out = {}
    for score, rows in buckets.items():
        rows.sort(key=lambda r: (r.get("date") or "", r.get("code") or ""), reverse=True)
        out[score] = {
            "score": score, "label": _QUALITY_LABELS[score], "n": len(rows),
            "t1_ge3": _hit(rows, "next_chg", 3.0),
            "t3": _horizon(rows, "chg_t3"),
            "t5": _horizon(rows, "chg_t5"),
            "stocks": rows,
        }
    return {"since": since, "until": until, "unknown_n": unknown,
            "n": sum(b["n"] for b in out.values()), "buckets": out}


def _fmt_chg(v: Any) -> str:
    try:
        return f"{float(v):+.1f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_avg(slot: dict) -> str:
    return (_fmt_chg(slot["avg"])
            if slot.get("n") and slot.get("avg") is not None else "—")


def quality_stock_text(rec: dict, with_date: bool = False) -> str:
    parts: List[str] = []
    if with_date:
        parts.append(str(rec.get("date") or "—"))
    parts.append(f"{rec.get('code') or '—'} {rec.get('name') or ''}".strip())
    t3 = rec.get("chg_t3")
    parts.append("T+3 " + (_fmt_chg(t3) if t3 is not None else "待填"))
    if rec.get("chg_t5") is not None:
        parts.append(f"T+5 {_fmt_chg(rec.get('chg_t5'))}")
    c = _sector_chg_of(rec)
    if c is not None:
        parts.append(f"板块{_fmt_chg(c)}")
    return "  ".join(parts)


def _week_start(day: str) -> str:
    d = utils.parse_date(day)
    if d is None:
        return day
    return (d - timedelta(days=d.weekday())).isoformat()


def _quality_bucket_text(st: dict) -> str:
    chunks = []
    for score in (2, 1, 0):
        b = st["buckets"][score]
        if not b["n"]:
            continue
        chunks.append(f"{b['label']}{b['n']}只 T+3 {_fmt_slot(b['t3'])} 均{_fmt_avg(b['t3'])}")
    return "｜".join(chunks)


def format_quality_compare(cfg: Optional[Config] = None) -> str:
    """复盘/CLI 可读的质量确认摘要（只读）。"""
    st = quality_stats(cfg)
    if not st["n"]:
        return "质量确认：暂无可用样本（需 tier + 板块涨幅 + 回填）"
    lines = ["===== 质量确认（档位+板块，只读）=====",
             f"  样本 {st['n']}｜未知 {st['unknown_n']}｜" + _quality_bucket_text(st)]
    b2 = st["buckets"][2]
    if b2["stocks"]:
        lines.append("  双确认：" + "；".join(
            quality_stock_text(r, with_date=True) for r in b2["stocks"][:3]))
    return "\n".join(lines)


def quality_daily_lines(cfg: Optional[Config] = None, day: Optional[str] = None,
                        top_n: int = 3) -> List[str]:
    """日终摘要用：今日分档数量 + 受影响股票 + 本周累计表现。"""
    cfg = cfg or load_config()
    day = day or utils.today_str()
    from tea.analysis.followthrough import load_valid_records
    recs = [r for r in load_valid_records(cfg) if r.get("date") == day]
    qs = {2: [], 1: [], 0: []}
    for r in recs:
        q = quality_of(r)
        if q["score"] is not None:
            qs[q["score"]].append(r)
    unknown = len(recs) - sum(len(v) for v in qs.values())
    head = (f"今日 双确认 {len(qs[2])} / 单确认 {len(qs[1])} / 无确认 {len(qs[0])}"
            if recs else "今日无落盘样本（质量确认待回填）")
    if unknown:
        head += f" / 未知 {unknown}"
    lines = [head]
    for score in (2, 1, 0):
        rows = qs[score][:top_n]
        if rows:
            names = "、".join(
                f"{r.get('code')} {r.get('name') or ''}".strip() for r in rows)
            more = f" 等{len(qs[score])}只" if len(qs[score]) > len(rows) else ""
            lines.append(f"  {_QUALITY_LABELS[score]}：{names}{more}")
    week = quality_stats(cfg, since=_week_start(day), until=day)
    if week["n"]:
        lines.append("本周 " + _quality_bucket_text(week))
    return lines


def quality_email_lines(cfg: Optional[Config] = None, since: Optional[str] = None,
                        until: Optional[str] = None, top_n: int = 3) -> List[str]:
    """周报邮件精简段：分档表现 + 双确认/无确认代表股票。"""
    st = quality_stats(cfg, since=since, until=until)
    if not st["n"]:
        return ["质量确认：本周暂无可比样本（需 tier + 板块涨幅 + 回填）"]
    lines = ["质量确认 " + _quality_bucket_text(st)]
    for score in (2, 1, 0):
        b = st["buckets"][score]
        if not b["n"]:
            continue
        items = "；".join(quality_stock_text(r) for r in b["stocks"][:top_n])
        more = f"；…另{len(b['stocks']) - top_n}只" if len(b["stocks"]) > top_n else ""
        lines.append(f"  {b['label']}：{items}{more}")
    return lines


def quality_weekly_markdown(cfg: Optional[Config] = None, since: Optional[str] = None,
                            until: Optional[str] = None, top_n: int = 12) -> List[str]:
    """周报完整版：质量分档表 + 受影响股票（含 T+3/T+5）。"""
    st = quality_stats(cfg, since=since, until=until)
    if not st["n"]:
        return ["暂无质量确认样本（需 tier + sector_chg；旧样本即时重算）。"]
    b2, b1, b0 = (st["buckets"][s] for s in (2, 1, 0))
    lines = [
        (f"样本 {st['n']}｜双确认 {b2['n']}｜单确认 {b1['n']}｜"
         f"无确认 {b0['n']}｜未知 {st['unknown_n']}（需 tier + 板块涨幅）"),
        "",
        "| 分档 | 样本 | T+1≥3% | T+3>0 | T+5>0 | T+3均涨 | T+5均涨 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for b in (b2, b1, b0):
        lines.append(
            f"| {b['label']} | {b['n']} | {_fmt_slot(b['t1_ge3'])} | "
            f"{_fmt_slot(b['t3'])} | {_fmt_slot(b['t5'])} | "
            f"{_fmt_avg(b['t3'])} | {_fmt_avg(b['t5'])} |")
    lines.append("")
    for b in (b2, b1, b0):
        if not b["stocks"]:
            continue
        items = "；".join(quality_stock_text(r, with_date=True)
                          for r in b["stocks"][:top_n])
        more = f"；…另{len(b['stocks']) - top_n}只" if len(b["stocks"]) > top_n else ""
        lines.append(f"- **{b['label']}**：{items}{more}")
    return lines
