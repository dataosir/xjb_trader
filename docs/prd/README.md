# TEA 产品需求文档（PRD）总目录

> 本文档体系由代码与现有 `docs/` **反向抽取**而成，用于后续按功能号独立迭代。  
> 文档总入口与 4 步闭环：[`../README.md`](../README.md)；全库清单：[`../INDEX.md`](../INDEX.md)；工程规范：[`../tech/00-engineering-standards.md`](../tech/00-engineering-standards.md)。  
> 策略演进笔记在 [`../archive/`](../archive/)，不替换本 PRD。

**产品**：XJB_TRADE（TEA）— A 股**交易准入引擎**（道 / 法 / 术串联否决）  
**非目标**：自动下单、券商对接、预测涨跌  

---

## 阅读顺序

1. [`00-product-overview.md`](00-product-overview.md) — 定位、用户、非目标  
2. [`01-domain-model.md`](01-domain-model.md) — 核心概念与状态对象  
3. [`02-daily-workflow.md`](02-daily-workflow.md) — 日/周时间线与命令映射  
4. [`03-features/`](03-features/) — 功能拆解（F01–F15，可独立开 PR）  
5. [`04-nfr-constraints.md`](04-nfr-constraints.md) — 非功能与硬约束  
6. [`05-roadmap-backlog.md`](05-roadmap-backlog.md) — 迭代 backlog（链到胜率/低吸路线图）  

---

## 功能索引（F01–F15）

| 编号 | 文档 | 一层摘要 | 主命令 / 入口 |
|---|---|---|---|
| F01 | [市场天气](03-features/F01-market-weather.md) | 道：情绪分 → 周期 → 姿态 → 仓位乘数 | `weather` / `status --weather` |
| F02 | [纪律门禁](03-features/F02-discipline-gates.md) | 法：计划绑定、窗口、限额、冷却 | `gate` / `run` 内嵌 |
| F03 | [种子筛选](03-features/F03-seed-screening.md) | 术：四步流 + 可买硬闸 | `seed-plan` |
| F04 | [预审共振](03-features/F04-preflight-resonance.md) | 9 分共振 / VETO / ATR / 身份 | `eval` / 种子内嵌 |
| F05 | [胜率通道](03-features/F05-winrate-channel.md) | 影子通道：只对比不买入 | `winrate-scan` |
| F06 | [低吸前夕](03-features/F06-lowbuy-eve.md) | 启动前夕样本积累（不写计划） | `seed-plan`（eve） |
| F07 | [计划生命周期](03-features/F07-plan-lifecycle.md) | 写 / 复核 / 作废 / 清除 | `plan` / `plan-check` |
| F08 | [执行四阶段](03-features/F08-execution-phases.md) | `run`：Phase1→4 准入 | `run` / `eval` |
| F09 | [持仓资金](03-features/F09-portfolio-capital.md) | 3/7 仓、平仓、流水 | `pos` / `capital` / `close` |
| F10 | [观察池](03-features/F10-watch-pool.md) | 观察轨纳入与复核 | `watch` |
| F11 | [复盘跟涨](03-features/F11-review-followthrough.md) | review / 种子落盘 / T+n | `review` / `followthrough` |
| F12 | [报告统计](03-features/F12-reporting-stats.md) | 状态 / 流水 / 周报 / 追溯 | `stats` / `weekly` / `trace` |
| F13 | [配置向导](03-features/F13-config-onboarding.md) | 410 参数 + setup | `setup` / `config` |
| F14 | [行情数据源](03-features/F14-data-providers.md) | 五源降级链与缓存 | `market.*` 配置 |
| F15 | [质量自测](03-features/F15-quality-selftest.md) | selftest / CI / 零依赖 | `selftest` |

**迭代约定**：改某能力时，PR / CHANGELOG 注明功能号（如「改 F03」），并同步对应 PRD 的「已知缺口」与验收标准。

---

## 命名约定

| 类型 | 模式 | 示例 |
|---|---|---|
| 总览章 | `NN-主题.md` | `00-product-overview.md` |
| 功能章 | `Fxx-能力短名.md` | `F03-seed-screening.md` |
| 语言 | 中文正文 | 与现有 `docs/` 一致 |

每份功能文档固定八段：背景与目标 → 用户故事 → In/Out → 主流程与边界 → 关键配置键 → 代码锚点 → 验收标准 → 已知缺口。

---

## 与文档体系的关系

| 文档 | 角色 |
|---|---|
| 本目录 `docs/prd/` | **需求与功能边界**（迭代契约） |
| [`../tech/`](../tech/) | 怎么做：架构 / 接口 / 持久化 / 工程规范 |
| [`../ops/`](../ops/) | 获客与反馈 |
| [`../project-state.md`](../project-state.md) | 当前焦点与下一步 |
| [`../CHANGELOG.md`](../CHANGELOG.md) | 已发生变更的事实记录 |
| [`../archive/`](../archive/) | 日期版胜率/低吸/评审笔记（只读） |
| 根目录 `README.md` | 用户向快速开始与命令速查 |

归档策略笔记**不删除**；PRD 只引用，不复制其实验细节。

---

## 维护规则

1. **先改 PRD 再改行为**（或同 PR 内同步）：阈值/闸门/可买定义变更必须更新对应 Fxx。  
2. **验收可对 CLI / selftest**：每条验收尽量可命令验证。  
3. **不把运行产物当文档**：`data/`、`reports/` 不进 PRD。  
4. **大改动先 Plan**：走 [`../README.md`](../README.md) 的 4 步闭环（Sync → Plan → Code & Doc → Log）。  
