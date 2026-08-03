"""多数据源包：5 家源 + 降级链工厂。

    base        IDataProvider（部分实现约定）与 ChainedProvider（降级链）
    eastmoney   报价 / 日 K / 指数快照（字段最全，标准 schema 的基准）
    tencent     报价 / 日 K / 指数快照
    sina        报价 / 日 K / 指数快照
    netease     报价（无 K 线）
    ifeng       日 K（无报价）

启用哪几家、按什么顺序，由 `market.data_sources` 决定；默认五家全开。
板块排名 / 板块成分 / 涨跌家数 / 涨停池不在这里——
那四项只有东财有对等接口，仍由 `Market` 直连。
"""
from __future__ import annotations

from typing import Any, List, Optional

from ...config_store import ALL_DATA_SOURCES, Config
from .base import ChainedProvider, IDataProvider, index_double_route, resolve_symbol
from .eastmoney import EastmoneyProvider, parse_quote
from .ifeng import IFengProvider
from .netease import NeteaseProvider
from .sina import SinaProvider
from .tencent import TencentProvider

#: 配置里的源名 → 实现类。加新源只需在这里登记一行。
REGISTRY = {
    EastmoneyProvider.name: EastmoneyProvider,
    TencentProvider.name: TencentProvider,
    SinaProvider.name: SinaProvider,
    NeteaseProvider.name: NeteaseProvider,
    IFengProvider.name: IFengProvider,
}

#: cfg 里没配（或配成空）时的回落：与 DEFAULTS 保持同一份名单
DEFAULT_SOURCES = list(ALL_DATA_SOURCES)


def build_provider(cfg: Config, fetcher: Any,
                   names: Optional[List[str]] = None) -> ChainedProvider:
    """按 `market.data_sources` 组装降级链（顺序即优先级）。

    单源也照样包一层 ChainedProvider：链上只有一家时行为与直连等价，
    但「谁供的数」「失败原因汇总」这些观察点不必分两套代码。
    未登记的源名直接忽略（拼错了不该让整个引擎起不来）；一个都不认就回落东财。
    """
    if names is None:
        names = list(cfg.get("market.data_sources") or DEFAULT_SOURCES)
    chain: List[IDataProvider] = []
    seen = set()
    for raw in names:
        key = str(raw).strip().lower()
        if key in seen or key not in REGISTRY:
            continue
        seen.add(key)
        chain.append(REGISTRY[key](cfg, fetcher))
    if not chain:
        chain.append(EastmoneyProvider(cfg, fetcher))
    return ChainedProvider(chain, cfg, fetcher)


__all__ = [
    "REGISTRY",
    "DEFAULT_SOURCES",
    "build_provider",
    "IDataProvider",
    "ChainedProvider",
    "EastmoneyProvider",
    "TencentProvider",
    "SinaProvider",
    "NeteaseProvider",
    "IFengProvider",
    "parse_quote",
    "resolve_symbol",
    "index_double_route",
]
