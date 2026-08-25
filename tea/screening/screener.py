"""术+法 — 系统化选股：种子扫描四步流（14:30 核心流程）。

第1步 板块综合排序（热度分×0.65 + 温和票结构分×0.35，影子池 +18）
第2步 三档涨幅窗筛选（严格 / 热点降级 / 强势跟涨，含降级逻辑与硬过滤）
第3步 VETO 过滤（软否决→观察轨，硬否决→REJECT）
第4步 预审快照 + 三档输出（可买 / 待启动观察 / 近失）
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple

from tea.analysis import followthrough, identity as ident_mod
from tea.analysis.sentiment import get_sentiment
from tea.config.config_store import Config, load_config
from tea.core import utils
from tea.data import Market, count_limit_ups
from tea.portfolio import watch_pool
from tea.reporting import seed_trace
from . import preflight, veto as veto_mod

TIER_STRICT = "严格档"
TIER_RELAXED = "热点降级档"
TIER_MOMENTUM = "强势跟涨档"
TIER_SPROUT = "萌芽窗口"
TIER_EVE = "前夕观察"

VERDICT_TRADEABLE = "HAS_TRADEABLE"
VERDICT_PENDING = "PENDING"
VERDICT_EMPTY = "EMPTY"

# 候选明细裁决（报告里逐只可追溯，含被 continue 丢弃的硬否决/数据缺）
CAND_BUYABLE = "可买（追高）"
CAND_WATCH = "观察轨"
CAND_NEAR = "近失"
CAND_HARD = "硬否决"
CAND_SOFT = "软否决"
CAND_ERROR = "数据缺"
CAND_SKIPPED = "未预审"
CAND_CHG_OUT = "涨幅移出窗口"


# ------------------------------------------------------------------ 档位参数

def dynamic_min_chg(max_sector_chg: Optional[float], base_min: float = 3.0,
                    floor: float = 2.0, strong_chg: float = 5.0,
                    weak_chg: float = 4.0) -> float:
    """涨幅窗口下限按当日最强板块涨幅自适应。

    固定 3% 下限在弱市里会系统性空仓：最强板块只涨 3% 的一天，几乎没有个股
    站得上 3%，于是三档全空、天天「宁缺毋滥」。按最强板块分档下调：
    ≥5%（标准/强市）保持基准；4%~5% 下调 0.5；<4%（弱市）落到地板。
    地板不能再破——止损空间压不下去，R:R 就撑不到 ≥3。

    max_sector_chg 为 None（板块涨幅未知）时不下调，按基准值走。
    """
    if max_sector_chg is None or max_sector_chg >= strong_chg:
        return base_min
    mid = max(floor + 0.5, base_min - 0.5)
    return min(base_min, mid if max_sector_chg >= weak_chg else floor)


def dyn_min_chg(cfg: Config, max_sector_chg: Optional[float],
                base_key: str = "strict_min_chg", base_default: float = 3.0) -> float:
    """按配置算动态下限（分档阈值与地板可调）；关闭动态化时退回配置基准值。"""
    base = float(cfg.get(f"seed.{base_key}", base_default))
    if max_sector_chg is None or not cfg.get("seed.dyn_min_chg_enabled", True):
        return base
    return dynamic_min_chg(max_sector_chg, base,
                           float(cfg.get("seed.dyn_min_chg_floor", 2.0)),
                           float(cfg.get("seed.dyn_strong_sector_chg", 5.0)),
                           float(cfg.get("seed.dyn_weak_sector_chg", 4.0)))


def dyn_window(cfg: Config, max_sector_chg: Optional[float]) -> dict:
    """动态窗口快照：供扫描输出/报告展示当前实际生效的下限。"""
    base = float(cfg.get("seed.strict_min_chg", 3.0))
    lo = dyn_min_chg(cfg, max_sector_chg)
    return {"min_chg": lo, "base_min": base, "max_sector_chg": max_sector_chg,
            "lowered": lo < base}


def dyn_window_text(win: dict) -> str:
    """动态窗口一行文案：下限 2.5%（最强板块 +4.3%，标准 3.0% → 下调至 2.5%）。"""
    chg = win.get("max_sector_chg")
    ref = f"最强板块 {chg:+.2f}%" if chg is not None else "最强板块 —"
    tail = (f"标准 {win['base_min']:.1f}% → 下调至 {win['min_chg']:.1f}%"
            if win.get("lowered") else f"标准 {win['base_min']:.1f}% 保持")
    return f"下限 {win['min_chg']:.1f}%（{ref}，{tail}）"


def tier_params(tier: str, cfg: Config, max_sector_chg: Optional[float] = None) -> dict:
    c = lambda k, d=None: cfg.get(f"seed.{k}", d)
    strict_min = dyn_min_chg(cfg, max_sector_chg)
    if tier == TIER_STRICT:
        return {"name": tier, "min_chg": strict_min, "max_chg": float(c("strict_max_chg", 5.5)),
                "min_identity": float(c("strict_min_identity", 70)), "min_pick": float(c("strict_min_pick", 60)),
                "rank_pct": float(c("strict_rank_pct", 0.50)), "min_turnover": float(c("strict_min_turnover", 2.0)),
                "cap_max": float(c("cap_max", 300.0)), "note": "温和突破，首选"}
    if tier == TIER_RELAXED:
        return {"name": tier, "min_chg": dyn_min_chg(cfg, max_sector_chg, "relaxed_min_chg"),
                "max_chg": float(c("relaxed_max_chg", 7.5)),
                "min_identity": float(c("relaxed_min_identity", 65)), "min_pick": float(c("relaxed_min_pick", 62)),
                "rank_pct": float(c("relaxed_rank_pct", 0.40)), "min_turnover": float(c("relaxed_min_turnover", 3.0)),
                "cap_max": float(c("relaxed_cap_max", 300.0)), "note": "相对跟涨，仍拒杂毛"}
    if tier == TIER_MOMENTUM:
        return {"name": tier, "min_chg": float(c("momentum_min_chg", 5.0)),
                "max_chg": float(c("momentum_max_chg", 8.8)),
                "min_identity": float(c("momentum_min_identity", 68)),
                "min_identity_hot": float(c("momentum_min_identity_hot", 62)),
                "min_pick": float(c("momentum_min_pick", 60)),
                "rank_pct": float(c("momentum_rank_pct", 0.48)), "min_turnover": float(c("strict_min_turnover", 2.0)),
                "cap_max": float(c("cap_max", 300.0)), "note": "前排 momentum"}
    if tier == TIER_SPROUT:
        return {"name": tier, "min_chg": strict_min, "max_chg": float(c("strict_max_chg", 5.5)),
                "min_identity": float(c("relaxed_min_identity", 65)), "min_pick": float(c("strict_min_pick", 60)) - 5,
                "rank_pct": float(c("relaxed_rank_pct", 0.40)), "min_turnover": float(c("strict_min_turnover", 2.0)),
                "cap_max": float(c("cap_max", 300.0)), "note": "萌芽窗口（严格窗内非涨停前排）"}
    # 前夕观察上限跟着动态下限收：下限降到 2.5% 时 1%~3% 的前夕窗会被严格窗盖住一截，
    # 同一只票两边都进，「等它涨入严格窗」的触发条件当场自相矛盾。
    eve_min = float(c("eve_min_chg", 1.0))
    eve_max = max(eve_min, min(float(c("eve_max_chg", 3.0)), strict_min))
    return {"name": TIER_EVE, "min_chg": eve_min, "max_chg": eve_max,
            "min_identity": 0.0, "min_pick": 0.0, "rank_pct": float(c("relaxed_rank_pct", 0.40)),
            "min_turnover": float(c("front_row_min_turnover", 1.5)), "cap_max": float(c("cap_max", 300.0)),
            "note": "前夕观察（仅观察，永不写计划）"}


# ------------------------------------------------------------------ 系统分

def _bracket_score(value: Optional[float], brackets: List[dict], none_score: float) -> float:
    """分档取分：value 落入首个 [min, max] 区间取其 score；无值回落 none_score；
    全不命中回落最后一档（兜底档）。"""
    if value is None:
        return float(none_score)
    for b in (brackets or []):
        if float(b.get("min", 0.0)) <= value <= float(b.get("max", 0.0)):
            return float(b.get("score", 0.0))
    return float(brackets[-1].get("score", 0.0)) if brackets else 0.0


def pick_score(member: dict, sector: dict, cfg: Config,
               min_chg: Optional[float] = None) -> dict:
    """系统分（0~100）：板块位置 35 + 板块内位置 25 + 涨幅贴合 20 + 换手 10 + 市值 10。

    min_chg 给「涨幅贴合」的下沿，缺省用配置基准值；动态下限下调时要一起传进来，
    否则窗口放宽后进来的票会在这一项上被扣分，白放宽。
    """
    p = lambda k, d=None: cfg.get(f"seed.pick.{k}", d)
    parts: Dict[str, float] = {}
    s_rank = sector.get("rank") or 99
    sec_w = float(p("sector_position_weight", 35.0))
    sec_denom = float(p("sector_rank_denom", 30.0))
    parts["板块位置"] = utils.clamp(sec_w * (1.0 - (s_rank - 1) / sec_denom), 0.0, sec_w)

    rank_pct = member.get("rank_pct")
    inner_w = float(p("inner_position_weight", 25.0))
    inner_default = float(p("inner_default_pct", 0.6))
    parts["板块内位置"] = utils.clamp(
        inner_w * (1.0 - (rank_pct if rank_pct is not None else inner_default)), 0.0, inner_w)

    lo = float(cfg.get("seed.strict_min_chg", 3.0)) if min_chg is None else float(min_chg)
    hi = float(cfg.get("seed.strict_max_chg", 5.5))
    chg_w = float(p("chg_weight", 20.0))
    chg_penalty = float(p("chg_penalty_per_pct", 5.0))
    chg = member.get("chg")
    if chg is None:
        parts["涨幅贴合"] = 0.0
    elif lo <= chg <= hi:
        parts["涨幅贴合"] = chg_w
    else:
        over = (chg - hi) if chg > hi else (lo - chg)
        parts["涨幅贴合"] = utils.clamp(chg_w - over * chg_penalty, 0.0, chg_w)

    parts["换手"] = _bracket_score(
        member.get("turnover"), p("turnover_score_brackets", []), p("turnover_none_score", 5.0))

    parts["市值"] = _bracket_score(
        member.get("cap_yi"), p("cap_score_brackets", []), p("cap_none_score", 5.0))

    total = sum(parts.values())
    return {"score": round(total, 1), "parts": {k: round(v, 1) for k, v in parts.items()}}


# ------------------------------------------------------------------ VETO 明细

def veto_detail(items: List[dict]) -> str:
    """VETO 追踪明细：每项展开为“条件（当前值 / 阈值）”，无值项退回否决说明。"""
    out: List[str] = []
    for it in items:
        v, th = it.get("value"), it.get("threshold")
        if v is None or th is None:
            out.append(f"{it['label']}（{it.get('detail') or '—'}）")
            continue
        # 分时位置是 0~1 比例，其余（涨幅 / 换手 / 乖离）是百分数
        fmt = ((lambda x: f"{x:.0%}") if str(it.get("name") or "").startswith("intraday")
               else (lambda x: f"{x:.2f}%"))
        out.append(f"{it['label']}（当前 {fmt(v)} / 阈值 {fmt(th)}）")
    return "；".join(out)


# ------------------------------------------------------------------ 候选明细

def candidate_row(cand: dict, ev: Optional[dict] = None,
                  verdict: str = "", reason: str = "") -> dict:
    """候选明细一行：初筛信息 + 预审裁决 + 淘汰原因。

    硬否决/数据缺的候选在 veto_filter 里被 continue 丢弃，不进任何输出桶；
    这份明细是它们唯一的出口，否则用户只能看到「4 个桶全空」。
    """
    ev = ev or {}
    idn = ev.get("identity") or cand.get("identity") or {}
    q = ev.get("quote") or {}
    ind = ev.get("ind") or {}
    lv = ev.get("levels") or {}
    sc = ev.get("scoring") or {}
    vt = ev.get("veto") or {}
    chg = q.get("chg_pct")
    # 数据积累：把预审的关键指标与 9 分共振逐维拆解一并落进 scan_details，
    # 供日后复盘「哪一维在系统性拖分、R:R 卡在哪」，据此迭代指标与阈值。
    return {
        "code": cand.get("code"), "name": cand.get("name"),
        "sector_name": cand.get("sector_name"), "tier_label": cand.get("tier"),
        "chg": chg if chg is not None else cand.get("chg"),
        "intraday": ev.get("intraday"),
        "score": ev.get("total_score"), "threshold": ev.get("pass_threshold"),
        "identity_tier": idn.get("tier"), "identity_score": idn.get("score"),
        # 技术/量价指标（评分与 VETO 的输入）
        "atr_pct": ind.get("atr_pct"),
        "bias_ma20": ind.get("bias_ma20"),
        "vol_ratio": q.get("vol_ratio"),
        "amount_yi": q.get("amount_yi"),
        "turnover": q.get("turnover"),
        "cap_yi": q.get("cap_yi"),
        # 止损止盈/R:R（评分维度⑥与盈亏比门槛的输出）
        "sl_pct": lv.get("sl_pct"), "tp_pct": lv.get("tp_pct"),
        "odds": lv.get("odds"), "min_odds": lv.get("min_odds"),
        # 9 分共振逐维拆解。带上 detail：中性化等特殊状态（如消息面 0/0）要靠它
        # 才能在 scan_details 里被复盘识别出来。
        "scoring_dims": [{"name": d.get("name"), "score": d.get("score"),
                          "max": d.get("max"), "detail": d.get("detail")}
                         for d in sc.get("dims", [])],
        # 会话/否决留痕
        "in_session": ev.get("in_session"),
        "intraday_skipped": vt.get("intraday_skipped"),
        "intraday_note": vt.get("intraday_note") or None,
        "strong_exempt": vt.get("strong_exempt"),
        "identity_flags": idn.get("flags") or [],
        "veto_labels": [i.get("label") for i in vt.get("items", [])],
        "verdict": verdict, "reason": reason,
    }


def finalize_candidates(details: List[dict], evaluations: List[dict],
                        dropped_codes: Optional[set] = None) -> List[dict]:
    """把 VETO 通过者的最终裁决（可买/观察轨/近失）回填到候选明细。"""
    dropped = dropped_codes or set()
    pending = {d["code"]: d for d in details if not d.get("verdict")}
    for ev in evaluations:
        d = pending.get(ev.get("code"))
        if d is None:
            continue
        d["intraday"] = ev.get("intraday")
        d["score"], d["threshold"] = ev.get("total_score"), ev.get("pass_threshold")
        if ev.get("verdict") == preflight.VERDICT_PASS:
            d["verdict"] = CAND_BUYABLE
            d["reason"] = (f"共振 {ev.get('total_score')}/{ev.get('pass_threshold')} 达标"
                           + ("（超单日输出上限，未写计划）" if ev.get("code") in dropped else ""))
        elif ev.get("track") == watch_pool.TRACK_PENDING:
            d["verdict"] = CAND_WATCH
            d["reason"] = (ev.get("winrate_gate")
                           or f"共振 {ev.get('total_score')}/{ev.get('pass_threshold')} 差 {ev.get('gap')} 分 → 待启动")
        else:
            d["verdict"] = CAND_NEAR
            d["reason"] = "；".join(ev.get("reasons") or []) or "共振分不足"
    for d in details:
        if not d.get("verdict"):
            d["verdict"] = CAND_NEAR
            d["reason"] = d.get("reason") or "预审完成但未进入任何输出档"
    return details


# ------------------------------------------------------------------ 影子池

try:  # Unix 才有 fcntl；Windows 下降级为无锁（保持原行为）
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - 仅 Windows
    _fcntl = None


@contextmanager
def _shadow_lock(cfg: Config, exclusive: bool) -> Iterator[None]:
    """影子池文件锁：防两个 seed-plan 进程互相覆盖 shadow_pool.json。

    锁加在旁路 sidecar（.lock）上而不是 json 本身：写入走 atomic_write（os.replace），
    目标 inode 会被换掉，锁在本体上护不住。读取取共享锁，写入取独占锁。
    拿不到锁（无 fcntl / 文件系统不支持）时不阻断业务，退回无锁。
    """
    if _fcntl is None:
        yield
        return
    lock_path = cfg.data_file("shadow_pool_file") + ".lock"
    fh = None
    try:
        utils.ensure_dir(os.path.dirname(os.path.abspath(lock_path)))
        fh = open(lock_path, "a+")
        _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX if exclusive else _fcntl.LOCK_SH)
    except OSError:
        if fh is not None:
            fh.close()
            fh = None
    try:
        yield
    finally:
        if fh is not None:
            try:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
            except OSError:
                pass
            fh.close()


def load_shadow(cfg: Config) -> dict:
    with _shadow_lock(cfg, exclusive=False):
        return (utils.read_json(cfg.data_file("shadow_pool_file"), default=None)
                or {"date": None, "sectors": []})


def save_shadow(cfg: Config, sectors: List[dict]) -> str:
    with _shadow_lock(cfg, exclusive=True):
        return utils.write_json(cfg.data_file("shadow_pool_file"), {
            "date": utils.today_str(),
            "sectors": [{"bk": s["bk"], "name": s["name"], "score": s["total_score"]} for s in sectors],
        })


class Screener:
    """种子扫描器。"""

    def __init__(self, cfg: Optional[Config] = None, market: Optional[Market] = None):
        self.cfg = cfg or load_config()
        self.mk = market or Market(self.cfg)

    # ============================================================== 第 1 步
    def rank_sectors(self, tracer: Optional[seed_trace.Tracer] = None,
                     io: Any = None) -> dict:
        cfg = self.cfg
        c = lambda k, d=None: cfg.get(f"seed.{k}", d)
        topn = int(c("sector_scan_topn", 30))
        utils.tell(io, "  ⏳ 获取板块排名...")
        with utils.timed("板块排名", io, threshold=0.5):
            sectors = self.mk.get_sector_ranking()[:topn]
        # 板块排名是选股的根：一旦是磁盘兜底（昨天的排序），选股方向可能整个错掉，
        # 必须醒目告警，而不是闷头用一份旧数据跑完整套扫描。
        if getattr(self.mk, "sector_stale", False):
            utils.tell(io, "  ⚠️  板块排名实时取数失败，正使用磁盘兜底缓存（可能是昨日数据，"
                          "板块强度已不计入共振分，选股方向仅供参考，建议稍后重跑 seed）")
        # 当日最强板块涨幅（取自排名表本身，比 TOP3 入池后的最大值更早拿到）：
        # 温和票结构分要在遍历成分股前就知道窗口下限。
        strongest = max([s.get("chg") or 0.0 for s in sectors], default=0.0)
        # 温和票的定义跟选股窗口对齐：窗口降到 2.5% 而结构分还按 3% 数，
        # 会把真正有温和票的板块当成「无温和票」剔出去。
        mild_low = dyn_min_chg(cfg, strongest, "mild_chg_low")
        shadow = load_shadow(cfg)
        shadow_names = {s.get("bk") for s in shadow.get("sectors", [])}
        scored: List[dict] = []

        # 每个板块的成分股都要单独拉（大板块还要翻页），这里是种子扫描最慢的一段。
        total_n_sectors = len(sectors)
        utils.tell(io, f"  ⏳ 扫描 {total_n_sectors} 个板块的成分股...")
        for i, s in enumerate(sectors, 1):
            t0 = time.time()
            try:
                members = self.mk.get_sector_members(s["bk"])
            except Exception as exc:
                if tracer:
                    tracer.add_sector(s["name"], "板块成分股列表拉取失败", str(exc), bk=s["bk"])
                utils.tell(io, f"    · [{i}/{total_n_sectors}] {s['name']} 拉取失败")
                continue
            if not members:
                if tracer:
                    tracer.add_sector(s["name"], "成分股为空", bk=s["bk"])
                utils.tell(io, f"    · [{i}/{total_n_sectors}] {s['name']} 成分股为空")
                continue
            utils.tell(io, f"    · [{i}/{total_n_sectors}] {s['name']} {len(members)} 只"
                           f" ({time.time() - t0:.1f}s)")
            total_n = len(members)
            # 只统计「可交易成员」的涨停/上涨/温和票：不可交易的创业板/北交所/科创板
            # 成分股再多也不该让板块进 TOP（否则选出的板块买不了，白占板块配额）。
            # 分母仍用 total_n，让「可交易成分股占比低」的板块结构分自然偏低。
            tradeable = [m for m in members if veto_mod.board_allowed(m.get("code", ""), cfg)]
            limit_ups = count_limit_ups(tradeable)
            ups = sum(1 for m in tradeable if (m.get("chg") or 0) > 0)
            mild = [m for m in tradeable
                    if m.get("chg") is not None and mild_low <= m["chg"] <= float(c("mild_chg_high", 5.5))
                    and m.get("cap_yi") is not None
                    and float(c("mild_cap_low", 50.0)) <= m["cap_yi"] <= float(c("mild_cap_high", 300.0))
                    and m["chg"] < utils.limit_up_pct(m.get("code", ""), m.get("name", "")) - float(c("mild_chg_below_limit_up", 0.2))]
            mild_ratio = len(mild) / total_n if total_n else 0.0

            rank_score = max(0.0, float(c("rank_score_base", 40.0)) - s["rank"] * float(c("rank_score_step", 3.0)))
            zt_score = min(limit_ups / float(c("limit_up_score_div", 5.0)) * float(c("limit_up_score_cap", 35.0)),
                           float(c("limit_up_score_cap", 35.0)))
            chg_score = (float(c("chg_score_value", 25.0))
                         if float(c("chg_score_low", 2.0)) <= (s.get("chg") or 0) <= float(c("chg_score_high", 8.0))
                         else 0.0)
            heat = rank_score + zt_score + chg_score
            mild_score = min(mild_ratio / float(c("mild_target_ratio", 0.20)) * float(c("mild_score_max", 100.0)),
                             float(c("mild_score_max", 100.0)))
            total_score = heat * float(c("heat_weight", 0.65)) + mild_score * float(c("mild_weight", 0.35))

            shadow_bonus = float(c("shadow_bonus", 18.0)) if s["bk"] in shadow_names else 0.0
            total_score += shadow_bonus

            entry = {
                **s, "members": members, "member_total": total_n, "limit_up_count": limit_ups,
                "tradeable_n": len(tradeable),
                "up_ratio": ups / total_n if total_n else None,
                "mild_n": len(mild), "mild_ratio": round(mild_ratio, 4),
                "heat_score": round(heat, 1), "mild_score": round(mild_score, 1),
                "shadow_bonus": shadow_bonus, "total_score": round(total_score, 1),
                "has_mild": bool(mild),
            }
            scored.append(entry)

        # 板块硬门槛：排名 ≤8 且 涨停 ≥2 家（或 涨停1家 + 综合分 ≥60）
        # 弱市补充通道：0 涨停但综合分 ≥65 且排名前 12（弱市中好板块常无涨停）
        min_rank = int(cfg.s("seed_min_sector_rank", 8))
        min_zt = int(cfg.s("seed_min_sector_limit_up", 2))
        relax_score = float(c("sector_relax_score", 60.0))
        relax_rank = int(c("sector_relax_rank", 5))
        relax_score_nozt = float(c("sector_relax_score_nozt", 65.0))
        relax_rank_nozt = int(c("sector_relax_rank_nozt", 12))
        qualified = []
        for e in scored:
            ok_hard = e["rank"] <= min_rank and e["limit_up_count"] >= min_zt
            ok_relax = (e["limit_up_count"] >= 1 and e["total_score"] >= relax_score
                        and e["rank"] <= relax_rank)
            ok_nozt = e["limit_up_count"] == 0 and e["total_score"] >= relax_score_nozt and e["rank"] <= relax_rank_nozt
            if ok_hard or ok_relax or ok_nozt:
                if ok_hard:
                    e["gate"] = "硬门槛"
                elif ok_relax:
                    e["gate"] = f"放宽（涨停1家+综合分≥{relax_score:.0f} 且排名≤{relax_rank}）"
                else:
                    e["gate"] = f"放宽（综合分≥{relax_score_nozt:.0f} 无涨停）"
                qualified.append(e)
            elif tracer:
                tracer.add_sector(e["name"], "板块硬门槛不足",
                                  f"排名 {e['rank']}（需≤{min_rank}）涨停 {e['limit_up_count']} 家"
                                  f"（需≥{min_zt}）综合分 {e['total_score']}", bk=e["bk"])

        qualified.sort(key=lambda x: -x["total_score"])
        want = int(cfg.s("seed_top_sectors", 3))
        top = qualified[:want]

        # 多元化：若 TOP 全无温和票，则替换末位为最佳有温和票板块。
        # 替换候选也必须满足可买板块排名上限，避免中游板块借「有温和票」漏进可买池。
        if top and not any(t["has_mild"] for t in top) and cfg.get("seed.diversify_replace_last", True):
            cand = next((e for e in qualified[want:]
                         if e["has_mild"] and (e.get("rank") or 99) <= min_rank), None)
            if cand:
                dropped = top[-1]
                top = top[:-1] + [cand]
                if tracer:
                    tracer.note(f"多元化替换：{dropped['name']}（无温和票）→ {cand['name']}"
                                f"（温和票 {cand['mild_n']} 只，排名 {cand.get('rank')}≤{min_rank}）")

        # 影子池：本次近榜板块留给次日 +18
        near = int(c("shadow_near_rank", 6))
        save_shadow(cfg, qualified[:near])

        max_chg = max([t.get("chg") or 0 for t in top], default=0.0)
        if tracer:
            tracer.note(f"板块综合排序：候选 {len(scored)} → 达标 {len(qualified)} → TOP{len(top)}"
                        f"（最强涨幅 {max_chg:.2f}%）")
        return {"top": top, "qualified": qualified, "scored": scored, "max_sector_chg": max_chg,
                "strongest_sector_chg": strongest, "mild_chg_low": mild_low}

    # ============================================================== 第 2 步
    def screen_tier(self, sectors: List[dict], tier: str,
                    tracer: Optional[seed_trace.Tracer] = None,
                    max_sector_chg: Optional[float] = None) -> List[dict]:
        """单档涨幅窗筛选（硬过滤 + 身份/系统分门槛）。"""
        cfg = self.cfg
        p = tier_params(tier, cfg, max_sector_chg)
        dyn_lo = dyn_min_chg(cfg, max_sector_chg)
        out: List[dict] = []
        cap_min = float(cfg.get("seed.cap_min", 30.0))
        to_max = float(cfg.get("seed.turnover_max", 20.0))
        front_k = int(cfg.get("seed.front_row_topk", 3))
        front_to = float(cfg.get("seed.front_row_min_turnover", 1.5))
        hot_chg = float(cfg.s("seed_hot_sector_chg", 6.0))
        # 跨板块去重：一只票常同时属于多个 TOP 板块，不去重会被重复预审
        # （拉两次行情、日志里出现两次）——以首次入选的板块为准。
        seen: Dict[str, str] = {}
        dup_n = 0

        for sec in sectors:
            members = sec.get("members") or []
            total_n = len(members)
            hot = (sec.get("chg") or 0) >= hot_chg
            front_sector = (sec.get("rank") or 99) <= int(cfg.get("identity.hot_front_sector_rank", 8))
            for m in members:
                code, name, chg = m.get("code"), m.get("name") or "", m.get("chg")
                rank_pct = (m.get("rank") / total_n) if (m.get("rank") and total_n) else None
                m = {**m, "rank_pct": rank_pct}
                trace = (lambda reason, detail="": tracer.add(
                    seed_trace.STEP_WINDOW, code, name, reason, detail, tier=tier,
                    sector=sec.get("name"), chg=chg) if tracer else None)

                if code in seen:
                    dup_n += 1
                    trace("跨板块重复", f"已在「{seen[code]}」入选，不重复预审")
                    continue

                if utils.is_st(name):
                    trace("ST 过滤", name)
                    continue
                if not veto_mod.board_allowed(code, cfg):
                    trace("板块无权限", veto_mod.BOARD_NAMES.get(utils.board_of(code), "?"))
                    continue
                near = float(cfg.s("veto_near_limit_pct", 9.0)) * (utils.limit_up_pct(code, name) / float(cfg.get("veto.limit_up_pct_base", 10.0)))
                if chg is not None and chg >= near:
                    trace("涨停/接近涨停", f"涨幅 {chg:.2f}% ≥ {near:.2f}%")
                    continue
                if chg is None or not (p["min_chg"] <= chg <= p["max_chg"]):
                    trace("涨幅不在窗口", f"涨幅 {utils.pct(chg)} 不在 {p['min_chg']}~{p['max_chg']}%")
                    continue
                cap = m.get("cap_yi")
                if cap is None or cap < cap_min or cap > p["cap_max"]:
                    trace("市值不合格", f"{utils.num(cap)}亿 需 {cap_min:.0f}~{p['cap_max']:.0f}亿")
                    continue
                to = m.get("turnover")
                min_to = p["min_turnover"]
                if front_sector and (m.get("rank") or 99) <= front_k:
                    min_to = min(min_to, front_to)
                if to is None or to < min_to or to > to_max:
                    trace("换手不合格", f"{utils.num(to)}% 需 {min_to}~{to_max}%")
                    continue
                if rank_pct is None or rank_pct > p["rank_pct"]:
                    trace("板块内排名靠后", f"前 {rank_pct:.0%} 需 ≤{p['rank_pct']:.0%}"
                          if rank_pct is not None else "排名未知")
                    continue

                sec_ctx = {**{k: v for k, v in sec.items() if k != "members"},
                           "stock_rank": m.get("rank"), "stock_rank_pct": rank_pct,
                           "member_total": total_n, "found": True}
                pseudo_quote = {"code": code, "name": name, "chg_pct": chg, "cap_yi": cap,
                                "turnover": to, "is_st": utils.is_st(name),
                                "limit_up_pct": utils.limit_up_pct(code, name)}
                idn = ident_mod.judge(pseudo_quote, sec_ctx, None, cfg)
                min_id = p["min_identity"]
                if tier == TIER_MOMENTUM and hot:
                    min_id = p.get("min_identity_hot", min_id)
                elif hot and tier == TIER_RELAXED:
                    min_id = min(min_id, float(cfg.get("seed.hot_identity_relax", 62)))
                if ident_mod.is_zamao(idn):
                    trace("杂毛过滤", f"身份分 {idn['score']}（{idn['tier']}）标记 {len(idn['flags'])} 项")
                    continue
                if idn["score"] < min_id:
                    trace("身份分不足", f"身份分 {idn['score']} < {min_id:.0f}")
                    continue
                ps = pick_score(m, sec_ctx, cfg, min_chg=dyn_lo)
                if ps["score"] < p["min_pick"]:
                    trace("系统分不足", f"系统分 {ps['score']} < {p['min_pick']:.0f}（{ps['parts']}）")
                    continue

                out.append({
                    "code": code, "name": name, "chg": chg, "turnover": to, "cap_yi": cap,
                    "rank": m.get("rank"), "rank_pct": rank_pct,
                    "sector": sec_ctx, "sector_name": sec.get("name"), "sector_rank": sec.get("rank"),
                    "sector_chg": sec.get("chg"), "identity": idn, "pick": ps,
                    "tier": tier, "hot_sector": hot,
                    # 入选板块戳记：多板块归属时以本次筛入板块为准，供可买一致性校验。
                    "pick_sector_bk": sec.get("bk"),
                    "pick_sector_name": sec.get("name"),
                    "pick_sector_rank": sec.get("rank"),
                    # 第 3 步拉到实时行情后要用最新涨幅再核一次窗口（见 veto_filter），
                    # 这里把当初筛入时用的窗口下/上限记下来，避免批量快照涨幅与实时行情的偏差。
                    "win_min": p["min_chg"], "win_max": p["max_chg"],
                })
                seen[code] = sec.get("name") or "?"
        out.sort(key=lambda x: (-x["identity"]["score"], -x["pick"]["score"]))
        if tracer:
            tracer.note(f"{tier}：初筛通过 {len(out)} 只"
                        + (f"（跨板块去重 {dup_n} 只）" if dup_n else ""))
        return out

    def screen_with_downgrade(self, sectors: List[dict], max_sector_chg: float,
                              tracer: Optional[seed_trace.Tracer] = None,
                              strongest_sector_chg: Optional[float] = None
                              ) -> Tuple[List[dict], str, List[str]]:
        """三档降级逻辑：普涨日跳过严格档；逐档降级；三档全空则扫萌芽窗口。"""
        cfg = self.cfg
        notes: List[str] = []
        hot_chg = float(cfg.s("seed_hot_sector_chg", 6.0))
        dyn_ref = strongest_sector_chg if strongest_sector_chg is not None else max_sector_chg
        notes.append("动态窗口：" + dyn_window_text(dyn_window(cfg, dyn_ref)))
        order = [TIER_STRICT, TIER_RELAXED, TIER_MOMENTUM]
        # 跳过严格档的判据要和动态窗口用同一个「最强板块涨幅」（都取自排名表首位，
        # 即 strongest_sector_chg），而不是 TOP3 入池后的最大值——否则两条备注会印出
        # 两个不同的「最强板块涨幅」（实测 13.56% vs 11.10%）。
        if dyn_ref >= hot_chg:
            order = [TIER_RELAXED, TIER_MOMENTUM]
            notes.append(f"板块最强涨幅 {dyn_ref:.2f}% ≥{hot_chg:.0f}% → 跳过严格档（普涨日严格窗必空）")
        for tier in order:
            cands = self.screen_tier(sectors, tier, tracer, max_sector_chg=dyn_ref)
            if cands:
                notes.append(f"启用 {tier}，候选 {len(cands)} 只")
                return cands, tier, notes
            notes.append(f"{tier} 0 只 → 降级")
        if cfg.get("seed.sprout_scan_enabled", True):
            cands = self.screen_tier(sectors, TIER_SPROUT, tracer, max_sector_chg=dyn_ref)
            if cands:
                notes.append(f"三档全空 → 萌芽窗口扫描，候选 {len(cands)} 只")
                return cands, TIER_SPROUT, notes
        notes.append("三档 + 萌芽窗口全空 → 主动不开新仓（宁缺毋滥）")
        return [], TIER_STRICT, notes

    # ============================================================== 第 3 步
    def veto_filter(self, candidates: List[dict], sent: Optional[dict],
                    tracer: Optional[seed_trace.Tracer] = None, io: Any = None) -> dict:
        """逐只拉行情做 VETO：软否决→观察轨，硬否决→REJECT。

        不论裁决如何，每只候选都进 candidates 明细（供报告透明展示）。
        """
        cfg = self.cfg
        cap = int(cfg.get("seed.candidate_fetch_cap", 30))
        passed: List[dict] = []
        soft: List[dict] = []
        details: List[dict] = []
        batch = candidates[:cap]
        # 逐只预审要拉行情 + 日 K，一只两次请求，候选多时这段同样以分钟计。
        utils.tell(io, f"  ⏳ 逐只预审 {len(batch)} 只候选（行情 + VETO）...")
        for i, cand in enumerate(batch, 1):
            utils.tell(io, f"    · [{i}/{len(batch)}] {cand['code']} {cand['name']}")
            try:
                ev = preflight.evaluate(
                    cand["code"], self.mk, cfg, sent=sent, sector=cand["sector"])
            except Exception as exc:
                details.append(candidate_row(cand, verdict=CAND_ERROR,
                                             reason=f"行情/指标异常：{exc}"))
                if tracer:
                    tracer.add(seed_trace.STEP_VETO, cand["code"], cand["name"], "行情异常", str(exc))
                continue
            ev["tier_label"] = cand["tier"]
            ev["pick"] = cand["pick"]
            # 入选板块戳记透传：可买闸门以筛入板块为准，避免多板块归属时归因错位。
            for k in ("pick_sector_bk", "pick_sector_name", "pick_sector_rank"):
                if cand.get(k) is not None:
                    ev[k] = cand[k]
            # 涨幅窗口复检：第 2 步用板块成分股的批量快照涨幅筛入窗口，可能比实时
            # 行情慢一拍。这里已拿到实时行情，用最新涨幅再核一次；移出窗口的候选
            # 不再作为种子输出（避免「显示 6.77%，实际已涨到 7.6% 却还当温和票」）。
            fresh_chg = (ev.get("quote") or {}).get("chg_pct")
            win_min, win_max = cand.get("win_min"), cand.get("win_max")
            if (fresh_chg is not None and win_min is not None and win_max is not None
                    and not (win_min <= fresh_chg <= win_max)):
                details.append(candidate_row(
                    cand, ev, CAND_CHG_OUT,
                    reason=f"实时涨幅 {fresh_chg:.2f}% 不在 {win_min:.2f}~{win_max:.2f}%"))
                if tracer:
                    tracer.add(seed_trace.STEP_VETO, cand["code"], cand["name"], "涨幅移出窗口",
                               f"实时 {fresh_chg:.2f}% 不在 {win_min:.2f}~{win_max:.2f}%",
                               tier=cand["tier"], sector=cand.get("sector_name"), chg=fresh_chg)
                continue
            vt = ev.get("veto") or {}
            if vt.get("rejected"):
                details.append(candidate_row(cand, ev, CAND_HARD, veto_detail(vt["hard"])))
                if tracer:
                    tracer.add(seed_trace.STEP_VETO, cand["code"], cand["name"], "硬否决",
                               f"{veto_detail(vt['hard'])} → 直接 REJECT（不进观察轨）",
                               tier=cand["tier"], sector=cand.get("sector_name"), chg=cand.get("chg"),
                               veto_items=[i["name"] for i in vt["hard"]])
                continue
            if vt.get("soft"):
                ev["track"] = watch_pool.TRACK_WATCH
                ev["triggers"] = followthrough.trigger_conditions(ev, cfg)
                wr = preflight.winrate_score(ev, cfg)
                ev["winrate_score"] = wr["score"]
                ev["winrate_detail"] = wr.get("detail")
                soft.append(ev)
                details.append(candidate_row(cand, ev, CAND_SOFT,
                                             veto_detail(vt["soft"]) + " → 观察轨等回踩"))
                if tracer:
                    tracer.add(seed_trace.STEP_VETO, cand["code"], cand["name"], "软否决→观察轨",
                               f"{veto_detail(vt['soft'])}；无硬否决 → 划入观察轨等回踩后重新预审"
                               f"（触发条件：{'；'.join(ev['triggers']) or '—'}）",
                               tier=cand["tier"], sector=cand.get("sector_name"), chg=cand.get("chg"),
                               veto_items=[i["name"] for i in vt["soft"]])
                continue
            passed.append(ev)
            details.append(candidate_row(cand, ev))  # 裁决等第 4 步回填
        for cand in candidates[cap:]:  # 超出拉取上限的候选也要能看到
            details.append(candidate_row(cand, verdict=CAND_SKIPPED,
                                         reason=f"超出单次预审上限 {cap} 只"))
        return {"passed": passed, "soft": soft, "candidates": details}

    # ============================================================== 胜率因子门槛（阶段 A）
    def _winrate_gate(self, ev: dict) -> Optional[str]:
        """把历史胜率低的特征从「可买」降级为观察。

        依据 62+ 条回填样本（跨 T+1/T+3/T+5 单调）：
        - 板块排名 1-3 胜率 50%、6~15 仅 0~17%
        - 突破阶段仅 6%（T+3 -5.2%）→ 默认一律不得可买
        - winrate_score 按实证因素加权，未达门槛的「共振过线」票多为追高后继乏力
        - 入选板块必须与预审板块一致，且入选排名 ≤ 可买上限（堵多板块错位/中游漏网）

        返回降级原因，放行返回 None。
        """
        cfg = self.cfg
        if not cfg.s("winrate_gate_enabled", True):
            return None
        stage = (ev.get("stage") or {}).get("stage")
        # 突破阶段：历史胜率 6%，默认一律降级（不再放行 rank≤3 的突破）。
        if stage == preflight.STAGE_BREAK and cfg.s("winrate_breakout_block", True):
            return "突破阶段（历史胜率 6%）→ 降级观察"
        buyable_max = int(cfg.s("winrate_sector_rank_buyable_max", 5))
        sec = ev.get("sector") or {}
        sec_rank = sec.get("rank")
        if sec_rank is not None:
            if sec_rank > buyable_max:
                return f"板块排名 {sec_rank} > {buyable_max}（历史胜率 0~17%）→ 降级观察"
            # 兼容旧逻辑：仅当未开启「突破一律否决」时，仍按「突破+排名>N」砍。
            if (not cfg.s("winrate_breakout_block", True)
                    and stage == preflight.STAGE_BREAK):
                break_max = int(cfg.s("winrate_breakout_sector_rank_max", 3))
                if sec_rank > break_max:
                    return (f"突破 + 板块排名 {sec_rank} > {break_max}"
                            f"（历史胜率 6%）→ 降级观察")
        # 入选板块一致性：可买必须以筛入板块为准（rank≤上限，且与预审板块同 bk/名）。
        if cfg.s("winrate_sector_consistency", True):
            pick_rank = ev.get("pick_sector_rank")
            if pick_rank is not None and pick_rank > buyable_max:
                return (f"入选板块排名 {pick_rank} > {buyable_max}"
                        f"（筛入板块非前{buyable_max}）→ 降级观察")
            pick_bk = ev.get("pick_sector_bk")
            pick_name = ev.get("pick_sector_name")
            cur_bk, cur_name = sec.get("bk"), sec.get("name")
            if pick_bk and cur_bk and pick_bk != cur_bk:
                return (f"入选板块「{pick_name or pick_bk}」≠ 预审「{cur_name or cur_bk}」"
                        f"（多板块错位）→ 降级观察")
            if (not pick_bk) and pick_name and cur_name and pick_name != cur_name:
                return (f"入选板块「{pick_name}」≠ 预审「{cur_name}」"
                        f"（多板块错位）→ 降级观察")
        # winrate_score 硬门槛：共振过线但仍是「追高弱票」时降级。
        if cfg.s("winrate_score_gate_enabled", True):
            wr_score = ev.get("winrate_score")
            if wr_score is None:
                wr = preflight.winrate_score(ev, cfg)
                wr_score = wr["score"]
                ev["winrate_score"] = wr_score
                ev["winrate_detail"] = wr.get("detail")
            th = int(cfg.get("winrate.buyable_threshold", 3))
            if wr_score < th:
                return f"胜率分 {wr_score} < {th} → 降级观察"
        return None

    # ============================================================== 第 4 步
    def preflight_outputs(self, evaluations: List[dict], tracer: Optional[seed_trace.Tracer] = None) -> dict:
        """预审三档输出：可买 / 待启动观察 / 近失。"""
        cfg = self.cfg
        max_out = int(cfg.s("seed_max_output", 2))
        max_watch = int(cfg.get("seed.max_watch_output", 3))
        max_near = int(cfg.get("seed.max_near_miss_output", 6))
        gap_watch = int(cfg.get("seed.near_miss_gap", 1))

        buyable, watch, near = [], [], []
        for ev in evaluations:
            gap = ev.get("gap") if ev.get("gap") is not None else 99
            ft = followthrough.evaluate((ev.get("stage") or {}).get("stage"),
                                        ev.get("tier_label"), ev.get("track"), cfg)
            ev["followthrough"] = ft
            # 影子/闸门共用：每只候选都算 winrate_score 并落盘，供 rule vs 实证对比。
            wr = preflight.winrate_score(ev, cfg)
            ev["winrate_score"] = wr["score"]
            ev["winrate_detail"] = wr.get("detail")
            # 胜率因子门槛（阶段 A）：历史低胜率特征从「可买」降级观察。PASS 的 gap≤0，
            # 降级为 WATCH 后自然落入下面的「差1分→观察轨」分支（gap≤1 恒成立）。
            if ev.get("verdict") == preflight.VERDICT_PASS:
                wr_note = self._winrate_gate(ev)
                if wr_note:
                    ev["verdict"] = preflight.VERDICT_WATCH
                    ev["winrate_gate"] = wr_note
            if ev.get("verdict") == preflight.VERDICT_PASS:
                buyable.append(ev)
            elif gap <= gap_watch:
                ev["track"] = watch_pool.TRACK_PENDING
                ev["triggers"] = followthrough.trigger_conditions(ev, cfg)
                watch.append(ev)
                if tracer:
                    tracer.add(seed_trace.STEP_PREFLIGHT, ev["code"], ev["name"],
                               "胜率因子→观察轨" if ev.get("winrate_gate") else "差1分→启动待定轨",
                               ev.get("winrate_gate") or f"共振 {ev.get('total_score')}/{ev.get('pass_threshold')}")
            else:
                near.append(ev)
                if tracer:
                    tracer.add(seed_trace.STEP_PREFLIGHT, ev["code"], ev["name"], "近失（只复盘）",
                               f"共振 {ev.get('total_score')}/{ev.get('pass_threshold')}，"
                               + "；".join(ev.get("reasons", [])))

        # 排序：胜率分 → 共振分 → 跟涨经验 → 身份分（胜率分优先，纠正「共振过线但追高」）
        keyf = lambda e: (-(e.get("winrate_score") if e.get("winrate_score") is not None else -99),
                          -(e.get("total_score") or 0),
                          -((e.get("followthrough") or {}).get("score") or 0),
                          -((e.get("identity") or {}).get("score") or 0))
        buyable.sort(key=keyf)
        watch.sort(key=keyf)
        near.sort(key=keyf)
        return {"buyable": buyable[:max_out], "watch": watch[:max_watch], "near_miss": near[:max_near],
                "buyable_all": buyable, "dropped_buyable": buyable[max_out:]}

    # ============================================================== 低吸板块池
    def lowbuy_sector_pool(self, scored: List[dict]) -> List[dict]:
        """低吸（启动前夕）板块池：排名 3~10、刚开始升温（涨幅 2~4%、涨停≤1）的板块。

        追涨吃的是 TOP1-3 已涨停板块的鱼尾，低吸吃的是排名靠后、刚开始升温的鱼身。
        依据 docs/LOWBUY_PLAN_2026-08-25.md：1~3% 的票大部分不会启动，先落盘积累样本
        验证「升温板块 + 低吸票」的胜率，再决定是否上线买入。
        """
        cfg = self.cfg
        lo = int(cfg.get("seed.lowbuy_rank_min", 3))
        hi = int(cfg.get("seed.lowbuy_rank_max", 10))
        chg_lo = float(cfg.get("seed.lowbuy_chg_min", 2.0))
        chg_hi = float(cfg.get("seed.lowbuy_chg_max", 4.0))
        zt_max = int(cfg.get("seed.lowbuy_limit_up_max", 1))
        return [s for s in scored
                if lo <= (s.get("rank") or 99) <= hi
                and chg_lo <= (s.get("chg") or 0) <= chg_hi
                and (s.get("limit_up_count") or 0) <= zt_max]

    def lowbuy_pool_diag(self, scored: List[dict]) -> str:
        """低吸板块池为空时的归因文案（哪一关把候选挡掉），便于攒样本时排查。"""
        cfg = self.cfg
        lo = int(cfg.get("seed.lowbuy_rank_min", 3))
        hi = int(cfg.get("seed.lowbuy_rank_max", 10))
        chg_lo = float(cfg.get("seed.lowbuy_chg_min", 2.0))
        chg_hi = float(cfg.get("seed.lowbuy_chg_max", 4.0))
        zt_max = int(cfg.get("seed.lowbuy_limit_up_max", 1))
        n = len(scored or [])
        n_rank = sum(1 for s in scored if lo <= (s.get("rank") or 99) <= hi)
        n_chg = sum(1 for s in scored
                    if lo <= (s.get("rank") or 99) <= hi
                    and chg_lo <= (s.get("chg") or 0) <= chg_hi)
        n_zt = sum(1 for s in scored
                   if lo <= (s.get("rank") or 99) <= hi
                   and chg_lo <= (s.get("chg") or 0) <= chg_hi
                   and (s.get("limit_up_count") or 0) <= zt_max)
        return (f"无低吸板块（需排名{lo}~{hi}、涨幅{chg_lo:g}~{chg_hi:g}%、涨停≤{zt_max}）："
                f"板块{n} → 排名合{n_rank} → 涨幅合{n_chg} → 涨停合{n_zt}")

    # ============================================================== 前夕观察 / 低吸候选
    def eve_scan(self, sectors: List[dict], sent: Optional[dict],
                 tracer: Optional[seed_trace.Tracer] = None, io: Any = None,
                 max_sector_chg: Optional[float] = None) -> List[dict]:
        """前夕/低吸候选（1%~3% 涨幅窗，上限随动态下限收）：仅观察落盘，暂不写计划。"""
        cfg = self.cfg
        cands = self.screen_tier(sectors, TIER_EVE, tracer, max_sector_chg=max_sector_chg)
        out: List[dict] = []
        limit = int(cfg.get("seed.max_watch_output", 3))
        for cand in cands[:limit * 3]:
            try:
                ev = preflight.evaluate(cand["code"], self.mk, cfg, sent=sent,
                                               sector=cand["sector"])
            except Exception:
                continue
            if (ev.get("veto") or {}).get("rejected"):
                continue
            if (ev.get("stage") or {}).get("overheat"):
                continue
            wr = preflight.winrate_score(ev, cfg)
            ev["winrate_score"] = wr["score"]
            ev["winrate_detail"] = wr.get("detail")
            for k in ("pick_sector_bk", "pick_sector_name", "pick_sector_rank"):
                if cand.get(k) is not None:
                    ev[k] = cand[k]
            ev["tier_label"] = TIER_EVE
            ev["track"] = watch_pool.TRACK_EVE
            ev["lowbuy"] = True  # 低吸（启动前夕）候选标签：落盘后可单独归因
            lo = dyn_min_chg(cfg, max_sector_chg)
            hi = float(cfg.get("seed.strict_max_chg", 5.5))
            intr = float(cfg.get("seed.eve_trigger_intraday", 0.75))
            ev["triggers"] = [f"涨入严格窗 {lo}~{hi}%", f"分时回落至 ≤{intr:.0%} 后再预审"]
            out.append(ev)
            if len(out) >= limit:
                break
        return out

    # ============================================================== 每日扫描明细日志
    def _write_scan_log(self, result: dict) -> None:
        """将本次扫描的完整候选明细写入 data/ 目录，供周度复盘使用。"""
        cfg = self.cfg
        today = utils.today_str()
        path = os.path.join(cfg.data_dir(), f"scan_details_{today}.json")
        payload = {
            "scan_date": today,
            "scan_id": result.get("scan_id"),
            "timestamp": result.get("at"),
            "verdict": result.get("verdict"),
            "tier": result.get("tier"),
            "notes": result.get("notes"),
            "dyn_window": result.get("dyn_window"),
            "sectors": result.get("sectors"),
            "candidates": result.get("candidates"),
            "buyable": [e.get("code") for e in result.get("buyable", [])],
            "watch": [e.get("code") for e in result.get("watch", [])],
            "near_miss": [e.get("code") for e in result.get("near_miss", [])],
        }
        utils.ensure_dir(cfg.data_dir())
        try:
            utils.write_json(path, payload)
        except Exception:
            # 日志写入失败不中断主流程
            pass

    # ============================================================== 主流程
    def seed_scan(self, sent: Optional[dict] = None, include_eve: bool = True,
                  write_trace: bool = True, io: Any = None) -> dict:
        """种子扫描四步流总入口。"""
        cfg = self.cfg
        tracer = seed_trace.Tracer(cfg)
        sent = sent if sent is not None else get_sentiment(self.mk, cfg, io=io)

        # 大盘趋势不再用硬闸提前短路（原 seed.require_market_uptrend）：弱势市不是
        # 「一票否决」而是由 9 分共振里的「大盘趋势」维做分级扣分（见 preflight.score_nine），
        # 让扫描照常跑完、候选照常落盘——否则拦截日不落盘，这条纪律永远无法回测证伪。
        step1 = self.rank_sectors(tracer, io=io)
        sectors = step1["top"]
        strongest = step1.get("strongest_sector_chg")
        eve_p = tier_params(TIER_EVE, cfg, strongest)
        result: Dict[str, Any] = {
            "at": utils.now().strftime("%Y-%m-%d %H:%M"), "scan_id": tracer.scan_id,
            "sentiment": sent, "sectors": [{k: v for k, v in s.items() if k != "members"} for s in sectors],
            "sector_pool": [{k: v for k, v in s.items() if k != "members"} for s in step1["qualified"][:10]],
            "max_sector_chg": step1["max_sector_chg"],
            "strongest_sector_chg": strongest,
            "dyn_window": dyn_window(cfg, strongest),
            "eve_window": [eve_p["min_chg"], eve_p["max_chg"]],
            "tier": None, "buyable": [], "watch": [], "near_miss": [], "eve": [],
            "candidates": [], "notes": [], "verdict": VERDICT_EMPTY,
        }
        if not sectors:
            result["notes"].append("无板块通过硬门槛（排名≤8 且涨停≥2家 / 涨停1家+综合分≥60 / 无涨停+综合分≥70且排名≤12）")
            tracer.note(result["notes"][-1])
            if write_trace:
                result["trace"] = tracer.flush()
            return result

        utils.tell(io, "  ⏳ 执行筛选（三档涨幅窗）...")
        cands, tier, notes = self.screen_with_downgrade(sectors, step1["max_sector_chg"], tracer,
                                                        strongest_sector_chg=strongest)
        result["tier"], result["notes"] = tier, notes

        vf = self.veto_filter(cands, sent, tracer, io=io)
        utils.tell(io, "  ⏳ 汇总三档输出...")
        out = self.preflight_outputs(vf["passed"], tracer)
        watch_items = vf["soft"] + out["watch"]
        finalize_candidates(vf["candidates"], vf["passed"],
                            {e.get("code") for e in out["dropped_buyable"]})

        result["buyable"] = out["buyable"]
        result["watch"] = watch_items[:int(cfg.get("seed.max_watch_output", 3)) + len(vf["soft"])]
        result["near_miss"] = out["near_miss"]
        result["candidates"] = vf["candidates"]
        result["candidates_n"] = len(cands)
        result["veto_passed_n"] = len(vf["passed"])
        result["soft_n"] = len(vf["soft"])

        if include_eve:
            utils.tell(io, "  ⏳ 低吸（启动前夕）扫描...")
            try:
                with utils.timed("低吸扫描", io, threshold=0.5):
                    # 低吸不追 TOP1-3 已涨停板块（那是鱼尾），改扫排名 3~10 升温板块。
                    lowbuy_sectors = self.lowbuy_sector_pool(step1["scored"])
                    result["lowbuy_sectors_n"] = len(lowbuy_sectors)
                    if lowbuy_sectors:
                        result["eve"] = self.eve_scan(lowbuy_sectors, sent, tracer, io=io,
                                                      max_sector_chg=strongest)
                    else:
                        result["eve"] = []
                        result["notes"].append(self.lowbuy_pool_diag(step1["scored"]))
            except Exception as exc:
                result["notes"].append(f"前夕观察扫描异常：{exc}")

        if result["buyable"]:
            result["verdict"] = VERDICT_TRADEABLE
        elif result["watch"] or result["eve"]:
            result["verdict"] = VERDICT_PENDING
        else:
            result["verdict"] = VERDICT_EMPTY

        if write_trace:
            result["trace"] = tracer.flush()

        # 将本次详细扫描日志写入 data/ 目录，供周末复盘
        self._write_scan_log(result)

        return result

    # ============================================================== 胜率选股
    def winrate_scan(self, sent: Optional[dict] = None, io: Any = None) -> dict:
        """胜率选股扫描：复用板块排序/涨幅窗/VETO，用 winrate_score 替代 9分共振分档。

        与 seed_scan 并行：seed_scan 走纪律型 9 分共振，这里走数据型胜率评分，
        两套候选都落盘（mode 区分），攒够样本后对比谁胜率高。
        """
        cfg = self.cfg
        tracer = seed_trace.Tracer(cfg)
        sent = sent if sent is not None else get_sentiment(self.mk, cfg, io=io)
        step1 = self.rank_sectors(tracer, io=io)
        sectors = step1["top"]
        strongest = step1.get("strongest_sector_chg")
        threshold = int(cfg.get("winrate.buyable_threshold", 3))
        result: Dict[str, Any] = {
            "at": utils.now().strftime("%Y-%m-%d %H:%M"), "scan_id": tracer.scan_id,
            "mode": "winrate",
            "sentiment": sent,
            "sectors": [{k: v for k, v in s.items() if k != "members"} for s in sectors],
            "winrate_threshold": threshold,
            "tier": None, "buyable": [], "watch": [], "candidates": [],
            "notes": [], "verdict": VERDICT_EMPTY,
        }
        if not sectors:
            result["notes"].append("无板块通过硬门槛")
            return result

        cands, tier, notes = self.screen_with_downgrade(sectors, step1["max_sector_chg"],
                                                        tracer, strongest_sector_chg=strongest)
        result["tier"], result["notes"] = tier, notes
        vf = self.veto_filter(cands, sent, tracer, io=io)

        buyable, watch = [], []
        for ev in vf["passed"]:
            wr = preflight.winrate_score(ev, cfg)
            ev["winrate_score"] = wr["score"]
            ev["winrate_detail"] = wr["detail"]
            # 与规则通道共用可买硬闸（突破/板块上限/入选一致性）；胜率分门槛仍用本通道阈值。
            wr_note = self._winrate_gate(ev)
            if wr_note:
                ev["track"] = watch_pool.TRACK_WATCH
                ev["winrate_gate"] = wr_note
                ev["triggers"] = followthrough.trigger_conditions(ev, cfg)
                watch.append(ev)
            elif wr["score"] >= threshold:
                buyable.append(ev)
            else:
                ev["track"] = watch_pool.TRACK_WATCH
                ev["triggers"] = followthrough.trigger_conditions(ev, cfg)
                watch.append(ev)
        buyable.sort(key=lambda e: -e.get("winrate_score", -99))

        result["buyable"] = buyable
        result["watch"] = watch[:int(cfg.get("seed.max_watch_output", 3))]
        result["candidates"] = vf["candidates"]
        result["candidates_n"] = len(cands)
        result["veto_passed_n"] = len(vf["passed"])
        if buyable:
            result["verdict"] = VERDICT_TRADEABLE
        elif watch:
            result["verdict"] = VERDICT_PENDING
        result["notes"].append(f"胜率评分门槛 {threshold} 分（数据启发，见 docs/WINRATE_ROADMAP）")
        return result
