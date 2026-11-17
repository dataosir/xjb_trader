"""术 — 9 分共振评分 / ATR 止损止盈 / 含滑点盈亏比 / 分时位置 / 启动阶段。

非交互预审（evaluate）等价于 Phase1-4 的评分部分，供种子扫描、计划复核、
观察池回踩复评共用。
"""
from __future__ import annotations

from typing import List, Optional

from tea.analysis import identity as ident_mod
from tea.analysis import shadow_lab as shadow_lab_mod
from tea.analysis.followthrough import resonance_core_score
from tea.config.config_store import Config, load_config
from tea.core import logger as logger_mod, utils
from tea.core.timing import Timing
from tea.data import Market, intraday_position
from tea.data.indicators import format_bollinger
from . import veto as veto_mod

_LOG = logger_mod.get_logger("score")

STAGE_SPROUT = "萌芽"
STAGE_BREAK = "突破"
STAGE_OVERHEAT = "过热"

VERDICT_PASS = "PASS"
VERDICT_REJECT = "REJECT"
VERDICT_WATCH = "WATCH"
VERDICT_NEAR_MISS = "NEAR_MISS"

# 共振名义满分（消息面中性化时实际 max 可能为 8）
RESONANCE_SCORE_MAX = 9
# 胜率选股满分（板块+3、阶段+1、身份+1、涨幅+1、量价+1）
WINRATE_SCORE_MAX = 7
# V2 影子分满分（板块+3、涨幅+1）；只落盘，不进闸
WINRATE_SCORE_V2_MAX = shadow_lab_mod.WINRATE_V2_MAX


# ------------------------------------------------------------------ 5.3 盈亏比

def odds_calc(price: float, sl_pct: float, tp_pct: float, cfg: Optional[Config] = None) -> dict:
    """含滑点盈亏比：买入价 +0.5%，止损价 -0.5%（做多更保守）。"""
    cfg = cfg or load_config()
    sb = float(cfg.get("position.slippage_buy_pct", 0.5)) / 100.0
    ss = float(cfg.get("position.slippage_stop_pct", 0.5)) / 100.0
    entry = float(price)
    stop = entry * (1 - sl_pct / 100.0)
    target = entry * (1 + tp_pct / 100.0)
    adj_entry = entry * (1 + sb)
    adj_stop = stop * (1 - ss)
    risk = adj_entry - adj_stop
    reward = target - adj_entry
    odds = (reward / risk) if risk > 0 else None
    return {
        "entry": round(entry, 4), "stop": round(stop, 4), "target": round(target, 4),
        "adj_entry": round(adj_entry, 4), "adj_stop": round(adj_stop, 4),
        "risk": round(risk, 4), "reward": round(reward, 4),
        "risk_pct": round(risk / adj_entry * 100.0, 2) if risk else None,
        "reward_pct": round(reward / adj_entry * 100.0, 2) if reward else None,
        "odds": round(odds, 2) if odds else None,
        "breakeven_wr": round(1.0 / (1.0 + odds), 4) if odds else None,
        "sl_pct": round(sl_pct, 2), "tp_pct": round(tp_pct, 2),
    }


def row_plan_entry(row: dict) -> Optional[float]:
    """计划止盈锚点价：优先挂单价，其次现价/预审 entry。"""
    for k in ("order_price", "price"):
        v = row.get(k)
        if v is not None:
            return float(v)
    lv = row.get("levels") or {}
    if lv.get("entry") is not None:
        return float(lv["entry"])
    q = row.get("quote") or {}
    if q.get("price") is not None:
        return float(q["price"])
    return None


def row_tp_target(row: dict) -> tuple:
    """从候选/evaluation 取止盈% / 目标价。"""
    tp_pct = row.get("tp_pct")
    target = row.get("target")
    if tp_pct is None or target is None:
        lv = row.get("levels") or {}
        if tp_pct is None:
            tp_pct = lv.get("tp_pct")
        if target is None:
            target = lv.get("target")
    return tp_pct, target


def plan_targets_by_day(entry: Optional[float], target: Optional[float],
                        tp_pct: Optional[float] = None) -> Optional[dict]:
    """T+1/T+2/T+3 计划止盈路径（挂单价→止盈价线性拆分，复盘对照用）。"""
    if entry is None:
        return None
    entry = float(entry)
    if target is None:
        if tp_pct is None:
            return None
        target = entry * (1 + float(tp_pct) / 100.0)
    else:
        target = float(target)
    if target <= entry:
        return None

    def _seg(frac: float) -> dict:
        price = round(entry + (target - entry) * frac, 4)
        pct = round((price / entry - 1.0) * 100.0, 2)
        return {"pct": pct, "price": price}

    return {
        "entry": round(entry, 4),
        "target": round(target, 4),
        "t1": _seg(1 / 3),
        "t2": _seg(2 / 3),
        "t3": _seg(1.0),
    }


def plan_targets_from_row(row: dict) -> Optional[dict]:
    tp_pct, target = row_tp_target(row)
    entry = row_plan_entry(row)
    return plan_targets_by_day(entry, target, tp_pct)


def _fmt_plan_pct(pct: float) -> str:
    return f"+{pct:.2f}%"


def format_tn_plan_hint(row: dict, style: str = "plain") -> Optional[str]:
    """计划止盈路径 T+1/T+2/T+3（复盘对照，非概率/非收益承诺）。"""
    plan = plan_targets_from_row(row)
    if not plan:
        return None

    prefix = "计划止盈路径（复盘对照，非概率）："

    def _seg(label: str, seg: dict) -> str:
        pct_s = _fmt_plan_pct(seg["pct"])
        price_s = utils.num(seg["price"])
        if style == "console":
            pct_s = utils.hl(pct_s, utils.COLOR_PROFIT)
            price_s = utils.hl(price_s, utils.COLOR_PROFIT)
        elif style == "md":
            pct_s = f"**{pct_s}**"
            price_s = f"**{price_s}**"
        return f"{label} {pct_s}→{price_s}"

    parts = [_seg("T+1", plan["t1"]), _seg("T+2", plan["t2"]), _seg("T+3", plan["t3"])]
    return prefix + "　".join(parts)


