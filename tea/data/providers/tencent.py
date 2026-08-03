"""腾讯源（qt.gtimg.cn + web.ifzq.gtimg.cn）。

报价是 GBK 的一行纯文本，字段用 `~` 分隔、只能按下标取——所以下标表就是这份
实现的全部风险。单位也和东财不一样：成交额是万元（东财是元），得自己换成亿。
"""
from __future__ import annotations

import re
from typing import List, Optional

from ... import utils
from ..errors import MarketError
from .base import IDataProvider, index_double_route, resolve_symbol

# qt.gtimg.cn 报价的 `~` 分隔下标。腾讯从不出文档，这份对照按社区长期验证的版本
# （easyquotation 等）取：44 流通市值(亿)、45 总市值(亿)、46 市净率、49 量比。
# Sam 的调研报告把总市值记在 45、流通市值记在 46、量比记在 47——46 实际是市净率，
# 照那份下标取会把「流通市值 1.2」这种荒唐值喂进市值打分。下面另有一道
# 「流通市值不得大于总市值」的兜底，错位时至少不会把两者取反。
_NAME, _PRICE, _PRE_CLOSE, _OPEN, _VOLUME = 1, 3, 4, 5, 6
_CHG_AMT, _CHG_PCT, _HIGH, _LOW = 31, 32, 33, 34
_AMOUNT_WAN, _TURNOVER = 37, 38
_FLOAT_CAP_YI, _CAP_YI, _VOL_RATIO = 44, 45, 49

_MIN_FIELDS = 46  # 少于这个数说明不是完整报价行（停牌/错码时腾讯回一段短文本）


class TencentProvider(IDataProvider):
    """腾讯财经：报价 + 前复权日 K + 指数快照。"""

    name = "tencent"
    DEFAULT_TIMEOUTS = {"quote": 4.0, "klines": 6.0, "index": 6.0}

    # -------------------------------------------------- 报价
    def _quote_fields(self, symbol: str) -> List[str]:
        raw = self.fetcher.get_text(self.cfg.get("market.tencent_quote_url") + symbol,
                                    encoding="gbk")
        m = re.search(r'v_' + re.escape(symbol) + r'="([^"]*)"', raw or "")
        if not m:
            raise MarketError(f"腾讯行情解析失败: {symbol}")
        fields = m.group(1).split("~")
        if len(fields) < _MIN_FIELDS:
            raise MarketError(f"腾讯行情字段不足({len(fields)}): {symbol}")
        return fields

    def fetch_quote(self, code: str) -> dict:
        prefix, c = resolve_symbol(code)
        if len(c) != 6:
            raise MarketError(f"非法股票代码: {code}")
        f = self._quote_fields(f"{prefix}{c}")
        g = lambda i: utils.to_float(f[i]) if i < len(f) else None
        price = g(_PRICE)
        if not price:
            raise MarketError(f"腾讯行情无有效价格: {c}")
        pre_close = g(_PRE_CLOSE)
        chg_amt = g(_CHG_AMT)
        if chg_amt is None and pre_close is not None:
            chg_amt = round(price - pre_close, 4)
        cap, float_cap = g(_CAP_YI), g(_FLOAT_CAP_YI)
        if cap is not None and float_cap is not None and float_cap > cap:
            cap, float_cap = float_cap, cap
        amount_wan = g(_AMOUNT_WAN)
        name = f[_NAME] or ""
        return {
            "code": c,
            "mkt": utils.market_of(c),
            "name": name,
            "price": price,
            "high": g(_HIGH),
            "low": g(_LOW),
            "open": g(_OPEN),
            "volume": g(_VOLUME),                       # 腾讯本来就是手
            "amount_yi": (amount_wan / 1e4) if amount_wan is not None else None,  # 万 → 亿
            "vol_ratio": g(_VOL_RATIO),
            "turnover": g(_TURNOVER),
            "chg_pct": g(_CHG_PCT),
            "chg_amt": chg_amt,
            "pre_close": pre_close,
            "cap_yi": cap,
            "float_cap_yi": float_cap,
            "industry": "",                             # 腾讯报价不带行业
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
        symbol = f"{prefix}{c}"
        # param 里的逗号与空字段（`sh600519,day,,,30,qfq`）直接拼在 URL 上，不过
        # urlencode：转成 %2C 后能不能被腾讯那边认没保证，而原样拼是各家客户端
        # 实测跑得通的形式。
        url = f"{self.cfg.get('market.tencent_kline_url')}?param={symbol},day,,,{lmt},qfq"
        js = self.fetcher.get_json(url, {})
        data = (js.get("data") or {}).get(symbol) or {}
        # qfq 参数下键是 qfqday；接口偶尔只给不复权的 day，聊胜于无。
        rows = data.get("qfqday") or data.get("day") or []
        out = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            out.append({
                "date": str(row[0]),
                "open": utils.to_float(row[1]),
                "close": utils.to_float(row[2]),        # 腾讯与东财同序：开/收/高/低
                "high": utils.to_float(row[3]),
                "low": utils.to_float(row[4]),
                "volume": utils.to_float(row[5]),       # 手
                "amount": None,                         # 腾讯日 K 不给成交额
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
        return utils.to_float(f[_PRICE]), utils.to_float(f[_CHG_PCT])
