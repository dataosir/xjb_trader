"""cli.py — 命令行入口：数字菜单 + 子命令。

设计原则：CLI 只做"解析参数 → 调 runner → 打印"，不含任何交易判断逻辑。
所有阈值都在 tea_config.json 里，可用 `tea config set` 调整。

  tea                      进入数字菜单
  tea run --code 600519    单标的准入评估（Phase1→4）
  tea seed-plan            14:30 种子扫描 + 写次日计划
  tea plan-check           09:35 计划复核
  tea review               盘后复核（跟涨回填 + 观察池）
  tea status / weather / pos / trades / stats / weekly
  tea selftest             离线自测（公式对齐验证，不联网）
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import __version__
from . import accumulator, config_store, followthrough as ft_mod, gates, portfolio
from . import plan as plan_mod
from . import preflight, runner, seed_trace, trades as trades_mod, utils, watch_pool
from .config_store import Config, load_config
from .data import Market
from .phases import IO
from .sentiment import clear_cache, format_weather
from .timing import Timing

PROG = "tea"


# ==================================================================== 工具

def _io() -> IO:
    return IO()


def _market(cfg: Config) -> Market:
    return Market(cfg)


def _parse_value(raw: str):
    """配置值解析：JSON 优先（数字/布尔/数组），失败则当字符串。"""
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def _flatten(node, prefix: str = "") -> List[tuple]:
    out: List[tuple] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out += _flatten(v, f"{prefix}.{k}" if prefix else k)
    else:
        out.append((prefix, node))
    return out


# ==================================================================== 命令

def cmd_run(args, cfg: Config) -> int:
    res = runner.run_once(capital=args.capital, code=args.code, cfg=cfg, io=_io(),
                          force=args.force, allow_buy=not args.no_buy,
                          require_window=not args.any_time)
    return 0 if res.get("decision") == "BUY" else 1


def cmd_seed_plan(args, cfg: Config) -> int:
    res = runner.seed_plan(cfg=cfg, io=_io(), include_eve=not args.no_eve,
                           write_plan=not args.no_plan, require_window=args.strict_window)
    return 0 if res.get("buyable") else 1


def cmd_plan_check(args, cfg: Config) -> int:
    res = runner.plan_check(cfg=cfg, io=_io(), apply=not args.dry)
    return 1 if res.get("changed") else 0


def cmd_plan(args, cfg: Config) -> int:
    io = _io()
    if args.clear:
        p = plan_mod.clear_plan(cfg)
        accumulator.record_plan("clear", p, "手动清空", cfg)
        io.say("计划已清空")
        return 0
    if args.invalidate:
        p = plan_mod.invalidate(args.invalidate, cfg)
        accumulator.record_plan("invalidate", p, args.invalidate, cfg)
        io.say(f"计划已作废：{args.invalidate}")
        return 0
    io.say(plan_mod.format_plan(plan_mod.load_plan(cfg)))
    return 0


def cmd_review(args, cfg: Config) -> int:
    runner.close_review(cfg=cfg, io=_io(), prune=not args.no_prune)
    return 0


def cmd_status(args, cfg: Config) -> int:
    runner.daily_status(cfg=cfg, io=_io(), with_weather=args.weather)
    return 0


def cmd_weather(args, cfg: Config) -> int:
    io = _io()
    if args.refresh:
        clear_cache()
    s = runner.weather(cfg, refresh=args.refresh)
    io.say(format_weather(s))
    io.say(f"  {Timing(cfg).describe()}")
    return 0 if s.get("allow_new") else 1


def cmd_eval(args, cfg: Config) -> int:
    """只算不买：单标的评估（不计入门禁、不写报告）。"""
    io = _io()
    ev = preflight.evaluate(args.code, _market(cfg), cfg,
                                   sl_pct=args.sl, tp_pct=args.tp,
                                   has_news=args.news)
    io.say(preflight.format_evaluation(ev))
    return 0 if ev.get("verdict") == preflight.VERDICT_PASS else 1


def cmd_watch(args, cfg: Config) -> int:
    io = _io()
    if args.review:
        runner.close_review(cfg=cfg, io=io, prune=not args.no_prune)
        return 0
    if args.add:
        ev = preflight.evaluate(args.add, _market(cfg), cfg)
        it = watch_pool.add(ev, track=args.track, source="manual",
                            triggers=ft_mod.trigger_conditions(ev, cfg), cfg=cfg)
        io.say(f"已纳入{it.get('track')}：{it.get('code')} {it.get('name')}"
               f"（保留 {it.get('keep_days')} 天）")
        for t in it.get("triggers") or []:
            io.say(f"    · {t}")
        return 0
    if args.rm:
        it = watch_pool.remove(args.rm, "手动剔除", cfg)
        io.say(f"已剔除 {args.rm}" if it else f"观察池中无 {args.rm}")
        return 0
    io.say(watch_pool.format_pool(cfg))
    return 0


def cmd_pos(args, cfg: Config) -> int:
    io = _io()
    io.say(portfolio.format_positions(cfg))
    io.say(f"  总资金 {utils.money(portfolio.get_capital(cfg))}"
           f"　可用 {utils.money(portfolio.available_cash(cfg))}")
    return 0


def cmd_capital(args, cfg: Config) -> int:
    io = _io()
    if args.amount is None:
        io.say(f"总资金 {utils.money(portfolio.get_capital(cfg))}"
               f"　可用 {utils.money(portfolio.available_cash(cfg))}")
        return 0
    portfolio.set_capital(args.amount, cfg)
    io.say(f"总资金已设为 {utils.money(args.amount)}")
    return 0


def cmd_add_confirm(args, cfg: Config) -> int:
    r = runner.confirm_position(args.code, args.price, cfg, io=_io())
    return 0 if r else 1


def cmd_close(args, cfg: Config) -> int:
    r = runner.close_trade(args.code, args.price, args.reason, cfg,
                           io=_io(), shares=args.shares)
    return 0 if r else 1


def cmd_trades(args, cfg: Config) -> int:
    io = _io()
    io.say(trades_mod.format_trades(cfg, limit=args.limit))
    return 0


def cmd_stats(args, cfg: Config) -> int:
    runner.stats_report(cfg=cfg, io=_io(), write=args.write)
    return 0


def cmd_weekly(args, cfg: Config) -> int:
    runner.weekly_report(days=args.days, cfg=cfg, io=_io(), write=not args.no_write)
    return 0


def cmd_accum(args, cfg: Config) -> int:
    io = _io()
    if args.days and args.days > 1:
        io.say(accumulator.format_range(accumulator.range_digest(args.days, cfg)))
    else:
        io.say(accumulator.format_day(accumulator.day_digest(args.date, cfg)))
    return 0


def cmd_trace(args, cfg: Config) -> int:
    _io().say(seed_trace.format_trace_summary(cfg, args.date))
    return 0


def cmd_followthrough(args, cfg: Config) -> int:
    io = _io()
    if args.update:
        upd = ft_mod.update_results(_market(cfg), cfg)
        io.say(f"回填 {upd.get('updated')} 条，待回填 {upd.get('pending')} 条")
    io.say(ft_mod.format_followthrough(cfg))
    return 0


def cmd_gate(args, cfg: Config) -> int:
    io = _io()
    if args.reset:
        gates.reset_state(cfg)
        io.say("单日状态已重置（新开/评估计数归零）")
    io.say(gates.format_status(gates.status(None, cfg)))
    return 0


def cmd_config(args, cfg: Config) -> int:
    io = _io()
    act = args.action
    if act == "count":
        io.say(f"可调参数 {config_store.count_params()} 个（配置文件 {cfg.path}）")
        return 0
    if act == "reset":
        config_store.reset_config()
        io.say("配置已重置为默认值")
        return 0
    if act == "get":
        if not args.key:
            io.say("用法：tea config get <点分键>")
            return 2
        io.say(f"{args.key} = {json.dumps(cfg.get(args.key), ensure_ascii=False)}")
        return 0
    if act == "set":
        if not args.key or args.value is None:
            io.say("用法：tea config set <点分键> <值>")
            return 2
        old = cfg.get(args.key)
        cfg.set(args.key, _parse_value(args.value))
        cfg.save()
        io.say(f"{args.key}: {json.dumps(old, ensure_ascii=False)} → "
               f"{json.dumps(cfg.get(args.key), ensure_ascii=False)}")
        return 0
    # list
    items = _flatten(cfg.as_dict())
    if args.key:
        items = [(k, v) for k, v in items if k.startswith(args.key)]
    io.say(f"===== 配置（{len(items)} 项，{cfg.path}）=====")
    for k, v in items:
        io.say(f"  {k} = {json.dumps(v, ensure_ascii=False)}")
    return 0


def cmd_selftest(args, cfg: Config) -> int:
    from . import selftest
    return selftest.main(verbose=not args.quiet, cfg=cfg)


def cmd_version(args, cfg: Config) -> int:
    io = _io()
    io.say(f"XJB_TRADE (TEA) {__version__}")
    io.say(f"  配置 {cfg.path}（{config_store.count_params()} 参数）")
    io.say(f"  数据 {cfg.data_dir()}")
    io.say(f"  报告 {cfg.reports_dir()}")
    return 0


# ==================================================================== 数字菜单

MENU = [
    ("1", "市场天气（道）", ["weather"]),
    ("2", "今日状态（法）", ["status"]),
    ("3", "单标的准入评估（Phase1→4）", ["run"]),
    ("4", "只算不买（单标的评估）", ["__eval__"]),
    ("5", "种子扫描 + 写次日计划（14:30）", ["seed-plan"]),
    ("6", "计划复核（09:35 / 14:35）", ["plan-check"]),
    ("7", "查看交易计划", ["plan"]),
    ("8", "观察池", ["watch"]),
    ("9", "盘后复核（跟涨回填 + 观察池）", ["review"]),
    ("10", "持仓 / 资金", ["pos"]),
    ("11", "补足确认仓（3/7 的 7）", ["__confirm__"]),
    ("12", "平仓登记", ["__close__"]),
    ("13", "交易流水", ["trades"]),
    ("14", "统计与归因", ["stats"]),
    ("15", "周复盘报告", ["weekly"]),
    ("16", "当日累积（为什么没交易）", ["accum"]),
    ("17", "落选追溯", ["trace"]),
    ("18", "跟涨经验", ["followthrough"]),
    ("19", "配置一览", ["config", "list"]),
    ("20", "离线自测", ["selftest"]),
]

# 展开视图按场景分组：20 条平铺时无从下手，分成 6 组后每组只有 2–4 条。
# 编号沿用 MENU，不重排——文档、README、肌肉记忆都指着这些数字。
MENU_GROUPS = [
    ("道法 · 先看天气", ["1", "2"]),
    ("准入 · 买之前", ["3", "4"]),
    ("计划 · 次日", ["5", "6", "7"]),
    ("持仓 · 买之后", ["10", "11", "12"]),
    ("复盘 · 收盘后", ["9", "8", "16", "17", "18", "13", "14", "15"]),
    ("工具", ["19", "20"]),
]


def suggest_keys(tm: Timing) -> list:
    """挑出此刻真正该做的几项，最多四条。

    同一时刻有意义的操作从来不超过四个：非交易日只能复盘和演练，买入窗口外
    再怎么评估也会被门禁挡回来。默认视图只印这几条，其余的按 m 展开。
    """
    if not tm.is_trading_day():
        return ["9", "5", "2", "1"]

    keys = []
    if tm.is_buy_window():
        keys += ["3", "7"]                      # 唯一新开窗口，先看计划再评估
    if tm.is_seed_window():
        keys += ["5"]                            # 14:30 扫种子、写次日计划
    if tm.is_plan_recheck_window() or tm.is_overnight_review_window():
        keys += ["6", "7"]
    if tm.is_after_close():
        keys += ["9", "5"]
    if not keys:
        keys = ["1", "8"] if tm.in_session() else ["1", "7"]  # 盘中盯观察池，盘前看计划
    keys += ["10", "2"]                          # 持仓和今日状态任何时候都想看

    out = []
    for k in keys:
        if k not in out:
            out.append(k)
    return out[:4]


def print_menu(io: IO, cfg: Config, full: bool = False) -> None:
    tm = Timing(cfg)
    labels = {k: label for k, label, _ in MENU}
    io.say("")
    io.say("=" * 56)
    io.say(f"XJB_TRADE (TEA) {__version__}　{tm.describe()}")
    io.say("  计划你的交易，交易你的计划。宁可空仓，不强行凑票。")
    io.say("=" * 56)
    if full:
        for title, keys in MENU_GROUPS:
            io.say(f"  ── {title} ──")
            for k in keys:
                io.say(f"  {k:>2}. {labels[k]}")
        io.say("   c. 收起，只看此刻该做的")
    else:
        io.say(f"  此刻（{tm.phase()}）建议：")
        for k in suggest_keys(tm):
            io.say(f"  {k:>2}. {labels[k]}")
        io.say("   m. 展开全部 20 项")
    io.say("   q. 退出　│　1-20 任意编号都可直接输入")


def menu_loop(cfg: Config) -> int:
    io = _io()
    table = {k: argv for k, _, argv in MENU}
    full = False
    while True:
        print_menu(io, cfg, full)
        try:
            choice = input("\n请选择> ").strip()
        except (EOFError, KeyboardInterrupt):
            io.say("")
            return 0
        if choice in ("q", "Q", "quit", "exit"):
            return 0
        if choice in ("m", "M"):
            full = True
            continue
        if choice in ("c", "C"):
            full = False
            continue
        if not choice:
            continue          # 直接回车是想再看一眼菜单，不是选错了
        argv = table.get(choice)
        if not argv:
            io.say("  无此选项")
            continue
        try:
            if argv[0] == "__eval__":
                code = input("股票代码> ").strip()
                if code:
                    main(["eval", code])
            elif argv[0] == "__confirm__":
                code = input("股票代码> ").strip()
                if code:
                    main(["add-confirm", code])
            elif argv[0] == "__close__":
                code = input("股票代码> ").strip()
                price = input("平仓价> ").strip()
                if code and price:
                    main(["close", code, price])
            else:
                main(argv)
        except KeyboardInterrupt:
            io.say("\n  已中断")
        except Exception as exc:  # 菜单里不让单条命令崩掉整个会话
            io.say(f"  执行出错：{exc}")


# ==================================================================== 解析器

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=PROG, description="XJB_TRADE (TEA) A 股交易准入引擎")
    p.add_argument("-v", "--version", action="store_true", help="显示版本与路径")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="单标的准入评估（Phase1→4）")
    r.add_argument("code", nargs="?", help="6 位股票代码")
    r.add_argument("--code", dest="code_opt", help="6 位股票代码")
    r.add_argument("--capital", type=float, help="总资金（默认读已保存值）")
    r.add_argument("--force", action="store_true", help="连亏冷却强制放行（需已复盘）")
    r.add_argument("--any-time", action="store_true", help="忽略 14:00-14:45 窗口限制（仅演练）")
    r.add_argument("--no-buy", action="store_true", help="只走流程不下单")
    r.set_defaults(func=cmd_run)

    e = sub.add_parser("eval", help="只算不买：单标的评估")
    e.add_argument("code")
    e.add_argument("--sl", type=float, help="手动止损百分比")
    e.add_argument("--tp", type=float, help="手动止盈百分比")
    e.add_argument("--news", action="store_true", help="有明确消息催化")
    e.set_defaults(func=cmd_eval)

    s = sub.add_parser("seed-plan", help="种子扫描四步流 + 写次日计划")
    s.add_argument("--no-eve", action="store_true", help="跳过前夕观察扫描")
    s.add_argument("--no-plan", action="store_true", help="只扫描不写计划")
    s.add_argument("--strict-window", action="store_true", help="非 14:30 窗口时提示")
    s.set_defaults(func=cmd_seed_plan)

    pc = sub.add_parser("plan-check", help="计划复核（任一变动整单作废）")
    pc.add_argument("--dry", action="store_true", help="只比对不落盘")
    pc.set_defaults(func=cmd_plan_check)

    pl = sub.add_parser("plan", help="查看 / 清空 / 作废交易计划")
    pl.add_argument("--clear", action="store_true", help="清空计划")
    pl.add_argument("--invalidate", metavar="REASON", help="按理由作废计划")
    pl.set_defaults(func=cmd_plan)

    rv = sub.add_parser("review", help="盘后复核：跟涨回填 + 观察池 + 当日累积")
    rv.add_argument("--no-prune", action="store_true", help="不自动清理超时观察项")
    rv.set_defaults(func=cmd_review)

    st = sub.add_parser("status", help="今日状态（门禁计数 / 计划 / 持仓）")
    st.add_argument("--weather", action="store_true", help="同时拉市场天气")
    st.set_defaults(func=cmd_status)

    w = sub.add_parser("weather", help="市场天气（情绪分 / 周期 / 姿态）")
    w.add_argument("--refresh", action="store_true", help="忽略 120s 缓存重算")
    w.set_defaults(func=cmd_weather)

    wp = sub.add_parser("watch", help="观察池：查看 / 复核 / 纳入 / 剔除")
    wp.add_argument("--review", action="store_true", help="执行盘后复核")
    wp.add_argument("--add", metavar="CODE", help="手动纳入观察池")
    wp.add_argument("--track", default=watch_pool.TRACK_WATCH, help="轨道名（默认观察轨）")
    wp.add_argument("--rm", metavar="CODE", help="剔除")
    wp.add_argument("--no-prune", action="store_true")
    wp.set_defaults(func=cmd_watch)

    po = sub.add_parser("pos", help="持仓与资金")
    po.set_defaults(func=cmd_pos)

    cp = sub.add_parser("capital", help="查看 / 设置总资金")
    cp.add_argument("amount", nargs="?", type=float)
    cp.set_defaults(func=cmd_capital)

    ac = sub.add_parser("add-confirm", help="突破确认后补足 70%% 确认仓")
    ac.add_argument("code")
    ac.add_argument("price", nargs="?", type=float, help="确认价（默认取现价）")
    ac.set_defaults(func=cmd_add_confirm)

    cl = sub.add_parser("close", help="平仓登记（写流水 + 回收资金）")
    cl.add_argument("code")
    cl.add_argument("price", nargs="?", type=float, help="平仓价（默认取现价）")
    cl.add_argument("--shares", type=int, help="部分平仓股数")
    cl.add_argument("--reason", default="手动平仓")
    cl.set_defaults(func=cmd_close)

    tr = sub.add_parser("trades", help="交易流水")
    tr.add_argument("--limit", type=int, default=10)
    tr.set_defaults(func=cmd_trades)

    sa = sub.add_parser("stats", help="统计与归因")
    sa.add_argument("--write", action="store_true", help="同时写 STATS_*.md")
    sa.set_defaults(func=cmd_stats)

    wk = sub.add_parser("weekly", help="周复盘（纪律自查 + 归因）")
    wk.add_argument("--days", type=int, default=7)
    wk.add_argument("--no-write", action="store_true")
    wk.set_defaults(func=cmd_weekly)

    au = sub.add_parser("accum", help="当日/区间累积（为什么今天没交易）")
    au.add_argument("--date", help="YYYY-MM-DD，默认今天")
    au.add_argument("--days", type=int, help="改看最近 N 天汇总")
    au.set_defaults(func=cmd_accum)

    tc = sub.add_parser("trace", help="落选追溯（每一步淘汰了谁）")
    tc.add_argument("--date", help="YYYY-MM-DD，默认今天")
    tc.set_defaults(func=cmd_trace)

    ft = sub.add_parser("followthrough", help="跟涨经验（T+1 胜率）")
    ft.add_argument("--update", action="store_true", help="先回填 T+1 结果")
    ft.set_defaults(func=cmd_followthrough)

    ga = sub.add_parser("gate", help="单日门禁状态")
    ga.add_argument("--reset", action="store_true", help="重置单日计数（谨慎）")
    ga.set_defaults(func=cmd_gate)

    cf = sub.add_parser("config", help="配置 list/get/set/reset/count")
    cf.add_argument("action", nargs="?", default="list",
                    choices=["list", "get", "set", "reset", "count"])
    cf.add_argument("key", nargs="?")
    cf.add_argument("value", nargs="?")
    cf.set_defaults(func=cmd_config)

    sf = sub.add_parser("selftest", help="离线自测（公式对齐验证，不联网）")
    sf.add_argument("--quiet", action="store_true")
    sf.set_defaults(func=cmd_selftest)

    mn = sub.add_parser("menu", help="进入数字菜单")
    mn.set_defaults(func=None)
    return p


def _force_utf8_output() -> None:
    """把 stdout / stderr 切到 UTF-8。

    Windows 上输出一旦不是直连控制台（管道、`> file`、CI 日志），Python
    用的就是 ANSI 代码页（cp1252 / cp936），而本程序所有文案都是中文，
    第一句 print 就会 UnicodeEncodeError 把进程整个打挂。

    必须在 parse_args 之前调：`--help` 是 argparse 在解析时就打印的。
    控制台直连时本来就是 UTF-8，这里是幂等的。
    """
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", "") or "").lower().replace("-", "").replace("_", "")
        if enc == "utf8":
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass   # 不是常规文本流（被测试框架或调用方探过），保持原样


def main(argv: Optional[List[str]] = None) -> int:
    _force_utf8_output()
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config()

    if getattr(args, "version", False) and not args.cmd:
        return cmd_version(args, cfg)
    if not args.cmd or args.cmd == "menu":
        return menu_loop(cfg)

    # run 允许位置参数或 --code 两种写法
    if args.cmd == "run" and getattr(args, "code_opt", None):
        args.code = args.code_opt

    func = getattr(args, "func", None)
    if func is None:
        return menu_loop(cfg)
    try:
        return int(func(args, cfg) or 0)
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
