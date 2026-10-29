# 证据周 Scorecard · 2026-10-29（占位 · Ops-2）

> **说明**：本文件为 2026-10-29 文档债补齐时的**运营占位**，非完整统计填表。  
> NAS 恢复日循环 + 本机/NAS 跑 `review` / `followthrough` 后，请复制 [`ops/04-evidence-scorecard.md`](../ops/04-evidence-scorecard.md) 模板重填并归档新日期版。

## 证据周 Scorecard · 2026-10-29

- **主帽占比**：交易员 70% / 策略研发 30%（NAS 断采优先修运营）
- **方案 A 状态**：已启动（2026-09-09 rank 5→3）；**post-change 统计未验收**（n&lt;30）
- **本周调参**：无

### 2.1 样本与缺口

| 指标 | 数值 | 门槛 / 备注 |
|---|---|---|
| `seed_records` 累计（primary） | 见本机 `data/seed_records.jsonl` | archive 定量止于 2026-09-03 |
| 待 T+1 回填 | 跑 `tea review` 后更新 | 应趋近 0 |
| 待 T+3 回填 | 同上 | NAS 10/24+ 无 review → 可能堆积 |
| 新闸后可买（post-gate）累计 n | 09-03 文档 ~1 条 | ≥10 才议微调效果 |
| `lowbuy` 回填 n | 积累中 | ≥30 才进阶段 2 |

### 2.2 跟涨（全样本）

| 窗口 | 上涨数 / 总数 | 比率 | 对照门槛 |
|---|---|---|---|
| T+1 ≥ 3% | archive 87 条 ~20% | ~20% | 历史口径含中游 |
| T+3 > 0 | archive 87 条 ~40% | ~40% | `t3_up_target` 60%（全样本未达标） |

### 2.3 影子桶（只对照，不驱动计划）

| 桶 | T+3>0 | n（T+3 已填） | 备注 |
|---|---|---|---|
| 影子桶合计 | 22/32 | 32 | 历史 **69%** 已达标 |
| 前三非突破 | — | — | 见 archive FACTOR_ATTRIB |
| 萌芽 | — | — | 子桶 n≥30 再议 |

### 2.4 板块 rank 切片（方案 A 核心）

| rank 段 | T+1≥3% | T+3>0 | n | 本周新增 |
|---|---|---|---|---|
| ≤3 | archive ~65% T+3 | — | 46 | **post-A 待填** |
| 4–5 | — | ~40% | 87 条混合 | 方案 A 后个案只记录 |
| >5 | — | — | — | 现行闸不可买 |

### 2.5 运营纪律

| 检查项 | 本周 |
|---|---|
| 交易日 seed-plan 漏跑天数 | **NAS：10/24、10/27、10/28 无日录**（最后 10/23） |
| 全量 review 次数 | 同期断档 |
| cron / launchd | NAS crontab 在；**白天机器离线** |
| error.log 新增致命项 | 东财 MarketError（10/23 前后）；**/vol1 磁盘满** 截断 docs rsync |
| 邮件 | SMTP 正常；`ops_summary.only_on_issues=true` → 全绿日 skip |

### 2.6 本周结论（三句话）

1. 样本：**不够** — post-A rank≤3 n≥30 未达；NAS 断采使 10 月数字不可信。  
2. 对照：**影子桶 / rank≤3 历史仍支持收紧**，不能因「没邮件/没票」反推放宽。  
3. 下周 MIT：**交易员**：NAS 交易日 09:00 前在线 + 补 cron + 腾 `/vol1` + `only_on_issues=false`（若需每日运维信）；**研发**：仅 reporting（gate_blocked / retrospective），**不动 DEFAULTS 闸**。

### 2.7 Kill / 暂停条件（触发任一条 → 停调闸，先修运营）

- [x] 连续 2 个交易日未 `seed-plan`（NAS 10/24+）  
- [ ] 待 T+3 一周只增不减（review 断档）— **待恢复后确认**  
- [ ] post-gate 可买 n 一周无增长且 scan 有产出  
- [x] 无 `param_change` 记录的 config 改动 — **禁止**为体感调闸  
- [ ] 为填表而降低 `pass_threshold` / `min_odds` 的冲动  

---

*归档自 Ops-2 模板；下一版请在 review 恢复后重跑 `tea followthrough` 并替换上表数字。*