def format_tn_plan_email_lines(row: dict) -> List[str]:
    """邮件正文用多行计划止盈（【】强调核心数字）。"""
    plan = plan_targets_from_row(row)
    if not plan:
        return []
    lines = ["计划止盈路径（复盘对照，非概率）："]
    for label, key in (("T+1", "t1"), ("T+2", "t2"), ("T+3", "t3")):
        seg = plan[key]
        lines.append(f"  {label}：【{_fmt_plan_pct(seg['pct'])}】 → 【{utils.num(seg['price'])}】")
    return lines


def min_tp_for_odds(price: float, sl_pct: float, min_odds: float,
                    cfg: Optional[Config] = None) -> float:
    """反推满足含滑点 R:R ≥ min_odds 的最低止盈百分比。"""
    cfg = cfg or load_config()
    sb = float(cfg.get("position.slippage_buy_pct", 0.5)) / 100.0
    ss = float(cfg.get("position.slippage_stop_pct", 0.5)) / 100.0
    adj_entry = price * (1 + sb)
    adj_stop = price * (1 - sl_pct / 100.0) * (1 - ss)
    risk = adj_entry - adj_stop
    target = adj_entry + min_odds * risk
    return max(0.0, (target / price - 1.0) * 100.0)


# ------------------------------------------------------------------ 5.2 止损止盈

def compute_levels(price: float, atr_pct: Optional[float], cfg: Optional[Config] = None) -> dict:
    """ATR 优先的止损止盈；ATR 不可用时回退固定止损 + 反推最低止盈。"""
    cfg = cfg or load_config()
    min_odds = float(cfg.s("min_odds", 3))
    tp_cap = float(cfg.s("atr_tp_cap_pct", 15.0))
    notes: List[str] = []

    if atr_pct:
        sl = atr_pct * float(cfg.s("atr_sl_mult", 1.5))
        sl = utils.clamp(sl, float(cfg.s("atr_sl_min_pct", 2.0)), float(cfg.s("atr_sl_max_pct", 8.0)))
        sl = min(sl, float(cfg.s("atr_sl_hard_max_pct", 6.0)))
        tp = min(atr_pct * float(cfg.s("atr_tp_mult", 3.0)), tp_cap)
        source = "ATR"
        notes.append(f"ATR% {atr_pct:.2f} → 止损 {sl:.2f}%（×1.5 夹紧）/ 止盈 {tp:.2f}%（×3 上限15%）")
    else:
        sl = float(cfg.get("position.fallback_sl_pct", 3.0))
        tp = min(min_tp_for_odds(price, sl, min_odds, cfg), tp_cap)
        source = "回退"
        notes.append(f"ATR 不可用 → 固定止损 {sl:.2f}% + 反推止盈满足 R:R≥{min_odds:.0f}")

    need = min_tp_for_odds(price, sl, min_odds, cfg)
    odds_ok = True
    if tp < need:
        if need <= tp_cap:
            tp = need
            notes.append(f"止盈抬至 {tp:.2f}% 以满足含滑点 R:R≥{min_odds:.0f}")
        else:
            tp = tp_cap
            odds_ok = False
            notes.append(f"止盈上限 {tp_cap:.0f}% 无法满足 R:R≥{min_odds:.0f}（需 {need:.2f}%）")

    res = odds_calc(price, sl, tp, cfg)
    res.update({"atr_pct": atr_pct, "source": source, "notes": notes,
                "odds_ok": bool(odds_ok and (res["odds"] or 0) >= min_odds),
                "sl_atr_mult": round(sl / atr_pct, 2) if atr_pct else None,
                # 留痕：本次 R:R 判定的门槛，便于复盘「为什么 odds 2.08 仍被拒/放行」
                "min_odds": min_odds})
    return res


# ------------------------------------------------------------------ 5.5 启动阶段

def classify_stage(quote: dict, ind: dict, identity: Optional[dict], intraday: Optional[float],
                   cfg: Optional[Config] = None) -> dict:
    """萌芽 / 突破 / 过热。"""
    cfg = cfg or load_config()
    chg = quote.get("chg_pct")
    bias = (ind or {}).get("bias_ma20")
    sprout_max = float(cfg.get("scoring.sprout_max_chg", 5.5))
    bull = bool((ind or {}).get("ma_bull"))
    base = STAGE_SPROUT if (chg is not None and chg <= sprout_max and bull) else STAGE_BREAK

    bias_th = veto_mod.overheat_bias_limit(base, identity, cfg)
    overheat_bias = bias is not None and bias > bias_th

    intr_th = float(cfg.get("scoring.intraday_overheat_pct", 0.85))
    tol = float(cfg.get("scoring.sprout_intraday_tol", 0.92))
    overheat_intraday = intraday is not None and intraday >= intr_th
    if (overheat_intraday and base == STAGE_SPROUT and chg is not None and chg <= sprout_max
            and intraday is not None and intraday < tol):
        overheat_intraday = False  # 萌芽容忍：涨幅温和且分时未封顶

    overheat = bool(overheat_bias or overheat_intraday)
    reasons = []
    if overheat_bias:
        reasons.append(f"乖离 {bias:.2f}% > {bias_th:.0f}%")
    if overheat_intraday:
        reasons.append(f"分时 {intraday:.0%} ≥ {intr_th:.0%}")
    return {
        "stage": STAGE_OVERHEAT if overheat else base,
        "base_stage": base,
        "overheat": overheat,
        "bias_threshold": bias_th,
        "reasons": reasons,
    }


# ------------------------------------------------------------------ 5.1 六维评分

