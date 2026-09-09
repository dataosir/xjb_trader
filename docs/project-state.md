# TEA 项目全局状态（project-state）

> **每次开发任务 Step 1 必读；Step 4 必更新。**  
> 详细需求见 `prd/`；架构见 `tech/`；历史实验见 `archive/`。

---

## 元信息

| 项 | 值 |
|---|---|
| 产品 | XJB_TRADE（TEA）— A 股交易准入引擎 |
| 代码版本 | `1.0.0`（`tea.__version__`） |
| 文档框架 | 一人公司全栈：`prd` / `tech` / `ops` + CHANGELOG + 本文件；**清单权威源** `docs/INDEX.md` |
| 更新日期 | 2026-09-09 |

---

## 业务目标（记住这个）

1. **纪律优先**：道/法/术串联否决；无计划不开仓；不自动下单。  
2. **胜率可审计**：用 `seed_records` + review 回填证明口径，不靠拍脑袋调参。  
3. **零依赖可跑**：标准库为主；公式变更必须 selftest 同步。

## 技术选型（记住这个）

- 语言：Python 3.8+  
- 架构：`tea/` 分层 L0–L5，单向依赖  
- 持久化：JSON / JSONL 原子写（无 SQL）  
- 行情：多源降级链（东财→腾讯→新浪→网易→凤凰）  
- 质量：`tea selftest` + CI 多版本矩阵  
- **依赖变更须先汇报并获批**（见根目录 `RULES.md`）；默认零第三方运行时依赖

---

## 当前焦点

**以攒证据为主**：缺口看板盯 T+1/T+3；影子桶（萌芽∪前三非突破）对照 **T+3>0≥60%**（验收门槛，非收益承诺）；巩固可买硬闸；低吸空池只观察。

已落地（代码）：突破/过热禁买、`winrate_score`、板块一致性、**自动轻量回填（默认后台异步）**、候选否决原因必展示、**样本缺口看板**、**`shadow_tag` 落盘对照**、大盘指数超时修复 + MA20 跨源补全 + `tea.data` 运行日志**、**共振分/行情关键节点 `tea.log` 追溯 + launchd 直调 python 修复**、**东财 K 线会话熔断（push2his 封禁时直切腾讯）+ 缺口横幅误报修复**、**布林线观测因子（只算不落闸，落盘+控制台）**、**F16 观察池盘中邮件提醒**（`tea watch-alert` + launchd 每分钟 + 163 SMTP 默认）。

## 进行中

| ID | 事项 | 说明 |
|---|---|---|
| B-P0-01 | 每日 `seed-plan` + 适时全量 `review` | 运营；**launchd 方案 A 已落地**（`ops/05` 种子 + `ops/06` 观察提醒）；看缺口看板补 T+3 |
| B-P0-02 / 07 | 因子与 `lowbuy` 样本积累 | 低吸**先观察**空池 diag，不放宽、不买入 |
| B-P1-01 | rule vs winrate 影子对照 | 1–2 周，不写计划 |
| B-P1-06 | `shadow_tag` T+3 对照 | **T+3 69% 已达标**（32 条）；**方案 0 已确认**（维持 2 周攒样本）— [`archive/SHADOW_BUCKET_PLAN_2026-09-04.md`](archive/SHADOW_BUCKET_PLAN_2026-09-04.md) |
| B-P1-04 | 逐因子归因 | **草稿已写** [`archive/FACTOR_ATTRIB_2026-09-04.md`](archive/FACTOR_ATTRIB_2026-09-04.md)；技术因子 n&lt;30 未定稿 |

## 明确不做（现在）

- 阶段 B 胜率先验调节门槛  
- 低吸买入 / 放宽低吸落盘条件  
- 共振维权重重构  
- 再降 `pass_threshold` / 凭感觉改 `min_odds`  
- 自动下单 / 拆除纪律  
- 「三日必赚」承诺（门槛只用于验收对照）  
- 有条件放开过热（与 B1 冲突，须影子桶样本够后再单独 Plan）

详见 [`prd/05-roadmap-backlog.md`](prd/05-roadmap-backlog.md)。

## 下一步计划

1. 日循环：`seed-plan`（launchd 14:30 或手动；自动回填）+ 每周至少一次菜单 `8` 全量 `review`，看缺口看板把待 T+3 压下去；**人**侧按 [`ops/03-operator-daily-sop.md`](ops/03-operator-daily-sop.md) 晨晚间 checklist。  
2. **方案 0 执行中**（~2026-09-18 复盘）：维持闸门不变；日 `seed-plan` + 补 T+1/T+3；目标新闸后可买 ≥10、rank≤3 n≥30、技术因子 ≥30。2 周后达标再议方案 A（rank 5→3）。归因草稿见 `archive/FACTOR_ATTRIB_2026-09-04.md`。  
3. 低吸：空池只读 diag；`lowbuy` 回填 ≥30 前不上买入。  
4. **Ops-2（待做）**：证据周 scorecard + 里程碑/Kill 表（`ops/04`–`05`）。

---

## 文档健康

| 检查项 | 状态 |
|---|---|
| `docs/INDEX.md` | **权威清单**（增删改 docs 内 md 必同步维护） |
| `docs/README.md` | 入口 + 4 步闭环（明细见 INDEX） |
| `docs/prd/` | 完整（F01–F15） |
| `docs/tech/` | 框架已建（00–03） |
| `docs/ops/` | 框架已建（00–03；Ops-1 日 SOP + 两帽） |
| `docs/archive/` | 历史策略/评审已迁入 |
| 根 `RULES.md` | **已存在**（技术迭代铁律权威源；`tech/00` 摘要引用；`.cursorrules` 已挂钩） |
