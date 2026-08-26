# 05 · 迭代 Backlog（链到现有路线图）

> 本页是 **PRD 层 backlog**，细节与实验笔记仍以日期版路线图为准。完成项打勾后请同步 CHANGELOG。

## 优先级图例

- **P0**：影响可买口径正确性或样本能否积累  
- **P1**：有数据支撑的增强  
- **P2**：样本门槛未达，禁止提前做  
- **Won't now**：胜率未升前不做（会放大亏损）

---

## P0 · 数据与可买口径（进行中 / 巩固）

| ID | 项 | 功能 | 状态 | 依据文档 |
|---|---|---|---|---|
| B-P0-01 | 每日 `seed-plan` + `review` 回填 T+n | F03/F11 | 运营纪律 | WINRATE_ROADMAP §4 阶段1 |
| B-P0-02 | 保证因子与标签落盘（`bias_ma20`/`vol_ratio`/`lowbuy`/`winrate_score`/`pick_sector_*`） | F11 | 代码已修，需持续验证 | CHANGELOG 2026-08-25 |
| B-P0-03 | 突破一律不得可买 | F03 | **已落地**（`winrate_breakout_block`） | CHANGELOG |
| B-P0-03b | 过热一律不得可买 | F03 | **已落地**（`winrate_overheat_block`） | CHANGELOG 2026-08-26 |
| B-P0-04 | 可买需 `winrate_score` 达标 | F03/F05 | **已落地** | CHANGELOG |
| B-P0-05 | 入选板块一致性 + 中游漏网封堵 | F03 | **已落地** | CHANGELOG |
| B-P0-06 | 低吸空池可归因；低吸只攒不买 | F06 | **已落地**阶段1 | LOWBUY_PLAN |
| B-P0-07 | 低吸样本 ≥30 条后再谈验证 | F06/F11 | 积累中 | LOWBUY_PLAN |

## P1 · 影子对照与归因

| ID | 项 | 功能 | 状态 | 说明 |
|---|---|---|---|---|
| B-P1-01 | `winrate-scan` 与规则可买并排对照 1–2 周 | F05 | 可用 | 不写计划 |
| B-P1-02 | 补跟涨字段：`amount_yi`/`cap_yi`/`rank_pct`/`odds`/`veto_labels` 等 | F11 | 待做 | ROADMAP §5.3 |
| B-P1-03 | shadow_pool 板块次日涨跌回填 | F10/F11 | 待做 | 验证板块动量持续性 |
| B-P1-04 | 逐因子归因（乖离/涨幅窗/身份/分时/各共振维） | F04/F11 | 等样本≥30/组 | ROADMAP 阶段2 |
| B-P1-05 | 将归因结论反馈为闸门微调（非拍脑袋） | F03 | 待阶段2 | 须 param_change 留痕 |

## P2 · 样本门槛未达 · HOLD

| ID | 项 | 功能 | 状态 | 触发条件 |
|---|---|---|---|---|
| B-P2-01 | 胜率先验调节 `pass_threshold`（阶段 B） | F05 | **HOLD** | 交叉桶样本≥30；见 WINRATE_PRIOR |
| B-P2-02 | 低吸阶段2特征验证 | F06 | 等待 | `lowbuy` 回填≥30 |
| B-P2-03 | 低吸阶段3上线买入档 | F06/F07/F09 | 禁止提前 | 阶段2通过 + 更高 min_odds 设计 |
| B-P2-04 | 共振维权重重构（阶段3） | F04 | 禁止提前 | 阶段2假设验证后 |

## Won't now（明确不做）

| ID | 项 | 原因 |
|---|---|---|
| B-W-01 | 再降 `pass_threshold` / 放松涨幅窗 | 胜率未升只放量亏损 |
| B-W-02 | 凭感觉回调 `min_odds` | 期望值结构已偏紧 |
| B-W-03 | 自动下单 / 跳过纪律 | NFR 硬约束 |
| B-W-04 | 消息面维「假装有数据」 | 应先有真实源或删除死维（待议） |

---

## 建议迭代切片（按 PR）

1. **Ops**：只跑日循环 + 样本质检脚本/手检字段（无代码或小修补）。  
2. **F11 字段包**：一次 PR 补齐 ROADMAP 待补落盘字段 + selftest。  
3. **F05 对照周报**：统计 rule vs winrate 两轨 T+1（reporting 小增强）。  
4. **阶段2 归因笔记**：产出 `docs/` 日期归因，再开 F04/F03 改权重 PR。  
5. **阶段 B / 低吸买入 / 共振重构**：各需独立 Plan + 样本门槛检查表。

---

## 文档链接

| 文档 | 用途 |
|---|---|
| [`../archive/WINRATE_ROADMAP_2026-08-24.md`](../archive/WINRATE_ROADMAP_2026-08-24.md) | 胜率三阶段 |
| [`../archive/WINRATE_PRIOR_PLAN_2026-08-24.md`](../archive/WINRATE_PRIOR_PLAN_2026-08-24.md) | 阶段 B HOLD |
| [`../archive/LOWBUY_PLAN_2026-08-25.md`](../archive/LOWBUY_PLAN_2026-08-25.md) | 低吸三阶段 |
| [`../CHANGELOG.md`](../CHANGELOG.md) | 已交付事实 |
| [`../tech/00-engineering-standards.md`](../tech/00-engineering-standards.md) | 工程门禁 |
| [`../project-state.md`](../project-state.md) | 当前全局状态 |

---

## 当前产品焦点（一句话）

**巩固 P0 可买硬闸与样本落盘 → 用 2 周真实回填验证口径 → 再开阶段2归因；在此之前不重构共振、不上低吸买入、不上阶段 B。**
