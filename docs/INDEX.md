# TEA 文档总索引（权威清单）

> **增删改 `docs/` 内任意 `.md` 时，必须同步更新本文件。**  
> 入口与 4 步闭环见 [`README.md`](README.md)；实现铁律见 [`../RULES.md`](../RULES.md)。

---

## 根（`docs/`）

| 文件 | 职责 |
|---|---|
| [`README.md`](README.md) | 知识库入口 + 分层说明 + 4 步闭环 |
| [`INDEX.md`](INDEX.md) | **本文件**：全库文件清单权威源 |
| [`project-state.md`](project-state.md) | 当前版本 / 进行中 / 下一步（必读必写） |
| [`CHANGELOG.md`](CHANGELOG.md) | 变更日志（关联 Fxx / tech） |

仓库根另有 [`../RULES.md`](../RULES.md)（实现铁律权威源，不在 `docs/` 内）。

---

## 产品层 `prd/`

| 文件 | 职责 | 关联 |
|---|---|---|
| [`prd/README.md`](prd/README.md) | PRD 层内导读与 F01–F15 表 | — |
| [`prd/00-product-overview.md`](prd/00-product-overview.md) | 定位 / 非目标 / 用户场景 | — |
| [`prd/01-domain-model.md`](prd/01-domain-model.md) | 道法术、计划、种子、可买/观察 | — |
| [`prd/02-daily-workflow.md`](prd/02-daily-workflow.md) | 日/周时间线与命令映射 | — |
| [`prd/03-features/F01-market-weather.md`](prd/03-features/F01-market-weather.md) | 道：情绪/周期/姿态 | F01 |
| [`prd/03-features/F02-discipline-gates.md`](prd/03-features/F02-discipline-gates.md) | 法：计划/窗口/限额/冷却 | F02 |
| [`prd/03-features/F03-seed-screening.md`](prd/03-features/F03-seed-screening.md) | 术：种子四步 + 可买硬闸 | F03 |
| [`prd/03-features/F04-preflight-resonance.md`](prd/03-features/F04-preflight-resonance.md) | 9 分共振 / VETO / ATR | F04 |
| [`prd/03-features/F05-winrate-channel.md`](prd/03-features/F05-winrate-channel.md) | 胜率影子通道 | F05 |
| [`prd/03-features/F06-lowbuy-eve.md`](prd/03-features/F06-lowbuy-eve.md) | 低吸前夕（只攒样本） | F06 |
| [`prd/03-features/F07-plan-lifecycle.md`](prd/03-features/F07-plan-lifecycle.md) | 计划写/复核/作废 | F07 |
| [`prd/03-features/F08-execution-phases.md`](prd/03-features/F08-execution-phases.md) | `run` Phase1→4 | F08 |
| [`prd/03-features/F09-portfolio-capital.md`](prd/03-features/F09-portfolio-capital.md) | 持仓/资金/3-7 仓 | F09 |
| [`prd/03-features/F10-watch-pool.md`](prd/03-features/F10-watch-pool.md) | 观察池 | F10 |
| [`prd/03-features/F11-review-followthrough.md`](prd/03-features/F11-review-followthrough.md) | review / 种子落盘 / T+n | F11 |
| [`prd/03-features/F12-reporting-stats.md`](prd/03-features/F12-reporting-stats.md) | 状态/流水/周报/追溯 | F12 |
| [`prd/03-features/F13-config-onboarding.md`](prd/03-features/F13-config-onboarding.md) | 配置与向导 | F13 |
| [`prd/03-features/F14-data-providers.md`](prd/03-features/F14-data-providers.md) | 行情源降级链 | F14 |
| [`prd/03-features/F15-quality-selftest.md`](prd/03-features/F15-quality-selftest.md) | 自测 / CI | F15 |
| [`prd/04-nfr-constraints.md`](prd/04-nfr-constraints.md) | 非功能硬约束 | NFR |
| [`prd/05-roadmap-backlog.md`](prd/05-roadmap-backlog.md) | 现行迭代 backlog | backlog |

---

## 技术层 `tech/`

| 文件 | 职责 | 关联 |
|---|---|---|
| [`tech/README.md`](tech/README.md) | 技术层导读 | — |
| [`tech/RULES.md`](tech/RULES.md) | 指针 → 根 `RULES.md`（防双源） | 铁律 |
| [`tech/00-engineering-standards.md`](tech/00-engineering-standards.md) | 工程规范 + 铁律摘要 | tech/00 |
| [`tech/01-architecture.md`](tech/01-architecture.md) | 分层 / 数据流 / 包↔PRD | tech/01 |
| [`tech/02-api-specs.md`](tech/02-api-specs.md) | CLI / 模块调用契约 | tech/02 |
| [`tech/03-db-schema.md`](tech/03-db-schema.md) | JSON/JSONL 持久化 | tech/03 |

---

## 运营层 `ops/`

| 文件 | 职责 |
|---|---|
| [`ops/README.md`](ops/README.md) | 运营层导读 |
| [`ops/00-operator-hats.md`](ops/00-operator-hats.md) | 运营者两顶帽子（交易员 / 策略研发）与时间占比 |
| [`ops/01-growth-channels.md`](ops/01-growth-channels.md) | 受众与推广渠道假设 |
| [`ops/02-user-feedback.md`](ops/02-user-feedback.md) | 反馈 / Bug 收集规则 |
| [`ops/03-operator-daily-sop.md`](ops/03-operator-daily-sop.md) | 交易运营日 SOP（MIT / 晨晚间 checklist；命令见 prd/02） |
| [`ops/05-seed-plan-scheduler.md`](ops/05-seed-plan-scheduler.md) | 种子扫描外部调度（launchd 方案 A：安装 / 日志 / 漏扫补救） |

---

## 归档 `archive/`（只读）

| 文件 | 职责 |
|---|---|
| [`archive/README.md`](archive/README.md) | 归档说明 |
| [`archive/WINRATE_ROADMAP_2026-08-24.md`](archive/WINRATE_ROADMAP_2026-08-24.md) | 胜率三阶段路线 |
| [`archive/WINRATE_PRIOR_PLAN_2026-08-24.md`](archive/WINRATE_PRIOR_PLAN_2026-08-24.md) | 阶段 B 先验（HOLD） |
| [`archive/LOWBUY_PLAN_2026-08-25.md`](archive/LOWBUY_PLAN_2026-08-25.md) | 低吸三阶段 |
| [`archive/STRATEGY_ADJUSTMENT_2026-08-18.md`](archive/STRATEGY_ADJUSTMENT_2026-08-18.md) | 早期策略死锁修复 |
| [`archive/CODE_REVIEW_2026-08-18.md`](archive/CODE_REVIEW_2026-08-18.md) | 代码评审纪要 |

现行 backlog 以 [`prd/05-roadmap-backlog.md`](prd/05-roadmap-backlog.md) 为准；归档不写新主文档。