def score_nine(quote: dict, ind: dict, sector: dict, sent: Optional[dict],
               levels: Optional[dict], has_news: Optional[bool] = None,
               cfg: Optional[Config] = None) -> dict:
    """9 分共振评分：板块强度2 + 大盘1 + 消息1 + 市值2 + 量价2 + 止损结构1。"""
    cfg = cfg or load_config()
    c = lambda k, d=None: cfg.get(f"scoring.{k}", d)
    dims: List[dict] = []
    # 技术指标（均线/乖离/ATR）的日K是否非「今日最近数据」。stale 时量价结构、
    # 止损结构两维弃用该指标，不拿昨日/更早的均线与 ATR 给今天选股打假分。
    ind_stale = bool((ind or {}).get("kline_stale"))

    # ① 板块强度（2）
    # 板块排名是这一维的唯一数据源。实时取数失败回退磁盘兜底时，排名可能是
    # 昨日收盘数据——拿旧排名给今天选股打「板块强」是假分，所以 stale 时整维归零。
    if (sector or {}).get("stale"):
        s1, d1 = 0, "板块排名为昨日缓存（实时取数失败），不参与共振分"
    else:
        rank = (sector or {}).get("rank")
        zt = int((sector or {}).get("limit_up_count") or 0)
        rank_pct = (sector or {}).get("stock_rank_pct")
        full_rank, full_zt = int(c("sector_rank_full", 8)), int(c("sector_limit_up_full", 2))
        half_rank, half_zt = int(c("sector_rank_half", 15)), int(c("sector_limit_up_half", 1))
        if rank is not None and rank <= full_rank and zt >= full_zt:
            s1, d1 = 2, f"板块第 {rank} 名（≤{full_rank}）且涨停 {zt} 家（≥{full_zt}）"
        elif rank is not None and rank <= half_rank and zt >= half_zt:
            s1, d1 = 1, f"板块第 {rank} 名（≤{half_rank}）且涨停 {zt} 家（≥{half_zt}）"
        else:
            s1 = 0
            d1 = (f"板块第 {rank} 名 / 涨停 {zt} 家（不达标）" if rank else "板块未识别")
        if rank_pct is not None:
            if rank_pct <= float(c("sector_inner_top_pct", 0.10)):
                s1 += int(c("sector_inner_bonus", 1))
                d1 += f"；板块内前 {rank_pct:.0%} +1"
            elif rank_pct > float(c("sector_inner_tail_pct", 0.50)):
                s1 -= int(c("sector_inner_penalty", 1))
                d1 += f"；板块内后 {1 - rank_pct:.0%} -1"
        s1 = int(utils.clamp(s1, 0, int(c("sector_dim_cap", 2))))
    dims.append({"no": 1, "name": "板块强度", "max": 2, "score": s1, "detail": d1})

    # ② 大盘趋势（只扣不加：弱势 -1，其余 0）
    # 趋势态势 = 位置（点位 vs MA20，带缓冲）+ 方向（MA20 斜率），合成 -2~+2。
    # 只把「偏弱/弱势」映成 -1，震荡与偏强一律 0：119 条已回填样本里，大盘趋势
    # +1 的 T+3 胜率 45%，-1 的反而是 67%——强趋势给 +1 分等于奖励了最差的桶。
    # 只扣不加后门槛的含义变成「顺风时不许有明显短板」，与「-1 封顶、满分 6=门槛 6」
    # 的宽容度一致：弱势市里完美票仍能过。
    idx = (sent or {}).get("index") or {}
    point, ma20 = idx.get("point"), idx.get("ma20")
    bias = idx.get("ma20_bias_pct")
    if bias is None and point and ma20:
        bias = (point / ma20 - 1) * 100.0
    slope = idx.get("ma20_slope_pct")
    buf = float(c("market_trend_bias_buffer", 0.3))
    slope_th = float(c("market_trend_slope_th", 0.2))

    pos = 1 if (bias is not None and bias > buf) else (-1 if (bias is not None and bias < -buf) else 0)
    dirn = 1 if (slope is not None and slope > slope_th) else (-1 if (slope is not None and slope < -slope_th) else 0)
    trend = pos + dirn  # -2 ~ +2

    def _sig(v: Optional[float]) -> str:
        return "—" if v is None else f"{v:+.2f}%"

    sig_txt = f"点位较 MA20 {_sig(bias)}、MA20 斜率 {_sig(slope)}"
    # 位置与方向都取不到才算缺数据；只缺一路时按 0 处理，不能把「取数不齐」当成利空。
    if pos == 0 and dirn == 0 and (bias is None or slope is None):
        s2, d2 = 0, "大盘数据缺失（不加不减）"
    elif trend >= 2:
        s2, d2 = 0, f"大盘强趋势（{sig_txt}；只扣不加，不额外加分）"
    elif trend == 1:
        s2, d2 = 0, f"大盘偏强（{sig_txt}）"
    elif trend == 0:
        s2, d2 = 0, f"大盘震荡（{sig_txt}）"
    elif trend == -1:
        s2, d2 = -1, f"大盘偏弱 → 共振 -1（{sig_txt}）"
    else:
        s2, d2 = -1, f"大盘弱势 → 共振 -1（{sig_txt}）"
    dims.append({"no": 2, "name": "大盘趋势", "max": 1, "score": s2, "detail": d2})

    # ③ 消息面（1）
    # 自动扫描没有消息数据源，has_news 会是 None：这一维既不扣分也不占满分，
    # 用 0/0 留一个「中性化」标识，方便扫完盘后在 scan_details 里区分
    # 「无明确催化（0/1）」与「无消息数据、不计分（0/0）」。
    if has_news is None:
        s3, d3, m3 = 0, "无消息数据（中性，不计分）", 0
    else:
        s3 = 1 if has_news else 0
        d3 = "有催化（政策/业绩/题材）" if has_news else "无明确催化"
        m3 = 1
    dims.append({"no": 3, "name": "消息面", "max": m3, "score": s3, "detail": d3})

    # ④ 市值区间（2）
    cap = quote.get("cap_yi")
    lo_mid = float(c("cap_mid_low", 30))
    lo_ideal = float(c("cap_ideal_low", 50))
    hi_ideal = float(c("cap_ideal_high", 300))
    hi_mid = float(c("cap_mid_high", 500))
    small = float(c("cap_small_zero_below", 30))
    if cap is None:
        s4, d4 = 0, "市值未知"
    elif lo_ideal <= cap <= hi_ideal:
        s4, d4 = 2, f"市值 {cap:.1f}亿（{lo_ideal:.0f}~{hi_ideal:.0f}亿 黄金区间）"
    elif lo_mid <= cap < lo_ideal or hi_ideal < cap <= hi_mid:
        s4, d4 = 1, f"市值 {cap:.1f}亿（{lo_mid:.0f}~{lo_ideal:.0f}亿 / {hi_ideal:.0f}~{hi_mid:.0f}亿）"
    elif cap < small:
        s4, d4 = 0, f"市值 {cap:.1f}亿（<{small:.0f}亿 过小）"
    else:
        s4, d4 = int(c("cap_huge_score", 1)), f"市值 {cap:.1f}亿（>{hi_mid:.0f}亿 大盘股）"
    dims.append({"no": 4, "name": "市值区间", "max": 2, "score": s4, "detail": d4})

    # ⑤ 量价结构（2）
    chg = quote.get("chg_pct")
    vr = quote.get("vol_ratio")
    bull = bool((ind or {}).get("ma_bull"))
    above20 = bool((ind or {}).get("above_ma20"))
    strong, shrink = float(c("vp_vol_ratio_strong", 1.2)), float(c("vp_vol_ratio_shrink", 0.8))
    penalties: List[str] = []
    if ind_stale:
        s5, d5 = 0, "技术指标为昨日数据（日K最后一根非今日），不参与共振分"
    elif chg is not None and vr is not None and chg > 0 and vr >= strong and bull:
        s5, d5 = 2, f"放量上涨（涨幅 {chg:+.2f}%>0 且 量比 {vr:.2f}≥{strong:.1f} 且 均线多头）"
    elif chg is not None and vr is not None and chg <= 0 and vr <= shrink and above20:
        s5, d5 = 2, f"缩量回调（涨幅 {chg:+.2f}%≤0 且 量比 {vr:.2f}≤{shrink:.1f} 且 MA20 上方）"
    elif chg is not None and vr is not None and chg > 0 and vr < strong and not bull:
        # 缩量上涨(量比<1.2)且非多头：今日硬拉但没后劲（后继乏力），不给分。
        # 依据：可买 0/5 全是这类弱票（永杉锂业 量比 1.1、跌破 MA20），被拒的强势票反而赢。
        s5, d5 = 0, f"缩量上涨/趋势不足（涨幅 {chg:+.2f}% 量比 {vr:.2f}，后劲存疑）"
    elif chg is not None and vr is not None and ((chg > 0 and vr >= 1.0) or (chg <= 0 and vr < 1.0)):
        cond = "涨幅>0 且 量比≥1.0" if chg > 0 else "涨幅≤0 且 量比<1.0"
        s5, d5 = 1, f"量价基本配合（{cond}；涨幅 {chg:+.2f}% 量比 {vr:.2f}）"
    elif chg is not None and vr is not None and chg < 0 and vr >= strong:
        s5, d5 = -1, f"放量下跌（涨幅 {chg:+.2f}%<0 且 量比 {vr:.2f}≥{strong:.1f} → 扣1分）"
    elif not above20 and not bull:
        s5, d5 = 0, "均线全失（MA20 下方且非多头）"
    else:
        s5, d5 = 1, f"量价一般（涨幅 {utils.pct(chg)} 量比 {utils.num(vr)}）"
    if not ind_stale:
        amt, to = quote.get("amount_yi"), quote.get("turnover")
        each = int(c("vp_penalty_each", 1))
        if amt is not None and amt < float(c("amount_min_yi", 2.0)):
            penalties.append(f"成交额 {amt:.2f}亿 <2亿")
        if amt is not None and amt > float(c("amount_max_yi", 80.0)):
            penalties.append(f"成交额 {amt:.2f}亿 >80亿")
        if to is not None and to < float(c("turnover_min_pct", 2.0)):
            penalties.append(f"换手 {to:.2f}% <2%")
        if to is not None and to > float(c("turnover_max_pct", 20.0)):
            penalties.append(f"换手 {to:.2f}% >20%")
        # 「乖离>8%」扣分已移除：乖离是追高/回踩风险信号，应交由 veto 的
        # bias_soft/bias_hard（普通 15% / 龙头 25% / 硬 30%）统一把关，而不是在
        # 量价结构里再用更严的 8% 罚一遍——实证这是在反向惩罚强势票（高乖离反而赢）。
        if penalties:
            s5 -= each * len(penalties)
            d5 += "；扣分：" + "、".join(penalties)
    # 量价结构下限 -1：放量下跌是强负信号要扣分（其余负面情形给 0），不能一视同仁。
    s5 = int(utils.clamp(s5, -1, int(c("vp_dim_max", 2))))
    dims.append({"no": 5, "name": "量价结构", "max": 2, "score": s5, "detail": d5})

    # ⑥ 止损结构（1）
    lv = levels or {}
    sl_pct, odds = lv.get("sl_pct"), lv.get("odds")
    atr_ratio = lv.get("sl_atr_mult")
    if ind_stale:
        s6, d6 = 0, "止损结构依赖昨日 ATR（日K最后一根非今日），不参与共振分"
    else:
        ok = (sl_pct is not None and sl_pct <= float(c("sl_struct_max_pct", 8.0))
              and (atr_ratio is None or atr_ratio <= float(c("sl_struct_atr_mult", 2.5)))
              and odds is not None and odds >= float(c("sl_struct_min_odds", 3.0)))
        s6 = 1 if ok else 0
        d6 = (f"止损 {utils.pct(sl_pct)}（/ATR {utils.num(atr_ratio)}）R:R {utils.num(odds)}"
              + ("" if ok else " → 不达标"))
    dims.append({"no": 6, "name": "止损结构", "max": 1, "score": s6, "detail": d6})

    total = sum(d["score"] for d in dims)
    # 有效分：消息面 0/0、止损结构恒满，两维不改变排序，只把满分垫高。
    # 单独给出「有效 X/Y」便于复盘时判断这个门槛到底是在卡什么。
    core_pts, core_max = resonance_core_score(dims)
    return {
        "total": int(total),
        # 满分由实际计入的维度决定：消息面中性化时其 max=0，满分从 9 降到 8，
        # 让「X/满分」诚实地反映当前可争取的上限，而不是恒定 9。
        "max": int(sum(d["max"] for d in dims)),
        "core_total": core_pts,
        "core_max": core_max,
        "dims": dims,
        "penalties": penalties,
    }


