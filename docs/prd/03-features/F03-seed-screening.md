# F03 · 种子筛选与可买硬闸（术 · 选股主链）

## 1. 背景与目标

盘后锚定时段执行「种子四步流」：板块排序 → 分档扫描 → 预审共振 → 输出可买/观察，并写次日计划。  
P0 纠偏后：**共振 PASS 不足以可买**，还需硬闸（突破/过热禁买、`winrate_score`、入选板块一致性等）。

## 2. 用户故事 / 场景

- 14:30 `seed-plan`：产出少量可买 + 观察池候选 + SEED 报告。  
- 交易者不希望中游板块（rank>5）、「突破」或「过热」阶段票写成可买计划。

## 3. 功能范围

**In**

- 板块排序（可交易成员统计、涨停/上涨比等）  
- 档位：严格 / 热点降级 / 强势跟涨 / 萌芽兜底  
- `screen_tier` → VETO → 预审 → `_winrate_gate`  
- 入选戳记 `pick_sector_bk/name/rank` 与一致性校验  
- 堵漏：`sector_relax_rank_nozt`、多元化替换均要求 rank≤可买上限  
- 写计划（可 `--no-plan`）  

**Out**

- 胜率影子独立扫描写计划（禁止，见 F05）  
- 低吸买入（禁止，见 F06）  
- 共振维权重重构（阶段 3，见 backlog）  

## 4. 主流程与边界

1. 天气 + 板块排序（仅统计 `board_allowed` 成员对涨停等指标）。  
2. 按档扫描成员，涨幅窗 + 身份/市值等过滤。  
3. 预审打分；每只算 `winrate_score`。  
4. `_winrate_gate`：突破/过热一律观察；rank 超限观察；入选与预审板块不一致观察；`winrate_score` 不足观察。  
5. 可买排序：胜率分优先；落盘 + 可选写计划。  

**边界**：多板块归属时，必须以**筛入板块戳记**为准，禁止事后 industry 重映射污染归因。  
当前可买阶段实质以**萌芽**为主（突破+过热默认禁买）；可买会更少、更常 EMPTY。

## 5. 关键配置键

| 键 | 默认意图 |
|---|---|
| `strategy.pass_threshold` | 共振可买门槛（如 6） |
| `strategy.seed_min_sector_rank` | 可买板块排名上限（如 5） |
| `strategy.winrate_gate_enabled` | 总闸 |
| `strategy.winrate_breakout_block` | 突破一律不得可买 |
| `strategy.winrate_overheat_block` | 过热一律不得可买 |
| `strategy.winrate_score_gate_enabled` | 可买需 winrate_score 达标 |
| `strategy.winrate_sector_consistency` | 入选板块一致性 |
| `strategy.winrate_sector_rank_buyable_max` | 一致性用的排名上限 |
| `seed.sector_relax_rank` / `sector_relax_rank_nozt` | 放宽通道排名封顶 |
| `scoring.sector_rank_full` / `half` | 板块强度满分档 |

## 6. 代码锚点

- `tea/screening/screener.py` · `Screener` / `_winrate_gate` / `rank_sectors` / `seed_scan`  
- `tea/runtime/runner.py` · `seed_plan`  
- CLI：`tea seed-plan [--no-eve] [--no-plan] [--strict-window]`

## 7. 验收标准

- [ ] 突破阶段候选不得进入可买（`winrate_breakout_block=true`）  
- [ ] 过热阶段候选不得进入可买（`winrate_overheat_block=true`）  
- [ ] 可买必有 `pick_sector_*` 且 rank≤上限；与预审板块一致  
- [ ] `ok_nozt` / 多元化路径不可引入 rank>5 可买  
- [ ] selftest 覆盖闸门开/关行为  
- [ ] 关闭 `winrate_gate_enabled` 可回滚总闸  

## 8. 已知缺口 / 待迭代

- 可买样本仍少，需每日 review 验证新口径  
- 共振分与真实胜率历史曾反向 → 权重重构等因子样本≥30（阶段 3）  
- 不建议在胜率未升前降低 `pass_threshold` 或 `min_odds`  
- 「三日必赚」无法保证；过热禁买只是提高期望，不是收益承诺