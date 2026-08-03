"""Phase2 数学计算：构建完整评估（含滑点 R:R）+ 初步仓位。

R:R 不达标不在这一步终止 —— 让 Phase3/Phase4 把完整账算出来再拒，
这样报告里能看到"差在哪"，而不是只有一句"不合格"。
"""
from __future__ import annotations

from tea.core import utils
from tea.portfolio import portfolio
from tea.screening import preflight
from .results import ABORT, OK
from .session import Session

def run(s: Session) -> str:
    io, cfg = s.io, s.cfg
    s.banner("Phase 2 · 数学计算（R:R + 仓位）")

    # 复用预审引擎：传入已有行情/板块与手动止损止盈，避免重复请求与公式分叉
    try:
        s.ev = preflight.evaluate(
            s.code, s.mk, cfg, sent=s.sent, quote=s.quote, sector=s.sector,
            has_news=s.has_news, sl_pct=s.sl_pct, tp_pct=s.tp_pct)
    except Exception as exc:
        io.say(f"  评估失败：{exc}")
        s.block(f"评估失败：{exc}")
        return ABORT

    lv = s.ev.get("levels") or {}
    io.say(preflight.format_levels(s.ev))
    min_odds = float(cfg.s("min_odds", 3))
    if not lv.get("odds_ok"):
        need = preflight.min_tp_for_odds(s.quote.get("price"), s.sl_pct, min_odds, cfg)
        io.say(f"  ✗ R:R {utils.num(lv.get('odds'))} < {min_odds:.0f}"
               f"　（当前止损 -{utils.num(s.sl_pct)}% 下需要止盈 ≥ +{utils.num(need)}%）")
        s.note(f"R:R 不达标：需止盈 ≥{utils.num(need)}% 或收紧止损")
    else:
        io.say(f"  ✓ R:R {utils.num(lv.get('odds'))} ≥ {min_odds:.0f}")

    # -------------------------------------------------- 资金
    if s.capital is None:
        cap_now = portfolio.get_capital(cfg)
        s.capital = io.ask_float("总资金（回车用已保存值）", key="capital", default=cap_now, lo=0.0)
        if s.capital and abs(s.capital - cap_now) > 1e-6:
            portfolio.set_capital(s.capital, cfg)
            s.note(f"资金已更新为 {utils.money(s.capital)}")
    s.available = portfolio.available_cash(cfg)

    # -------------------------------------------------- 初步仓位（仅情绪乘数）
    sent_mult = float((s.sent or {}).get("base_pos_mult") or 1.0)
    s.mults = {"sentiment": sent_mult, "expectancy": None, "followthrough": None}
    s.sizing = portfolio.compute_position(s.capital or 0.0, s.quote.get("price"),
                                          sent_mult, cfg, s.sl_pct)
    io.say(f"  资金 {utils.money(s.capital)}　可用 {utils.money(s.available)}")
    io.say(f"  初步仓位（仅情绪乘数 ×{utils.num(sent_mult, 2)}）：")
    io.say(portfolio.format_sizing(s.sizing))
    if not s.sizing.get("enough"):
        s.note("资金不足 1 手，Phase4 将无法开仓")
    s.log(f"Phase2 完成：R:R {utils.num(lv.get('odds'))}"
          f"（{'达标' if lv.get('odds_ok') else '不达标'}）"
          f"，初步灰度 {s.sizing.get('gray_shares')} 股")
    return OK