def gate_failed(total: Optional[int], threshold: Optional[int],
                core: Optional[int] = None, core_th: Optional[int] = None) -> bool:
    """共振门槛是否未过：总分与有效分两把尺子，任一不过即不过。

    单看总分会被两维常量维度放水（消息面 0/0 已不占满分，止损结构在止损规则内
    恒 +1），于是 5/9 也能贴着 7 分的门槛说「差 2 分」；有效分（7 分制）扣掉这两维
    后，同样的票是 4/7，差距才与账面一致。缺任一数据时退回只比总分。
    """
    if total is None or threshold is None:
        return False
    if total < threshold:
        return True
    return core is not None and core_th is not None and core < core_th


def format_resonance_shortfall(ev: Optional[dict] = None, *,
                               dims: Optional[List[dict]] = None,
                               total: Optional[int] = None,
                               threshold: Optional[int] = None,
                               prefix: str = "短板") -> str:
    """列出未拿满分的共振维度，供观察/近失/候选明细展开「哪一维拖分」。

    只报「共振 4/6」是黑盒：复盘时不知道掉在板块强度还是量价结构。与 plan.check_item
    的六维对比互补——这里面向单次快照，列出当前 score < max 的维度及简要原因。

    判定与 ``gate_failed`` 一致：总分过线但有效分没过时也要展开（否则会看到
    「5/9 门槛7 … 差 2 分达门槛 7」这种 5<7 却写差 2 的自相矛盾）。
    """
    sc = (ev or {}).get("scoring") or {}
    dims = dims or sc.get("dims") or (ev or {}).get("scoring_dims") or []
    if not dims:
        return ""
    if total is None:
        total = sc.get("total") if sc else (ev or {}).get("total_score")
    if threshold is None:
        threshold = (ev or {}).get("pass_threshold")
    core = sc.get("core_total") if sc else (ev or {}).get("core_score")
    core_th = (ev or {}).get("core_threshold")
    if not gate_failed(total, threshold, core, core_th):
        return ""

    weak: List[dict] = []
    for d in dims:
        mx = int(d.get("max") or 0)
        sc_ = d.get("score")
        if sc_ is None or mx <= 0:
            continue
        if sc_ < mx:
            weak.append(d)
    if not weak:
        return ""

    def _brief(d: dict) -> str:
        detail = (d.get("detail") or "").strip()
        if not detail:
            return f"{d.get('name')} {d.get('score')}/{d.get('max')}"
        if len(detail) > 28:
            detail = detail[:27] + "…"
        return f"{d.get('name')} {d.get('score')}/{d.get('max')}（{detail}）"

    msg = prefix + "：" + "、".join(_brief(d) for d in weak)
    if total is not None and threshold is not None:
        if total >= threshold and core is not None and core_th is not None:
            msg += f"（总分 {total}/{threshold} 过线，但有效分 {core}/{core_th} 差 {core_th - core} 分）"
        elif total >= threshold:
            msg += f"（总分已过线 {total}/{threshold}）"
        else:
            msg += f"（差 {threshold - total} 分达门槛 {threshold}）"
    return msg


