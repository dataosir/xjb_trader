# 02 · 日常工作流

## 1. 日时间线（默认）

| 时刻 | 动作 | 命令 | 相关功能 |
|---|---|---|---|
| ~13:30 | 观察盘面强弱 | `weather` | F01 |
| **~14:30** | 种子四步流 → 写次日计划 | `seed-plan` | F03 / F04 / F06 / F07 |
| 可选 | 胜率影子扫描（对比） | `winrate-scan` | F05 |
| ~14:35 | 计划复核（要素变动 → 整单作废） | `plan-check` | F07 |
| 收盘后 | 跟涨回填 + 观察池 + 当日累积 | `review` | F11 / F10 |
| **T+1 14:00–14:45** | 唯一买入窗口，执行计划内标的 | `run <代码>` | F02 / F08 / F09 |
| 盘中任意（演练） | 只算不买 | `eval <代码>` | F04 / F08 |
| 持仓管理 | 确认仓 / 平仓 / 流水 | `add-confirm` / `close` / `trades` | F09 |

> 窗口类限制可用 `--any-time`（仅演练）或配置调整；生产纪律默认开启。

## 2. 周节奏

| 频率 | 动作 | 命令 |
|---|---|---|
| 每周五（建议） | 纪律自查 + 归因 | `weekly` |
| 持续 | 统计与落选追溯 | `stats` / `trace` / `followthrough` |
| 策略迭代前 | 对齐路线图样本门槛 | 见 `05-roadmap-backlog.md` |

## 3. 命令 → 功能映射（速查）

| 命令 | 功能号 |
|---|---|
| `weather` | F01 |
| `gate` / `status` | F02 / F12 |
| `seed-plan` | F03（主）+ F04 + F06 + F07 |
| `winrate-scan` | F05 |
| `plan` / `plan-check` / `plan-clear` | F07 |
| `run` / `eval` | F08（+ F02/F04） |
| `pos` / `pos-add` / `pos-rm` / `capital` / `add-confirm` / `close` | F09 |
| `watch` | F10 |
| `review` / `followthrough` | F11 |
| `stats` / `weekly` / `accum` / `trace` / `trades` | F12 |
| `setup` / `config` | F13 |
| （隐式）行情拉取 | F14 |
| `selftest` | F15 |

## 4. 推荐日循环（可买口径纠偏后）

1. `seed-plan`：预期「可买」更少但口径更严（突破禁买、`winrate_score`、板块一致性）。  
2. `plan-check`：确认板块/要素未漂移。  
3. 次日窗口 `run`：计划内代码；通过后手动下单，再用 `pos-add` 或引擎登记路径补登。  
4. 当日/次日 `review`：回填 T+n，保证因子字段与 `lowbuy`/`winrate_score` 进样本。  
5. 可选 `winrate-scan`：影子通道对照，**不写计划**。

## 5. 空仓日也有价值

- `accum`：解释「为何没交易」  
- `trace`：淘汰链路  
- 低吸空池：`lowbuy_pool_diag` 逐关归因（见 F06）  
- 数据缺口横幅：种子结束时醒目提示（见 CHANGELOG）

## 6. 与报告产物

| 产物 | 触发 |
|---|---|
| `reports/SEED_*.md` | `seed-plan` |
| `reports/TRADE_CHECK_*.md` | `run` / `eval` |
| `reports/STATS_*.md` / 周报 | `stats --write` / `weekly` |
