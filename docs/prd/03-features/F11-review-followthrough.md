# F11 · 复盘 / 跟涨落盘 / T+n 回填

## 1. 背景与目标

把「选过的票」变成可归因样本：落盘 → T+1/T+3/T+5 回填 → 胜率与因子统计。没有稳定 review，所有策略迭代都是猜。

## 2. 用户故事 / 场景

- 收盘后 `review`：回填跟涨、观察池、当日累积。  
- `seed-plan` 收尾 / 进菜单（盘后·隔夜窗）：**自动轻量回填**待填历史样本（可关）。  
- `followthrough --update`：专注跟涨回填与胜率。  
- 开发者检查 `seed_records.jsonl` 是否含 `lowbuy`/`winrate_score`/`pick_sector_*`。

## 3. 功能范围

**In**

- `record_seed`：候选全字段落盘（含因子、闸门、模式）  
- T+n 回填与胜负口径（默认 T+1 涨≥3% 计胜）  
- 低吸样本进度统计  
- 与 `close_review` 编排（跟涨 + 观察池 + accumulator）  
- **自动轻量回填**（默认开）：只跑 `update_results`，不替代全量 `review`

**Out**

- 根据回填自动改权重上线（须人工/阶段门控）  
- 删除历史样本「美化胜率」  
- 系统级 cron / 自动下单  

## 4. 主流程与边界

1. 种子结束：`_ft_entries` 收集可买/观察/低吸/胜率影子。  
2. `record_seed` 追加 jsonl（原子友好追加策略以实现为准）。  
3. **自动回填（可选）**：`runner.maybe_auto_backfill(trigger=seed|menu)`。  
4. `review`/`followthrough --update`：按交易日取后续行情填 T+n；全量复核另含观察池。  
5. 聚合输出胜率、分桶（供路线图与未来先验）。  

**边界**：

- 今日刚落盘的样本**回填不了**（须下一交易日收盘后）。  
- 字段缺失会导致阶段 2 归因失败——新字段必须双端（entries + record_seed）同时写。  
- 菜单自动回填每天最多 1 次（`daily_state.auto_backfill_menu`）。  
- **已完整回填**（有 `result` 且有 `chg_t5`）的行会被跳过，**不改写历史结论**。  
- 种子收尾自动回填：**静默进度**，控制台只留一行 `回填 x 条，仍待 y 条`；无 pending 则不输出。

## 5. 关键配置键

| 键 | 用途 |
|---|---|
| `followthrough.min_samples` | 聚合最小样本（先验另用更严阈值） |
| `followthrough.win_chg_pct` | T+1 计胜阈值（默认 3%） |
| `followthrough.auto_backfill_on_seed` | seed-plan 收尾自动轻量回填（默认 true） |
| `followthrough.auto_backfill_on_menu` | 进菜单盘后/隔夜窗自动回填（默认 true） |
| `followthrough.auto_backfill_full_review` | true 则自动路径改为全量 `close_review`（默认 false） |

## 6. 代码锚点

- `tea/analysis/followthrough.py`  
- `tea/portfolio/accumulator.py`  
- `tea/runtime/runner.py` · `close_review` / `maybe_auto_backfill` / `_ft_entries`  
- CLI：`review` / `followthrough`；菜单 `8` 盘后复核

## 7. 验收标准

- [ ] 新种子记录含：`winrate_score`、`lowbuy`、`mode`、`pick_sector_*`、因子字段  
- [ ] `review` 后 T+1 可回填（交易日与停牌边界正确）  
- [ ] seed/menu 自动回填可开关；失败不阻断种子或菜单  
- [ ] 自动回填路径不刷 `n/N` 进度，种子收尾最多一行数量摘要  
- [ ] 低吸进度可打印  
- [ ] selftest 覆盖 record/回填/自动回填关键路径  

## 8. 已知缺口 / 待迭代

- 路线图仍列待补字段：`amount_yi`/`cap_yi`/`rank_pct`/`veto_labels` 等  
- T+3/T+5 回填完整度常落后于 T+1，需每日跑  
- 计胜口径对低吸可能过严（F06 阶段 3 再议）  