# ------------------------------------------------------------------ 8.4 有效门槛

#: 满分 9 里恒定拿满分/恒定 0 分的维度：消息面 1、止损结构 1。
_CONSTANT_DIM_POINTS = 2


def core_threshold(threshold: int, max_score: int = 9) -> int:
    """把满分门槛按比例换算到有效分满分（默认 7 分）。"""
    if max_score <= 0:
        return int(threshold)
    # 有效满分 = 满分 − 常量维（消息面 1、止损结构 1）。两者都在总分里占满额。
    core_max = max_score - _CONSTANT_DIM_POINTS
    if core_max <= 0:
        return int(threshold)
    return int(round(threshold * core_max / max_score))


def effective_threshold(identity: Optional[dict] = None, sent: Optional[dict] = None,
                        insufficient_samples: bool = False,
                        cfg: Optional[Config] = None,
                        max_score: int = 9) -> dict:
    """动态通过门槛：base 6 + 杂毛warn +1 + 样本不足 +1，龙头 -1（下限 6），防守 +1。

    龙头 -1 是门槛公式的一部分，不设调用方开关：种子扫描、计划复核、run 执行、
    eval/watch 全走同一套口径，避免「选票按 6 分、复核按 7 分」这类分叉。

    另给 ``core_threshold``：把同一门槛换算到「有效分」（只数板块强度/大盘趋势/
    市值区间/量价结构）。消息面恒 0/0、止损结构在止损规则内恒 +1，两维一起去掉
    后满分从 9 降到 7，原样沿用 6/7 的门槛等价于比原来更严——换算成 4/7 才与
    「共振 6/9 过线」是同一件事。

    顺序有意为之：龙头 -1 先于防守 +1，所以防守市里龙头也要按 7 分卡——
    防守 +1 不被龙头 -1 抵消（否则防守等于没收紧，逆势选票）。
    """
    cfg = cfg or load_config()
    base = int(cfg.s("pass_threshold", 6))
    th = base
    notes: List[str] = [f"基准门槛 {base}"]

    bump = ident_mod.pass_bump(identity or {}, cfg)
    if bump:
        th += bump
        notes.append(f"杂毛预警 +{bump}")
    if insufficient_samples:
        inc = int(cfg.get("expectancy.insufficient_pass_bump", 1))
        th += inc
        notes.append(f"历史样本不足 +{inc}")
    if ident_mod.is_leader(identity or {}):
        floor = int(cfg.get("seed.leader_pass_floor", 6))
        relax = int(cfg.get("seed.leader_pass_bonus", 1))
        new_th = max(floor, th - relax)
        if new_th != th:
            notes.append(f"龙头 -{th - new_th}（下限 {floor}）")
        th = new_th
    if sent and sent.get("stance") == "防守":
        bump_defend = int(cfg.s("defend_stance_bump", 1))
        th += bump_defend
        notes.append(f"防守姿态 +{bump_defend}")
    return {"threshold": int(th), "base": base, "notes": notes,
            "core_threshold": core_threshold(th, max_score)}


# ------------------------------------------------------------------ 胜率选股评分

