"""道 — 市场天气：情绪评分 / 周期判定 / 交易姿态 / 冰点降仓。

数据并行 3 路：大盘指数（涨跌幅+MA20）、热点板块前 20、情绪硬指标（涨跌家数+最高连板）。
结果内存缓存 120 秒，同会话连评多只票不重复拉行情。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from . import utils
from .config_store import Config, load_config
from .data import Market

CYCLE_ICE = "冰点"
CYCLE_REPAIR = "修复"
CYCLE_FERMENT = "发酵"
CYCLE_MAIN = "主升"
CYCLE_CLIMAX = "高潮"
CYCLE_EBB = "退潮"

STANCE_EMPTY = "空仓"
STANCE_DEFEND = "防守"
STANCE_ATTACK = "进攻"

_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}


# ------------------------------------------------------------------ 采集

def fetch_raw(market: Market, io: Any = None) -> dict:
    """并行采集三路原始数据，单路失败不影响整体（降级为 None）。"""
    out: Dict[str, Any] = {"index": {}, "sectors": [], "breadth": {}, "limit_up": {}, "errors": []}
    labels = {"index": "大盘指数", "sectors": "板块排名", "hard": "涨跌家数/涨停池"}
    t0 = time.time()

    def _index():
        return market.get_index()

    def _sectors():
        return market.get_sector_ranking()

    def _hard():
        return market.get_breadth(), market.get_limit_up_stats()

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_idx, f_sec, f_hard = ex.submit(_index), ex.submit(_sectors), ex.submit(_hard)
        # 在主线程按固定顺序收结果再打印，避开三个线程抢着往屏上写。
        for name, fut in (("index", f_idx), ("sectors", f_sec), ("hard", f_hard)):
            try:
                res = fut.result()
            except Exception as exc:
                out["errors"].append(f"{name}: {exc}")
                utils.tell(io, f"    · {labels[name]} 失败：{exc}")
                continue
            utils.tell(io, f"    · {labels[name]} 就绪 ({time.time() - t0:.1f}s)")
            if name == "index":
                out["index"] = res or {}
            elif name == "sectors":
                out["sectors"] = res or []
            else:
                out["breadth"], out["limit_up"] = res[0] or {}, res[1] or {}
    return out


def summarize_sectors(sectors: List[dict], cfg: Config) -> dict:
    topn = int(cfg.get("sentiment.hot_sector_topn", 20))
    hot_chg = float(cfg.get("sentiment.hot_sector_chg", 3.0))
    top = sectors[:topn]
    hot = [s for s in top if (s.get("chg") or 0) >= hot_chg]
    top5 = sectors[:5]
    return {
        "top": top,
        "hot_sectors": hot,
        "hot_n": len(hot),
        "avg5": utils.mean([s.get("chg") for s in top5]),
        "top5": top5,
    }


# ------------------------------------------------------------------ 评分

def compute_score(raw: dict, cfg: Config) -> dict:
    """情绪评分（满分 100，基准 50），返回分数与逐项加减分明细。"""
    c = lambda k, d=None: cfg.get(f"sentiment.{k}", d)
    score = float(c("base_score", 50.0))
    deltas: List[dict] = []

    def add(label: str, delta: float, detail: str) -> None:
        nonlocal score
        score += delta
        deltas.append({"item": label, "delta": delta, "detail": detail})

    idx = raw.get("index") or {}
    sec = summarize_sectors(raw.get("sectors") or [], cfg)
    breadth = raw.get("breadth") or {}
    zt = raw.get("limit_up") or {}

    ratio = breadth.get("advance_ratio")
    if ratio is not None:
        if ratio < float(c("advance_low", 0.35)):
            add("涨跌比", float(c("advance_low_delta", -12)), f"{ratio:.1%} < 35%")
        elif ratio >= float(c("advance_high", 0.55)):
            add("涨跌比", float(c("advance_high_delta", 8)), f"{ratio:.1%} ≥ 55%")

    boards = zt.get("max_boards")
    if boards is not None:
        if boards <= int(c("boards_low", 2)):
            add("最高连板", float(c("boards_low_delta", -10)), f"{boards} 板 ≤ 2")
        elif boards >= int(c("boards_high", 5)):
            add("最高连板", float(c("boards_high_delta", 6)), f"{boards} 板 ≥ 5")

    if idx.get("ma20") is not None and idx.get("point") is not None:
        if idx.get("ma20_above"):
            add("上证MA20", float(c("ma20_above_delta", 12)), "指数在 MA20 上方")
        else:
            add("上证MA20", float(c("ma20_below_delta", -12)), "指数在 MA20 下方")

    ichg = idx.get("chg_pct")
    if ichg is not None:
        if ichg >= float(c("index_up_pct", 0.5)):
            add("上证涨幅", float(c("index_up_delta", 8)), f"{ichg:+.2f}% ≥ +0.5%")
        elif ichg <= float(c("index_down_pct", -0.8)):
            add("上证涨幅", float(c("index_down_delta", -10)), f"{ichg:+.2f}% ≤ -0.8%")

    hot_n = sec["hot_n"]
    if raw.get("sectors"):
        if hot_n >= int(c("hot_n_strong", 8)):
            add("热点板块数", float(c("hot_n_strong_delta", 15)), f"{hot_n} 个 ≥ 8")
        elif hot_n >= int(c("hot_n_mid", 4)):
            add("热点板块数", float(c("hot_n_mid_delta", 8)), f"{hot_n} 个 ≥ 4")
        elif hot_n <= int(c("hot_n_weak", 1)):
            add("热点板块数", float(c("hot_n_weak_delta", -10)), f"{hot_n} 个 ≤ 1")

    avg5 = sec["avg5"]
    if avg5 is not None:
        if avg5 >= float(c("avg5_overheat", 6.0)):
            add("前5板块均涨", float(c("avg5_overheat_delta", -8)), f"{avg5:.2f}% ≥ 6%（过热）")
        elif float(c("avg5_healthy_low", 2.0)) <= avg5 <= float(c("avg5_healthy_high", 5.0)):
            add("前5板块均涨", float(c("avg5_healthy_delta", 5)), f"{avg5:.2f}% 健康区间")

    score = utils.clamp(score, 0.0, float(c("max_score", 100.0)))
    return {
        "score": round(score, 1),
        "deltas": deltas,
        "sector_summary": sec,
        "advance_ratio": ratio,
        "max_boards": boards,
        "limit_up_count": zt.get("limit_up_count"),
        "limit_up_date": zt.get("date"),
        "limit_up_fallback": bool(zt.get("fallback")),
        "limit_up_error": zt.get("error"),
        "index": idx,
        "breadth": breadth,
    }


# ------------------------------------------------------------------ 周期/姿态

def classify(scored: dict, cfg: Config) -> dict:
    c = lambda k, d=None: cfg.get(f"sentiment.{k}", d)
    score = float(scored["score"])
    idx = scored.get("index") or {}
    sec = scored.get("sector_summary") or {}
    hot_n = sec.get("hot_n") or 0
    avg5 = sec.get("avg5")
    ma20_above = bool(idx.get("ma20_above"))
    # 指数取不到时 ma20_above 也是 False，门禁会拦住新开——对一个纪律引擎来说
    # 这个方向是对的，但不能把「不知道」写成「在下方」。
    ma20_known = idx.get("ma20") is not None and idx.get("point") is not None
    ichg = idx.get("chg_pct")
    notes: List[str] = []

    # 退潮：MA20 下 + 上证跌 + 热点仍多 → 高位分歧，额外 -5。
    # 「MA20 下」必须是确知在下方：kline 取不到时 ma20_above 也是 False，但那是「不知道」，
    # 不能据此编出「退潮」。位置未知时姿态照样落防守（见下 defend_why），但不谎报周期。
    ebb = (ma20_known and not ma20_above) and (ichg is not None and ichg < 0) and hot_n >= int(c("ebb_hot_n", 6))
    if ebb:
        score += float(c("ebb_extra_delta", -5.0))
        notes.append("退潮特征：MA20 下 + 指数下跌 + 热点未减，情绪分 -5")
        cycle = CYCLE_EBB
    else:
        if score < float(c("cycle_ice_below", 35)):
            cycle = CYCLE_ICE
        elif score < float(c("cycle_repair_below", 50)):
            cycle = CYCLE_REPAIR
        elif score < float(c("cycle_ferment_below", 70)):
            cycle = CYCLE_FERMENT
        else:
            cycle = CYCLE_MAIN
        if (avg5 is not None and avg5 >= float(c("climax_avg5", 6.0))
                and hot_n >= int(c("climax_hot_n", 10))):
            cycle = CYCLE_CLIMAX
            notes.append(f"高潮特征：前5板块均涨 {avg5:.2f}% + 热点 {hot_n} 个")

    score = utils.clamp(score, 0.0, float(c("max_score", 100.0)))

    # 交易姿态。提示只列真正触发的那几条：把三个候选原因一股脑印出来，会出现
    # 「情绪分 84」旁边写着「情绪分偏低」这种自相矛盾的画面，读的人只能靠猜。
    allow_new = True
    empty_why = []
    if score < float(c("stance_empty_below", 40)):
        empty_why.append(f"情绪分 {score:.1f} < 40")
    if cycle == CYCLE_ICE:
        empty_why.append("处于冰点")
    defend_why = []
    if score < float(c("stance_defend_below", 55)):
        defend_why.append(f"情绪分 {score:.1f} < 55")
    if not ma20_above:
        defend_why.append("上证在 MA20 下方" if ma20_known else "上证位置未知（指数取数失败）")
    if cycle == CYCLE_EBB:
        defend_why.append("退潮")

    if empty_why:
        stance = STANCE_EMPTY
        allow_new = False
        notes.append("姿态=空仓：" + " + ".join(empty_why))
    elif defend_why:
        stance = STANCE_DEFEND
        notes.append("姿态=防守：" + " + ".join(defend_why))
    else:
        stance = STANCE_ATTACK

    if cycle == CYCLE_CLIMAX and avg5 is not None and avg5 >= float(c("climax_block_avg5", 7.0)):
        allow_new = False
        notes.append(f"高潮易分歧：前5板块均涨 {avg5:.2f}% ≥ 7%，禁止新开")

    # 冰点降仓：最高连板 ≤3 且 全市场普跌
    boards = scored.get("max_boards")
    ratio = scored.get("advance_ratio")
    ice_cut = (boards is not None and boards <= int(c("ice_cut_boards", 3))
               and ratio is not None and ratio < float(c("ice_cut_advance", 0.35)))
    base_mult = float(c("ice_cut_mult", 0.25)) if ice_cut else 1.0
    if ice_cut:
        notes.append(f"冰点降仓：最高连板 {boards} ≤3 且涨跌比 {ratio:.1%} <35% → 半仓乘数 {base_mult}")

    return {
        "score": round(score, 1),
        "cycle": cycle,
        "stance": stance,
        "allow_new": allow_new,
        "ice_cut": ice_cut,
        "base_pos_mult": base_mult,
        "ma20_above": ma20_above,
        "ma20_known": ma20_known,
        "notes": notes,
    }


# ------------------------------------------------------------------ 门面

def get_sentiment(market: Optional[Market] = None, cfg: Optional[Config] = None,
                  force: bool = False, io: Any = None) -> dict:
    """市场天气总入口（带 120s 内存缓存）。"""
    cfg = cfg or load_config()
    ttl = float(cfg.get("sentiment.cache_sec", 120))
    if not force and _CACHE["data"] is not None and (time.time() - _CACHE["ts"]) <= ttl:
        cached = dict(_CACHE["data"])
        cached["cached"] = True
        return cached

    utils.tell(io, "  ⏳ 正在采集市场天气（指数 / 板块 / 涨停）...")
    t0 = time.time()
    mk = market or Market(cfg)
    raw = fetch_raw(mk, io)
    scored = compute_score(raw, cfg)
    cls = classify(scored, cfg)
    sec = scored["sector_summary"]
    out = {
        "ts": utils.now().strftime("%Y-%m-%d %H:%M:%S"),
        "score": cls["score"],
        "raw_score": scored["score"],
        "cycle": cls["cycle"],
        "stance": cls["stance"],
        "allow_new": cls["allow_new"],
        "ice_cut": cls["ice_cut"],
        "base_pos_mult": cls["base_pos_mult"],
        "ma20_above": cls["ma20_above"],
        "ma20_known": cls["ma20_known"],
        "index": scored["index"],
        "breadth": scored["breadth"],
        "advance_ratio": scored["advance_ratio"],
        "max_boards": scored["max_boards"],
        "limit_up_count": scored["limit_up_count"],
        "limit_up_date": scored["limit_up_date"],
        "limit_up_fallback": scored["limit_up_fallback"],
        "limit_up_error": scored["limit_up_error"],
        "hot_n": sec.get("hot_n"),
        "avg5": sec.get("avg5"),
        "hot_sectors": [{"name": s["name"], "chg": s["chg"], "rank": s["rank"]} for s in sec.get("hot_sectors", [])],
        "top5": [{"name": s["name"], "chg": s["chg"]} for s in sec.get("top5", [])],
        "deltas": scored["deltas"],
        "notes": cls["notes"],
        "errors": raw.get("errors", []),
        "cached": False,
    }
    _CACHE["ts"], _CACHE["data"] = time.time(), out
    # 收尾报一声谁供的数：上面可能刚刷过几行「改用腾讯」，这里给个总账。
    net = mk.stats_line() if hasattr(mk, "stats_line") else ""
    utils.tell(io, f"  ✓ 市场天气采集完成 ({time.time() - t0:.1f}s)" + (f"，{net}" if net else ""))
    return out


def clear_cache() -> None:
    _CACHE["ts"], _CACHE["data"] = 0.0, None


def format_weather(s: dict) -> str:
    """CLI 单屏市场天气。"""
    idx = s.get("index") or {}
    lines = [
        "===== 道 · 市场天气 =====",
        f"情绪分 {s['score']}  周期 {s['cycle']}  姿态 {s['stance']}  "
        f"新开 {'允许' if s['allow_new'] else '禁止'}",
        f"上证 {utils.num(idx.get('point'))} ({utils.pct(idx.get('chg_pct'))})  "
        f"MA20 {utils.num(idx.get('ma20'))} → "
        f"{('上方' if s.get('ma20_above') else '下方') if s.get('ma20_known') else '未知'}",
        f"涨跌比 {('%.1f%%' % (s['advance_ratio'] * 100)) if s.get('advance_ratio') is not None else '—'}"
        f"（涨 {utils.num((s.get('breadth') or {}).get('rising'), 0)} / 跌 {utils.num((s.get('breadth') or {}).get('falling'), 0)}）  "
        f"最高连板 {utils.num(s.get('max_boards'), 0)}  涨停 {utils.num(s.get('limit_up_count'), 0)} 家",
        f"热点板块 {s.get('hot_n')} 个  前5板块均涨 {utils.pct(s.get('avg5'))}  "
        f"半仓乘数(情绪) {s.get('base_pos_mult')}",
    ]
    if s.get("limit_up_error"):
        lines.append(f"! 数据缺口 {s['limit_up_error']}")
    # 非交易日（或盘前）涨停池会回退到上一个交易日。不标出日子的话，屏上就是
    # 一个无法分辨的数字——而它和旁边的板块涨幅到底是不是同一天，直接影响周期判断。
    if s.get("limit_up_fallback") and s.get("limit_up_date"):
        lines.append(f"· 涨停数据来自上一交易日 {s['limit_up_date']}")
    if (s.get("breadth") or {}).get("exact") is False:
        lines.append("· 涨跌家数探测预算用尽，上面的值是估值")
    if s.get("hot_sectors"):
        top = "  ".join(f"{x['name']}{x['chg']:+.2f}%" for x in s["hot_sectors"][:6])
        lines.append(f"热点：{top}")
    for n in s.get("notes", []):
        lines.append(f"· {n}")
    # 数据缺口告警只在「本次真去取数且失败」时报：命中 120s 缓存的重复展示不
    # 再刷告警（否则一次失败后每次进菜单都跳一遍）；同一批错误按内容去重只提一次。
    if not s.get("cached"):
        seen = set()
        for e in s.get("errors", []):
            if e in seen:
                continue
            seen.add(e)
            lines.append(f"! 数据缺口 {e}")
    return "\n".join(lines)
