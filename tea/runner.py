"""runner.py — 主流程编排：把 道（天气）→ 法（门禁）→ 术（评分）串成可执行的一天。

对外入口（CLI 只负责解析参数，不做任何判断）：
    weather()       市场天气速览
    run_once()      单标的准入评估 Phase1 → Phase4
    seed_plan()     14:30 种子扫描 → 写次日计划 → SEED 报告
    plan_check()    次日 09:35 计划复核（任一变动整单作废）
    close_review()  盘后复核：T+1 回填 / 观察池复核 / 当日累积
    daily_status()  今日状态（门禁计数 / 计划 / 持仓）

设计约束：这里只做编排与落盘，所有公式都在各自模块里，runner 不重算任何一个分数。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from . import accumulator, followthrough as ft_mod, gates, portfolio
from . import plan as plan_mod
from . import report as report_mod
from . import screener as screener_mod
from . import seed_report, seed_trace, stats, trades as trades_mod, utils, watch_pool, weekly
from .config_store import Config, load_config
from .data import Market
from .phases import IO, Session, phase1, phase2, phase3, phase4, results
from .sentiment import format_weather, get_sentiment
from .timing import Timing

STAGE_SESSION_GATE = "session_gate"
STAGE_PHASE1 = "phase1"
STAGE_PHASE2 = "phase2"
STAGE_PHASE3 = "phase3"
STAGE_PHASE4 = "phase4"
STAGE_DONE = "done"


# ==================================================================== 道

def weather(cfg: Optional[Config] = None, market: Optional[Market] = None,
            refresh: bool = False, io: Optional[IO] = None) -> dict:
    """取市场天气（120s 缓存，refresh=True 强制重算）。"""
    cfg = cfg or load_config()
    mk = market or Market(cfg)
    return get_sentiment(mk, cfg, force=refresh, io=io)


# ==================================================================== 单标的准入

def run_once(capital: Optional[float] = None, code: Optional[str] = None,
             cfg: Optional[Config] = None, market: Optional[Market] = None,
             io: Optional[IO] = None, force: bool = False, allow_buy: bool = True,
             require_window: bool = True, sent: Optional[dict] = None,
             ask_levels: bool = True) -> dict:
    """一次完整的准入评估，返回 {decision, stage, ctx, report_path, session}。"""
    cfg = cfg or load_config()
    io = io or IO()
    mk = market or Market(cfg)
    s = Session(cfg=cfg, market=mk, io=io, force=force, capital=capital)
    out: Dict[str, Any] = {"decision": None, "stage": STAGE_SESSION_GATE,
                           "ctx": None, "report_path": None, "session": s}

    # ---------------------------------------------------------- 1. 持仓 / 资金
    s.capital = capital if capital is not None else portfolio.get_capital(cfg)
    s.available = portfolio.available_cash(cfg)
    io.say("=" * 56)
    io.say(f"XJB_TRADE · 准入评估　{s.at}　{s.tm.describe()}")
    io.say("=" * 56)
    io.say(portfolio.format_positions(cfg))
    io.say(f"  总资金 {utils.money(s.capital)}　可用 {utils.money(s.available)}")

    # ---------------------------------------------------------- 2~3. 天气 + 会话门禁
    s.sent = sent if sent is not None else weather(cfg, mk, io=io)
    io.say(format_weather(s.sent))

    g = gates.check_session_start(s.sent, cfg, s.tm, force=force, require_window=require_window)
    s.session_gate = g.to_dict()
    io.say(g.format("会话门禁（8.1）"))
    accumulator.record_session(s.sent, s.session_gate, cfg)
    if not g.allowed:
        for b in g.blocks:
            s.block(f"[{b['rule']}] {b['detail']}")
        s.decision = report_mod.DECISION_REJECT
        s.log("会话门禁拦截，未进入 Phase1")
        io.say("\n→ 结论：REJECT（会话门禁未通过，今日不新开）")
        if g.requires_force:
            io.say("  连亏冷却中：先做复盘，再用 --force 放行")
        accumulator.note("会话门禁拦截：" + "；".join(s.blocks), cfg)
        out.update(decision=s.decision, ctx=s.to_ctx())
        return out

    # ---------------------------------------------------------- 4~5. Phase1
    out["stage"] = STAGE_PHASE1
    r1 = phase1.run(s, code=code, ask_levels=ask_levels)
    if r1 != results.OK:
        s.decision = (report_mod.DECISION_REJECT if r1 == results.REJECT else None)
        return _finish(s, out, archive=(s.decision is not None))

    # ---------------------------------------------------------- 6. Phase2
    out["stage"] = STAGE_PHASE2
    if phase2.run(s) != results.OK:
        return _finish(s, out, archive=False)

    # ---------------------------------------------------------- 7. Phase3
    out["stage"] = STAGE_PHASE3
    phase3.run(s)

    # ---------------------------------------------------------- 8. Phase4
    out["stage"] = STAGE_PHASE4
    phase4.run(s, allow_buy=allow_buy)
    out["stage"] = STAGE_DONE
    return _finish(s, out, archive=True)


def _finish(s: Session, out: Dict[str, Any], archive: bool) -> Dict[str, Any]:
    """收尾：打印结论 → 写 TRADE_CHECK 报告 → 记入当日累积。"""
    io, cfg = s.io, s.cfg
    if s.decision:
        io.say("")
        io.say(f"→ 结论：{report_mod.DECISION_LABEL.get(s.decision, s.decision)}")
        for b in s.blocks:
            io.say(f"    ✗ {b}")
        for n in s.notes:
            io.say(f"    · {n}")
    if archive and s.ev is not None:
        s.report_path = report_mod.write_report(s.to_ctx(), cfg)
        if s.report_path:
            io.say(f"  报告已存档：{s.report_path}")
        accumulator.record_eval(s.ev, s.decision or results.ABORT,
                                "；".join(s.blocks) or "；".join(s.notes), cfg)
    elif archive:
        accumulator.note(f"{s.code} {s.decision}：" + ("；".join(s.blocks) or "无 ev"), cfg)
    out.update(decision=s.decision, ctx=s.to_ctx(), report_path=s.report_path)
    return out


# ==================================================================== 种子扫描 + 计划

def seed_plan(cfg: Optional[Config] = None, market: Optional[Market] = None,
              io: Optional[IO] = None, sent: Optional[dict] = None,
              include_eve: bool = True, write_plan: bool = True,
              require_window: bool = False) -> dict:
    """14:30 种子扫描四步流 → 有可买则写次日计划 → SEED 报告 + 观察池闭环。"""
    cfg = cfg or load_config()
    io = io or IO()
    mk = market or Market(cfg)
    sc = screener_mod.Screener(cfg, mk)
    tm = Timing(cfg)
    t_start = time.time()

    io.say("=" * 56)
    io.say(f"XJB_TRADE · 种子扫描　{utils.now().strftime('%Y-%m-%d %H:%M')}　{tm.phase()}")
    io.say("=" * 56)
    if require_window and not tm.is_seed_window():
        io.say(f"  ! 当前不在种子扫描窗口（{cfg.get('timing.seed_scan')} 前后），结果仅供参考")
    io.say("  ⏳ 开始种子扫描（网络取数较多，预计 1~2 分钟）...")

    sent = sent if sent is not None else weather(cfg, mk, io=io)
    io.say(format_weather(sent))

    result = sc.seed_scan(sent=sent, include_eve=include_eve, io=io)
    # 网络摘要只是一行装饰，不能因为注入的是个简易 fetcher 就把扫描结果带死。
    net = mk.f.stats_line() if hasattr(mk.f, "stats_line") else ""
    io.say(f"  ✓ 种子扫描完成 ({time.time() - t_start:.1f}s)" + (f"，{net}" if net else ""))
    io.say(seed_report.format_result(result, cfg))

    # ---------------------------------------------------------- 写计划
    plan = None
    buyable = result.get("buyable") or []
    if buyable and write_plan:
        notes = [f"种子扫描 {result.get('scan_id')}　档位 {result.get('tier')}",
                 f"天气：{sent.get('cycle')} / {sent.get('stance')} "
                 f"情绪 {sent.get('score')} 乘数 ×{utils.num(sent.get('base_pos_mult'), 2)}"]
        notes += list(result.get("notes") or [])[:3]
        plan = plan_mod.write_plan(buyable, cfg, notes=notes)
        accumulator.record_plan("write", plan, f"种子扫描产出 {len(buyable)} 只", cfg)
        io.say("")
        io.say(plan_mod.format_plan(plan))
        io.say("  → 次日 09:35 先跑 plan-check，复核无变动才可在 14:00-14:45 执行")
    elif buyable:
        io.say("  （write_plan=False，本次不落计划）")
    else:
        io.say("  宁缺毋滥：今日无可买标的，不写计划")
        cur = plan_mod.load_plan(cfg)
        if plan_mod.active_items(cur):
            io.say(f"  ! 注意：仍存在未执行的旧计划（{plan_mod.planned_codes(cur)}），"
                   f"如已过期请执行 plan-clear")

    # ---------------------------------------------------------- 观察池闭环
    added: List[str] = []
    for ev in (result.get("watch") or []):
        track = ev.get("track") or watch_pool.TRACK_WATCH
        watch_pool.add(ev, track=track, source=f"seed:{result.get('scan_id')}",
                       triggers=ev.get("triggers"), cfg=cfg)
        added.append(f"{ev.get('code')}→{track}")
    for ev in (result.get("eve") or []):
        watch_pool.add(ev, track=watch_pool.TRACK_EVE, source=f"seed:{result.get('scan_id')}",
                       triggers=ev.get("triggers"), cfg=cfg)
        added.append(f"{ev.get('code')}→{watch_pool.TRACK_EVE}")
    if added:
        io.say(f"  观察池新增/续期 {len(added)} 项：" + "、".join(added))

    # ---------------------------------------------------------- 跟涨样本 + 报告
    entries = _ft_entries(result)
    n = ft_mod.record_seed(entries, cfg) if entries else 0
    if n:
        io.say(f"  已落 {n} 条跟涨样本，次日 close-review 自动回填 T+1 结果")

    path = seed_report.write_report(result, cfg)
    result["report_path"] = path
    if path:
        io.say(f"  报告已存档：{path}")
    accumulator.record_seed(seed_report.summarize(result), cfg)
    result["plan"] = plan
    return result


def _ft_entries(result: dict) -> List[dict]:
    """把三档输出摊平成跟涨样本（可买/观察/前夕都要跟踪，才知道哪一档真的有钱）。"""
    entries: List[dict] = []
    groups = [("可买", result.get("buyable")), (None, result.get("watch")),
              (None, result.get("eve"))]
    for default_track, evs in groups:
        for ev in (evs or []):
            q = ev.get("quote") or {}
            entries.append({
                "code": ev.get("code"), "name": ev.get("name"),
                "stage": (ev.get("stage") or {}).get("stage"),
                "tier": ev.get("tier_label"),
                "track": ev.get("track") or default_track,
                "chg_pct": q.get("chg_pct"), "price": q.get("price"),
                "total_score": ev.get("total_score"),
                "identity_tier": (ev.get("identity") or {}).get("tier"),
                "identity_score": (ev.get("identity") or {}).get("score"),
                "sector_name": (ev.get("sector") or {}).get("name"),
                "sector_rank": (ev.get("sector") or {}).get("rank"),
            })
    return entries


# ==================================================================== 计划复核

def plan_check(cfg: Optional[Config] = None, market: Optional[Market] = None,
               io: Optional[IO] = None, sent: Optional[dict] = None,
               apply: bool = True) -> dict:
    """次日 09:35 复核：参考价/共振分/身份/VETO 任一变动 → 整单作废。"""
    cfg = cfg or load_config()
    io = io or IO()
    mk = market or Market(cfg)
    sent = sent if sent is not None else weather(cfg, mk, io=io)
    io.say(format_weather(sent))

    io.say("  ⏳ 逐只复核计划内标的（参考价 / 共振分 / 身份 / VETO）...")
    with utils.timed("计划复核", io, threshold=0.5):
        res = plan_mod.check_plan(mk, cfg, sent=sent, apply=apply)
    io.say(plan_mod.format_check(res))
    plan = res.get("plan") or {}
    accumulator.record_plan("check", plan,
                            "作废" if res.get("changed") else "有效", cfg)
    if not res.get("changed") and plan_mod.active_items(plan):
        io.say(f"  → 可在 {Timing(cfg).buy_window_text()} 用 run 逐只执行准入")
    return res


# ==================================================================== 盘后复核

def close_review(cfg: Optional[Config] = None, market: Optional[Market] = None,
                 io: Optional[IO] = None, sent: Optional[dict] = None,
                 prune: bool = True) -> dict:
    """盘后：回填跟涨 T+1 结果 → 观察池复核 → 当日累积摘要。"""
    cfg = cfg or load_config()
    io = io or IO()
    mk = market or Market(cfg)
    out: Dict[str, Any] = {}
    io.say("=" * 56)
    io.say(f"XJB_TRADE · 盘后复核　{utils.now().strftime('%Y-%m-%d %H:%M')}")
    io.say("=" * 56)

    # 1. 跟涨经验回填
    io.say("  ⏳ 回填跟涨样本 T+1 结果...")
    with utils.timed("跟涨样本回填", io, threshold=0.5):
        upd = ft_mod.update_results(mk, cfg)
    out["followthrough"] = upd
    io.say(f"===== 跟涨样本回填 =====\n  回填 {upd.get('updated')} 条，"
           f"待回填 {upd.get('pending')} 条，样本累计 {upd.get('total') or 0} 条")
    io.say(ft_mod.format_followthrough(cfg))

    # 2. 观察池复核
    sent = sent if sent is not None else weather(cfg, mk, io=io)
    io.say("  ⏳ 逐只复核观察池...")
    with utils.timed("观察池复核", io, threshold=0.5):
        rev = watch_pool.review(mk, cfg, sent=sent, apply=True)
    out["watch_review"] = rev
    io.say(watch_pool.format_review(rev))
    if prune and cfg.get("watch.auto_prune_on_review", True):
        pruned = watch_pool.prune_expired(cfg)
        out["pruned"] = pruned
        if pruned:
            io.say(f"  已清理超时 {len(pruned)} 项："
                   + "、".join(f"{p.get('code')}" for p in pruned))
    io.say(watch_pool.format_pool(cfg))

    # 3. 当日累积
    dg = accumulator.day_digest(None, cfg)
    out["digest"] = dg
    io.say(accumulator.format_day(dg))
    io.say(seed_trace.format_trace_summary(cfg))
    return out


# ==================================================================== 状态 / 统计

def daily_status(cfg: Optional[Config] = None, io: Optional[IO] = None,
                 sent: Optional[dict] = None, with_weather: bool = False,
                 market: Optional[Market] = None) -> dict:
    """今日状态：门禁计数 + 计划 + 持仓 + 观察池。"""
    cfg = cfg or load_config()
    io = io or IO()
    if with_weather and sent is None:
        sent = weather(cfg, market, io=io)
    if sent:
        io.say(format_weather(sent))
    st = gates.status(sent, cfg)
    io.say(gates.format_status(st))
    io.say(portfolio.format_positions(cfg))
    io.say(plan_mod.format_plan(plan_mod.load_plan(cfg)))
    io.say(watch_pool.format_pool(cfg))
    io.say(trades_mod.format_trades(cfg, limit=5))
    return st


def stats_report(cfg: Optional[Config] = None, io: Optional[IO] = None,
                 write: bool = False) -> dict:
    """交易统计 + 归因（样本 <10 笔时结论仅供参考）。"""
    cfg = cfg or load_config()
    io = io or IO()
    st = stats.overall(cfg)
    io.say(stats.format_stats(st, cfg))
    if write:
        path = cfg.report_file(f"STATS_{utils.stamp()}.md")
        utils.atomic_write(path, stats.render_md(st, cfg))
        st["report_path"] = path
        io.say(f"  报告已存档：{path}")
    return st


def weekly_report(days: int = 7, cfg: Optional[Config] = None,
                  io: Optional[IO] = None, write: bool = True) -> dict:
    """周复盘：纪律自查 + 每日流水 + 归因。"""
    cfg = cfg or load_config()
    io = io or IO()
    wk = weekly.collect(days, cfg)
    io.say(weekly.format_weekly(wk, cfg))
    if write:
        path = weekly.write_report(days, cfg)
        wk["report_path"] = path
        io.say(f"  报告已存档：{path}")
    return wk


# ==================================================================== 持仓动作

def confirm_position(code: str, price: Optional[float] = None, cfg: Optional[Config] = None,
                     market: Optional[Market] = None, io: Optional[IO] = None) -> Optional[dict]:
    """突破确认后补足 70% 确认仓。"""
    cfg = cfg or load_config()
    io = io or IO()
    if price is None:
        price = ((market or Market(cfg)).get_quote(code) or {}).get("price")
    pos = portfolio.add_confirm(code, float(price), cfg)
    if not pos:
        io.say(f"  未找到 {utils.norm_code(code)} 的灰度仓（或已是满仓）")
        return None
    io.say(f"  ✓ 确认仓已补足：{pos.get('shares')} 股，均价 {utils.num(pos.get('entry'))}")
    accumulator.note(f"{pos.get('code')} 确认仓补足至 {pos.get('shares')} 股 @ "
                     f"{utils.num(price)}", cfg)
    return pos


def close_trade(code: str, price: Optional[float] = None, reason: str = "手动平仓",
                cfg: Optional[Config] = None, market: Optional[Market] = None,
                io: Optional[IO] = None, shares: Optional[int] = None) -> Optional[dict]:
    """平仓 → 写流水 → 回收资金 → 记入当日累积。"""
    cfg = cfg or load_config()
    io = io or IO()
    if price is None:
        price = ((market or Market(cfg)).get_quote(code) or {}).get("price")
    rec = trades_mod.close_position(code, float(price), reason, cfg, shares)
    if not rec:
        io.say(f"  未找到持仓 {utils.norm_code(code)}")
        return None
    io.say(f"  ✓ 已平仓 {rec.get('code')} {rec.get('name')}：{rec.get('result')}　"
           f"盈亏 {utils.money(rec.get('pnl'))}（{utils.pct(rec.get('pnl_pct'))}，"
           f"R {utils.num(rec.get('r_multiple'))}）")
    accumulator.record_trade(rec, "close", cfg)
    cl = trades_mod.consec_losses(cfg)
    limit = int(cfg.s("consec_loss_limit", 2))
    if cl >= limit:
        io.say(f"  ! 连亏 {cl} 笔 ≥ {limit} → 进入冷却，下次新开需先复盘并 --force")
    return rec