def winrate_score(ev: dict, cfg: Optional[Config] = None) -> dict:
    """胜率选股评分：按已回填样本的实证胜率因素加权（数据启发，非拍脑袋）。

    权重来自 62 条回填样本的胜率拆解（跨 T+1/T+3/T+5）：
    - 板块排名 1-3 胜 50%、4-5 胜 28%、6-8 胜 6~17%、>8 胜 0~8%（最强、单调）
    - 突破阶段 6%（最差）、过热 26%
    - 身份分 75-85 胜 5%、85-93 胜 33%、93+ 胜 25%（透支不加分）
    - 涨幅 3-5% 胜 27%、5-7% 胜 14%
    - 放量上涨+多头 赢、放量下跌 亏

    与 9 分共振（纪律型启发式）并行：本函数是「数据型」通道，落盘 mode=winrate
    后与 mode=rule 对比，攒够样本再重拟合权重（见 docs/archive/WINRATE_ROADMAP）。
    """
    cfg = cfg or load_config()
    parts: List[str] = []
    score = 0

    # 板块排名（权重最高，跨周期单调）
    rank = (ev.get("sector") or {}).get("rank")
    if rank is not None:
        if rank <= 3:
            score += 3
            parts.append(f"板块排名{rank}≤3 +3")
        elif rank <= 5:
            score += 1
            parts.append(f"板块排名{rank}∈4-5 +1")
        elif rank <= 8:
            parts.append(f"板块排名{rank}∈6-8 0")
        else:
            score -= 2
            parts.append(f"板块排名{rank}>8 -2")

    # 阶段：突破最差
    stage = (ev.get("stage") or {}).get("stage")
    if stage == "突破":
        score -= 1
        parts.append("突破阶段 -1")
    elif stage == "过热":
        score += 1
        parts.append("过热阶段 +1")

    # 身份分：85-93 最优，<75 最差，93+ 透支不加分
    idn = (ev.get("identity") or {}).get("score")
    if idn is not None:
        if 85 <= idn <= 93:
            score += 1
            parts.append(f"身份分{idn:.0f}∈85-93 +1")
        elif idn < 75:
            score -= 1
            parts.append(f"身份分{idn:.0f}<75 -1")

    # 涨幅：3-5% 最优，5-7% 反而差
    chg = (ev.get("quote") or {}).get("chg_pct")
    if chg is not None:
        if 3.0 <= chg <= 5.0:
            score += 1
            parts.append(f"涨幅{chg:.1f}%∈3-5 +1")
        elif 5.0 < chg <= 7.0:
            score -= 1
            parts.append(f"涨幅{chg:.1f}%∈5-7 -1")

    # 量价：放量+多头 加分、放量下跌 扣分
    vr = (ev.get("quote") or {}).get("vol_ratio")
    bull = bool((ev.get("ind") or {}).get("ma_bull"))
    if chg is not None and vr is not None:
        if chg > 0 and vr >= 1.2 and bull:
            score += 1
            parts.append("放量上涨+多头 +1")
        elif chg < 0 and vr >= 1.2:
            score -= 1
            parts.append("放量下跌 -1")

    return {
        "score": score,
        "parts": parts,
        "detail": "；".join(parts) if parts else "无胜率信号",
        "max": WINRATE_SCORE_MAX,
    }


def winrate_score_v2(ev: dict, cfg: Optional[Config] = None) -> dict:
    """V2 影子分：只保留样本仍支持的板块排名与涨幅，不参与任何闸门。

    63 条 T+3 样本中，rank 4-5 是最差桶（T+3 25%、T+1≥3% 10%），而阶段/身份/
    量价在 rank≤3 池内没有区分度；V2 因此去掉这些权重，与 V1 并行落盘对照。
    公式放在 shadow_lab，历史样本回算与实时打分共用同一实现。
    """
    return shadow_lab_mod.winrate_v2_parts(
        (ev.get("sector") or {}).get("rank"),
        (ev.get("quote") or {}).get("chg_pct"))


def apply_winrate_scores(ev: dict, cfg: Optional[Config] = None) -> dict:
    """同时写入 V1 闸门分与 V2 影子分，返回 V1 结果供调用方判门槛。"""
    wr = winrate_score(ev, cfg)
    ev["winrate_score"] = wr["score"]
    ev["winrate_detail"] = wr.get("detail")
    v2 = winrate_score_v2(ev, cfg)
    ev["winrate_score_v2"] = v2["score"]
    ev["winrate_score_v2_detail"] = v2.get("detail")
    return wr


def format_resonance_score(score: Optional[int], threshold: Optional[int],
                           max_score: Optional[int] = None,
                           style: str = "plain") -> str:
    """共振分展示：6/9 门槛7（得分/满分，门槛动态）。style: plain | console | md"""
    if score is None or threshold is None:
        return "—"
    mx = int(max_score if max_score is not None else RESONANCE_SCORE_MAX)
    text = f"{score}/{mx} 门槛{threshold}"
    if style == "console":
        from tea.core import utils as _utils
        color = _utils.COLOR_PROFIT if score >= threshold else _utils.COLOR_WARN
        return _utils.hl(text, color)
    if style == "md":
        return f"**{text}**"
    return text


def format_winrate_score(score: Optional[int], threshold: int,
                         max_score: int = WINRATE_SCORE_MAX,
                         style: str = "plain") -> str:
    """胜率分展示：3/7 门槛≥3（满分 7，门槛默认 3）。style: plain | console | md"""
    if score is None:
        return "—"
    text = f"{score}/{max_score} 门槛≥{threshold}"
    if style == "console":
        from tea.core import utils as _utils
        color = _utils.COLOR_PROFIT if score >= threshold else _utils.COLOR_WARN
        return _utils.hl(text, color)
    if style == "md":
        return f"**{text}**"
    return text


def format_winrate_t3_prob(prob: Optional[float], min_prob: Optional[float] = None,
                           cfg: Optional[Config] = None,
                           style: str = "plain") -> str:
    """T+3>0 获利概率展示：34% 门槛≥35%（实证对照，非收益承诺）。"""
    if prob is None:
        return "—"
    cfg = cfg or load_config()
    th = float(min_prob if min_prob is not None
               else cfg.get("winrate.t3_prob.min_prob", 0.35))
    text = f"{prob:.0%} 门槛≥{th:.0%}"
    if style == "console":
        from tea.core import utils as _utils
        color = _utils.COLOR_PROFIT if prob >= th else _utils.COLOR_WARN
        return _utils.hl(text, color)
    if style == "md":
        return f"**{text}**"
    return text


def apply_winrate_t3(ev: dict, cfg: Optional[Config] = None) -> dict:
    """计算并写入 ``winrate_t3_prob`` / ``winrate_t3_detail`` / ``winrate_t3_min``。"""
    from tea.analysis import followthrough as ft
    cfg = cfg or load_config()
    t3 = ft.estimate_t3_up_prob(ev, cfg)
    ev["winrate_t3_prob"] = t3.get("prob")
    ev["winrate_t3_detail"] = t3.get("detail")
    ev["winrate_t3_min"] = t3.get("min_prob")
    return t3


