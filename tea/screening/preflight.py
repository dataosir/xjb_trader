"""术 — 9 分共振评分 / ATR 止损止盈 / 含滑点盈亏比 / 分时位置 / 启动阶段。

非交互预审（evaluate）等价于 Phase1-4 的评分部分，供种子扫描、计划复核、
观察池回踩复评共用。
"""
from __future__ import annotations

from typing import List, Optional

from tea.analysis import identity as ident_mod
from tea.config.config_store import Config, load_config
from tea.core import logger as logger_mod, utils
from tea.core.timing import Timing
from tea.data import Market, intraday_position
from . import veto as veto_mod

STAGE_SPROUT = "萌芽"
STAGE_BREAK = "突破"
STAGE_OVERHEAT = "过热"

VERDICT_PASS = "PASS"
VERDICT_REJECT = "REJECT"
VERDICT_WATCH = "WATCH"
VERDICT_NEAR_MISS = "NEAR_MISS"


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

    # ② 大盘趋势（分级扣分 -1~+1，扣分封顶 -1）
    # 更稳的趋势定义：位置（点位 vs MA20，带缓冲）+ 方向（MA20 斜率）合成 -2~+2
    # 的态势，再映射成共振分加减——弱势时扣除、强势时 +1，替代原来「涨 + MA20 上」
    # 的二元 0/1 硬判定，也替代已被移除的大盘趋势硬闸。
    # 扣分封顶 -1：弱势市满分 6 = 门槛 6，完美票仍有机会过，而不是被锁死。
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

    if point is None and ma20 is None and slope is None:
        s2, d2 = 0, "大盘数据缺失"
    elif trend >= 2:
        s2, d2 = 1, f"大盘强趋势（点位较 MA20 {_sig(bias)}、MA20 斜率 {_sig(slope)}）"
    elif trend == 1:
        s2, d2 = 0, f"大盘偏强未确认（点位较 MA20 {_sig(bias)}、MA20 斜率 {_sig(slope)}）"
    elif trend == 0:
        s2, d2 = 0, f"大盘震荡（点位较 MA20 {_sig(bias)}、MA20 斜率 {_sig(slope)}）"
    elif trend == -1:
        s2, d2 = -1, f"大盘偏弱 → 共振 -1（点位较 MA20 {_sig(bias)}、MA20 斜率 {_sig(slope)}）"
    else:
        s2, d2 = -1, f"大盘弱势 → 共振 -1（点位较 MA20 {_sig(bias)}、MA20 斜率 {_sig(slope)}）"
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
    elif chg is not None and vr is not None and ((chg > 0 and vr >= 1.0) or (chg <= 0 and vr < 1.0)):
        cond = "涨幅>0 且 量比≥1.0" if chg > 0 else "涨幅≤0 且 量比<1.0"
        s5, d5 = 1, f"量价基本配合（{cond}；涨幅 {chg:+.2f}% 量比 {vr:.2f}）"
    elif chg is not None and vr is not None and chg < 0 and vr >= strong:
        s5, d5 = 0, f"放量下跌（涨幅 {chg:+.2f}%<0 且 量比 {vr:.2f}≥{strong:.1f}）"
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
    s5 = int(utils.clamp(s5, 0, int(c("vp_dim_max", 2))))
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
    return {
        "total": int(total),
        # 满分由实际计入的维度决定：消息面中性化时其 max=0，满分从 9 降到 8，
        # 让「X/满分」诚实地反映当前可争取的上限，而不是恒定 9。
        "max": int(sum(d["max"] for d in dims)),
        "dims": dims,
        "penalties": penalties,
    }


# ------------------------------------------------------------------ 8.4 有效门槛

def effective_threshold(identity: Optional[dict] = None, sent: Optional[dict] = None,
                        insufficient_samples: bool = False,
                        cfg: Optional[Config] = None) -> dict:
    """动态通过门槛：base 6 + 杂毛warn +1 + 样本不足 +1，龙头 -1（下限 6），防守 +1。

    龙头 -1 是门槛公式的一部分，不设调用方开关：种子扫描、计划复核、run 执行、
    eval/watch 全走同一套口径，避免「选票按 6 分、复核按 7 分」这类分叉。

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
    return {"threshold": int(th), "base": base, "notes": notes}


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
    q = quote or mk.get_quote(code)
    code = q["code"]
    ind = mk.get_indicators(code, q.get("price"))
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
    th = effective_threshold(idn, sent, insufficient_samples, cfg)
    total, threshold = scored["total"], th["threshold"]

    reasons: List[str] = []
    # 门槛用绝对分（total >= threshold），刻意不用 total/max 归一化：满分 max 会随
    # 消息面中性化在 8~9 之间浮动，绝对门槛 6 是一条固定保守线，避免 max 变化让门槛漂移。
    if vt["rejected"]:
        verdict = VERDICT_REJECT
        reasons.append("硬否决：" + "；".join(i["label"] for i in vt["hard"]))
    elif vt["watchable"]:
        verdict = VERDICT_WATCH
        reasons.append("软否决：" + "；".join(i["label"] for i in vt["soft"]) + " → 等回踩")
    elif total >= threshold:
        verdict = VERDICT_PASS
    elif threshold - total <= int(cfg.get("seed.near_miss_gap", 1)):
        verdict = VERDICT_WATCH
        reasons.append(f"共振分 {total}/{threshold} 差 {threshold - total} 分 → 观察轨")
    else:
        verdict = VERDICT_NEAR_MISS
        reasons.append(f"共振分 {total}/{threshold} 差 {threshold - total} 分 → 近失")
    if not (levels.get("odds_ok", True)):
        reasons.append(f"R:R {utils.num(levels.get('odds'))} < {cfg.s('min_odds', 3)}")
        if verdict == VERDICT_PASS:
            verdict = VERDICT_NEAR_MISS
    if ident_mod.rejects_zamao(cfg) and ident_mod.is_zamao(idn):
        verdict = VERDICT_REJECT
        reasons.append("杂毛（reject 模式）")

    return {
        "code": code, "name": q.get("name"),
        "quote": q, "ind": ind, "sector": sec, "identity": idn,
        "intraday": intr, "in_session": in_session,
        "stage": stage, "veto": vt, "levels": levels,
        "scoring": scored, "threshold": th,
        "total_score": total, "pass_threshold": threshold,
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
        "atr_pct": (ev.get("ind") or {}).get("atr_pct"),
        "ma_bull": (ev.get("ind") or {}).get("ma_bull"),
        "intraday": ev.get("intraday"), "stage": (ev.get("stage") or {}).get("stage"),
        "total_score": ev.get("total_score"), "pass_threshold": ev.get("pass_threshold"),
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
        ident_mod.format_identity(ev.get("identity") or {}),
        veto_mod.format_veto(ev.get("veto") or {}),
        format_levels(ev),
        format_scoring(ev),
        f"结论：{ev.get('verdict')}" + ("（" + "；".join(ev.get("reasons", [])) + "）" if ev.get("reasons") else ""),
    ]
    return "\n".join(parts)
