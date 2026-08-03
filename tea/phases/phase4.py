"""Phase4 准入决策：期望值 → 仓位调整 → 买入门禁 → BUY / REJECT / CANCEL。

- BUY：门禁全放行 → 记录灰度仓（3/7 的 3）+ plan.mark_executed + 计数 +1
- CANCEL：共振分够高（≥cancel_high_score_min）但纪律不允许 → 主动撤单存档
- REJECT：其余否决

CANCEL 与 REJECT 的区别只在复盘价值：CANCEL 是"票没问题，是规则不让"，
这类记录多了就该检讨规则，而不是检讨选股。
"""
from __future__ import annotations

from typing import Optional

from tea.analysis import expectancy as exp_mod, followthrough as ft_mod
from tea.core import utils
from tea.portfolio import plan as plan_mod, portfolio, watch_pool
from tea.reporting.report import DECISION_BUY, DECISION_CANCEL, DECISION_REJECT
from tea.screening import gates
from .session import Session


def _track_for(s: Session) -> Optional[str]:
    """决定观察轨道：软否决 → 观察轨；差 1 分 → 启动待定轨。"""
    ev = s.ev or {}
    if (ev.get("veto") or {}).get("soft"):
        return watch_pool.TRACK_WATCH
    if ev.get("gap") is not None and 0 < ev["gap"] <= int(s.cfg.get("seed.near_miss_gap", 1)):
        return watch_pool.TRACK_PENDING
    return None


def run(s: Session, allow_buy: bool = True) -> str:
    io, cfg = s.io, s.cfg
    s.banner("Phase 4 · 准入决策")
    ev = s.ev or {}
    lv = ev.get("levels") or {}

    # -------------------------------------------------- 期望值
    s.expectancy = exp_mod.evaluate(ev.get("total_score"), lv.get("odds"), cfg)
    io.say(exp_mod.format_expectancy(s.expectancy))

    tier_label = (s.plan_item or {}).get("tier")
    track = (s.plan_item or {}).get("track") or "可买"
    s.followthrough = ft_mod.evaluate((ev.get("stage") or {}).get("stage"), tier_label, track, cfg)
    io.say(f"===== 跟涨经验 =====\n  {s.followthrough.get('note')}")

    # -------------------------------------------------- 仓位调整
    sent_mult = float((s.sent or {}).get("base_pos_mult") or 1.0)
    exp_mult = float(s.expectancy.get("mult") or 1.0)
    ft_mult = float(s.followthrough.get("mult") or 1.0)
    raw_mult = sent_mult * exp_mult * ft_mult
    lo = float(cfg.get("position.half_pos_mult_min", 0.25))
    hi = float(cfg.get("position.half_pos_mult_max", 1.0))
    s.mults = {"sentiment": sent_mult, "expectancy": exp_mult, "followthrough": ft_mult,
               "raw": round(raw_mult, 4), "clamped": not (lo <= raw_mult <= hi)}
    s.sizing = portfolio.compute_position(s.capital or 0.0, (s.quote or {}).get("price"),
                                          raw_mult, cfg, s.sl_pct)
    io.say(f"  半仓乘数 = 情绪 {utils.num(sent_mult, 2)} × 期望 {utils.num(exp_mult, 2)}"
           f" × 跟涨 {utils.num(ft_mult, 2)} = {utils.num(raw_mult, 3)}"
           f" → 夹紧后 ×{utils.num(s.sizing.get('half_pos_mult'), 2)}")
    io.say(portfolio.format_sizing(s.sizing))

    # -------------------------------------------------- 8.3 买入门禁
    g = gates.check_buy_gate(s.code, ev, s.sent, cfg, s.tm, s.force)
    s.buy_gate = g.to_dict()
    io.say(g.format("买入门禁（8.3）"))
    for b in g.blocks:
        s.block(f"[{b['rule']}] {b['detail']}")
    if not s.sizing.get("enough"):
        s.block(f"仓位不足 1 手（灰度 {s.sizing.get('gray_shares')} 股）")
    if not s.expectancy.get("positive"):
        s.block(f"非正期望 E[R] {utils.num(s.expectancy.get('er'))} ≤ 0")

    # -------------------------------------------------- 裁决
    high = int(cfg.s("cancel_high_score_min", 7))
    total = ev.get("total_score") or 0
    if not s.blocks and allow_buy:
        s.decision = DECISION_BUY
    elif total >= high and not (ev.get("veto") or {}).get("rejected"):
        s.decision = DECISION_CANCEL
        s.note(f"共振分 {total} ≥ {high} 但纪律不允许 → CANCEL（规则值得复盘）")
    else:
        s.decision = DECISION_REJECT

    # -------------------------------------------------- 落地
    if s.decision == DECISION_BUY:
        confirm = io.ask_yes(f"确认买入 {s.code} {s.name} 灰度 {s.sizing.get('gray_shares')} 股"
                             f"（{utils.money(s.sizing.get('gray_amount'))}）",
                             key="confirm_buy",
                             default=not cfg.get("ui.confirm_buy", True))
        if not confirm:
            s.decision = DECISION_CANCEL
            s.note("门禁已放行，但操作者主动放弃买入")
            s.log("Phase4 用户放弃 → CANCEL")
        else:
            pos = portfolio.open_position(s.code, s.name, (s.quote or {}).get("price"),
                                         s.sizing, ev, cfg=cfg)
            gates.bump_new_open(cfg)
            plan_mod.mark_executed(s.code, cfg, {
                "shares": s.sizing.get("gray_shares"),
                "price": (s.quote or {}).get("price"),
                "amount": s.sizing.get("gray_amount"),
            })
            io.say(f"  ✓ 灰度仓已记录：{pos.get('shares')} 股 @ {utils.num(pos.get('entry'))}"
                   f"　止损 {utils.num(pos.get('stop'))}　止盈 {utils.num(pos.get('target'))}")
            io.say(f"  确认仓 {s.sizing.get('confirm_shares')} 股待突破确认后加仓"
                   f"（tea add-confirm {s.code}）")
            s.log(f"Phase4 BUY：灰度 {s.sizing.get('gray_shares')} 股，"
                  f"计划标记 executed，单日新开 +1")
    else:
        track = _track_for(s)
        if track:
            triggers = ft_mod.trigger_conditions(ev, cfg)
            watch_pool.add(ev, track=track, source="run_once", triggers=triggers, cfg=cfg)
            io.say(f"  → 已纳入{track}，触发条件：" + "；".join(triggers))
            s.note(f"纳入{track}：" + "；".join(triggers))
        s.log(f"Phase4 {s.decision}：" + ("；".join(s.blocks) or "无可买理由"))

    return s.decision
