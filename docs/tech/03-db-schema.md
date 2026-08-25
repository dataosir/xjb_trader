# 03 · 持久化结构（文件型「库」）

> 本项目**无传统 SQL 数据库**。状态落在 `$TEA_HOME`（或 CWD）下的 JSON / JSONL；报告落 `reports/`。  
> 所有写入必须走原子写（先 `.tmp` 再 `rename`），见工程规范。

---

## 1. 目录约定

| 路径 | 用途 |
|---|---|
| `tea_config.json` | 运行配置（含本金等私密项，**gitignore**） |
| `data/` | 跨日/当日状态与样本 |
| `reports/` | Markdown 报告（准入 / 种子扫描等） |

---

## 2. 核心状态文件

| 文件 | 角色 | 主要维护模块 | PRD |
|---|---|---|---|
| `data/trade_plan.json` | 次日/当日交易计划（纪律锚点） | `portfolio/plan.py` | F07 |
| `data/daily_state.json` | 单日门禁计数（开仓/评估/复筛） | `screening/gates.py` | F02 |
| `data/capital_state.json` | 资金与持仓 | `portfolio/portfolio.py` | F09 |
| `data/watch_pool.json` | 观察池 | `portfolio/watch_pool.py` | F10 |
| `data/trades.json` | 交易流水 | `portfolio/trades.py` | F09/F12 |
| `data/seed_records.jsonl` | 种子样本 + T+n 回填 | `analysis/followthrough.py` | F11 |
| `data/seed_trace.jsonl` | 落选追溯（结构化） | `reporting/seed_trace.py` | F12 |
| `data/accumulator.jsonl` | 当日累积事件（含 param_change） | `portfolio/accumulator.py` | F12 |
| `.tea_sector_cache.json` 等 | 板块/行情缓存 | `data/cache.py` | F14 |

报告示例：`reports/TRADE_CHECK_*.md`、`reports/SEED_*.md`。

---

## 3. 种子记录关键字段（schema 摘要）

`seed_records.jsonl` 每行一对象。迭代可买口径时至少保证以下字段可归因（详见 F11 / 归档 WINRATE 路线图）：

| 字段组 | 示例键 | 用途 |
|---|---|---|
| 身份 | `code` `name` `date` `mode`（`rule`/`winrate`） | 主键与通道区分 |
| 板块 | `sector_*`、`pick_sector_bk/name/rank` | 入选一致性归因 |
| 评分 | `score` `winrate_score` `tier` | 共振 vs 胜率 |
| 标签 | `lowbuy` `buyable` / 观察原因 | 低吸与可买分流 |
| 因子 | `bias_ma20` `vol_ratio` `atr_pct` `turnover` `intraday` … | 阶段 2 归因 |
| 回填 | `t1_chg` `t3_chg` `t5_chg` 等 | 胜率统计 |

字段增删 → **同步本文 + F11 + `record_seed` + selftest**，并在 CHANGELOG 留痕。

---

## 4. 计划状态机

`trade_plan.json` 生命周期：

```
pending → ready_exec → executed
              ↘ invalid / cleared
```

复核失败或要素变动 → `invalid`（整单作废，禁止凑合执行）。

---

## 5. 维护约定

- 禁止把 `data/`、`reports/` 运行产物当文档提交。  
- 改文件名/字段语义属于破坏性变更：先 Plan，再改代码与 PRD/tech，并考虑旧样本兼容或迁移说明。
