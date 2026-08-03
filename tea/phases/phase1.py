"""Phase1 标的锁定：代码门禁 → 拉行情 → 板块/身份判定 → VETO → 输入止损止盈。

这一阶段的职责是"确认这只票值得算"，任何硬否决都在此直接终止流程。
"""
from __future__ import annotations

from typing import Optional

from .. import identity as ident_mod
from .. import gates, plan as plan_mod, preflight, utils
from .. import veto as veto_mod
from ..data import intraday_position
from .results import ABORT, OK, REJECT
from .session import Session

def run(s: Session, code: Optional[str] = None, ask_levels: bool = True) -> str:
    """返回 results 里的 OK / REJECT / ABORT 之一。"""
    io, cfg = s.io, s.cfg
    s.banner("Phase 1 · 标的锁定")

    # -------------------------------------------------- 代码
    raw = code if code is not None else io.ask("请输入股票代码（6 位）", key="code")
    if not raw:
        io.say("  未输入代码，流程终止")
        return ABORT
    s.code = utils.norm_code(raw)
    if len(s.code) != 6 or not s.code.isdigit():
        io.say(f"  代码格式不合法：{raw}")
        return ABORT

    # -------------------------------------------------- 8.2 代码门禁
    g = gates.check_code_gate(s.code, s.sent, cfg, s.tm)
    s.code_gate = g.to_dict()
    io.say(g.format("代码门禁（8.2）"))
    plan = (g.context or {}).get("plan") or {}
    s.plan_item = plan_mod.find_item(plan, s.code)
    if s.plan_item:
        s.plan_item = {**s.plan_item, "planned_date": plan.get("planned_date"),
                       "execute_date": plan.get("execute_date")}
    if not g.allowed:
        for b in g.blocks:
            s.block(f"[{b['rule']}] {b['detail']}")
        s.log(f"Phase1 代码门禁拦截：{len(g.blocks)} 项")
        return REJECT

    gates.bump_evaluation(s.code, cfg)
    s.log(f"Phase1 代码门禁通过，已计入单日评估（{s.code}）")

    # -------------------------------------------------- 行情
    io.say(f"  ⏳ 获取 {s.code} 行情...")
    try:
        with utils.timed("行情获取", io, threshold=0.5):
            s.quote = s.mk.get_quote(s.code)
    except Exception as exc:
        io.say(f"  行情拉取失败：{exc}")
        s.block(f"行情拉取失败：{exc}")
        return ABORT
    s.name = s.quote.get("name")
    io.say("  ⏳ 计算技术指标（日 K / MA / ATR）...")
    with utils.timed("技术指标", io, threshold=0.5):
        s.ind = s.mk.get_indicators(s.code, s.quote.get("price"))
    io.say("  ⏳ 解析板块上下文...")
    with utils.timed("板块上下文", io, threshold=0.5):
        s.sector = s.mk.sector_context(s.quote)
    s.identity = ident_mod.judge(s.quote, s.sector, s.ind, cfg)
    s.intraday = intraday_position(s.quote.get("price"), s.quote.get("high"), s.quote.get("low"))
    s.stage = preflight.classify_stage(s.quote, s.ind, s.identity, s.intraday, cfg)
    s.veto = veto_mod.check(s.quote, s.ind, s.identity, s.intraday, cfg)

    q, sec, ind = s.quote, s.sector, s.ind
    io.say(f"  {s.code} {s.name}　现价 {utils.num(q.get('price'))}　"
           f"涨幅 {utils.pct(q.get('chg_pct'))}　换手 {utils.pct(q.get('turnover'))}　"
           f"量比 {utils.num(q.get('vol_ratio'))}　市值 {utils.num(q.get('cap_yi'))} 亿")
    io.say(f"  板块 {sec.get('name') or '未识别'}（第 {sec.get('rank')} 名 "
           f"{utils.pct(sec.get('chg'))}　涨停 {sec.get('limit_up_count')} 家）"
           f"　板块内 {sec.get('stock_rank')}/{sec.get('member_total')}")
    io.say(f"  MA5/10/20 {utils.num(ind.get('ma5'))}/{utils.num(ind.get('ma10'))}/"
           f"{utils.num(ind.get('ma20'))}　多头 {'是' if ind.get('ma_bull') else '否'}"
           f"　乖离 {utils.pct(ind.get('bias_ma20'))}　ATR% {utils.num(ind.get('atr_pct'))}")
    io.say(f"  分时位置 {('%.0f%%' % (s.intraday * 100)) if s.intraday is not None else '—'}"
           f"　阶段 {s.stage.get('stage')}　{s.stage.get('detail') or ''}")
    io.say(ident_mod.format_identity(s.identity))
    io.say(veto_mod.format_veto(s.veto))

    # -------------------------------------------------- VETO
    if s.veto.get("rejected"):
        for i in s.veto.get("hard", []):
            s.block(f"硬否决：{i['label']}")
        s.log("Phase1 硬否决 → 终止")
        return REJECT
    if s.veto.get("soft"):
        s.note("存在软否决 → 建议纳入观察轨等回踩，本次不应买入")
        s.log("Phase1 软否决（进入后续阶段仅供参考）")

    # -------------------------------------------------- 止损止盈
    price = q.get("price")
    if not price:
        s.block("现价缺失，无法计算止损止盈")
        return ABORT
    sug = preflight.compute_levels(price, ind.get("atr_pct"), cfg)
    io.say(f"  ATR 建议：止损 -{utils.num(sug.get('sl_pct'))}%（{utils.num(sug.get('stop'))}）"
           f"　止盈 +{utils.num(sug.get('tp_pct'))}%（{utils.num(sug.get('target'))}）"
           f"　R:R {utils.num(sug.get('odds'))}")
    for n in sug.get("notes") or []:
        io.say(f"    · {n}")

    if ask_levels:
        s.sl_pct = io.ask_float("止损百分比（不含符号，回车用 ATR 建议）", key="sl_pct",
                                default=sug.get("sl_pct"),
                                lo=float(cfg.s("atr_sl_min_pct", 2.0)),
                                hi=float(cfg.s("atr_sl_hard_max_pct", 6.0)))
        s.tp_pct = io.ask_float("止盈百分比（回车用 ATR 建议）", key="tp_pct",
                                default=sug.get("tp_pct"), lo=0.5,
                                hi=float(cfg.s("atr_tp_cap_pct", 15.0)))
    else:
        s.sl_pct, s.tp_pct = sug.get("sl_pct"), sug.get("tp_pct")

    s.has_news = io.ask_yes("是否有明确消息/题材催化（影响⑶消息面 1 分）", key="has_news",
                            default=False)
    s.log(f"Phase1 完成：止损 -{utils.num(s.sl_pct)}% 止盈 +{utils.num(s.tp_pct)}% "
          f"消息面 {'有' if s.has_news else '无'}")
    return OK
