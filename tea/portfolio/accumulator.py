"""日积累：把每天的扫描/评估/决策沉淀成 accumulator.jsonl，并汇总落选追溯。

"宁缺毋滥"最大的成本是"空仓日说不清为什么空"。本模块负责回答：
今天扫了多少、卡在哪一步、为什么没票、当时的市场天气如何。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from tea.config.config_store import Config, load_config
from tea.core import utils
from tea.reporting import seed_trace

KIND_SEED = "seed_scan"
KIND_EVAL = "evaluation"
KIND_SESSION = "session"
KIND_PLAN = "plan"
KIND_TRADE = "trade"
KIND_NOTE = "note"
KIND_PARAM = "param_change"


def log_path(cfg: Optional[Config] = None) -> str:
    return (cfg or load_config()).data_file("accumulator_file")


def record(kind: str, payload: dict, cfg: Optional[Config] = None) -> dict:
    """追加一条积累记录（JSONL 追加写，不覆盖历史）。"""
    cfg = cfg or load_config()
    rec = {
        "date": utils.today_str(),
        "ts": utils.now().strftime("%Y-%m-%d %H:%M:%S"),
        "kind": kind,
    }
    rec.update(payload or {})
    utils.append_jsonl(log_path(cfg), rec)
    return rec


# ------------------------------------------------------------------ 各类记录

def record_seed(summary: dict, cfg: Optional[Config] = None) -> dict:
    """记录一次种子扫描（传 seed_report.summarize() 的结果）。"""
    return record(KIND_SEED, dict(summary), cfg)


def record_eval(ev: dict, decision: str, note: str = "", cfg: Optional[Config] = None) -> dict:
    idn = ev.get("identity") or {}
    lv = ev.get("levels") or {}
    q = ev.get("quote") or {}
    return record(KIND_EVAL, {
        "code": ev.get("code"), "name": ev.get("name"), "decision": decision,
        "price": q.get("price"), "chg": q.get("chg_pct"),
        "score": ev.get("total_score"), "threshold": ev.get("pass_threshold"),
        "identity_score": idn.get("score"), "identity_tier": idn.get("tier"),
        "stage": (ev.get("stage") or {}).get("stage"),
        "sector": (ev.get("sector") or {}).get("name"),
        "sl_pct": lv.get("sl_pct"), "tp_pct": lv.get("tp_pct"), "odds": lv.get("odds"),
        "veto": [i["label"] for i in (ev.get("veto") or {}).get("items", [])],
        "reasons": ev.get("reasons") or [], "note": note,
    }, cfg)


def record_session(sent: Optional[dict], gate: Optional[dict] = None,
                   cfg: Optional[Config] = None) -> dict:
    return record(KIND_SESSION, {
        "sentiment_score": (sent or {}).get("score"),
        "cycle": (sent or {}).get("cycle"), "stance": (sent or {}).get("stance"),
        "base_pos_mult": (sent or {}).get("base_pos_mult"),
        "ice_cut": (sent or {}).get("ice_cut"),
        "allow_new": (sent or {}).get("allow_new"),
        "gate_allowed": (gate or {}).get("allowed"),
        "gate_blocks": (gate or {}).get("blocks") or [],
    }, cfg)


def record_plan(action: str, plan: dict, note: str = "", cfg: Optional[Config] = None) -> dict:
    return record(KIND_PLAN, {
        "action": action, "status": plan.get("status"),
        "planned_date": plan.get("planned_date"), "execute_date": plan.get("execute_date"),
        "codes": [i.get("code") for i in (plan.get("items") or [])], "note": note,
    }, cfg)


def record_trade(trade: dict, action: str, cfg: Optional[Config] = None) -> dict:
    return record(KIND_TRADE, {
        "action": action, "code": trade.get("code"), "name": trade.get("name"),
        "entry": trade.get("entry_price"), "exit": trade.get("exit_price"),
        "shares": trade.get("shares"), "pnl": trade.get("pnl"),
        "pnl_pct": trade.get("pnl_pct"), "r_multiple": trade.get("r_multiple"),
        "reason": trade.get("exit_reason") or trade.get("reason"),
    }, cfg)


def note(text: str, cfg: Optional[Config] = None) -> dict:
    return record(KIND_NOTE, {"text": text}, cfg)


def record_param(key: str, old: Any, new: Any, cfg: Optional[Config] = None) -> dict:
    """留痕一次参数变更：old → new，供「何时动了哪个阈值」复盘。

    调参是高频动作，若不落盘，事后无法回答「这个阈值是那天改的吗、改前是多少」。
    写进 accumulator.jsonl（param_change），与扫描/评估/交易同一条追溯链。
    """
    return record(KIND_PARAM, {"key": key, "old": old, "new": new}, cfg)


# ------------------------------------------------------------------ 查询

def load_log(cfg: Optional[Config] = None, kind: Optional[str] = None,
         date: Optional[str] = None, since: Optional[str] = None) -> List[dict]:
    recs = utils.read_jsonl(log_path(cfg))
    if kind:
        recs = [r for r in recs if r.get("kind") == kind]
    if date:
        recs = [r for r in recs if r.get("date") == date]
    if since:
        recs = [r for r in recs if (r.get("date") or "") >= since]
    return recs


def dates(cfg: Optional[Config] = None) -> List[str]:
    return sorted({r.get("date") for r in load_log(cfg) if r.get("date")})


def day_digest(date: Optional[str] = None, cfg: Optional[Config] = None) -> dict:
    """单日汇总：天气、扫描漏斗、评估决策分布、落选原因 TOP。"""
    cfg = cfg or load_config()
    date = date or utils.today_str()
    recs = load_log(cfg, date=date)
    seeds = [r for r in recs if r.get("kind") == KIND_SEED]
    evals = [r for r in recs if r.get("kind") == KIND_EVAL]
    sessions = [r for r in recs if r.get("kind") == KIND_SESSION]
    trades = [r for r in recs if r.get("kind") == KIND_TRADE]

    decisions: Dict[str, int] = {}
    for e in evals:
        d = e.get("decision") or "?"
        decisions[d] = decisions.get(d, 0) + 1

    last_seed = seeds[-1] if seeds else {}
    last_sess = sessions[-1] if sessions else {}
    traces = seed_trace.load_traces(cfg, date)
    reasons: Dict[str, int] = {}
    for t in traces:
        reasons[t.get("reason", "?")] = reasons.get(t.get("reason", "?"), 0) + 1

    return {
        "date": date, "records": len(recs),
        "sentiment_score": last_sess.get("sentiment_score") or last_seed.get("sentiment_score"),
        "cycle": last_sess.get("cycle") or last_seed.get("cycle"),
        "stance": last_sess.get("stance") or last_seed.get("stance"),
        "scans": len(seeds), "verdict": last_seed.get("verdict"),
        "tier": last_seed.get("tier"),
        "candidates_n": last_seed.get("candidates_n"),
        "veto_passed_n": last_seed.get("veto_passed_n"),
        "buyable_n": len(last_seed.get("buyable") or []),
        "watch_n": len(last_seed.get("watch") or []),
        "sectors": last_seed.get("sectors") or [],
        "evaluations": len(evals), "decisions": decisions,
        "trades": len(trades),
        "trace_n": len(traces),
        "top_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])[:8]),
        "notes": [r.get("text") for r in recs if r.get("kind") == KIND_NOTE],
    }


def range_digest(days: int = 7, cfg: Optional[Config] = None) -> dict:
    """近 N 个有记录日的汇总（用于周报）。"""
    cfg = cfg or load_config()
    ds = dates(cfg)[-days:]
    digests = [day_digest(d, cfg) for d in ds]
    verdicts: Dict[str, int] = {}
    reasons: Dict[str, int] = {}
    for d in digests:
        v = d.get("verdict") or "NO_SCAN"
        verdicts[v] = verdicts.get(v, 0) + 1
        for k, n in (d.get("top_reasons") or {}).items():
            reasons[k] = reasons.get(k, 0) + n
    scores = [d["sentiment_score"] for d in digests if d.get("sentiment_score") is not None]
    return {
        "days": ds, "digests": digests, "verdicts": verdicts,
        "reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "avg_sentiment": utils.mean(scores),
        "total_evaluations": sum(d.get("evaluations") or 0 for d in digests),
        "total_buyable": sum(d.get("buyable_n") or 0 for d in digests),
        "total_trades": sum(d.get("trades") or 0 for d in digests),
    }


# ------------------------------------------------------------------ 展示

def format_day(dg: dict) -> str:
    lines = [f"===== 日积累 {dg.get('date')}（{dg.get('records')} 条记录）====="]
    if dg.get("sentiment_score") is not None:
        lines.append(f"  天气：{utils.num(dg['sentiment_score'], 1)} 分 · {dg.get('cycle')} · "
                     f"姿态 {dg.get('stance')}")
    if dg.get("scans"):
        lines.append(f"  扫描 {dg['scans']} 次   裁决 {dg.get('verdict')}   档位 {dg.get('tier') or '—'}")
        lines.append(f"  漏斗：初筛 {dg.get('candidates_n') or 0} → VETO 过 {dg.get('veto_passed_n') or 0}"
                     f" → 可买 {dg.get('buyable_n')} · 观察 {dg.get('watch_n')}")
        for s in (dg.get("sectors") or [])[:3]:
            lines.append(f"    #{s.get('rank')} {s.get('name')} {utils.pct(s.get('chg'))}"
                         f" 涨停 {s.get('limit_up_count')} 家")
    else:
        lines.append("  今日无种子扫描记录")
    if dg.get("evaluations"):
        dec = "  ".join(f"{k} {v}" for k, v in (dg.get("decisions") or {}).items())
        lines.append(f"  评估 {dg['evaluations']} 次   {dec}")
    if dg.get("trades"):
        lines.append(f"  交易动作 {dg['trades']} 次")
    if dg.get("top_reasons"):
        lines.append(f"  ---- 落选原因 TOP（共 {dg.get('trace_n')} 条）----")
        for k, v in dg["top_reasons"].items():
            lines.append(f"    {v:>3}  {k}")
    for n in (dg.get("notes") or []):
        lines.append(f"  · {n}")
    return "\n".join(lines)


def format_range(rd: dict) -> str:
    ds = rd.get("days") or []
    lines = [f"===== 近期积累（{len(ds)} 个交易日）====="]
    if not ds:
        lines.append("  暂无记录")
        return "\n".join(lines)
    lines.append(f"  区间 {ds[0]} ~ {ds[-1]}   平均情绪 {utils.num(rd.get('avg_sentiment'), 1)}")
    lines.append("  裁决分布：" + "  ".join(f"{k} {v} 天" for k, v in (rd.get("verdicts") or {}).items()))
    lines.append(f"  累计评估 {rd.get('total_evaluations')} 次 · 可买 {rd.get('total_buyable')} 只"
                 f" · 交易动作 {rd.get('total_trades')} 次")
    lines.append("  ---- 每日 ----")
    for d in rd.get("digests", []):
        lines.append(f"    {d.get('date')}  情绪 {utils.num(d.get('sentiment_score'), 1):>5}"
                     f"  {str(d.get('stance') or '—'):<4}  {str(d.get('verdict') or 'NO_SCAN'):<14}"
                     f"  初筛 {d.get('candidates_n') or 0:>3} → 可买 {d.get('buyable_n')}")
    if rd.get("reasons"):
        lines.append("  ---- 落选原因累计 TOP ----")
        for k, v in list(rd["reasons"].items())[:10]:
            lines.append(f"    {v:>4}  {k}")
    return "\n".join(lines)