# ------------------------------------------------------------------ 预审主函数

def evaluate(code: str, market: Optional[Market] = None, cfg: Optional[Config] = None,
                    sent: Optional[dict] = None, quote: Optional[dict] = None,
                    sector: Optional[dict] = None, has_news: Optional[bool] = None,
                    sl_pct: Optional[float] = None, tp_pct: Optional[float] = None,
                    insufficient_samples: bool = False,
                    prefer_sector: Optional[str] = None) -> dict:
    """轻量版 9 分共振预审：行情 → 身份 → VETO → 评分 → 判定。"""
    cfg = cfg or load_config()
    mk = market or Market(cfg)
    code = utils.norm_code(code)
    _LOG.info("共振预审开始 code=%s", code)
    try:
        q = quote or mk.get_quote(code)
    except Exception as exc:
        _LOG.warning("共振预审中止 code=%s 行情不可用: %s", code, exc)
        raise
    code = q["code"]
    try:
        ind = mk.get_indicators(code, q.get("price"))
    except Exception as exc:
        _LOG.warning("共振预审中止 code=%s 指标不可用: %s", code, exc)
        raise
    sec = sector if sector is not None else mk.sector_context(q, prefer=prefer_sector)
    idn = ident_mod.judge(q, sec, ind, cfg)
    intr = intraday_position(q.get("price"), q.get("high"), q.get("low"))
    # 分时否决的会话判定：仅盘中交易时段生效；盘前/午间/盘后由 veto.check 跳过并留痕。
    in_session = Timing(cfg).in_session()
    stage = classify_stage(q, ind, idn, intr, cfg)
    vt = veto_mod.check(q, ind, idn, intr, cfg, in_session=in_session)
    if vt.get("intraday_skipped"):
        logger_mod.get_logger("veto").info("分时否决跳过 %s %s：%s",
                                           code, q.get("name"), vt.get("intraday_note"))

    price = q.get("price")
    if price and sl_pct is not None:
        tp = tp_pct if tp_pct is not None else min(
            min_tp_for_odds(price, sl_pct, float(cfg.s("min_odds", 3)), cfg),
            float(cfg.s("atr_tp_cap_pct", 15.0)))
        levels = odds_calc(price, sl_pct, tp, cfg)
        levels.update({"atr_pct": ind.get("atr_pct"), "source": "手动",
                       "sl_atr_mult": round(sl_pct / ind["atr_pct"], 2) if ind.get("atr_pct") else None,
                       "odds_ok": (levels.get("odds") or 0) >= float(cfg.s("min_odds", 3)),
                       "notes": ["手动输入止损止盈"]})
    elif price:
        # 日K最后一根非今日时，ATR 基于昨日/更早收盘，不能拿旧波动当今天的风险。
        # 传 None 走固定止损回退；止损结构维度在 score_nine 里据此归零，这里的
        # levels 仅作计划/报告上的保守兜底，不把 stale ATR 带入止损止盈。
        atr_pct = None if ind.get("kline_stale") else ind.get("atr_pct")
        levels = compute_levels(price, atr_pct, cfg)
    else:
        levels = {}

    scored = score_nine(q, ind, sec, sent, levels, has_news, cfg)
    th = effective_threshold(idn, sent, insufficient_samples, cfg,
                             max_score=scored.get("max") or 9)
    total, threshold = scored["total"], th["threshold"]

    reasons: List[str] = []
    # 门槛用绝对分（total >= threshold），刻意不用 total/max 归一化：满分 max 会随
    # 消息面中性化在 8~9 之间浮动，绝对门槛 6 是一条固定保守线，避免 max 变化让门槛漂移。
    core_pts, core_th = scored.get("core_total"), th.get("core_threshold")
    if vt["rejected"]:
        verdict = VERDICT_REJECT
        reasons.append("硬否决：" + "；".join(i["label"] for i in vt["hard"]))
    elif vt["watchable"]:
        verdict = VERDICT_WATCH
        reasons.append("软否决：" + "；".join(i["label"] for i in vt["soft"]) + " → 等回踩")
    elif not gate_failed(total, threshold, core_pts, core_th):
        verdict = VERDICT_PASS
    elif total < threshold and threshold - total <= int(cfg.get("seed.near_miss_gap", 1)):
        verdict = VERDICT_WATCH
        reasons.append(f"共振分 {total}/{threshold} 差 {threshold - total} 分 → 观察轨")
    elif total < threshold:
        verdict = VERDICT_NEAR_MISS
        reasons.append(f"共振分 {total}/{threshold} 差 {threshold - total} 分 → 近失")
    else:
        verdict = VERDICT_NEAR_MISS
        reasons.append(f"共振 {total}/{threshold} 过线，但有效分 {core_pts}/{core_th} "
                       f"差 {core_th - core_pts} 分 → 近失")
    shortfall = format_resonance_shortfall(dims=scored.get("dims"),
                                           total=total, threshold=threshold,
                                           ev={"scoring": scored, "pass_threshold": threshold,
                                               "core_threshold": core_th})
    if shortfall:
        reasons.append(shortfall)
    if not (levels.get("odds_ok", True)):
        reasons.append(f"R:R {utils.num(levels.get('odds'))} < {cfg.s('min_odds', 3)}")
        if verdict == VERDICT_PASS:
            verdict = VERDICT_NEAR_MISS
    if ident_mod.rejects_zamao(cfg) and ident_mod.is_zamao(idn):
        verdict = VERDICT_REJECT
        reasons.append("杂毛（reject 模式）")

    dim_brief = " ".join(
        f"{d.get('name', '?')}{d.get('score', '?')}" for d in (scored.get("dims") or []))
    _LOG.info(
        "共振预审 %s %s | %d/%d %s | stage=%s identity=%s | %s | %s",
        code, q.get("name"), total, threshold, verdict,
        (stage or {}).get("stage"), idn.get("tier"),
        "；".join(reasons[:3]) or "—", dim_brief)
    if ind.get("kline_stale"):
        _LOG.warning("日K非今日 %s %s → 量价/止损维可能归零", code, q.get("name"))
    if vt.get("rejected"):
        _LOG.info("硬否决 %s %s: %s", code, q.get("name"),
                  "；".join(i.get("label", "") for i in vt.get("hard") or []))

    return {
        "code": code, "name": q.get("name"),
        "quote": q, "ind": ind, "sector": sec, "identity": idn,
        "intraday": intr, "in_session": in_session,
        "stage": stage, "veto": vt, "levels": levels,
        "scoring": scored, "threshold": th,
        "total_score": total, "pass_threshold": threshold,
        "core_score": scored.get("core_total"), "core_threshold": th.get("core_threshold"),
        "gap": threshold - total,
        "odds": levels.get("odds"), "sl_pct": levels.get("sl_pct"), "tp_pct": levels.get("tp_pct"),
        "verdict": verdict, "reasons": reasons,
        "ts": utils.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ------------------------------------------------------------------ 快照/展示

def snapshot(ev: dict) -> dict:
    """用于计划落盘/变动检测的精简快照。"""
    q, sec, idn, lv = ev.get("quote") or {}, ev.get("sector") or {}, ev.get("identity") or {}, ev.get("levels") or {}
    return {
        "code": ev.get("code"), "name": ev.get("name"),
        "price": q.get("price"), "chg_pct": q.get("chg_pct"),
        "turnover": q.get("turnover"), "cap_yi": q.get("cap_yi"),
        "amount_yi": q.get("amount_yi"), "vol_ratio": q.get("vol_ratio"),
        "sector_name": sec.get("name"), "sector_bk": sec.get("bk"),
        "sector_rank": sec.get("rank"), "sector_chg": sec.get("chg"),
        "sector_limit_ups": sec.get("limit_up_count"),
        "inner_rank": sec.get("stock_rank"), "inner_rank_pct": sec.get("stock_rank_pct"),
        "identity_score": idn.get("score"), "identity_tier": idn.get("tier"),
        "bias_ma20": (ev.get("ind") or {}).get("bias_ma20"),
        "bb_mid": (ev.get("ind") or {}).get("bb_mid"),
        "bb_upper": (ev.get("ind") or {}).get("bb_upper"),
        "bb_lower": (ev.get("ind") or {}).get("bb_lower"),
        "bb_pct_b": (ev.get("ind") or {}).get("bb_pct_b"),
        "bb_bandwidth": (ev.get("ind") or {}).get("bb_bandwidth"),
        "atr_pct": (ev.get("ind") or {}).get("atr_pct"),
        "ma_bull": (ev.get("ind") or {}).get("ma_bull"),
        "intraday": ev.get("intraday"), "stage": (ev.get("stage") or {}).get("stage"),
        "total_score": ev.get("total_score"), "pass_threshold": ev.get("pass_threshold"),
        # 有效分两把尺子：计划复核要用和选票时同一套判定，缺了就只能比总分。
        "core_score": (ev.get("scoring") or {}).get("core_total"),
        "core_threshold": ev.get("core_threshold"),
        # 六维拆解（不含 detail，只留可对比的得分）：计划复核时据此指出「共振分
        # 具体掉在哪一维」，而不是只报一句 6<7 让人猜。
        "scoring_dims": [
            {"no": d.get("no"), "name": d.get("name"), "score": d.get("score"),
             "max": d.get("max")}
            for d in (ev.get("scoring") or {}).get("dims", [])
        ],
        "odds": lv.get("odds"), "sl_pct": lv.get("sl_pct"), "tp_pct": lv.get("tp_pct"),
        "verdict": ev.get("verdict"), "ts": ev.get("ts"),
    }


def format_scoring(ev: dict) -> str:
    sc = ev.get("scoring") or {}
    lines = [f"===== 术 · 9 分共振（{sc.get('total')}/{sc.get('max')}，门槛 {ev.get('pass_threshold')}）====="]
    for d in sc.get("dims", []):
        lines.append(f"  {d['no']}. {d['name']} {d['score']}/{d['max']} — {d['detail']}")
    lines.append("门槛构成：" + " / ".join((ev.get("threshold") or {}).get("notes", [])))
    return "\n".join(lines)


def format_levels(ev: dict) -> str:
    lv = ev.get("levels") or {}
    if not lv:
        return "止损止盈：数据不足"
    return ("止损止盈（{src}）：买入 {e} / 止损 {s}（-{sl}%）/ 止盈 {t}（+{tp}%）\n"
            "含滑点 R:R {odds}（打平胜率 {bw}）ATR% {atr}").format(
        src=lv.get("source"), e=utils.num(lv.get("entry")), s=utils.num(lv.get("stop")),
        sl=utils.num(lv.get("sl_pct")), t=utils.num(lv.get("target")), tp=utils.num(lv.get("tp_pct")),
        odds=utils.num(lv.get("odds")),
        bw=(f"{lv['breakeven_wr']:.1%}" if lv.get("breakeven_wr") else "—"),
        atr=utils.num(lv.get("atr_pct")))


def format_evaluation(ev: dict) -> str:
    q = ev.get("quote") or {}
    sec = ev.get("sector") or {}
    parts = [
        f"===== {ev.get('code')} {ev.get('name')} =====",
        f"现价 {utils.num(q.get('price'))}  涨幅 {utils.pct(q.get('chg_pct'))}  "
        f"换手 {utils.pct(q.get('turnover'))}  量比 {utils.num(q.get('vol_ratio'))}  "
        f"成交额 {utils.num(q.get('amount_yi'))}亿  市值 {utils.num(q.get('cap_yi'))}亿",
        f"板块 {sec.get('name') or '未识别'}（第 {sec.get('rank')} 名，涨幅 {utils.pct(sec.get('chg'))}，"
        f"涨停 {sec.get('limit_up_count')} 家）板块内 {sec.get('stock_rank')}/{sec.get('member_total')}",
        f"分时位置 {('%.0f%%' % (ev['intraday'] * 100)) if ev.get('intraday') is not None else '—'}  "
        f"阶段 {(ev.get('stage') or {}).get('stage')}  乖离 {utils.pct((ev.get('ind') or {}).get('bias_ma20'))}",
        format_bollinger(ev.get("ind") or {}),
        ident_mod.format_identity(ev.get("identity") or {}),
        veto_mod.format_veto(ev.get("veto") or {}),
        format_levels(ev),
        format_scoring(ev),
        f"结论：{ev.get('verdict')}" + ("（" + "；".join(ev.get("reasons", [])) + "）" if ev.get("reasons") else ""),
    ]
    return "\n".join(parts)
