# F14 · 行情数据源与降级链

## 1. 背景与目标

零强依赖下稳定取数：多源按序降级，单源能力不足则静默跳过并试下一家。领域翻译在 `market`，原始 HTTP 在 `fetcher`。

## 2. 用户故事 / 场景

- 默认链：东财 → 腾讯 → 新浪 → 网易 → 凤凰。  
- 内网只放通东财：缩成单源，避免超时拖垮。  
- 境外：`market.use_env_proxy`。

## 3. 功能范围

**In**

- `IDataProvider` 接口与 ChainedProvider  
- 五家 provider 实现（只覆盖真实能力）  
- 缓存、重试、超时、防封相关头（如新浪 Referer）  
- 指标纯函数（MA/ATR/斜率等）不联网  

**Out**

- 付费私有行情 SDK 作为硬依赖  
- 在 provider 内做交易决策  

## 4. 主流程与边界

1. Market 按配置顺序问源。  
2. 失败/空 → 下一家；全失败 → 上层 data_gap。  
3. `data/` 内部依赖单向：`indicators` → `cache` → `fetcher` → `market`。  

**边界**：涨停幅度按板块正确（主板 10% / 创业科创 20% / 北交所 30% / ST 5%）；错判会污染板块排序。

## 5. 关键配置键

| 键 | 用途 |
|---|---|
| `market.data_sources` | 有序列表 |
| `market.retries` / 超时 | 网络韧性 |
| `market.use_env_proxy` | 代理 |

## 6. 代码锚点

- `tea/data/market.py` / `fetcher.py` / `cache.py` / `indicators.py`  
- `tea/data/providers/*.py` / `base.py`  
- `tea/core/utils.py` · `limit_up_pct` 等  

## 7. 验收标准

- [ ] 单测/自测不联网路径不依赖真实源  
- [ ] 新源：继承接口 + 挂链 + 文档域名表  
- [ ] 北交所涨停判定正确  
- [ ] 可选 `requests` 必须可回退 urllib  

## 8. 已知缺口 / 待迭代

- 东财字段变更/限流需运维级关注  
- 板块 stale 时整维归零的体验问题（与 F01/F04）  
