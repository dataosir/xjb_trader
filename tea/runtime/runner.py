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

from tea.analysis import followthrough as ft_mod, pricetrack, stats
from tea.analysis.sentiment import format_weather, get_sentiment
from tea.config.config_store import Config, load_config
from tea.core import logger as logger_mod, utils
from tea.core.timing import Timing
from tea.data import Market
from tea.phases import IO, Session, phase1, phase2, phase3, phase4, results
from tea.portfolio import accumulator, plan as plan_mod, portfolio, trades as trades_mod, watch_pool
from tea.reporting import report as report_mod, seed_trace, weekly
from tea.screening import gates, screener as screener_mod, seed_report

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
    # 运行日志留痕：每次扫描的漏斗结果，供日后按日志复盘「为什么没票」。
    logger_mod.get_logger("scan").info(
        "扫描完成 %s | 裁决 %s | 档位 %s | 初筛 %s | VETO过 %s | 可买 %d | 观察 %d | 近失 %d",
        result.get("scan_id"), result.get("verdict"), result.get("tier"),
        result.get("candidates_n"), result.get("veto_passed_n"),
        len(result.get("buyable") or []), len(result.get("watch") or []),
        len(result.get("near_miss") or []))
    # 网络摘要只是一行装饰，不能因为注入的是个简易 fetcher 就把扫描结果带死。
    # 优先报源命中（东财 45｜腾讯 18）：降级链有没接上比累计耗时更值得看。
    net = mk.stats_line() if hasattr(mk, "stats_line") else (
        mk.f.stats_line() if hasattr(getattr(mk, "f", None), "stats_line") else "")
    io.say(f"  ✓ 种子扫描完成 ({time.time() - t_start:.1f}s)" + (f"，{net}" if net else ""))
    io.say(seed_report.format_result(result, cfg))

    # ---------------------------------------------------------- 写计划
    plan = None
    buyable = result.get("buyable") or []
    if buyable and write_plan:
        codes = [ev.get("code") for ev in buyable if ev.get("code")]
        execute_date = utils.today_str(utils.next_trading_day())
        cur = plan_mod.load_plan(cfg)
        if codes and plan_mod.active_codes_equal(cur, codes, execute_date=execute_date):
            # 同日多次扫描产出同一批 code：不重写、不重记 plan.write，幂等跳过。
            # 否则 accumulator 里同一计划刷三遍，事后按 code 维度复盘会被重复计数。
            plan = cur
            io.say("")
            io.say("  计划与现有未执行计划一致（按 code 幂等），跳过重写")
            io.say(plan_mod.format_plan(plan))
        else:
            notes = [f"种子扫描 {result.get('scan_id')}　档位 {result.get('tier')}",
                     f"天气：{sent.get('cycle')} / {sent.get('stance')} "
                     f"情绪 {sent.get('score')} 乘数 ×{utils.num(sent.get('base_pos_mult'), 2)}"]
            notes += list(result.get("notes") or [])[:3]
            plan = plan_mod.write_plan(buyable, cfg, execute_date=execute_date, notes=notes)
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
            io.say(f"  ! 注意：仍存在未执行的旧计划（{'、'.join(plan_mod.planned_labels(cur))}），"
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
    ft_res = ft_mod.record_seed(entries, cfg) if entries else {"added": 0, "skipped": 0, "updated": 0}
    logger_mod.get_logger("scan").info("跟涨样本落盘 %s | 新增 %d | 升级 %d | 去重跳过 %d",
                                       result.get("scan_id"),
                                       ft_res.get("added"), ft_res.get("updated"),
                                       ft_res.get("skipped"))
    if ft_res.get("added") or ft_res.get("updated"):
        parts = []
        if ft_res.get("added"):
            parts.append(f"已落 {ft_res['added']} 条")
        if ft_res.get("updated"):
            parts.append(f"升级 {ft_res['updated']} 条")
        io.say(f"  跟涨样本 {'、'.join(parts)}（去重跳过 {ft_res.get('skipped', 0)} 条），"
               f"次日 close-review 自动回填 T+1 结果")
    elif ft_res.get("skipped"):
        io.say(f"  跟涨样本全部与历史重复（{ft_res['skipped']} 条），未新增落盘")
    pending_ft = ft_mod.pending_backfill(cfg)
    if pending_ft:
        io.say(f"  ⏳ 仍有 {pending_ft} 条历史跟涨样本未回填 T+1，"
               f"跑 `tea review` 补齐后跟涨胜率才有数据")
    # 归档提醒进 SEED 报告：控制台消息转瞬即逝，写进 notes 才能随 MD 存档复盘。
    # 「宁缺毋滥」与「回填提醒」是积累数据闭环的关键，不该只在控制台一闪而过。
    if not buyable:
        result.setdefault("notes", []).append("宁缺毋滥：今日无可买标的，不写计划")
    if pending_ft:
        result.setdefault("notes", []).append(
            f"⏳ 仍有 {pending_ft} 条历史跟涨样本未回填 T+1，"
            f"跑 `tea review` 补齐后跟涨胜率才有数据")

    # ---------------------------------------------------------- 每日价格跟踪
    codes = [e.get("code") for e in entries if e.get("code")]
    names = {e.get("code"): e.get("name") for e in entries if e.get("code")}
    added_track = pricetrack.ensure_tracked(codes, names, cfg)
    pr_res = pricetrack.record_daily(mk, cfg)
    if added_track or pr_res.get("recorded"):
        io.say(f"  价格跟踪：新纳入 {added_track} 只，记当日价 {pr_res['recorded']} 只"
               f"（跟踪中 {pr_res['tracking']} 只）")

    path = seed_report.write_report(result, cfg)
    result["report_path"] = path
    if path:
        io.say(f"  报告已存档：{path}")
    accumulator.record_seed(seed_report.summarize(result), cfg)
    result["plan"] = plan
    return result


def _ft_entries(result: dict) -> List[dict]:
    """把三档输出摊平成跟涨样本（可买/观察/前夕都要跟踪，才知道哪一档真的有钱）。

    除了个股快照，还带选中时的市场天气（情绪/周期/姿态/大盘趋势）与 9 分共振
    六维拆解——事后复盘「选了 3 天全跌」时，才能按市场环境与维度归因，而不只是
    看着一个总分干瞪眼。
    """
    entries: List[dict] = []
    sent = result.get("sentiment") or {}
    idx = sent.get("index") or {}
    market = {
        "market_score": sent.get("score"),
        "market_cycle": sent.get("cycle"),
        "market_stance": sent.get("stance"),
        "market_ma20_above": idx.get("ma20_above"),
        "market_idx_chg": idx.get("chg_pct"),
    }
    groups = [("可买", result.get("buyable")), (None, result.get("watch")),
              (None, result.get("eve"))]
    for default_track, evs in groups:
        for ev in (evs or []):
            q = ev.get("quote") or {}
            ind = ev.get("ind") or {}
            entries.append({
                "code": ev.get("code"), "name": ev.get("name"),
                "stage": (ev.get("stage") or {}).get("stage"),
                "tier": ev.get("tier_label"),
                "track": ev.get("track") or default_track,
                "chg_pct": q.get("chg_pct"), "price": q.get("price"),
                "total_score": ev.get("total_score"),
                "pass_threshold": ev.get("pass_threshold"),
                "identity_tier": (ev.get("identity") or {}).get("tier"),
                "identity_score": (ev.get("identity") or {}).get("score"),
                "sector_name": (ev.get("sector") or {}).get("name"),
                "sector_rank": (ev.get("sector") or {}).get("rank"),
                "scoring_dims": (ev.get("scoring") or {}).get("dims"),
                # 技术指标（T+N 回填后做逐因子胜率归因）：乖离/波动/量比/换手/分时位
                "bias_ma20": ind.get("bias_ma20"),
                "atr_pct": ind.get("atr_pct"),
                "vol_ratio": q.get("vol_ratio"),
                "turnover": q.get("turnover"),
                "intraday": ev.get("intraday"),
                **market,
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
        upd = ft_mod.update_results(mk, cfg, io=io)
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
    pricetrack.mark_sold(code, cfg)
    cl = trades_mod.consec_losses(cfg)
    limit = int(cfg.s("consec_loss_limit", 2))
    if cl >= limit:
        io.say(f"  ! 连亏 {cl} 笔 ≥ {limit} → 进入冷却，下次新开需先复盘并 --force")
    return rec


def buy_plan_item(code: str, shares: int, entry: Optional[float] = None,
                  cfg: Optional[Config] = None, market: Optional[Market] = None,
                  io: Optional[IO] = None) -> Optional[dict]:
    """按计划买入：录入持仓（复用手动录入逻辑）+ 标记计划项 executed。

    现价/成本价默认自动取现价，也可由调用方显式传 entry（用户在交互里改过价）。
    """
    cfg = cfg or load_config()
    io = io or IO()
    mk = market or Market(cfg)
    code = utils.norm_code(code)
    plan = plan_mod.load_plan(cfg)
    item = plan_mod.find_item(plan, code)
    if not item:
        io.say(f"  计划中无 {code}")
        return None
    if item.get("status") not in (plan_mod.STATUS_PENDING, plan_mod.STATUS_READY):
        io.say(f"  {code} 当前状态 {item.get('status')}，不可买入")
        return None
    if entry is None:
        try:
            entry = (mk.get_quote(code) or {}).get("price")
        except Exception:
            entry = None
    if entry is None:
        io.say(f"  取不到 {code} 现价，请手动指定成本价")
        return None
    shares = int(shares)
    name = item.get("name") or code
    res = portfolio.add_manual_position(code, name, shares, float(entry), cfg,
                                        sl_pct=item.get("sl_pct"), tp_pct=item.get("tp_pct"))
    plan_mod.mark_executed(code, cfg, detail={"shares": shares, "entry": float(entry),
                                              "source": "plan_buy"})
    accumulator.note(f"{code} 按计划买入 {shares} 股 @ {utils.num(entry)}", cfg)
    io.say(f"  ✓ 已买入 {code} {name}：{shares} 股 @ {utils.num(entry)}，计划项标记 executed")
    return res["pos"]


def holdings_review(cfg: Optional[Config] = None, market: Optional[Market] = None,
                    io: Optional[IO] = None) -> dict:
    """持仓盈亏 + 种子历史对照：逐只拉现价算浮动盈亏，并回溯是否被种子选过。

    手动录入的实盘持仓往往没有共振/身份快照，这里把它们和历史 seed_records
    按代码对上，让「我手里的票 vs 程序选过的票」一目了然。
    """
    cfg = cfg or load_config()
    io = io or IO()
    mk = market or Market(cfg)
    pos = portfolio.positions(cfg)
    io.say("=" * 56)
    io.say(f"XJB_TRADE · 持仓盈亏与种子对照　{utils.now().strftime('%Y-%m-%d %H:%M')}")
    io.say("=" * 56)
    if not pos:
        io.say("  当前无持仓。手动录入：`tea pos-add <代码> <股数> <成本价>`")
        return {"positions": [], "total_pnl": 0.0, "total_cost": 0.0, "matched": 0}

    seed_by_code: Dict[str, List[dict]] = {}
    for r in ft_mod.load_records(cfg):
        seed_by_code.setdefault(r.get("code"), []).append(r)
    for recs in seed_by_code.values():
        recs.sort(key=lambda x: x.get("date") or "")

    rows: List[dict] = []
    total_pnl = 0.0
    total_cost = 0.0
    matched = 0
    for p in pos:
        code = p.get("code")
        entry = float(p.get("entry") or 0.0)
        shares = int(p.get("shares") or 0)
        cost = round(entry * shares, 2)
        total_cost += cost
        cur = chg = None
        try:
            q = mk.get_quote(code) or {}
            cur = q.get("price")
            chg = q.get("chg_pct")
        except Exception:
            q = {}
        io.say(f"  {code} {p.get('name')}  {shares} 股 @ {utils.num(entry)}"
               + (f"（{p.get('opened_date')}）" if p.get('opened_date') else ""))
        pnl = pnl_pct = None
        if cur is not None and entry:
            pnl = round((float(cur) - entry) * shares, 2)
            pnl_pct = round((float(cur) - entry) / entry * 100.0, 2)
            total_pnl += pnl
            io.say(f"    现价 {utils.num(cur)}（{utils.hl(utils.pct(chg), utils.sign_color(chg))}）  浮动盈亏 "
                   f"{utils.hl(utils.money(pnl) + '（' + utils.pct(pnl_pct) + '）', utils.sign_color(pnl))}"
                   f"（未含卖出手续费）")
        else:
            io.say("    现价获取失败，无法计算浮动盈亏")
        recs = seed_by_code.get(code, [])
        if not recs:
            io.say("    · 种子对照：历史种子记录未出现")
        else:
            matched += 1
            buyable = [r for r in recs if r.get("track") == "可买"]
            show = (buyable or recs)[-3:]
            for r in show:
                t1 = r.get("next_chg")
                t1txt = f"T+1 {t1:+.2f}%" if t1 is not None else "T+1 未回填"
                mark = "★" if r.get("track") == "可买" else " "
                track = (utils.hl(r.get("track"), utils.COLOR_SEED)
                         if r.get("track") == "可买" else r.get("track"))
                io.say(f"    {mark} {r.get('date')} {track}（{r.get('tier')}）"
                       f"共振 {r.get('total_score')} {r.get('identity_tier')}"
                       f"{r.get('identity_score')} {r.get('sector_name')} → {t1txt}")
        rows.append({"code": code, "name": p.get("name"), "shares": shares,
                     "entry": entry, "current": cur, "chg": chg,
                     "pnl": pnl, "pnl_pct": pnl_pct, "seed": recs})

    seed_c = utils.COLOR_WARN if matched == 0 else utils.COLOR_SEED
    io.say(f"  合计：成本 {utils.money(total_cost)}，浮动盈亏 "
           f"{utils.hl(utils.money(round(total_pnl, 2)), utils.sign_color(total_pnl))}（未含卖出手续费）"
           f"　种子命中 {utils.hl(f'{matched}/{len(pos)}', seed_c)}")
    return {"positions": rows, "total_pnl": round(total_pnl, 2),
            "total_cost": round(total_cost, 2), "matched": matched}
