"""行情数据层。

按关注点分四层，依赖方向单向向下，不存在回边：

    indicators  纯指标数学，不联网、不读配置
    cache       进程内 TTL 缓存
    fetcher     HTTP + 防封（只管取回 JSON/文本，不懂行情语义）
    providers   各家数据源的字段映射 + 降级链（谁供的数在这一层决定）
    market      行情门面（对外只有领域字段，看不见源的差异）

外部一律从本包顶层导入，不要直接引用子模块——这样内部再调整文件划分时，
调用方不受影响。
"""
from __future__ import annotations

from .cache import MemCache
from .errors import MarketError
from .fetcher import Fetcher
from .indicators import (
    atr,
    bollinger,
    bb_derived,
    compute_indicators,
    count_limit_ups,
    intraday_position,
    ma,
)
from .market import Market
from .providers import ChainedProvider, IDataProvider, build_provider

__all__ = [
    "MarketError",
    "MemCache",
    "Fetcher",
    "Market",
    "IDataProvider",
    "ChainedProvider",
    "build_provider",
    "ma",
    "atr",
    "bollinger",
    "bb_derived",
    "compute_indicators",
    "count_limit_ups",
    "intraday_position",
]
