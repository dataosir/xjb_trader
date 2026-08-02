"""交易统计：整体绩效 + 分组归因（评分档 / 身份 / 阶段 / 板块 / 持仓天数）。

回答两个问题：
1. 这套纪律赚不赚钱（胜率、R 期望、盈亏比、回撤）
2. 钱是从哪个维度赚来的（哪个评分档/身份/阶段值得加仓）
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from . import trades as trades_mod
from . import utils
from .config_store import Config, load_config

RESULT_WIN = trades_mod.RESULT_WIN
RESULT_LOSS = trades_mod.RESULT_LOSS


# ------------------------------------------------------------------ 基础指标

def perf(ts: List[dict]) -> Dict[str, Any]:
    """一组交易的绩效指标。"""
    n = len(ts)
    wins = [t for t in ts if t.get("result") == RESULT_WIN]
    losses = [t for t in ts if t.get("result") == RESULT_LOSS]
    pnls = [float(t.get("pnl") or 0.0) for t in ts]
    rs = [float(t["r_multiple"]) for t in ts if t.get("r_multiple") is not None]
    win_pnl = sum(float(t.get("pnl") or 0.0) for t in wins)
    loss_pnl = sum(abs(float(t.get("pnl") or 0.0)) for t in losses)
    avg_win = utils.mean([float(t.get("pnl") or 0.0) for t in wins])
    avg_loss = utils.mean([abs(float(t.get("pnl") or 0.0)) for t in losses])
    wr = (len(wins) / n) if n else None
    payoff = (avg_win / avg_loss) if (avg_win and avg_loss) else None
    return {
        "n": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(wr, 4) if wr is not None else None,
        "pnl": round(sum(pnls), 2),
        "avg_pnl": round(utils.mean(pnls), 2) if pnls else None,
        "avg_win": round(avg_win, 2) if avg_win else None,
        "avg_loss": round(avg_loss, 2) if avg_loss else None,
        "payoff": round(payoff, 2) if payoff else None,
        "profit_factor": round(win_pnl / loss_pnl, 2) if loss_pnl else None,
        "avg_r": round(utils.mean(rs), 2) if rs else None,
        "sum_r": round(sum(rs), 2) if rs else None,
        "best": round(max(pnls), 2) if pnls else None,
        "worst": round(min(pnls), 2) if pnls else None,
        # 实测期望：E[R] = 胜率×平均盈利R − (1−胜率)×平均亏损R
        "expectancy_r": round(utils.mean(rs), 3) if rs else None,
        "avg_hold_days": round(utils.mean([float(t["hold_days"]) for t in ts
                                           if t.get("hold_days") is not None]), 1)
        if any(t.get("hold_days") is not None for t in ts) else None,
    }


def equity_curve(ts: List[dict], start_capital: Optional[float] = None) -> Dict[str, Any]:
    """资金曲线与最大回撤（按平仓顺序累计）。"""
    base = float(start_capital or 0.0)
    eq = base
    peak = base
    curve: List[dict] = []
    max_dd = 0.0
    max_dd_pct = 0.0
    for t in ts:
        eq += float(t.get("pnl") or 0.0)
        peak = max(peak, eq)
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = (dd / peak * 100.0) if peak else 0.0
        curve.append({"date": t.get("closed_date"), "code": t.get("code"),
                      "pnl": t.get("pnl"), "equity": round(eq, 2)})
    return {"curve": curve, "final": round(eq, 2), "peak": round(peak, 2),
            "max_drawdown": round(max_dd, 2), "max_drawdown_pct": round(max_dd_pct, 2),
            "start": round(base, 2)}


def streaks(ts: List[dict]) -> Dict[str, int]:
    """最长连胜 / 最长连亏 / 当前连亏。"""
    best_w = best_l = cur_w = cur_l = 0
    for t in ts:
        if t.get("result") == RESULT_WIN:
            cur_w += 1
            cur_l = 0
        elif t.get("result") == RESULT_LOSS:
            cur_l += 1
            cur_w = 0
        else:
            cur_w = cur_l = 0
        best_w = max(best_w, cur_w)
        best_l = max(best_l, cur_l)
    return {"max_win_streak": best_w, "max_loss_streak": best_l, "current_loss_streak": cur_l}


# ------------------------------------------------------------------ 分组归因

def group_by(ts: List[dict], keyf: Callable[[dict], Any],
             min_n: int = 1) -> Dict[str, Dict[str, Any]]:
    buckets: Dict[str, List[dict]] = {}
    for t in ts:
        k = keyf(t)
        buckets.setdefault("—" if k is None else str(k), []).append(t)
    out = {k: perf(v) for k, v in buckets.items() if len(v) >= min_n}
    return dict(sorted(out.items(), key=lambda kv: -(kv[1]["pnl"] or 0)))


def _score_key(t: dict) -> str:
    s = t.get("total_score")
    if s is None:
        return "未记录"
    s = int(s)
    if s >= 8:
        return "8~9 分"
    if s == 7:
        return "7 分"
    if s == 6:
        return "6 分"
    return "≤5 分"


def _hold_key(t: dict) -> str:
    d = t.get("hold_days")
    if d is None:
        return "未记录"
    d = int(d)
    if d <= 1:
        return "T+1 内"
    if d <= 3:
        return "2~3 天"
    if d <= 5:
        return "4~5 天"
    return ">5 天"


def attribution(cfg: Optional[Config] = None) -> Dict[str, Any]:
    """全维度归因（供周报/统计菜单使用）。"""
    cfg = cfg or load_config()
    ts = trades_mod.effective_trades(cfg)
    return {
        "by_score": group_by(ts, _score_key),
        "by_identity": group_by(ts, lambda t: t.get("identity_tier")),
        "by_stage": group_by(ts, lambda t: t.get("stage_label")),
        "by_sector": group_by(ts, lambda t: t.get("sector_name"), min_n=2),
        "by_hold": group_by(ts, _hold_key),
        "by_reason": group_by(ts, lambda t: t.get("reason")),
    }


def overall(cfg: Optional[Config] = None) -> Dict[str, Any]:
    """整体统计（含资金曲线、连续段、归因）。"""
    from . import portfolio
    cfg = cfg or load_config()
    ts = trades_mod.effective_trades(cfg)
    ts = sorted(ts, key=lambda t: (t.get("closed_date") or "", t.get("closed_at") or ""))
    p = perf(ts)
    cap_now = portfolio.get_capital(cfg)
    start = cap_now - (p["pnl"] or 0.0)
    return {
        "perf": p, "streaks": streaks(ts),
        "equity": equity_curve(ts, start),
        "capital_now": round(cap_now, 2),
        "positions": len(portfolio.positions(cfg)),
        "attribution": attribution(cfg),
        "cancelled": len([t for t in trades_mod.load_trades(cfg)
                          if t.get("result") == "cancelled"]),
        "first_date": ts[0].get("closed_date") if ts else None,
        "last_date": ts[-1].get("closed_date") if ts else None,
    }


# ------------------------------------------------------------------ 展示

def fmt_wr(v: Optional[float]) -> str:
    """胜率百分比文本（None → 破折号）。"""
    return f"{v * 100:.1f}%" if v is not None else "—"


_wr = fmt_wr


def _perf_line(name: str, p: dict, width: int = 12) -> str:
    return (f"  {name:<{width}} {p['n']:>3} 笔  胜率 {_wr(p['win_rate']):>6}"
            f"  盈亏 {utils.money(p['pnl']):>12}  均R {utils.num(p.get('avg_r')):>6}"
            f"  盈亏比 {utils.num(p.get('payoff')):>5}")


def format_stats(st: Optional[dict] = None, cfg: Optional[Config] = None) -> str:
    st = st or overall(cfg)
    p, sk, eq = st["perf"], st["streaks"], st["equity"]
    lines = ["===== 交易统计 ====="]
    if not p["n"]:
        lines.append("  暂无已平仓交易")
        return "\n".join(lines)
    lines += [
        f"  区间 {st.get('first_date')} ~ {st.get('last_date')}"
        f"   持仓中 {st.get('positions')} 只   撤销 {st.get('cancelled')} 笔",
        f"  {p['n']} 笔（{p['wins']} 胜 / {p['losses']} 负）  胜率 {_wr(p['win_rate'])}",
        f"  累计盈亏 {utils.money(p['pnl'])}   均笔 {utils.money(p.get('avg_pnl'))}"
        f"   最好 {utils.money(p.get('best'))}   最差 {utils.money(p.get('worst'))}",
        f"  平均盈利 {utils.money(p.get('avg_win'))}   平均亏损 {utils.money(p.get('avg_loss'))}"
        f"   盈亏比 {utils.num(p.get('payoff'))}   Profit Factor {utils.num(p.get('profit_factor'))}",
        f"  平均 R {utils.num(p.get('avg_r'))}   累计 R {utils.num(p.get('sum_r'))}"
        f"   平均持仓 {utils.num(p.get('avg_hold_days'), 1)} 天",
        f"  资金 {utils.money(eq.get('start'))} → {utils.money(st.get('capital_now'))}"
        f"   峰值 {utils.money(eq.get('peak'))}"
        f"   最大回撤 {utils.money(eq.get('max_drawdown'))}（{utils.num(eq.get('max_drawdown_pct'))}%）",
        f"  最长连胜 {sk['max_win_streak']}   最长连亏 {sk['max_loss_streak']}"
        f"   当前连亏 {sk['current_loss_streak']}",
    ]
    for title, key in (("按共振分", "by_score"), ("按身份", "by_identity"),
                       ("按阶段", "by_stage"), ("按持仓天数", "by_hold"),
                       ("按板块", "by_sector"), ("按平仓原因", "by_reason")):
        groups = (st.get("attribution") or {}).get(key) or {}
        if not groups:
            continue
        lines.append(f"  ---- {title} ----")
        for k, gp in groups.items():
            lines.append(_perf_line(k, gp))
    return "\n".join(lines)


def render_md(st: Optional[dict] = None, cfg: Optional[Config] = None) -> str:
    """统计的 Markdown 片段（周报复用）。"""
    st = st or overall(cfg)
    p, sk, eq = st["perf"], st["streaks"], st["equity"]
    if not p["n"]:
        return "## 交易统计\n\n暂无已平仓交易。\n"
    lines = [
        "## 交易统计", "",
        f"- 区间：{st.get('first_date')} ~ {st.get('last_date')}",
        f"- 交易 **{p['n']}** 笔（{p['wins']} 胜 / {p['losses']} 负），胜率 **{_wr(p['win_rate'])}**",
        f"- 累计盈亏 **{utils.money(p['pnl'])}**，均笔 {utils.money(p.get('avg_pnl'))}，"
        f"平均 R **{utils.num(p.get('avg_r'))}**，累计 R {utils.num(p.get('sum_r'))}",
        f"- 盈亏比 {utils.num(p.get('payoff'))}，Profit Factor {utils.num(p.get('profit_factor'))}，"
        f"平均持仓 {utils.num(p.get('avg_hold_days'), 1)} 天",
        f"- 资金 {utils.money(eq.get('start'))} → {utils.money(st.get('capital_now'))}，"
        f"最大回撤 {utils.money(eq.get('max_drawdown'))}（{utils.num(eq.get('max_drawdown_pct'))}%）",
        f"- 最长连胜 {sk['max_win_streak']}，最长连亏 {sk['max_loss_streak']}，"
        f"当前连亏 **{sk['current_loss_streak']}**",
        "",
    ]
    for title, key in (("按共振分", "by_score"), ("按身份", "by_identity"),
                       ("按阶段", "by_stage"), ("按持仓天数", "by_hold"),
                       ("按板块", "by_sector")):
        groups = (st.get("attribution") or {}).get(key) or {}
        if not groups:
            continue
        lines += [f"### 归因 · {title}", "",
                  "| 分组 | 笔数 | 胜率 | 盈亏 | 均 R | 盈亏比 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for k, gp in groups.items():
            lines.append(f"| {k} | {gp['n']} | {_wr(gp['win_rate'])} | {utils.money(gp['pnl'])} "
                         f"| {utils.num(gp.get('avg_r'))} | {utils.num(gp.get('payoff'))} |")
        lines.append("")
    return "\n".join(lines)
