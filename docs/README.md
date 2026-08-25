# TEA 文档总目录（一人公司全栈迭代框架）

> 本目录是长期 AI 自动化迭代的**唯一知识库入口**。  
> 产品「做什么」在 `prd/`；技术「怎么做」在 `tech/`；运营在 `ops/`；当前状态在 `project-state.md`。  
> **实现铁律**权威源在仓库根目录 [`../RULES.md`](../RULES.md)（禁止擅自加依赖、KISS、禁空 catch、文档同步）。

---

## 目录结构

```
docs/
├── README.md                 # 本文件：索引 + 迭代契约
├── project-state.md          # 当前全局状态（版本 / 进行中 / 下一步）
├── CHANGELOG.md              # 变更日志（时间 · 干了什么 · 关联 prd/tech）
├── prd/                      # 【产品层】做什么 / 为什么
├── tech/                     # 【技术层】怎么做（架构 / 接口 / 持久化 / 工程规范）
├── ops/                      # 【运营层】获客 / 反馈 / 维护
└── archive/                  # 【归档】有价值的历史策略笔记与评审（只读引用）
```

| 层 | 路径 | 职责 |
|---|---|---|
| 铁律 | [`../RULES.md`](../RULES.md) | 技术实现红线（依赖 / 分层 / KISS / 异常 / 文档同步） |
| 产品 | [`prd/`](prd/README.md) | 定位、领域模型、日常流程、F01–F15、NFR、backlog |
| 技术 | [`tech/`](tech/) | 工程规范、架构、模块契约、持久化 schema |
| 运营 | [`ops/`](ops/) | 增长渠道、用户反馈与 Bug 收集 |
| 状态 | [`project-state.md`](project-state.md) | 当前焦点与下一步（每次迭代必读必写） |
| 变更 | [`CHANGELOG.md`](CHANGELOG.md) | 已发生事实；条目宜标注关联 Fxx / tech 章节 |
| 归档 | [`archive/`](archive/) | 日期版路线图 / 评审；**不删**，新迭代不要往这里写主文档 |

---

## 每次开发任务的 4 步闭环（强制）

下达开发任务后，助手必须按序执行：

1. **Step 1 · Sync**  
   读取 [`project-state.md`](project-state.md)，以及本次相关的 [`prd/`](prd/)（对应 Fxx）与 [`tech/`](tech/) 文档。

2. **Step 2 · Plan**  
   输出明确 `Plan`（含：PRD 是否调整、技术方案、触及文件、不做事项），**等你确认后再动手**。

3. **Step 3 · Code & Doc**  
   实现代码；**同一次迭代**同步更新对应的 `docs/prd/` 和/或 `docs/tech/`（阈值/闸门/接口/落盘字段变更不得只改代码）。

4. **Step 4 · Log**  
   在 [`CHANGELOG.md`](CHANGELOG.md) 追加记录（注明关联 PRD/tech），并更新 [`project-state.md`](project-state.md)。

---

## 归档索引（只读）

| 文档 | 用途 |
|---|---|
| [`archive/WINRATE_ROADMAP_2026-08-24.md`](archive/WINRATE_ROADMAP_2026-08-24.md) | 胜率三阶段路线 |
| [`archive/WINRATE_PRIOR_PLAN_2026-08-24.md`](archive/WINRATE_PRIOR_PLAN_2026-08-24.md) | 阶段 B 先验（HOLD） |
| [`archive/LOWBUY_PLAN_2026-08-25.md`](archive/LOWBUY_PLAN_2026-08-25.md) | 低吸三阶段 |
| [`archive/STRATEGY_ADJUSTMENT_2026-08-18.md`](archive/STRATEGY_ADJUSTMENT_2026-08-18.md) | 早期策略死锁修复笔记 |
| [`archive/CODE_REVIEW_2026-08-18.md`](archive/CODE_REVIEW_2026-08-18.md) | 代码评审纪要 |

现行 backlog 摘要见 [`prd/05-roadmap-backlog.md`](prd/05-roadmap-backlog.md)，细节仍链到上述归档。
