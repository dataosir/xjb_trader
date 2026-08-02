"""技术指标：纯函数，不联网。

输入 K 线列表（`{date, open, close, high, low, volume, amount}`），输出指标值。
与抓取层完全解耦，因此可以直接用构造数据测试——离线自测正是这么做的。
"""
from __future__ import annotations

from typing import List, Optional

from .. import utils


def ma(klines: List[dict], n: int) -> Optional[float]:
    closes = [k.get("close") for k in klines if k.get("close") is not None]
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


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


def compute_indicators(klines: List[dict], price: Optional[float] = None) -> dict:
    last_close = klines[-1]["close"] if klines else None
    px = price if price is not None else last_close
    ma5, ma10, ma20 = ma(klines, 5), ma(klines, 10), ma(klines, 20)
    a = atr(klines, 14)
    bias = None
    if px is not None and ma20:
        bias = (px - ma20) / ma20 * 100.0
    return {
        "ma5": ma5, "ma10": ma10, "ma20": ma20,
        "ma_bull": bool(ma5 and ma10 and ma20 and ma5 >= ma10 >= ma20),
        "above_ma20": bool(px is not None and ma20 and px > ma20),
        "atr": a,
        "atr_pct": (a / px * 100.0) if (a and px) else None,
        "bias_ma20": bias,
        "prev_close": klines[-2]["close"] if len(klines) >= 2 else None,
        "kline_n": len(klines),
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
