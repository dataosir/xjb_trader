# F05 · 胜率影子通道

## 1. 背景与目标

与 9 分共振（纪律型 rule）**并行**的数据型通道：用 `winrate_score` 选票，**只落盘对比、不写计划、不买入**，攒够样本再决定是否替换规则通道。

规则通道已把 `winrate_score` 接入可买硬闸（F03）；本功能仍保留独立扫描与 `mode=winrate` 落盘，用于对照实验。

## 2. 用户故事 / 场景

- 菜单「复盘工具 ▸ → 胜率选股」或 `winrate-scan`：当日产出影子可买列表，与 `seed-plan` 可买并排对比（低频，不占顶层）。  
- 复盘时按 `mode` 字段分轨统计胜率。

## 3. 功能范围

**In**

- `Screener.winrate_scan`：复用板块排序/涨幅窗/VETO，以 winrate_score 分档  
- `runner.winrate_plan`：展示与落盘，`mode=winrate`  
- 与规则通道共用 `_winrate_gate` 硬规则（突破/一致性等）  
- 门槛 `winrate.buyable_threshold`（默认 3）  

**Out**

- 写 `trade_plan`（禁止）  
- 阶段 B 胜率先验调节 `pass_threshold`（HOLD，见 WINRATE_PRIOR）  

## 4. 主流程与边界

1. 拉天气与板块 → 扫描候选。  
2. 算 `winrate_score`（板块排名、阶段、身份、涨幅、放量多头等加权）。  
3. 硬闸过滤 → 达标标为胜率可买（影子）。  
4. `record_seed` 落盘，供 F11 回填。  

**边界**：样本交叉桶不足时，禁止上线先验自动调门槛。

## 5. 关键配置键

| 键 | 用途 |
|---|---|
| `winrate.buyable_threshold` | 影子/硬闸共用达标线 |
| `strategy.winrate_*` | 与 F03 硬闸开关一致 |

## 6. 代码锚点

- `tea/screening/preflight.py` · `winrate_score`  
- `tea/screening/screener.py` · `winrate_scan`  
- `tea/runtime/runner.py` · `winrate_plan`  
- CLI：`tea winrate-scan`

## 7. 验收标准

- [ ] `winrate-scan` 不修改有效交易计划（或不写计划）  
- [ ] 落盘含 `mode=winrate` 与 `winrate_score`  
- [ ] 突破/板块一致性闸门行为与规则通道一致  
- [ ] selftest 覆盖打分与门槛  

## 8. 已知缺口 / 待迭代

- 阶段 B 先验：交叉桶 ≥30 前 HOLD（`WINRATE_PRIOR_PLAN`）  
- 与规则通道长期对照后，再决策「谁驱动计划」  
- 路线图阶段 3：共振重构后可能合并通道  
