"""cli.py — 命令行入口：数字菜单 + 子命令。

设计原则：CLI 只做"解析参数 → 调 runner → 打印"，不含任何交易判断逻辑。
所有阈值都在 tea_config.json 里，可用 `tea config set` 调整。

  tea                      进入数字菜单
  tea run --code 600519    单标的准入评估（Phase1→4）
  tea seed-plan            14:30 种子扫描 + 写次日计划
  tea plan-check           09:35 计划复核
  tea plan-clear           清除过期旧计划
  tea review               盘后复核（跟涨回填 + 观察池）
  tea status / weather / pos / trades / stats / weekly
  tea setup                配置向导（首次启动自动进入）
  tea selftest             离线自测（公式对齐验证，不联网）
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import List, Optional

from tea import __version__
from tea.analysis import followthrough as ft_mod, pricetrack
from tea.analysis.sentiment import clear_cache, format_weather
from tea.config import config_store, onboarding
from tea.config.config_store import Config, load_config
from tea.core import logger as logger_mod, utils
from tea.core.timing import Timing
from tea.data import Market
from tea.phases import IO
from tea.portfolio import accumulator, plan as plan_mod, portfolio, trades as trades_mod, watch_pool
from tea.reporting import seed_trace
from tea.screening import gates, preflight
from . import runner

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


def _clean_number(raw: str) -> str:
    """数字输入容错：全角数字转半角 + 去掉半角/全角空格。

    交互录入成本价时，输入法/终端有时会插入一个删不掉的全角空格（如「1 .109」），
    直接 float() 会失败或把「1」弄丢。这里先把空格清掉再解析。
    """
    return (utils.normalize_digits(raw or "")
            .replace(" ", "").replace("\u3000", "").replace("\t", "").replace("\u00a0", ""))


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


def _plan_expired(plan: dict, cfg: Config) -> bool:
    """计划是否已过期/作废：还挂着标的，但今日已不可执行。

    已清除 / 已执行的计划没有收尾可做，不算过期。
    """
    if not plan or not plan.get("items"):
        return False
    status = plan.get("status")
    if status in (plan_mod.STATUS_CLEARED, plan_mod.STATUS_EXECUTED):
        return False
    if status == plan_mod.STATUS_INVALID:
        return True
    return plan.get("execute_date") != utils.today_str()


def _offer_clear_expired_plan(cfg: Config, io: IO) -> bool:
    """复核完发现计划已过期/作废时，当场问一句要不要清除（非交互不阻塞）。

    过期计划留在盘上会一直触发菜单 22 的提醒，顺手清掉比记着再敲一条命令省事。
    """
    plan = plan_mod.load_plan(cfg)
    if not _plan_expired(plan, cfg):
        return False
    why = "计划已作废" if plan.get("status") == plan_mod.STATUS_INVALID else \
        f"执行日 {plan.get('execute_date')} 已过"
    if not io.ask_yes(f"该计划已过期（{why}），是否清除？", key="plan_clear"):
        io.say("  已保留计划（随时可执行 plan-clear 清除）")
        return False
    labels = plan_mod.planned_labels(plan) or plan_mod.planned_labels(plan, only_active=False)
    reason = "计划复核确认清除"
    p = plan_mod.clear_plan(cfg, reason=reason)
    accumulator.record_plan("clear", p, reason, cfg)
    io.say(f"已清除旧计划：{'、'.join(labels)}")
    io.say(f"  清除时间 {p.get('cleared_at')}")
    return True


def cmd_plan_check(args, cfg: Config) -> int:
    io = _io()
    res = runner.plan_check(cfg=cfg, io=io, apply=not args.dry)
    if not args.dry:
        _offer_clear_expired_plan(cfg, io)
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


def cmd_plan_clear(args, cfg: Config) -> int:
    """清除过期旧计划：种子扫描提示的「请执行 plan-clear」指的就是这条。"""
    io = _io()
    labels = plan_mod.planned_labels(plan_mod.load_plan(cfg))
    if not labels:
        io.say("当前无待清除计划")
        return 0
    p = plan_mod.clear_plan(cfg, reason=args.reason)
    accumulator.record_plan("clear", p, args.reason, cfg)
    io.say(f"已清除旧计划：{'、'.join(labels)}")
    io.say(f"  清除时间 {p.get('cleared_at')}")
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
    s = runner.weather(cfg, refresh=args.refresh, io=io)
    io.say(format_weather(s))
    io.say(f"  {Timing(cfg).describe()}")
    return 0 if s.get("allow_new") else 1


def cmd_eval(args, cfg: Config) -> int:
    """只算不买：单标的评估（不计入门禁、不写报告）。"""
    io = _io()
    io.say(f"  ⏳ 正在评估 {utils.norm_code(args.code)}（行情 / 指标 / 板块）...")
    with utils.timed("评估完成", io, threshold=0.5):
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
        io.say(f"  ⏳ 正在评估 {utils.norm_code(args.add)}...")
        with utils.timed("评估完成", io, threshold=0.5):
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
    runner.holdings_review(cfg=cfg, io=io)
    io.say(f"  总资金 {utils.money(portfolio.get_capital(cfg))}"
           f"　可用 {utils.money(portfolio.available_cash(cfg))}")
    return 0


def cmd_pos_add(args, cfg: Config) -> int:
    io = _io()
    code = utils.norm_code(args.code)
    name = args.name or ""
    if not name:
        try:
            name = (_market(cfg).get_quote(code) or {}).get("name") or code
        except Exception:
            name = code
    res = portfolio.add_manual_position(code, name, args.shares, args.price, cfg,
                                        sl_pct=args.sl, tp_pct=args.tp,
                                        opened_date=args.date)
    pos = res["pos"]
    verb = "已更新" if res["updated"] else "已录入"
    io.say(f"  ✓ {verb} {code} {name}：{args.shares} 股 @ {utils.num(args.price)}"
           f"（成本 {utils.money(args.shares * args.price)}"
           + (f"，建仓日 {pos.get('opened_date')}" if pos.get('opened_date') else "") + "）")
    return 0


def cmd_pos_rm(args, cfg: Config) -> int:
    io = _io()
    code = utils.norm_code(args.code)
    removed = portfolio.remove_position(code, cfg)
    if not removed:
        io.say(f"  持仓中无 {code}")
        return 1
    pricetrack.mark_removed(code, cfg)
    io.say(f"  ✓ 已删除持仓 {code} {removed.get('name')}")
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
        io.say("  ⏳ 回填跟涨样本 T+1 结果...")
        with utils.timed("跟涨样本回填", io, threshold=0.5):
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
        new = _parse_value(args.value)
        cfg.set(args.key, new)
        cfg.save()
        # 留痕：参数变更写进 accumulator.jsonl + 运行日志，双轨追溯「何时改了哪个阈值」。
        accumulator.record_param(args.key, old, cfg.get(args.key), cfg)
        logger_mod.get_logger("config").info("配置变更 %s: %s → %s",
                                             args.key, old, cfg.get(args.key))
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


def cmd_setup(args, cfg: Config) -> int:
    """配置向导：首次启动自动进入，之后可随时手动重跑。"""
    res = onboarding.run_wizard(cfg=cfg, io=_io(), use_defaults=args.defaults,
                                first_run=not cfg.get("meta.initialized", False))
    return 0 if res.get("saved") else 1


def cmd_selftest(args, cfg: Config) -> int:
    from tea import selftest
    return selftest.main(verbose=not args.quiet, cfg=cfg)


def cmd_version(args, cfg: Config) -> int:
    io = _io()
    io.say(f"XJB_TRADE (TEA) {__version__}")
    io.say(f"  配置 {cfg.path}（{config_store.count_params()} 参数）")
    io.say(f"  数据 {cfg.data_dir()}")
    io.say(f"  报告 {cfg.reports_dir()}")
    return 0


# ==================================================================== 数字菜单


def _normalize_choice(raw: str) -> str:
    """全角数字→半角，兼容中文输入法。"""
    return utils.normalize_digits(raw)


MENU = [
    ("1", "市场天气（道）", ["weather"]),
    ("2", "今日状态（法）", ["status"]),
    ("3", "种子扫描 + 写计划（14:30）", ["seed-plan"]),
    ("4", "计划复核（09:35 / 14:35）", ["plan-check"]),
    ("5", "查看交易计划", ["__plan__"]),
    ("6", "单标的准入评估", ["run"]),
    ("7", "持仓 / 资金", ["pos"]),
    ("8", "平仓登记", ["__close__"]),
    ("9", "观察池", ["watch"]),
    ("10", "盘后复核（跟涨回填+观察池）", ["review"]),
    ("11", "复盘工具 ▸", ["__submenu__", "复盘工具"]),
    ("12", "持仓管理 ▸", ["__submenu__", "持仓管理"]),
    ("13", "配置与维护 ▸", ["__submenu__", "配置与维护"]),
]

# 二级子菜单：常用功能留在顶层一键直达，低频功能收进来，顶层从 24 项压到 13 项。
SUBMENUS = {
    "复盘工具": [
        ("1", "当日累积（为什么没交易）", ["accum"]),
        ("2", "落选追溯", ["trace"]),
        ("3", "跟涨经验", ["followthrough"]),
        ("4", "交易流水", ["trades"]),
        ("5", "统计与归因", ["stats"]),
        ("6", "周复盘报告", ["weekly"]),
    ],
    "持仓管理": [
        ("1", "手动录入持仓", ["__pos_add__"]),
        ("2", "删除持仓", ["__pos_rm__"]),
        ("3", "补足确认仓（3/7 的 7）", ["__confirm__"]),
        ("4", "只算不买（单标的评估）", ["__eval__"]),
    ],
    "配置与维护": [
        ("1", "配置一览", ["config", "list"]),
        ("2", "配置向导（重新配置）", ["setup"]),
        ("3", "离线自测", ["selftest"]),
        ("4", "清除过期计划", ["plan-clear"]),
    ],
}

# 顶层展开视图的分组（子菜单只占一项）。
MENU_GROUPS = [
    ("道法 · 先看天气", ["1", "2"]),
    ("计划 · 次日", ["3", "4", "5"]),
    ("交易 · 买与卖", ["6", "7", "8"]),
    ("观察 · 盘中与盘后", ["9", "10"]),
    ("更多功能", ["11", "12", "13"]),
]


def _has_stale_plan(cfg: Optional[Config]) -> bool:
    """是否挂着过期旧计划：还有未执行标的，但执行日已经不是今天。"""
    if cfg is None:
        return False
    try:
        plan = plan_mod.load_plan(cfg)
    except Exception:
        return False       # 计划文件坏了不该拖累菜单
    return bool(plan_mod.active_items(plan)) and not plan_mod.is_valid_today(plan, cfg)


def suggest_keys(tm: Timing, cfg: Optional[Config] = None) -> list:
    """挑出此刻真正该做的几项，最多四条。

    同一时刻有意义的操作从来不超过四个：非交易日只能复盘和演练，买入窗口外
    再怎么评估也会被门禁挡回来。默认视图只印这几条，其余的按 m 展开。
    """
    if not tm.is_trading_day():
        keys = ["10", "3", "2", "1"]
    else:
        keys = []
        if tm.is_buy_window():
            keys += ["6", "5"]                      # 唯一新开窗口，先看计划再评估
        if tm.is_seed_window():
            keys += ["3"]                            # 14:30 扫种子、写次日计划
        if tm.is_plan_recheck_window() or tm.is_overnight_review_window():
            keys += ["4", "5"]
        if tm.is_after_close():
            keys += ["10", "3"]
        if not keys:
            keys = ["1", "9"] if tm.in_session() else ["1", "5"]  # 盘中盯观察池，盘前看计划
        keys += ["7", "2"]                          # 持仓和今日状态任何时候都想看

    if _has_stale_plan(cfg):
        keys.insert(0, "13")                         # 旧计划没清干净 → 配置与维护里清除

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
        for k in suggest_keys(tm, cfg):
            io.say(f"  {k:>2}. {labels[k]}")
        io.say(f"   m. 展开全部 {len(MENU)} 项（或直接输入 1-{len(MENU)}）")
    io.say(f"   q. 退出 │ 1-{len(MENU)} 任意编号 │ 回车刷新菜单")


def _run_argv(argv: List[str], io: IO, cfg: Config) -> None:
    """执行一条菜单动作（含需要交互输入的特殊动作）。"""
    if argv[0] == "__eval__":
        code = input("股票代码> ").strip()
        if code:
            main(["eval", code])
        else:
            io.say("  已取消：未输入股票代码")
    elif argv[0] == "__confirm__":
        code = input("股票代码> ").strip()
        if code:
            main(["add-confirm", code])
        else:
            io.say("  已取消：未输入股票代码")
    elif argv[0] == "__close__":
        code = input("股票代码> ").strip()
        price = input("平仓价> ").strip()
        if code and price:
            main(["close", code, price])
        else:
            io.say("  已取消：平仓需同时输入股票代码与平仓价")
    elif argv[0] == "__pos_add__":
        io.say("  批量录入持仓：逐只输入，代码留空回车保存退出")
        n = 0
        while True:
            raw_code = utils.normalize_digits(input("  股票代码> ").strip())
            if not raw_code:
                break
            shares_raw = _clean_number(input("  股数> ").strip())
            price_raw = _clean_number(input("  成本价> ").strip())
            if not (len(raw_code) == 6 and raw_code.isdigit()):
                io.say(f"  ! 代码「{raw_code}」不是 6 位数字，跳过")
                continue
            try:
                shares = int(shares_raw)
                price = float(price_raw)
            except (ValueError, TypeError):
                io.say(f"  ! 股数「{shares_raw}」或成本价「{price_raw}」格式不对，跳过")
                continue
            if shares <= 0 or price <= 0:
                io.say("  ! 股数与成本价需 >0，跳过")
                continue
            rc = cmd_pos_add(argparse.Namespace(
                code=raw_code, name=None, shares=shares, price=price,
                sl=None, tp=None, date=None), cfg)
            if rc == 0:
                n += 1
        io.say(f"  批量录入结束，本次处理 {n} 只")
    elif argv[0] == "__pos_rm__":
        positions = portfolio.positions(cfg)
        if not positions:
            io.say("  当前无持仓")
        else:
            for i, p in enumerate(positions, 1):
                io.say(f"  {i}. {p['code']} {p.get('name')} {p.get('shares')} 股 @ {utils.num(p.get('entry'))}")
            sel = input("  删除编号（回车取消）> ").strip()
            if not sel:
                io.say("  已取消")
            elif sel.isdigit() and 1 <= int(sel) <= len(positions):
                main(["pos-rm", positions[int(sel) - 1]["code"]])
            else:
                io.say(f"  ! 无效编号「{sel}」")
    elif argv[0] == "__plan__":
        while True:
            plan = plan_mod.load_plan(cfg)
            io.say(plan_mod.format_plan(plan))
            items = plan.get("items") or []
            if not plan_mod.active_items(plan):
                io.say("  无待执行计划项（仅 pending/ready 可买入或删除）")
                break
            act = input("  操作：买入 `b <编号>`，删除 `d <编号>`，回车返回 > ").strip().lower()
            if not act:
                break
            cmd, num = act[0], act[1:].strip()
            if cmd not in ("b", "d") or not num.isdigit():
                io.say("  ! 格式：`b 1` 买入第 1 项 / `d 1` 删除第 1 项")
                continue
            idx = int(num)
            if not (1 <= idx <= len(items)):
                io.say(f"  ! 编号 {idx} 越界（共 {len(items)} 项）")
                continue
            item = items[idx - 1]
            code = item.get("code")
            if cmd == "b":
                if item.get("status") not in (plan_mod.STATUS_PENDING, plan_mod.STATUS_READY):
                    io.say(f"  ! {code} 当前状态 {item.get('status')}，不可买入")
                    continue
                try:
                    cur = (_market(cfg).get_quote(code) or {}).get("price")
                except Exception:
                    cur = None
                if cur is None:
                    io.say(f"  ! 取不到 {code} 现价")
                    continue
                io.say(f"  买入 {code} {item.get('name')}，现价 {utils.num(cur)}")
                shares_raw = _clean_number(input("  买入数量> ").strip())
                try:
                    shares = int(shares_raw)
                except (ValueError, TypeError):
                    io.say(f"  ! 数量「{shares_raw}」格式不对")
                    continue
                if shares <= 0:
                    io.say("  ! 数量需 >0")
                    continue
                entry_raw = _clean_number(input(f"  成本价（回车=现价 {utils.num(cur)}）> ").strip())
                try:
                    entry = float(entry_raw) if entry_raw else float(cur)
                except (ValueError, TypeError):
                    io.say(f"  ! 成本价「{entry_raw}」格式不对")
                    continue
                runner.buy_plan_item(code, shares, entry, cfg=cfg, io=io)
            elif cmd == "d":
                if item.get("status") not in (plan_mod.STATUS_PENDING, plan_mod.STATUS_READY):
                    io.say(f"  ! {code} 当前状态 {item.get('status')}，无需删除")
                    continue
                plan_mod.remove_item(code, cfg)
                pricetrack.mark_removed(code, cfg)
                io.say(f"  ✓ 已删除计划项 {code} {item.get('name')}")
            else:
                io.say("  ! 命令仅支持 b（买入）/ d（删除）")
    else:
        main(argv)


def menu_loop(cfg: Config) -> int:
    io = _io()
    onboarding.maybe_run(cfg, io)      # 首次启动先把必要配置问一遍
    table = {k: argv for k, _, argv in MENU}
    full = False
    while True:
        print_menu(io, cfg, full)
        try:
            choice = _normalize_choice(input("\n请选择> ").strip())
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
        # 前导零标准化："01"→"1"，"010"→"10"
        if choice.isdigit() and len(choice) > 1 and choice[0] == '0':
            choice = str(int(choice))
        argv = table.get(choice)
        if not argv:
            io.say("  无此选项")
            continue
        # 子菜单：进入二级选择，b/回车返回主菜单
        if argv[0] == "__submenu__":
            name = argv[1]
            sub = SUBMENUS.get(name, [])
            subtable = {k: av for k, _, av in sub}
            while True:
                io.say("")
                io.say(f"  ── {name} ──")
                for k, label, _ in sub:
                    io.say(f"  {k:>2}. {label}")
                io.say("   b. 返回主菜单 │ q. 退出")
                try:
                    subchoice = _normalize_choice(input("\n请选择> ").strip())
                except (EOFError, KeyboardInterrupt):
                    io.say("")
                    return 0
                if subchoice in ("q", "Q", "quit", "exit"):
                    return 0
                if subchoice in ("b", "B") or not subchoice:
                    break
                subargv = subtable.get(subchoice)
                if not subargv:
                    io.say("  无此选项")
                    continue
                try:
                    _run_argv(subargv, io, cfg)
                except KeyboardInterrupt:
                    io.say("\n  已中断")
                except Exception as exc:  # 子菜单里同样不让单条命令崩掉会话
                    io.say(f"  执行出错：{exc}")
                if io.pause() in ("q", "quit", "exit"):
                    return 0
            continue
        try:
            _run_argv(argv, io, cfg)
        except KeyboardInterrupt:
            io.say("\n  已中断")
        except Exception as exc:  # 菜单里不让单条命令崩掉整个会话
            io.say(f"  执行出错：{exc}")
        # 结果出完先停住，别把刚打印的东西立刻用菜单顶掉
        if io.pause() in ("q", "quit", "exit"):
            return 0


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

    plc = sub.add_parser("plan-clear", help="清除过期旧计划（计划过期后的收尾）")
    plc.add_argument("--reason", default="手动清除过期计划", help="清除理由（写入计划备注）")
    plc.set_defaults(func=cmd_plan_clear)

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
    wp.add_argument("--track", default=watch_pool.TRACK_WATCH, choices=list(watch_pool.KNOWN_TRACKS),
                    help="轨道名（默认观察轨）")
    wp.add_argument("--rm", metavar="CODE", help="剔除")
    wp.add_argument("--no-prune", action="store_true")
    wp.set_defaults(func=cmd_watch)

    po = sub.add_parser("pos", help="持仓与资金（含盈亏与种子对照）")
    po.set_defaults(func=cmd_pos)

    pa = sub.add_parser("pos-add", help="手动录入持仓（实盘买入补登）")
    pa.add_argument("code")
    pa.add_argument("shares", type=int)
    pa.add_argument("price", type=float)
    pa.add_argument("--name", help="股票名（默认自动取行情）")
    pa.add_argument("--date", help="建仓日 YYYY-MM-DD（默认今天）")
    pa.add_argument("--sl", type=float, help="止损百分比")
    pa.add_argument("--tp", type=float, help="止盈百分比")
    pa.set_defaults(func=cmd_pos_add)

    pr = sub.add_parser("pos-rm", help="删除一条持仓记录")
    pr.add_argument("code")
    pr.set_defaults(func=cmd_pos_rm)

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

    sw = sub.add_parser("setup", help="配置向导（首次启动自动进入，之后可随时重跑）")
    sw.add_argument("--defaults", action="store_true", help="不提问，直接采用推荐默认值")
    sw.set_defaults(func=cmd_setup)

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
    # 运行日志：落到 logs/（与 data/ 分开），按日切割。失败不打断主流程。
    _level = getattr(logging, str(cfg.get("logs.level", "INFO")).upper(), logging.INFO)
    logger_mod.init_logging(cfg, _level)
    logger_mod.get_logger("cli").info("启动 tea v%s | 配置 %s | 数据 %s | 日志 %s",
                                      __version__, cfg.path, cfg.data_dir(), cfg.logs_dir())

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
