"""凤凰源（api.finance.ifeng.com）：只有日 K，没有报价。

字段顺序是全链路唯一的异类：`[日期, 开, 高, 收, 低, 量, ...]`——**开/高/收/低**，
东财与腾讯都是开/收/高/低。照着别家的顺序抄会把收盘价读成最高价，
MA/ATR/乖离全跟着错，而且数值都在合理区间里，肉眼看不出来。
"""
from __future__ import annotations

from typing import List, Optional

from tea.core import utils
from ..errors import MarketError
from .base import IDataProvider, resolve_symbol

#: record 行内下标：0 日期、1 开、2 高、3 收、4 低、5 量(手)
_DATE, _OPEN, _HIGH, _CLOSE, _LOW, _VOLUME = 0, 1, 2, 3, 4, 5
_MIN_COLS = 6


class IFengProvider(IDataProvider):
    """凤凰财经：仅日 K（报价与指数快照保持不支持，链上会被静默跳过）。"""

    name = "ifeng"
    DEFAULT_TIMEOUTS = {"klines": 5.0}

    def fetch_klines(self, code: str, limit: Optional[int] = None,
                     secid: Optional[str] = None) -> List[dict]:
        lmt = int(limit or self.cfg.get("market.kline_limit", 30))
        prefix, c = resolve_symbol(code, secid)
        if len(c) != 6:
            raise MarketError(f"非法股票代码: {code}")
        js = self.fetcher.get_json(self.cfg.get("market.ifeng_kline_url"), {
            "code": f"{prefix}{c}",
            # type=last 给最近一年多的日线，够所有指标用；接口不支持按条数裁剪。
            "type": self.cfg.get("market.ifeng_kline_type", "last"),
        })
        rows = (js or {}).get("record") or []
        out = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < _MIN_COLS:
                continue
            out.append({
                "date": str(row[_DATE])[:10],
                "open": utils.to_float(row[_OPEN]),
                "close": utils.to_float(row[_CLOSE]),
                "high": utils.to_float(row[_HIGH]),
                "low": utils.to_float(row[_LOW]),
                "volume": utils.to_float(row[_VOLUME]),   # 手，与东财同单位
                "amount": None,                           # 凤凰不给成交额
            })
        # 接口按日期升序给全量，本项目的约定是「新的在后」，取尾部 lmt 根。
        return out[-lmt:]
