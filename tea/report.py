"""TRADE_CHECK 报告生成：每次评估（BUY / REJECT / CANCEL）存档为 Markdown。

ctx 由 runner.run_once 组装，字段见 `EXPECTED_KEYS`。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import preflight, utils
from .config_store import Config, load_config

DECISION_BUY = "BUY"
DECISION_REJECT = "REJECT"
DECISION_CANCEL = "CANCEL"

DECISION_LABEL = {
    DECISION_BUY: "BUY（准入通过，灰度仓已记录）",
    DECISION_REJECT: "REJECT（准入否决）",
    DECISION_CANCEL: "CANCEL（评分达标但纪律不允许，主动撤单）",
}

EXPECTED_KEYS = (
    "at", "code", "name", "decision", "force", "sentiment", "session_gate", "code_gate",
    "buy_gate", "ev", "expectancy", "followthrough", "sizing", "capital", "available",
    "plan_item", "blocks", "notes", "phase_log",
)


# ------------------------------------------------------------------ 片段

def _gate_block(title: str, g: Optional[dict]) -> List[str]:
    if not g:
        return []
    lines = [f"### {title}", ""]
    lines.append(f"- 结果：{'放行' if g.get('allowed') else '拦截'}"
                 + ("（需 FORCE）" if g.get("requires_force") else ""))
    for b in g.get("blocks") or []:
        lines.append(f"- ✗ **[{b['rule']}]** {b['detail']}")
    for w in g.get("warnings") or []:
        lines.append(f"- ! [{w['rule']}] {w['detail']}")
    lines.append("")
    return lines


def _sentiment_block(sent: Optional[dict]) -> List[str]:
    lines = ["## 道 · 市场天气", ""]
    if not sent:
        lines += ["情绪数据缺失。", ""]
        return lines
    lines += [
        f"- 情绪分：**{utils.num(sent.get('score'), 1)}** / 100",
        f"- 周期：{sent.get('cycle')}　姿态：**{sent.get('stance')}**",
        f"- 半仓基数：×{utils.num(sent.get('base_pos_mult'), 2)}"
        + ("（冰点降仓生效）" if sent.get("ice_cut") else ""),
        f"- 允许新开：{'是' if sent.get('allow_new', True) else '否'}",
    ]
    br = sent.get("breadth") or {}
    idx = sent.get("index") or {}
    lines.append(f"- 涨停 {sent.get('limit_up_count')} 家　最高板 {sent.get('max_boards')}　"
                 f"上涨占比 {('%.1f%%' % (sent['advance_ratio'] * 100)) if sent.get('advance_ratio') is not None else '—'}"
                 f"（涨 {br.get('rising')} / 跌 {br.get('falling')}）　"
                 f"上证 {utils.num(idx.get('point'))}（{utils.pct(idx.get('chg_pct'))}，"
                 f"MA20 {'上方' if sent.get('ma20_above') else '下方'}）")
    lines.append(f"- 热点板块 {sent.get('hot_n')} 个　前 5 板块均涨 {utils.pct(sent.get('avg5'))}")
    if sent.get("hot_sectors"):
        lines.append("- 热点：" + "　".join(f"{x['name']} {x['chg']:+.2f}%" for x in sent["hot_sectors"][:6]))
    if sent.get("deltas"):
        lines += ["", "| 情绪加减项 | 分值 | 依据 |", "| --- | --- | --- |"]
        for d in sent["deltas"]:
            lines.append(f"| {d.get('item')} | {d.get('delta'):+g} | {d.get('detail') or ''} |")
    for n in sent.get("notes") or []:
        lines.append(f"- {n}")
    lines.append("")
    return lines


def _quote_block(ev: dict) -> List[str]:
    q, sec, ind = ev.get("quote") or {}, ev.get("sector") or {}, ev.get("ind") or {}
    return [
        "## 术 · 标的快照", "",
        f"- 现价 **{utils.num(q.get('price'))}**　涨幅 **{utils.pct(q.get('chg_pct'))}**　"
        f"最高 {utils.num(q.get('high'))}　最低 {utils.num(q.get('low'))}　昨收 {utils.num(q.get('pre_close'))}",
        f"- 换手 {utils.pct(q.get('turnover'))}　量比 {utils.num(q.get('vol_ratio'))}　"
        f"成交额 {utils.num(q.get('amount_yi'))} 亿　流通市值 {utils.num(q.get('cap_yi'))} 亿",
        f"- 板块 **{sec.get('name') or '未识别'}**（第 {sec.get('rank')} 名，涨幅 {utils.pct(sec.get('chg'))}，"
        f"涨停 {sec.get('limit_up_count')} 家）　板块内 {sec.get('stock_rank')}/{sec.get('member_total')}",
        f"- MA5 {utils.num(ind.get('ma5'))}　MA10 {utils.num(ind.get('ma10'))}　MA20 {utils.num(ind.get('ma20'))}"
        f"　多头排列 {'是' if ind.get('ma_bull') else '否'}",
        f"- MA20 乖离 **{utils.pct(ind.get('bias_ma20'))}**　ATR% {utils.num(ind.get('atr_pct'))}"
        f"　分时位置 **{('%.0f%%' % (ev['intraday'] * 100)) if ev.get('intraday') is not None else '—'}**"
        f"　阶段 **{(ev.get('stage') or {}).get('stage')}**",
        "",
    ]


def _identity_block(ev: dict) -> List[str]:
    idn = ev.get("identity") or {}
    lines = [f"## 术 · 身份判定：**{idn.get('tier')}**（{utils.num(idn.get('score'), 1)} 分）", ""]
    if idn.get("deltas"):
        lines += ["| 维度 | 分值 | 说明 |", "| --- | --- | --- |"]
        for d in idn["deltas"]:
            lines.append(f"| {d.get('name')} | {d.get('delta'):+g} | {d.get('detail') or ''} |")
    if idn.get("flags"):
        lines += ["", "杂毛标记：" + "；".join(idn["flags"])]
    if idn.get("forced_zamao"):
        lines.append("标记 ≥2 项 → 强制判为杂毛。")
    lines.append("")
    return lines


def _veto_block(ev: dict) -> List[str]:
    vt = ev.get("veto") or {}
    lines = ["## 术 · VETO 一票否决", ""]
    if not vt.get("items"):
        lines += ["无否决项。", ""]
        return lines
    lines += ["| 类型 | 否决项 |", "| --- | --- |"]
    for i in vt.get("hard") or []:
        lines.append(f"| **硬否决** | {i['label']} |")
    for i in vt.get("soft") or []:
        lines.append(f"| 软否决 | {i['label']} |")
    lines.append("")
    if vt.get("watchable"):
        lines += ["仅软否决 → 可纳入观察轨等回踩。", ""]
    return lines


def _levels_block(ev: dict) -> List[str]:
    lv = ev.get("levels") or {}
    if not lv:
        return ["## 术 · 数学计算", "", "数据不足，无法计算。", ""]
    return [
        "## 术 · 数学计算（R:R）", "",
        f"- 来源：{lv.get('source')}　ATR% {utils.num(lv.get('atr_pct'))}"
        + (f"　止损 = ATR% × {lv.get('sl_atr_mult')}" if lv.get("sl_atr_mult") else ""),
        f"- 买入 **{utils.num(lv.get('entry'))}**　止损 **{utils.num(lv.get('stop'))}**"
        f"（-{utils.num(lv.get('sl_pct'))}%）　止盈 **{utils.num(lv.get('target'))}**"
        f"（+{utils.num(lv.get('tp_pct'))}%）",
        f"- 含滑点盈亏比 **R:R = {utils.num(lv.get('odds'))}**"
        f"（打平胜率 {('%.1f%%' % (lv['breakeven_wr'] * 100)) if lv.get('breakeven_wr') else '—'}）"
        f"　达标：{'是' if lv.get('odds_ok') else '否'}",
        "",
    ]


def _scoring_block(ev: dict) -> List[str]:
    sc = ev.get("scoring") or {}
    th = ev.get("threshold") or {}
    lines = [f"## 术 · 9 分共振评分：**{sc.get('total')} / {sc.get('max')}**"
             f"（有效门槛 **{ev.get('pass_threshold')}**）", "",
             "| # | 维度 | 得分 | 说明 |", "| --- | --- | --- | --- |"]
    for d in sc.get("dims", []):
        lines.append(f"| {d['no']} | {d['name']} | {d['score']}/{d['max']} | {d['detail']} |")
    lines.append("")
    if th.get("notes"):
        lines += ["门槛构成：" + " / ".join(th["notes"]), ""]
    return lines


def _math_block(ctx: dict) -> List[str]:
    exp = ctx.get("expectancy") or {}
    ft = ctx.get("followthrough") or {}
    lines = ["## 法 · 期望值与跟涨经验", ""]
    if exp:
        lines += [
            f"- 同分档胜率 p̂ = **{('%.1f%%' % (exp['p_hat'] * 100)) if exp.get('p_hat') is not None else '—'}**"
            f"（{exp.get('source')}，样本 {exp.get('samples')} 笔）",
            f"- 期望值 E[R] = p̂ × R:R − (1 − p̂) = **{utils.num(exp.get('er'))}**"
            f"　结论 {'正期望' if exp.get('positive') else '非正期望'}",
            f"- 打平胜率 {('%.1f%%' % (exp['breakeven_wr'] * 100)) if exp.get('breakeven_wr') else '—'}"
            f"　期望系数 ×{utils.num(exp.get('mult'), 2)}"
            + ("（样本不足，门槛 +1）" if exp.get("insufficient") else ""),
        ]
    if ft:
        lines.append(f"- 跟涨经验（阶段×档位×轨道）：{ft.get('stage') or '—'} / {ft.get('tier') or '—'}"
                     f" / {ft.get('track') or '—'}")
        lines.append(f"- {ft.get('note')}")
    lines.append("")
    return lines


def _sizing_block(ctx: dict) -> List[str]:
    s = ctx.get("sizing") or {}
    lines = ["## 法 · 仓位（3/7 灰度建仓）", ""]
    if not s:
        lines += ["未计算仓位。", ""]
        return lines
    m = ctx.get("mults") or {}
    cfg = load_config()
    lines += [
        f"- 总资金 {utils.money(s.get('capital') or ctx.get('capital'))}　"
        f"可用 {utils.money(ctx.get('available'))}",
        f"- 半仓乘数 = 情绪 {utils.num(m.get('sentiment'), 2)} × 期望 {utils.num(m.get('expectancy'), 2)}"
        f" × 跟涨 {utils.num(m.get('followthrough'), 2)} = **×{utils.num(s.get('half_pos_mult'), 2)}**"
        + ("（已按 [0.25, 1.0] 夹紧）" if m.get("clamped") else ""),
        f"- 半仓额度 = 总资金 × {float(cfg.s('max_position_pct', 0.5)):.0%} × 乘数 = "
        f"{utils.money(s.get('half_pos'))}",
        f"- 满仓 {s.get('full_shares')} 股（{utils.money(s.get('full_amount'))}，"
        f"占总资金 {utils.num(s.get('position_pct'))}%）",
        f"- 灰度仓 {float(cfg.s('gray_ratio', 0.3)):.0%} = **{s.get('gray_shares')} 股**"
        f"（{utils.money(s.get('gray_amount'))}）　← 本次买入",
        f"- 确认仓 {float(cfg.s('confirm_ratio', 0.7)):.0%} = {s.get('confirm_shares')} 股"
        f"（{utils.money(s.get('confirm_amount'))}），突破确认后加仓",
        f"- 单笔风险敞口 {utils.money(s.get('risk_amount'))}"
        f"（占总资金 {utils.num(s.get('risk_pct_of_capital'))}%）",
        f"- 股数是否达标（≥1 手）：{'是' if s.get('enough') else '否'}",
        "",
    ]
    return lines


# ------------------------------------------------------------------ Markdown

def render_md(ctx: dict, cfg: Optional[Config] = None) -> str:
    cfg = cfg or load_config()
    ev = ctx.get("ev") or {}
    decision = ctx.get("decision") or DECISION_REJECT
    lines: List[str] = [
        f"# TRADE_CHECK {ctx.get('code')} {ctx.get('name') or ''}".rstrip(),
        "",
        f"- 时间：{ctx.get('at')}",
        f"- **决策：{DECISION_LABEL.get(decision, decision)}**",
        f"- 共振：{ev.get('total_score')} / {ev.get('pass_threshold')}"
        f"　身份：{(ev.get('identity') or {}).get('tier')}"
        f"　R:R：{utils.num((ev.get('levels') or {}).get('odds'))}",
    ]
    if ctx.get("force"):
        lines.append("- ⚠ 本次评估使用了 FORCE 强制放行")
    if ctx.get("plan_item"):
        pi = ctx["plan_item"]
        lines.append(f"- 计划绑定：{pi.get('code')} 计划日 {pi.get('planned_date') or '—'}"
                     f" 执行日 {pi.get('execute_date') or '—'} 状态 {pi.get('status') or '—'}")
    if ctx.get("blocks"):
        lines += ["", "**拦截原因：**"] + [f"- {b}" for b in ctx["blocks"]]
    lines.append("")

    lines += _sentiment_block(ctx.get("sentiment"))

    gates = [("会话开始门禁（8.1）", ctx.get("session_gate")),
             ("代码门禁（8.2）", ctx.get("code_gate")),
             ("买入门禁（8.3）", ctx.get("buy_gate"))]
    if any(g for _, g in gates):
        lines += ["## 法 · 门禁", ""]
        for title, g in gates:
            lines += _gate_block(title, g)

    if ev:
        lines += _quote_block(ev)
        lines += _identity_block(ev)
        lines += _veto_block(ev)
        lines += _levels_block(ev)
        lines += _scoring_block(ev)
    lines += _math_block(ctx)
    lines += _sizing_block(ctx)

    if ev.get("reasons"):
        lines += ["## 结论明细", ""] + [f"- {r}" for r in ev["reasons"]] + [""]
    if ctx.get("phase_log"):
        lines += ["## 阶段流水", ""] + [f"{i + 1}. {t}" for i, t in enumerate(ctx["phase_log"])] + [""]
    if ctx.get("notes"):
        lines += ["## 备注", ""] + [f"- {n}" for n in ctx["notes"]] + [""]

    lines += ["---", "",
              f"> 生成于 {utils.now().strftime('%Y-%m-%d %H:%M:%S')} · "
              f"pass_threshold={cfg.s('pass_threshold', 6)} min_odds={cfg.s('min_odds', 3)} "
              f"max_position_pct={cfg.s('max_position_pct', 0.5)}"]
    return "\n".join(lines)


def _cleanup_reports(cfg: Optional[Config] = None) -> List[str]:
    """按 paths.keep_reports 保留最新 N 份报告，删掉更旧的。

    只动 reports 目录下的 .md 文件（SEED_* / WEEKLY_* / TRADE_CHECK_* 等），不递归子
    目录、不动非 .md；SEED_TRACE.md 是持续覆写的主日志，永不删。
    keep_reports ≤ 0 视为“不清理”。返回已删文件名列表。
    """
    cfg = cfg or load_config()
    keep = int(utils.to_float(cfg.get("paths.keep_reports", 200), 0) or 0)
    if keep <= 0:
        return []
    d = cfg.reports_dir()
    protected = {str(cfg.get("paths.seed_trace_md", "SEED_TRACE.md"))}
    try:
        names = os.listdir(d)
    except OSError:
        return []
    files = []
    for name in names:
        if not name.lower().endswith(".md") or name in protected:
            continue
        p = os.path.join(d, name)
        if not os.path.isfile(p):
            continue
        try:
            files.append((os.path.getmtime(p), name, p))
        except OSError:
            continue
    if len(files) <= keep:
        return []
    files.sort(key=lambda x: (x[0], x[1]), reverse=True)
    removed = []
    for _, name, p in files[keep:]:
        try:
            os.remove(p)
            removed.append(name)
        except OSError:
            continue
    return removed


def write_report(ctx: dict, cfg: Optional[Config] = None) -> Optional[str]:
    """落盘 TRADE_CHECK_<code>_<stamp>.md。"""
    cfg = cfg or load_config()
    if not cfg.get("report.write_trade_check", True):
        return None
    prefix = cfg.get("report.trade_check_prefix", "TRADE_CHECK")
    name = f"{prefix}_{ctx.get('code') or 'NA'}_{ctx.get('decision') or 'NA'}_{utils.stamp()}.md"
    path = utils.atomic_write(cfg.report_file(name), render_md(ctx, cfg))
    _cleanup_reports(cfg)
    return path


# ------------------------------------------------------------------ 控制台

def format_decision(ctx: dict) -> str:
    ev = ctx.get("ev") or {}
    decision = ctx.get("decision") or DECISION_REJECT
    lines = [
        "=" * 52,
        f"决策：{DECISION_LABEL.get(decision, decision)}",
        f"标的：{ctx.get('code')} {ctx.get('name') or ''}",
    ]
    if ev:
        lv = ev.get("levels") or {}
        lines.append(f"共振 {ev.get('total_score')}/{ev.get('pass_threshold')}"
                     f"  身份 {(ev.get('identity') or {}).get('tier')}"
                     f" {utils.num((ev.get('identity') or {}).get('score'), 1)}"
                     f"  阶段 {(ev.get('stage') or {}).get('stage')}"
                     f"  R:R {utils.num(lv.get('odds'))}")
        if lv:
            lines.append(f"买入 {utils.num(lv.get('entry'))}  止损 {utils.num(lv.get('stop'))}"
                         f"（-{utils.num(lv.get('sl_pct'))}%）"
                         f"  止盈 {utils.num(lv.get('target'))}（+{utils.num(lv.get('tp_pct'))}%）")
    s = ctx.get("sizing") or {}
    if s:
        lines.append(f"灰度仓 {s.get('gray_shares')} 股 {utils.money(s.get('gray_amount'))}"
                     f"（半仓乘数 ×{utils.num(s.get('half_pos_mult'), 2)}）"
                     f"  确认仓待加 {s.get('confirm_shares')} 股")
    for b in (ctx.get("blocks") or []):
        lines.append(f"  ✗ {b}")
    for r in (ev.get("reasons") or []):
        lines.append(f"  · {r}")
    for n in (ctx.get("notes") or []):
        lines.append(f"  · {n}")
    if ctx.get("report_path"):
        lines.append(f"报告已存档：{ctx['report_path']}")
    lines.append("=" * 52)
    return "\n".join(lines)


def format_full(ctx: dict) -> str:
    """控制台完整版：天气 + 门禁 + 评估 + 决策。"""
    from .sentiment import format_weather
    parts: List[str] = []
    if ctx.get("sentiment"):
        parts.append(format_weather(ctx["sentiment"]))
    for title, key in (("会话开始门禁", "session_gate"), ("代码门禁", "code_gate"),
                       ("买入门禁", "buy_gate")):
        g = ctx.get(key)
        if not g:
            continue
        lines = [f"===== 法 · {title} ====="]
        if g.get("allowed"):
            lines.append("  通过：全部门禁项放行")
        for b in g.get("blocks") or []:
            lines.append(f"  ✗ [{b['rule']}] {b['detail']}")
        for w in g.get("warnings") or []:
            lines.append(f"  ! [{w['rule']}] {w['detail']}")
        parts.append("\n".join(lines))
    ev = ctx.get("ev") or {}
    if ev:
        parts.append(preflight.format_evaluation(ev))
    if ctx.get("expectancy"):
        from .expectancy import format_expectancy
        parts.append(format_expectancy(ctx["expectancy"]))
    if ctx.get("sizing"):
        from .portfolio import format_sizing
        parts.append(format_sizing(ctx["sizing"]))
    parts.append(format_decision(ctx))
    return "\n".join(parts)


def summarize(ctx: dict) -> Dict[str, Any]:
    ev = ctx.get("ev") or {}
    return {
        "at": ctx.get("at"), "code": ctx.get("code"), "name": ctx.get("name"),
        "decision": ctx.get("decision"), "force": bool(ctx.get("force")),
        "score": ev.get("total_score"), "threshold": ev.get("pass_threshold"),
        "identity_tier": (ev.get("identity") or {}).get("tier"),
        "odds": (ev.get("levels") or {}).get("odds"),
        "gray_shares": (ctx.get("sizing") or {}).get("gray_shares"),
        "blocks": ctx.get("blocks") or [],
        "report_path": ctx.get("report_path"),
    }
