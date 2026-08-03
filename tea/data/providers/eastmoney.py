"""东方财富源：字段最全的一家，也是标准 schema 的定义者。

报价 / 日 K / 指数快照三项，逻辑从 `market.Market` 原样搬来（含 fltt=2 不缩放、
指数点位与 MA20 两路取源）。板块排名 / 板块成分 / 涨跌家数 / 涨停池仍留在
`Market` 里直连东财——那四项别家没有对等接口，做成 provider 也无从降级。
"""
from __future__ import annotations

from typing import List, Optional

from tea.core import utils
from ..errors import MarketError
from .base import IDataProvider, index_double_route

#: 行情/K 线接口的公开 ut 令牌（与涨停池那套不是同一个）
EM_UT = "fa5fd1943c7b386f172d6893dbfba10b"


def parse_quote(code: str, mkt: int, d: dict) -> dict:
    """东财报价 → 标准 schema。这份字段名与单位就是全链路的基准。"""
    # 请求带的是 fltt=2，东财在这个参数下返回的已经是最终浮点数（f43=1350.6、
    # f170=-0.82），不是放大 100 倍的整数——那是 fltt=1 的形式。所以这里一律
    # 不再缩放，否则价格会小 100 倍，涨幅会小 100 倍。
    g = lambda k: utils.to_float(d.get(k))
    price = g("f43")
    chg_pct = g("f170")
    chg_amt = g("f169")
    pre_close = None
    if price is not None and chg_amt is not None:
        pre_close = round(price - chg_amt, 4)
    name = d.get("f58") or ""
    return {
        "code": code,
        "mkt": mkt,
        "name": name,
        "price": price,
        "high": g("f44"),
        "low": g("f45"),
        "open": g("f46"),
        "volume": g("f47"),
        "amount_yi": (g("f48") / 1e8) if g("f48") is not None else None,
        "vol_ratio": g("f50"),
        "turnover": g("f168"),
        "chg_pct": chg_pct,
        "chg_amt": chg_amt,
        "pre_close": pre_close,
        "cap_yi": (g("f116") / 1e8) if g("f116") is not None else None,
        "float_cap_yi": (g("f117") / 1e8) if g("f117") is not None else None,
        "industry": d.get("f127") or d.get("f100") or "",
        "board": utils.board_of(code),
        "limit_up_pct": utils.limit_up_pct(code, name),
        "is_st": utils.is_st(name),
        "ts": utils.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


class EastmoneyProvider(IDataProvider):
    """东方财富（push2 / push2his）。"""

    name = "eastmoney"
    DEFAULT_TIMEOUTS = {"quote": 6.0, "klines": 8.0, "index": 8.0}

    def fetch_quote(self, code: str) -> dict:
        code = utils.norm_code(code)
        if len(code) != 6:
            raise MarketError(f"非法股票代码: {code}")
        mkt = utils.market_of(code)
        data = self.fetcher.get_json(self.cfg.get("market.quote_url"), {
            "secid": f"{mkt}.{code}",
            "fields": self.cfg.get("market.quote_fields"),
            "invt": 2, "fltt": 2, "ut": EM_UT,
        }, host_pool="cdn_hosts_quote").get("data") or {}
        if not data:
            raise MarketError(f"行情为空: {code}")
        return parse_quote(code, mkt, data)

    def fetch_klines(self, code: str, limit: Optional[int] = None,
                     secid: Optional[str] = None) -> List[dict]:
        lmt = int(limit or self.cfg.get("market.kline_limit", 30))
        c = utils.norm_code(code)
        sid = secid or f"{utils.market_of(c)}.{c}"
        js = self.fetcher.get_json(self.cfg.get("market.kline_url"), {
            "secid": sid,
            "klt": self.cfg.get("market.kline_klt", 101),
            "fqt": self.cfg.get("market.kline_fqt", 1),
            "lmt": lmt,
            "end": "20500101",
            "fields1": self.cfg.get("market.kline_fields1"),
            "fields2": self.cfg.get("market.kline_fields2"),
            "ut": EM_UT,
        }, host_pool="cdn_hosts_kline")
        rows = ((js.get("data") or {}).get("klines") or [])
        out = []
        for line in rows:
            p = str(line).split(",")
            if len(p) < 5:
                continue
            out.append({
                "date": p[0],
                "open": utils.to_float(p[1]),
                "close": utils.to_float(p[2]),
                "high": utils.to_float(p[3]),
                "low": utils.to_float(p[4]),
                "volume": utils.to_float(p[5]) if len(p) > 5 else None,
                "amount": utils.to_float(p[6]) if len(p) > 6 else None,
            })
        return out

    def fetch_index_snapshot(self, secid: Optional[str] = None) -> dict:
        sid = secid or self.cfg.get("market.index_secid", "1.000001")
        lmt = int(self.cfg.get("market.index_kline_limit", 25))
        return index_double_route(lambda: self._point_chg(sid),
                                  lambda: self.fetch_klines("", limit=lmt, secid=sid))

    def _point_chg(self, secid: str) -> tuple:
        """指数点位与涨跌幅（走 quote 池，push2delay 兜底）。"""
        js = self.fetcher.get_json(self.cfg.get("market.quote_url"), {
            "secid": secid, "fields": "f43,f170", "invt": 2, "fltt": 2, "ut": EM_UT,
        }, host_pool="cdn_hosts_quote")
        d = js.get("data") or {}
        # 同 parse_quote：fltt=2 已是最终值。除以 100 会把上证 3832 点算成 38 点，
        # 而 MA20 来自 K 线（本来就是真实点位），于是 ma20_above 恒为 False，
        # 「上证在 MA20 下方」这条门禁会永久锁死新开仓。
        return utils.to_float(d.get("f43")), utils.to_float(d.get("f170"))
