"""数据层异常。

单独成模块，让 fetcher 与 market 都能引用而不互相依赖。
"""
from __future__ import annotations


class MarketError(RuntimeError):
    """行情获取失败。"""
