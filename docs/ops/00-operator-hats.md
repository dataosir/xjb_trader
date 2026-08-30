# 00 · 运营者两顶帽子

> **分工**：本页管「人怎么分配注意力」；命令与时间线见 [`../prd/02-daily-workflow.md`](../prd/02-daily-workflow.md)。  
> 当前阶段定义见 [`../project-state.md`](../project-state.md)。

---

## 1. 为什么只有两顶帽子

一人公司做 TEA 时，最常见的自嗨是：**用策略研发（改代码、调参、写文档）掩盖交易员职责（跑种子、压回填、对照证据）**。

indie-build-log 用 CEO/CMO/CTO/COO 四帽管商业化漏斗；TEA 在**攒证据阶段**只需两帽，足够且更轻。

| 帽子 | 职责 | 典型产出 |
|---|---|---|
| **交易员** | 按日工作流跑通纪律链；手动下单与持仓登记；盯缺口看板与影子桶 | `seed-plan`、`plan-check`、`run`、`review`、`weekly` 输出 |
| **策略研发** | 修 bug、补 selftest、文档与 backlog 对齐；**样本够门槛后**才开闸门 Plan | PR + CHANGELOG + `param_change` 留痕 |

---

## 2. 证据阶段时间占比（默认）

> 对齐 `project-state`：**以攒证据为主**。样本未达门槛前，交易员帽优先。

| 帽子 | 建议占比 | 说明 |
|---|---|---|
| **交易员** | **≥70%** | 含盘中执行、盘后复核、周 scorecard 填写（Ops-2 待建） |
| **策略研发** | **≤30%** | 仅限 selftest 修复、体验文案、文档；**禁止**无样本改 `pass_threshold` / 放宽低吸 |

**升权条件**（满足其一才可把策略研发提到 ~50%）：

- 影子桶 `shadow_tag` 回填 ≥ `shadow_min_samples` 且 T+3>0 ≥ `t3_up_target`（默认 60%）  
- `lowbuy` 回填 ≥30 且已开独立 Plan（见 [`../archive/LOWBUY_PLAN_2026-08-25.md`](../archive/LOWBUY_PLAN_2026-08-25.md)）  
- backlog 中目标项从 P2 解禁并经 4 步闭环 Plan 确认  

未达门槛却连续两天策略研发 >50% → 视为**逃避运营**，当晚 SOP 复盘必须写原因。

---

## 3. 各帽 Do / Don't

### 交易员帽

**Do**

- 每日 `seed-plan`；每周 ≥1 次全量 `review`（菜单 `8`）  
- 读缺口看板：待 T+1、待 T+3、低吸条数、影子桶对照——**只观察，不因数字难看而要求改闸**  
- 调参冲动 → 记入 [`02-user-feedback.md`](02-user-feedback.md) 反馈日志，**当天不开 config PR**  
- 实盘窗口内只跑计划内 `run`；FORCE / 破纪律事件记周报

**Don't**

- 用「顺便改一下 screener」代替没跑 `seed-plan`  
- 影子桶未达标就讨论「放宽过热 / 萌芽可买」  
- 低吸空池 → 要求放宽落盘或买入（见 backlog B-P0-07）

### 策略研发帽

**Do**

- 行为与 PRD/Fxx 不符 → bug 修 + selftest  
- 文档与 `INDEX.md` / `CHANGELOG` 同步  
- 闸门变更：**决策依据写清** → `config set`（自动 `param_change`）→ CHANGELOG  

**Don't**

- 证据不足时主动提议降 `pass_threshold`、改 `min_odds`、加第三方依赖  
- 为「可买太少」单独开策略 PR（先查是否突破/过热闸正常工作）  
- 跳过 Plan 直接改 screener 权重（见 [`../prd/05-roadmap-backlog.md`](../prd/05-roadmap-backlog.md) B-P2）

---

## 4. 与证据漏斗的对应

```text
落盘（seed-plan）→ 回填（review）→ 分桶对照（shadow / lowbuy）→ 门控调参（样本够 + Plan）
     ↑ 交易员 70%                    ↑ 交易员读板              ↑ 策略研发，且须留痕
```

| 漏斗阶段 | 主帽 | 禁做 |
|---|---|---|
| 落盘 + 轻量回填 | 交易员 | 改 config |
| 压待 T+3 / 周 review | 交易员 | 用编码代替 review |
| 影子桶 / lowbuy 对照 | 交易员（读） | 未达标调闸 |
| 闸门微调 / 因子权重 | 策略研发 | 无 `param_change`、无 CHANGELOG |

---

## 5. 切换仪式（简版）

1. **开盘前**：明确今日主帽（默认交易员）；若开 IDE，先写下「今日 MIT 是否与研发相关」。  
2. **14:30 前**：交易员帽——禁止「只改一行」式提交。  
3. **收盘后**：先 `review` / 看缺口，再考虑是否切策略研发帽。  
4. **周末**：交易员帽填证据周表（[`04-evidence-scorecard.md`](04-evidence-scorecard.md)，Ops-2）；策略研发仅处理 CI / 文档债。

---

## 6. 相关文档

| 文档 | 用途 |
|---|---|
| [`03-operator-daily-sop.md`](03-operator-daily-sop.md) | 日 MIT + 晨晚间 checklist |
| [`../prd/02-daily-workflow.md`](../prd/02-daily-workflow.md) | 命令与时间线 |
| [`../project-state.md`](../project-state.md) | 当前焦点与 Won't now |
| [`../prd/05-roadmap-backlog.md`](../prd/05-roadmap-backlog.md) | 样本门槛与 backlog |
