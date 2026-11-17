# F05 · 胜率影子通道

## 1. 背景与目标

与 9 分共振（纪律型 rule）**并行**的数据型通道：用 `winrate_score` 选票，**只落盘对比、不写计划、不买入**，攒够样本再决定是否替换规则通道。

规则通道已把 `winrate_score` 接入可买硬闸（F03）；本功能仍保留独立扫描与 `mode=winrate` 落盘，用于对照实验。

## 2. 用户故事 / 场景

- 菜单 `4`「胜率选股（盘面浏览，不写计划）」或 `winrate-scan`：盘中浏览全市场影子可买/观察列表，与 14:30 `seed-plan` 可买并排对比；**只落盘 `mode=winrate`，不写计划、不入观察池**。  
- 复盘时按 `mode` 字段分轨统计胜率。

## 3. 功能范围

**In**

- `Screener.winrate_scan`：复用板块排序/涨幅窗/VETO，以 winrate_score 分档  
- `runner.winrate_plan`：展示与落盘，`mode=winrate`  
- 与规则通道共用 `_winrate_gate` 硬规则（突破/一致性等）  
- 门槛 `winrate.buyable_threshold`（默认 3）  
- **V2 影子分**（`winrate_score_v2`）：只保留样本仍支持的板块排名与涨幅，与 V1 并行落盘；**不参与任何可买判定/排序/闸门**

**Out**

- 写 `trade_plan`（禁止）  
- 阶段 B 胜率先验调节 `pass_threshold`（HOLD，见 WINRATE_PRIOR）  

## 4. 主流程与边界

1. 拉天气与板块 → 扫描候选。  
2. 算 `winrate_score`（板块排名、阶段、身份、涨幅、放量多头等加权）；同一处并行算 `winrate_score_v2`（`rank≤3 +3 / 4-5 -1 / 6-8 0 / >8 -2`，涨幅 3–5% +1，其余 0；满分 4）。  
3. 硬闸过滤 → 达标标为胜率可买（影子）。闸门只读 V1，不读 V2。  
4. `record_seed` 落盘 `winrate_score_v2` / `winrate_score_v2_detail`，供后续 V1 vs V2 对照。  
5. 历史样本无 V2 字段时，`review` / `followthrough` 按 `sector_rank` / `chg_pct` **即时重算**，不改历史行；看板输出 V1≥3 vs V2≥2 的 T+1≥3% / T+3>0 / T+5>0。

**边界**：样本交叉桶不足时，禁止上线先验自动调门槛；V2 仅影子字段，不得接入可买/排序/计划。

## 5. 关键配置键

| 键 | 用途 |
|---|---|
| `winrate.buyable_threshold` | 影子/硬闸共用达标线 |
| `strategy.winrate_*` | 与 F03 硬闸开关一致 |

## 6. 代码锚点

- `tea/analysis/shadow_lab.py` · `resolve_winrate_v2` / `winrate_v2_stats` / `format_winrate_v2_compare`  
- `tea/screening/preflight.py` · `winrate_score` / `winrate_score_v2` / `apply_winrate_scores`  
- `tea/screening/screener.py` · `winrate_scan`  
- `tea/runtime/runner.py` · `winrate_plan` / `_ft_entries`  
- CLI：`tea winrate-scan`

## 7. 验收标准

- [ ] `winrate-scan` 不修改有效交易计划（或不写计划）  
- [ ] 落盘含 `mode=winrate` 与 `winrate_score`  
- [ ] 新样本同时含 `winrate_score_v2` / `winrate_score_v2_detail`，且 V2 不改变可买与排序  
- [ ] 历史样本可按 `sector_rank` / `chg_pct` 即时重算 V2，复盘看板可对照 V1/V2  
- [ ] 突破/板块一致性闸门行为与规则通道一致  
- [ ] selftest 覆盖打分、V2 并行落盘与「闸门只认 V1」  

## 8. 已知缺口 / 待迭代

- 阶段 B 先验：交叉桶 ≥30 前 HOLD（`WINRATE_PRIOR_PLAN`）  
- V2 影子分需累计每桶 T+3 ≥30 且前后半段稳定，才讨论是否替换 V1 进入闸门  
- 与规则通道长期对照后，再决策「谁驱动计划」  
- 路线图阶段 3：共振重构后可能合并通道  

## 9. 样本落盘（2026-09-11）

- `winrate_gate` / `winrate_detail` / `winrate_would_buy` 随 `record_seed` 落盘（区分「分不够」vs「过热/突破等硬闸」）。  
- 观察轨**全量**落盘；控制台仍截断 `seed.max_watch_output`。  
- 周报 `mode_channel_stats`：`rule` vs `winrate` T+1/T+3 并排（B-P1-01）。  
