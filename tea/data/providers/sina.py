"""新浪源（hq.sinajs.cn + money.finance.sina.com.cn）。

两个坑：
- 报价必须带 `Referer: https://finance.sina.com.cn`，否则 403；编码固定 GB2312。
- K 线接口回的是 JS 对象字面量（键不带引号），`json.loads` 直接报错，得先补引号。
单位上成交量是**股**，东财是手，差 100 倍。
"""
from __future__ import annotations

import json
import re
from typing import List, Optional

from ... import utils
from ..errors import MarketError
from .base import IDataProvider, index_double_route, resolve_symbol

# hq.sinajs.cn 的逗号分隔下标（股票与指数同一套布局）
_NAME, _OPEN, _PRE_CLOSE, _PRICE, _HIGH, _LOW = 0, 1, 2, 3, 4, 5
_VOLUME_SHARES, _AMOUNT_YUAN = 8, 9

_MIN_FIELDS = 10
#: 给 JS 字面量的裸键补引号：{day:"2026-08-03" → {"day":"2026-08-03"
_BARE_KEY = re.compile(r'([{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:')


def _loads_loose(raw: str) -> object:
    """先按标准 JSON 试，失败再把裸键补上引号重试。"""
    try:
        return json.loads(raw)
    except ValueError:
        return json.loads(_BARE_KEY.sub(r'\1"\2":', raw))


class SinaProvider(IDataProvider):
    """新浪财经：报价 + 日 K + 指数快照。"""

    name = "sina"
    DEFAULT_TIMEOUTS = {"quote": 4.0, "klines": 6.0, "index": 6.0}

    def _headers(self) -> dict:
        # 缺 Referer 直接 403，这不是防封优化而是硬性要求，所以走 per-request 头
        # 覆盖，不依赖 referers 轮换池（那池子里全是东财的页面）。
        return {"Referer": self.cfg.get("market.sina_referer",
                                        "https://finance.sina.com.cn")}

    # -------------------------------------------------- 报价
    def _quote_fields(self, symbol: str) -> List[str]:
        raw = self.fetcher.get_text(self.cfg.get("market.sina_quote_url") + symbol,
                                    encoding="gb2312", extra_headers=self._headers())
        m = re.search(r'hq_str_' + re.escape(symbol) + r'="([^"]*)"', raw or "")
        if not m:
            raise MarketError(f"新浪行情解析失败: {symbol}")
        fields = m.group(1).split(",")
        if len(fields) < _MIN_FIELDS:
            raise MarketError(f"新浪行情字段不足({len(fields)}): {symbol}")
        return fields

    def fetch_quote(self, code: str) -> dict:
        prefix, c = resolve_symbol(code)
        if len(c) != 6:
            raise MarketError(f"非法股票代码: {code}")
        f = self._quote_fields(f"{prefix}{c}")
        g = lambda i: utils.to_float(f[i]) if i < len(f) else None
        price = g(_PRICE)
        if not price:
            raise MarketError(f"新浪行情无有效价格: {c}")
        pre_close = g(_PRE_CLOSE)
        vol_shares = g(_VOLUME_SHARES)
        amount = g(_AMOUNT_YUAN)
        name = f[_NAME] or ""
        return {
            "code": c,
            "mkt": utils.market_of(c),
            "name": name,
            "price": price,
            "high": g(_HIGH),
            "low": g(_LOW),
            "open": g(_OPEN),
            "volume": (vol_shares / 100.0) if vol_shares is not None else None,  # 股 → 手
            "amount_yi": (amount / 1e8) if amount is not None else None,
            # 新浪报价不给量比/换手率/市值，宁缺勿造：上游对 None 都有容错分支。
            "vol_ratio": None,
            "turnover": None,
            "chg_pct": (round((price / pre_close - 1) * 100, 4) if pre_close else None),
            "chg_amt": (round(price - pre_close, 4) if pre_close is not None else None),
            "pre_close": pre_close,
            "cap_yi": None,
            "float_cap_yi": None,
            "industry": "",
            "board": utils.board_of(c),
            "limit_up_pct": utils.limit_up_pct(c, name),
            "is_st": utils.is_st(name),
            "ts": utils.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # -------------------------------------------------- 日 K
    def fetch_klines(self, code: str, limit: Optional[int] = None,
                     secid: Optional[str] = None) -> List[dict]:
        lmt = int(limit or self.cfg.get("market.kline_limit", 30))
        prefix, c = resolve_symbol(code, secid)
        raw = self.fetcher.get_text(self.cfg.get("market.sina_kline_url"), {
            "symbol": f"{prefix}{c}",
            "scale": int(self.cfg.get("market.sina_kline_scale", 240)),  # 240 分钟＝日线
            # 官方硬限 1023 个节点，多要不给。
            "datalen": min(max(lmt, 1), int(self.cfg.get("market.sina_kline_max", 1023))),
            "ma": "no",
        }, extra_headers=self._headers())
        rows = _loads_loose(raw) if (raw or "").strip() else []
        if not isinstance(rows, list):
            raise MarketError(f"新浪 K 线格式异常: {prefix}{c}")
        out = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            vol = utils.to_float(r.get("volume"))
            out.append({
                "date": str(r.get("day") or "")[:10],
                "open": utils.to_float(r.get("open")),
                "close": utils.to_float(r.get("close")),
                "high": utils.to_float(r.get("high")),
                "low": utils.to_float(r.get("low")),
                "volume": (vol / 100.0) if vol is not None else None,  # 股 → 手
                "amount": None,
            })
        return out[-lmt:]

    # -------------------------------------------------- 指数
    def fetch_index_snapshot(self, secid: Optional[str] = None) -> dict:
        sid = secid or self.cfg.get("market.index_secid", "1.000001")
        lmt = int(self.cfg.get("market.index_kline_limit", 25))
        return index_double_route(lambda: self._point_chg(sid),
                                  lambda: self.fetch_klines("", limit=lmt, secid=sid))

    def _point_chg(self, secid: str) -> tuple:
        prefix, c = resolve_symbol("", secid)
        f = self._quote_fields(f"{prefix}{c}")
        point = utils.to_float(f[_PRICE])
        pre = utils.to_float(f[_PRE_CLOSE])
        # 新浪不给涨跌幅字段，按昨收算——比猜一个下标可靠。
        chg = round((point / pre - 1) * 100, 2) if (point and pre) else None
        return point, chg
