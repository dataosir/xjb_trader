"""onboarding.py — 首次启动配置向导。

默认值是按一套固定的资金体量和风险偏好调出来的，换个人就不合身：资金差一个
量级，仓位算出来的股数就没意义；只做主板的人不该被创业板的票占掉当日唯一的
新开名额。所以第一次跑起来时把真正因人而异的几项问一遍，其余几百个参数一律
留默认——问得越多，半路退出的人越多。

判据只看 meta.initialized（见 config_store.DEFAULTS）：配置文件不存在，或存在
但没打过标记，都会在进主菜单前引导一次。引导完打标记，此后只能从菜单/`tea
setup` 主动重进。

本模块只读写配置与资金状态，不含任何交易判断逻辑。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import portfolio, utils
from .config_store import ALL_DATA_SOURCES, DEFAULTS, Config, load_config
from .phases import IO

WIZARD_VERSION = 1

MAX_RETRY = 3                      # 连续 3 次非法输入就采用默认值，不跟人较劲
CAPITAL_FALLBACK = 100000.0        # 资金不落配置文件（在 capital_state.json），得有个起点

MODE_DEFAULTS = "defaults"
MODE_CUSTOM = "custom"
MODE_SKIP = "skip"
MODE_ABORT = "abort"


# ==================================================================== 引导项

# 每项都是「一个配置键 + 一句人话 + 一个安全区间」。dotted 为 None 的是特殊项
# （资金存 capital_state.json、板块权限是一组开关），单独处理。
NUMERIC_ITEMS: List[Dict[str, Any]] = [
    {
        "key": "max_position_pct", "dotted": "strategy.max_position_pct",
        "title": "单笔最大仓位（占总资金比例）",
        "desc": "一只票最多敢押多少钱。0.5 = 半仓封顶，是纪律的第一道闸。",
        "advice": "0.2 ~ 0.5（越高越搏，超过 0.5 一次看错就伤本金）",
        "lo": 0.05, "hi": 1.0,
    },
    {
        "key": "strict_min_chg", "dotted": "seed.strict_min_chg",
        "title": "选股涨幅下限（%）",
        "desc": "当日涨幅低于此值的票不进种子池——没启动的票等着也是耗时间。",
        "advice": "2.0 ~ 4.0",
        "lo": 0.0, "hi": 20.0,
    },
    {
        "key": "strict_max_chg", "dotted": "seed.strict_max_chg",
        "title": "选股涨幅上限（%）",
        "desc": "涨幅超过此值视为追高，直接淘汰。留出次日空间才有肉吃。",
        "advice": "5.0 ~ 7.5（必须大于下限）",
        "lo": 0.5, "hi": 20.0, "gt_key": "strict_min_chg",
    },
    {
        "key": "cap_min", "dotted": "seed.cap_min",
        "title": "市值下限（亿）",
        "desc": "小于此市值的票不看：盘子太小容易被一笔大单拍在地板上。",
        "advice": "20 ~ 50",
        "lo": 0.0, "hi": 10000.0,
    },
    {
        "key": "cap_max", "dotted": "seed.cap_max",
        "title": "市值上限（亿）",
        "desc": "大于此市值的票不看：大票拉不动，赚不到弹性。",
        "advice": "200 ~ 500（必须大于下限）",
        "lo": 1.0, "hi": 100000.0, "gt_key": "cap_min",
    },
    {
        "key": "min_odds", "dotted": "strategy.min_odds",
        "title": "最低风险回报比 R:R",
        "desc": "含滑点后赚赔比达不到此倍数就不开仓（止盈会先被自动抬高试一次）。",
        "advice": "2 ~ 3（调低会放进一堆赚小赔大的票）",
        "lo": 1.0, "hi": 10.0,
    },
    {
        "key": "pass_threshold", "dotted": "strategy.pass_threshold",
        "title": "共振分准入门槛（满分 9）",
        "desc": "9 分共振打分达不到此分数不予准入。调低=多交易，调高=更挑。",
        "advice": "5 ~ 7",
        "lo": 1.0, "hi": 9.0, "int": True,
    },
]

# 板块权限：没开通的板块必须关掉，否则票选出来了却买不进，白占当日名额。
BOARD_ITEMS: List[Dict[str, str]] = [
    {"key": "main", "title": "沪深主板"},
    {"key": "gem", "title": "创业板（300/301，需开通）"},
    {"key": "star", "title": "科创板（688，需 50 万+2 年）"},
    {"key": "bse", "title": "北交所（8/4 开头，需开通）"},
]

# 数据源降级链：顺序即优先级。开的源越多越抗单点故障，代价是主源真挂了的那次
# 请求要多等几秒；一家都不通的概率则大幅下降。默认（选项 1）全开。
SOURCE_PRESETS: List[Dict[str, Any]] = [
    {"choice": "1", "title": "全部启用（东财→腾讯→新浪→网易/凤凰，最抗故障，默认）",
     "value": list(ALL_DATA_SOURCES)},
    {"choice": "2", "title": "东财+腾讯+新浪（三家都有报价和 K 线，够用）",
     "value": ["eastmoney", "tencent", "sina"]},
    {"choice": "3", "title": "仅东财（字段最全但单点，东财一抖整轮就失败）",
     "value": ["eastmoney"]},
]
SOURCE_DEFAULT_CHOICE = "1"


# ==================================================================== 首次判定

def is_first_run(cfg: Optional[Config] = None) -> bool:
    """配置文件不存在，或没有「已初始化」标记，都算首次运行。"""
    cfg = cfg or load_config()
    if not os.path.exists(cfg.path):
        return True
    return not bool(cfg.get("meta.initialized", False))


def mark_initialized(cfg: Config, skipped: bool = False) -> str:
    cfg.set("meta.initialized", True)
    cfg.set("meta.initialized_at", utils.now().strftime("%Y-%m-%d %H:%M:%S"))
    cfg.set("meta.wizard_version", WIZARD_VERSION)
    cfg.set("meta.wizard_skipped", bool(skipped))
    return cfg.save()


# ==================================================================== 提问

def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, int):
        return str(v)
    f = utils.to_float(v)
    if f is None:
        return "—"
    return str(int(f)) if abs(f - int(f)) < 1e-9 else utils.num(f)


def _default_of(item: Dict[str, Any], cfg: Config) -> float:
    """当前值优先，回落 DEFAULTS——重跑向导时应该在现有配置上改。"""
    cur = cfg.get(item["dotted"])
    if cur is None:
        cur = _defaults_value(item["dotted"])
    return cur


def _defaults_value(dotted: str) -> Any:
    cur: Any = DEFAULTS
    for part in dotted.split("."):
        cur = (cur or {}).get(part)
    return cur


def _ask_number(io: IO, item: Dict[str, Any], current: Any,
                collected: Dict[str, Any]) -> Optional[Any]:
    """问一个数值项：印含义与建议范围，回车取默认，非法重问。

    返回 None 表示用户按了 Ctrl-C / EOF，由上层中止整个向导。
    """
    lo, hi = item.get("lo"), item.get("hi")
    gt = collected.get(item.get("gt_key") or "")
    io.say("")
    io.say(f"  · {item['title']}　[{item['dotted']}]")
    io.say(f"    {item['desc']}")
    io.say(f"    建议 {item['advice']}")
    for _ in range(MAX_RETRY):
        raw = io.ask("    输入新值", item["key"], _fmt(current))
        if raw is None:
            return None
        raw = utils.normalize_digits(str(raw).strip())
        if not raw:
            return current
        v = utils.to_float(raw)
        if v is None:
            io.say(f"    ✗ 请输入数字，如 {_fmt(current)}")
        elif lo is not None and v < lo:
            io.say(f"    ✗ 不得小于 {_fmt(lo)}")
        elif hi is not None and v > hi:
            io.say(f"    ✗ 不得大于 {_fmt(hi)}")
        elif gt is not None and v <= float(gt):
            io.say(f"    ✗ 必须大于上一项（{_fmt(gt)}）")
        else:
            return int(round(v)) if item.get("int") else v
        if not io.interactive:
            return current
    io.say(f"    多次无效，采用默认 {_fmt(current)}")
    return current


def _ask_sources(io: IO, cfg: Config) -> Optional[List[str]]:
    """问降级链。返回 None 表示用户中止；非法输入回落当前值。"""
    cur = list(cfg.get("market.data_sources") or _defaults_value("market.data_sources") or [])
    io.say("")
    io.say("  · 行情数据源降级链　[market.data_sources]")
    io.say("    主源被限流/改接口时自动换下一家取数，不至于整轮评估直接失败。")
    io.say(f"    当前：{' → '.join(cur) or '—'}")
    for s in SOURCE_PRESETS:
        io.say(f"      {s['choice']}) {s['title']}")
    for _ in range(MAX_RETRY):
        raw = io.ask("    输入新值", "data_sources", SOURCE_DEFAULT_CHOICE)
        if raw is None:
            return None
        choice = utils.normalize_digits(str(raw).strip())
        if not choice:
            return cur
        for s in SOURCE_PRESETS:
            if choice == s["choice"]:
                return list(s["value"])
        io.say("    ✗ 请输入 1 / 2 / 3")
        if not io.interactive:
            return cur
    io.say(f"    多次无效，采用当前值 {' → '.join(cur)}")
    return cur


def _ask_capital(io: IO, current: float) -> Optional[float]:
    io.say("")
    io.say("  · 总资金（元）　[capital_state.json]")
    io.say("    仓位/股数/单笔风险都按它算。填你真打算投进这套系统的钱，不是全部身家。")
    io.say("    建议 ≥ 5 万（太小会算不出整手）")
    for _ in range(MAX_RETRY):
        raw = io.ask("    输入新值", "capital", _fmt(current))
        if raw is None:
            return None
        raw = utils.normalize_digits(str(raw).strip()).replace(",", "")
        if not raw:
            return current
        v = utils.to_float(raw)
        if v is None or v <= 0:
            io.say("    ✗ 请输入正数，如 100000")
        elif v < 1000:
            io.say("    ✗ 资金过小（<1000 元），无法按整手建仓")
        else:
            return v
        if not io.interactive:
            return current
    io.say(f"    多次无效，采用默认 {_fmt(current)}")
    return current


# ==================================================================== 主流程

def _collect(io: IO, cfg: Config) -> Optional[Dict[str, Any]]:
    """逐项收集；返回 None 表示用户中止。"""
    out: Dict[str, Any] = {}
    cap = _ask_capital(io, portfolio.get_capital(cfg) or CAPITAL_FALLBACK)
    if cap is None:
        return None
    out["capital"] = cap
    for item in NUMERIC_ITEMS:
        v = _ask_number(io, item, _default_of(item, cfg), out)
        if v is None:
            return None
        out[item["key"]] = v
    io.say("")
    io.say("  · 板块交易权限　[permissions.*]")
    io.say("    没开通的板块请关掉：选出来买不进，等于白占当日唯一的新开名额。")
    for b in BOARD_ITEMS:
        cur = bool(cfg.get(f"permissions.{b['key']}", False))
        out[f"perm_{b['key']}"] = io.ask_yes(f"    交易{b['title']}",
                                            f"perm_{b['key']}", cur)
    srcs = _ask_sources(io, cfg)
    if srcs is None:
        return None
    out["data_sources"] = srcs
    return out


def _defaults_snapshot(cfg: Config) -> Dict[str, Any]:
    out: Dict[str, Any] = {"capital": portfolio.get_capital(cfg) or CAPITAL_FALLBACK}
    for item in NUMERIC_ITEMS:
        out[item["key"]] = _defaults_value(item["dotted"])
    for b in BOARD_ITEMS:
        out[f"perm_{b['key']}"] = bool(_defaults_value(f"permissions.{b['key']}"))
    out["data_sources"] = list(_defaults_value("market.data_sources") or [])
    return out


def format_summary(values: Dict[str, Any]) -> str:
    lines = ["", "===== 配置摘要（回车的项已取默认值）====="]
    lines.append(f"  总资金：{utils.money(values.get('capital'))}"
                 f"（{_fmt(values.get('capital'))} 元）　[capital_state.json]")
    for item in NUMERIC_ITEMS:
        lines.append(f"  {item['title']}：{_fmt(values.get(item['key']))}"
                     f"　[{item['dotted']}]")
    perms = "　".join(f"{b['title'].split('（')[0]} "
                      f"{'✓' if values.get('perm_' + b['key']) else '✗'}"
                      for b in BOARD_ITEMS)
    lines.append(f"  板块权限：{perms}　[permissions.*]")
    srcs = values.get("data_sources") or []
    lines.append(f"  数据源降级链：{' → '.join(srcs) or '—'}　[market.data_sources]")
    return "\n".join(lines)


def apply_values(values: Dict[str, Any], cfg: Config) -> str:
    """落盘：数值项写 tea_config.json，资金写 capital_state.json。

    顺序要紧：mark_initialized 会置 meta.initialized=True 并落盘，打过标就再也不
    自动进向导。若先打标、后写资金而资金落盘失败，就留下「配置已初始化、资金没写」
    的半成品且无法自愈。故把易失败的资金写在最前——资金成功了才写配置并打标，
    保证要么整体成功、要么整体像没跑过（下次照常重进向导）。
    """
    cap = utils.to_float(values.get("capital"))
    if cap and cap > 0:
        portfolio.set_capital(cap, cfg)
    for item in NUMERIC_ITEMS:
        if item["key"] in values:
            cfg.set(item["dotted"], values[item["key"]])
    for b in BOARD_ITEMS:
        k = f"perm_{b['key']}"
        if k in values:
            cfg.set(f"permissions.{b['key']}", bool(values[k]))
    srcs = values.get("data_sources")
    if srcs:
        cfg.set("market.data_sources", list(srcs))
    return mark_initialized(cfg)


def _welcome(io: IO, cfg: Config, first_run: bool) -> None:
    io.say("")
    io.say("=" * 56)
    if first_run:
        io.say("欢迎使用 XJB_TRADE (TEA)　—　首次启动配置")
        io.say("  这套系统的默认值按一套固定的资金与风险偏好调的，先花 1 分钟")
        io.say("  改成你自己的。只问 9 项，其余几百个参数留默认，随时可改。")
    else:
        io.say("重新运行配置向导")
        io.say("  当前值会作为默认值出现，回车即保留。")
    io.say(f"  配置文件：{cfg.path}")
    io.say("=" * 56)


def run_wizard(cfg: Optional[Config] = None, io: Optional[IO] = None,
               first_run: bool = True, use_defaults: bool = False) -> dict:
    """跑一遍向导。返回 {"mode", "saved", "values", "path"}。

    use_defaults=True 时不提问，直接按 DEFAULTS 落盘并打标记（脚本/自测用）。
    """
    cfg = cfg or load_config()
    io = io or IO()
    if use_defaults:
        values = _defaults_snapshot(cfg)
        path = apply_values(values, cfg)
        io.say(f"  ✓ 已按推荐默认值完成配置：{path}")
        return {"mode": MODE_DEFAULTS, "saved": True, "values": values, "path": path}

    _welcome(io, cfg, first_run)
    while True:
        io.say("")
        io.say("  1. 全部使用推荐默认值（10 秒开跑，之后再改）")
        io.say("  2. 逐项自定义（约 1 分钟，推荐）")
        io.say("  s. 跳过（不再自动提示，可从菜单重进）")
        raw = io.ask("请选择", "wizard_mode", "2")
        if raw is None:
            io.say("  已中断，配置未改动（下次启动会再问一次）")
            return {"mode": MODE_ABORT, "saved": False, "values": {}, "path": cfg.path}
        choice = utils.normalize_digits(str(raw).strip()).lower()
        if choice in ("s", "skip", "q"):
            path = mark_initialized(cfg, skipped=True)
            io.say("  已跳过，全部沿用默认值。想改随时用菜单「配置向导」或 `tea setup`。")
            return {"mode": MODE_SKIP, "saved": False, "values": {}, "path": path}
        if choice == "1":
            values, mode = _defaults_snapshot(cfg), MODE_DEFAULTS
        elif choice == "2":
            collected = _collect(io, cfg)
            if collected is None:
                io.say("  已中断，配置未改动（下次启动会再问一次）")
                return {"mode": MODE_ABORT, "saved": False, "values": {}, "path": cfg.path}
            values, mode = collected, MODE_CUSTOM
        else:
            io.say("  无此选项")
            if not io.interactive:
                return {"mode": MODE_ABORT, "saved": False, "values": {}, "path": cfg.path}
            continue

        io.say(format_summary(values))
        # 这一步不能借 ask_yes：它把 Ctrl-C/EOF 的 None 当成 False，会让用户
        # 在确认关口按 Ctrl-C 反被拽回循环重来。这里显式取 raw 区分「中断」与「否」。
        raw = io.ask("确认写入配置 [Y/n]", "wizard_confirm", "y")
        if raw is None:
            io.say("  已中断，配置未改动（下次启动会再问一次）")
            return {"mode": MODE_ABORT, "saved": False, "values": {}, "path": cfg.path}
        if str(raw).strip().lower() in ("y", "yes", "1", "true", "是"):
            path = apply_values(values, cfg)
            io.say("")
            io.say(f"  ✓ 配置已保存：{path}")
            io.say("  ✓ 资金已记入持仓状态（菜单 10 可查）")
            io.say("  之后想改：菜单「配置向导」、`tea setup`，或 `tea config set <键> <值>`")
            return {"mode": mode, "saved": True, "values": values, "path": path}
        io.say("  未保存，重新来一遍")
        if not io.interactive:
            return {"mode": mode, "saved": False, "values": values, "path": cfg.path}


def maybe_run(cfg: Optional[Config] = None, io: Optional[IO] = None) -> bool:
    """首次运行时引导；已初始化则什么都不做。返回是否跑过向导。"""
    cfg = cfg or load_config()
    if not is_first_run(cfg):
        return False
    run_wizard(cfg=cfg, io=io or IO(), first_run=True)
    return True
