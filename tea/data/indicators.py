"""技术指标：纯函数，不联网。

输入 K 线列表（`{date, open, close, high, low, volume, amount}`），输出指标值。
与抓取层完全解耦，因此可以直接用构造数据测试——离线自测正是这么做的。

`compute_indicators` 额外输出 `kline_date` / `kline_stale`：当日K最后一根不是
「今日最近数据」（交易日即今天，非交易日/盘前即上一交易日）时标记 stale，
供共振评分弃用该指标。stale 判定用 `today` 参数可覆盖，离线测试据此保持确定性。
"""
from __future__ import annotations

from typing import List, Optional

from tea.core import utils


def ma(klines: List[dict], n: int) -> Optional[float]:
    closes = [k.get("close") for k in klines if k.get("close") is not None]
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def ma_slope_pct(klines: List[dict], n: int = 20, lookback: int = 5) -> Optional[float]:
    """MA(n) 近 lookback 个交易日的涨跌斜率（%）。

    更稳的趋势定义不再用「现价是否高于 MA」这种单点比较，而是看 MA 本身在
    抬升还是下压：MA 上行意味着中期成本重心在上移，即使现价短暂跌破 MA，
    趋势也未必转坏（反之亦然）。klines 需 ≥ n + lookback 根才够算。
    """
    closes = [k.get("close") for k in klines if k.get("close") is not None]
    if len(closes) < n + lookback:
        return None
    now = sum(closes[-n:]) / n
    prev = sum(closes[-(n + lookback):-lookback]) / n
    if not prev:
        return None
    return (now - prev) / prev * 100.0


def bollinger(klines: List[dict], n: int = 20, k: float = 2.0) -> dict:
    """布林线（Bollinger Bands，默认 N=20、K=2）。

    中轨 MB = SMA(n)；σ 为近 n 日收盘价的总体标准差（÷n，与东财/同花顺对齐）；
    上轨 UB = MB + K·σ，下轨 LB = MB − K·σ。
    仅观测因子，不参与共振评分与 VETO。
    """
    closes = [c.get("close") for c in klines if c.get("close") is not None]
    if len(closes) < n:
        return {"bb_mid": None, "bb_upper": None, "bb_lower": None}
    window = closes[-n:]
    mid = sum(window) / n
    var = sum((x - mid) ** 2 for x in window) / n
    std = var ** 0.5
    return {
        "bb_mid": mid,
        "bb_upper": mid + k * std,
        "bb_lower": mid - k * std,
    }


def bb_derived(px: Optional[float], bb: dict) -> dict:
    """%B 与带宽：需现价与上下轨齐全。"""
    upper, lower, mid = bb.get("bb_upper"), bb.get("bb_lower"), bb.get("bb_mid")
    pct_b, bandwidth = None, None
    if px is not None and upper is not None and lower is not None and upper > lower:
        pct_b = (px - lower) / (upper - lower)
    if mid and upper is not None and lower is not None and mid > 0:
        bandwidth = (upper - lower) / mid * 100.0
    return {"bb_pct_b": pct_b, "bb_bandwidth": bandwidth}


def format_bollinger(ind: dict) -> str:
    """控制台一行布林摘要。"""
    ub, mb, lb = ind.get("bb_upper"), ind.get("bb_mid"), ind.get("bb_lower")
    if ub is None or mb is None or lb is None:
        return "布林 —"
    pct_b = ind.get("bb_pct_b")
    bw = ind.get("bb_bandwidth")
    pct_s = f"{pct_b * 100:.0f}%" if pct_b is not None else "—"
    bw_s = f"{bw:.1f}%" if bw is not None else "—"
    return (
        f"布林 UB/MB/LB {utils.num(ub)}/{utils.num(mb)}/{utils.num(lb)}"
        f"  %B {pct_s}  带宽 {bw_s}"
    )


def atr(klines: List[dict], n: int = 14) -> Optional[float]:
    """ATR(n) = 最近 n 日 TR 均值；TR = max(H-L, |H-昨收|, |L-昨收|)。"""
    if len(klines) < n + 1:
        return None
    trs = []
    for i in range(1, len(klines)):
        cur, prev = klines[i], klines[i - 1]
        h, l, pc = cur.get("high"), cur.get("low"), prev.get("close")
        if None in (h, l, pc):
            continue
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < n:
        return None
    return sum(trs[-n:]) / n


def compute_indicators(klines: List[dict], price: Optional[float] = None,
                       today: Optional[str] = None) -> dict:
    last_close = klines[-1]["close"] if klines else None
    px = price if price is not None else last_close
    ma5, ma10, ma20 = ma(klines, 5), ma(klines, 10), ma(klines, 20)
    bb = bollinger(klines, 20, 2.0)
    bb_extra = bb_derived(px, bb)
    a = atr(klines, 14)
    bias = None
    if px is not None and ma20:
        bias = (px - ma20) / ma20 * 100.0
    # 日K最后一根必须是「今日最近数据」（交易日即今天，非交易日/盘前即上一交易日）。
    # 更旧则技术指标全部基于昨日/更早收盘，共振评分据此弃用该指标（见 preflight.score_nine）。
    kline_date = klines[-1].get("date") if klines else None
    ref = today or utils.latest_trading_day_str()
    kline_stale = bool(kline_date and kline_date != ref)
    return {
        "ma5": ma5, "ma10": ma10, "ma20": ma20,
        "ma_bull": bool(ma5 and ma10 and ma20 and ma5 >= ma10 >= ma20),
        "above_ma20": bool(px is not None and ma20 and px > ma20),
        "atr": a,
        "atr_pct": (a / px * 100.0) if (a and px) else None,
        "bias_ma20": bias,
        "bb_mid": bb.get("bb_mid"),
        "bb_upper": bb.get("bb_upper"),
        "bb_lower": bb.get("bb_lower"),
        "bb_pct_b": bb_extra.get("bb_pct_b"),
        "bb_bandwidth": bb_extra.get("bb_bandwidth"),
        "prev_close": klines[-2]["close"] if len(klines) >= 2 else None,
        "kline_n": len(klines),
        "kline_date": kline_date,
        "kline_stale": kline_stale,
    }


def count_limit_ups(members: List[dict], tolerance: float = 0.2) -> int:
    """统计成分股涨停家数（按代码判定 10%/20%/5% 涨停幅）。"""
    n = 0
    for m in members:
        chg = m.get("chg")
        if chg is None:
            continue
        cap = utils.limit_up_pct(m.get("code", ""), m.get("name", ""))
        if chg >= cap - tolerance:
            n += 1
    return n


def intraday_position(price: Optional[float], high: Optional[float], low: Optional[float]) -> Optional[float]:
    """分时位置 = (现价-最低)/(最高-最低)，0~1。"""
    if None in (price, high, low) or high is None or low is None:
        return None
    rng = high - low
    if rng <= 0:
        return 0.5
    return utils.clamp((price - low) / rng, 0.0, 1.0)
