"""网易源（api.money.126.net）：只有报价，没有 K 线。

代码前缀是反直觉的：**上交所 0**（0600519）、**深交所 1**（1000001），
和 sh/sz 恰好对不上，也和东财 secid 的 1=沪 / 0=深 正好相反。
返回体是 JSONP（`_ntes_quote_callback({...});`），得先剥壳。
"""
from __future__ import annotations

import json

from tea.core import utils
from ..errors import MarketError
from .base import IDataProvider, resolve_symbol


def netease_code(prefix: str, code: str) -> str:
    """sh/sz + 6 位 → 网易代码（沪 0 开头、深 1 开头）。"""
    return ("0" if prefix == "sh" else "1") + code


def _strip_jsonp(raw: str) -> dict:
    """剥掉 `_ntes_quote_callback(...)` 外壳取里面的 JSON。"""
    text = (raw or "").strip()
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        raise MarketError("网易行情响应无法解析")
    js = json.loads(text[i:j + 1])
    return js if isinstance(js, dict) else {}


class NeteaseProvider(IDataProvider):
    """网易财经：仅实时报价（K 线与指数快照保持不支持，链上会被静默跳过）。"""

    name = "netease"
    DEFAULT_TIMEOUTS = {"quote": 3.0}

    def fetch_quote(self, code: str) -> dict:
        prefix, c = resolve_symbol(code)
        if len(c) != 6:
            raise MarketError(f"非法股票代码: {code}")
        nc = netease_code(prefix, c)
        raw = self.fetcher.get_text(self.cfg.get("market.netease_quote_url") + nc)
        d = _strip_jsonp(raw).get(nc) or {}
        price = utils.to_float(d.get("price"))
        if not price:
            raise MarketError(f"网易行情无有效价格: {c}")
        pre_close = utils.to_float(d.get("yestclose"))
        pct = utils.to_float(d.get("percent"))
        chg_amt = utils.to_float(d.get("updown"))
        if chg_amt is None and pre_close is not None:
            chg_amt = round(price - pre_close, 4)
        vol_shares = utils.to_float(d.get("volume"))
        amount = utils.to_float(d.get("turnover"))
        name = d.get("name") or ""
        return {
            "code": c,
            "mkt": utils.market_of(c),
            "name": name,
            "price": price,
            "high": utils.to_float(d.get("high")),
            "low": utils.to_float(d.get("low")),
            "open": utils.to_float(d.get("open")),
            "volume": (vol_shares / 100.0) if vol_shares is not None else None,  # 股 → 手
            "amount_yi": (amount / 1e8) if amount is not None else None,         # 元 → 亿
            # 网易的 turnover 是成交额（元）而不是换手率，别被名字骗了；
            # 换手率/量比/市值它一概不给，一律留 None。
            "vol_ratio": None,
            "turnover": None,
            # percent 是小数（0.0213 = 2.13%），乘 100 才是本项目的口径。
            "chg_pct": (round(pct * 100, 4) if pct is not None else None),
            "chg_amt": chg_amt,
            "pre_close": pre_close,
            "cap_yi": None,
            "float_cap_yi": None,
            "industry": "",
            "board": utils.board_of(c),
            "limit_up_pct": utils.limit_up_pct(c, name),
            "is_st": utils.is_st(name),
            "ts": utils.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
