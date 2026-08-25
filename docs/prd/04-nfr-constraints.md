# 04 · 非功能需求与硬约束

## 1. 产品硬约束（不可协商）

| ID | 约束 | 说明 |
|---|---|---|
| NFR-P1 | **不下单** | 不对接券商；`BUY` 仅准入判断 |
| NFR-P2 | **纪律不可跳过** | 禁止「一键跳过」计划/窗口/限额；演练开关须显式且文档标明风险 |
| NFR-P3 | **串联否决** | 道/法/术任一层否决即整笔作废，不作加权抵消 |
| NFR-P4 | **计划绑定** | 无有效计划或代码不在计划内 → 禁止评估买入 |

## 2. 工程硬约束

| ID | 约束 | 验收 |
|---|---|---|
| NFR-E1 | **运行时零强依赖** | 仅标准库；`requests` 可选且必须 urllib 回退 |
| NFR-E2 | **Python 3.8+** | CI 覆盖至 3.13；Linux/macOS/Windows |
| NFR-E3 | **原子写** | 状态文件先 `.tmp` 再 `rename`（经 `utils`） |
| NFR-E4 | **配置驱动阈值** | 禁止业务硬编码魔法数；入 `DEFAULTS` |
| NFR-E5 | **公式↔断言同步** | 改评分/门禁必须改 selftest 独立重算 |
| NFR-E6 | **分层单向依赖** | runtime→…→core；同层不缠；子包 `__init__` 为契约 |
| NFR-E7 | **CLI 无交易逻辑** | 逻辑在 runner/各层；CLI 只解析与展示 |
| NFR-E8 | **IO 可注入** | phases 经 `prompt.IO`，禁直接 input/print |

## 3. 数据与隐私

| ID | 约束 |
|---|---|
| NFR-D1 | `tea_config.json` / `data/` / `reports/` 不提交公开仓库（gitignore） |
| NFR-D2 | 支持 `TEA_HOME` 隔离沙箱与多账户 |
| NFR-D3 | 行情来自第三方公开接口：可能延迟、出错、停服；需降级与缺口可见 |

## 4. 可靠性与可观测

| ID | 要求 |
|---|---|
| NFR-R1 | 数据缺口醒目提示（控制台 + 报告备注） |
| NFR-R2 | 关键决策可追溯（seed_trace / accumulator / seed_records） |
| NFR-R3 | 策略闸门可配置回滚（如 `winrate_gate_enabled=false`） |
| NFR-R4 | 离线 selftest 不污染真实 TEA_HOME |

## 5. 性能与网络（务实目标）

| ID | 要求 |
|---|---|
| NFR-N1 | 天气短缓存避免重复打满源 |
| NFR-N2 | 降级链可缩源，避免无权限域名拖死超时 |
| NFR-N3 | 种子扫描对权限外板块成员不计涨停贡献（可交易过滤） |

## 6. 安全与合规表述

- 仅供个人学习与交易纪律研究，**不构成投资建议**。  
- 引擎输出过线 ≠ 盈利保证。  
- 用户对实盘交易与资金损失自负。

## 7. 明确拒绝的改动类型

见 [`docs/tech/00-engineering-standards.md`](../tech/00-engineering-standards.md)「不接受的改动」：自动下单、拆除纪律、引入重量级依赖。

## 8. 文档与迭代约束

| ID | 要求 |
|---|---|
| NFR-DOC1 | 功能变更同步对应 `docs/prd/03-features/Fxx-*.md` |
| NFR-DOC2 | 行为变更写入 `CHANGELOG.md` |
| NFR-DOC3 | 策略大改先 Plan，确认后再改代码（一人公司协作约定） |
