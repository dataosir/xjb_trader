# F11 · 复盘 / 跟涨落盘 / T+n 回填

## 1. 背景与目标

把「选过的票」变成可归因样本：落盘 → T+1/T+3/T+5 回填 → 胜率与因子统计。没有稳定 review，所有策略迭代都是猜。

## 2. 用户故事 / 场景

- 收盘后 `review`：回填跟涨、观察池、当日累积。  
- `seed-plan` 收尾 / 进菜单（盘后·隔夜窗）：**自动轻量回填**待填历史样本（可关）。  
- `followthrough --update`：专注跟涨回填与胜率。  
- 开发者检查 `seed_records.jsonl` 是否含 `lowbuy`/`winrate_score`/`pick_sector_*`/`shadow_tag`。  
- 看**样本缺口看板**与影子桶 T+3 对照，决定是否够本谈策略（不够就继续攒）。

## 3. 功能范围

**In**

- `record_seed`：候选全字段落盘（含因子、布林观测字段、闸门、模式、`shadow_tag`）  
- T+n 回填与胜负口径（默认 T+1 涨≥3% 计胜）  
- 低吸样本进度统计  
- **样本缺口看板**：待 T+1 / 待 T+3 / 今日待下一交易日 / 低吸 / 因子覆盖 / 新闸门后可买  
- **影子桶对照**：萌芽 ∪（非突破∧rank≤3）→ `shadow_tag`；统计 T+3>0 vs `t3_up_target`（默认 60%）  
- 与 `close_review` 编排（跟涨 + 观察池 + accumulator）  
- **自动轻量回填**（默认开）：只跑 `update_results`，不替代全量 `review`

**Out**

- 根据回填自动改权重上线（须人工/阶段门控）  
- 影子桶驱动可买 / 写计划  
- 删除历史样本「美化胜率」  
- 系统级 cron / 自动下单  
- 「三日必赚」承诺（门槛只用于验收对照）

## 4. 主流程与边界

1. 种子结束：`_ft_entries` 收集可买/观察/低吸/胜率影子。  
2. `record_seed` 追加 jsonl；自动算 `shadow_tag`（萌芽 / 前三非突破）。  
3. **自动回填（可选）**：`runner.maybe_auto_backfill(trigger=seed|menu)`；默认**后台异步**（`auto_backfill_async`），不阻塞种子或菜单。  
4. `review`/`followthrough`：回填 T+n；打印经验胜率 + **缺口看板** + **影子桶** + 阶段 B / 低吸进度。  
5. 聚合输出胜率、分桶（供路线图与未来先验）。  

**边界**：

- 今日刚落盘的样本**回填不了**（须下一交易日收盘后）。  
- 字段缺失会导致阶段 2 归因失败——新字段必须双端（entries + record_seed）同时写。  
- 菜单自动回填每天最多 1 次（`daily_state.auto_backfill_menu`）。  
- **已完整回填**（有 `result` 且有 `chg_t5`）的行会被跳过，**不改写历史结论**。  
- 种子收尾自动回填：**默认后台线程**，控制台一行「后台回填已启动（待 N 条）」；结果见 `logs/tea.log`（`tea.ft`）；无 pending 则不输出。  
- 同步路径：`maybe_auto_backfill(..., background=False)` 或 `auto_backfill_async: false`。  
- 旧样本无 `shadow_tag` 时，统计侧按阶段/排名**重算**兼容。

## 5. 关键配置键

| 键 | 用途 |
|---|---|
| `followthrough.min_samples` | 聚合最小样本（先验另用更严阈值） |
| `followthrough.win_chg_pct` | T+1 计胜阈值（默认 3%） |
| `followthrough.auto_backfill_on_seed` | seed-plan 收尾自动轻量回填（默认 true） |
| `followthrough.auto_backfill_on_menu` | 进菜单盘后/隔夜窗自动回填（默认 true） |
| `followthrough.auto_backfill_full_review` | true 则自动路径改为全量 `close_review`（默认 false） |
| `followthrough.auto_backfill_async` | true 则自动回填走后台线程（默认 true） |
| `followthrough.p0_gate_date` | 新闸门后可买起算日（默认 `2026-08-26`） |
| `followthrough.t3_up_target` | 影子桶验收：T+3>0 目标比例（默认 0.60） |
| `followthrough.shadow_min_samples` | 影子桶最少 T+3 回填条数（默认 15） |

## 6. 代码锚点

- `tea/analysis/followthrough.py`（含 `sample_gap_stats` / `shadow_t3_stats` / `compute_shadow_tag`）  
- `tea/portfolio/accumulator.py`  
- `tea/runtime/runner.py` · `close_review` / `maybe_auto_backfill` / `_ft_entries`  
- CLI：`review` / `followthrough`；菜单 `8` 盘后复核

## 7. 验收标准

- [ ] 新种子记录含：`winrate_score`、`lowbuy`、`mode`、`pick_sector_*`、`shadow_tag`、因子字段  
- [ ] `review` 后 T+1 可回填（交易日与停牌边界正确）  
- [ ] seed/menu 自动回填可开关；失败不阻断种子或菜单；**默认异步不阻塞主流程**  
- [ ] 自动回填路径不刷 `n/N` 进度，种子收尾最多一行数量摘要  
- [ ] `review`/`followthrough` 打印缺口看板与影子桶文案  
- [ ] 低吸进度可打印  
- [ ] selftest 覆盖 record/回填/自动回填/缺口/影子关键路径  

## 8. 已知缺口 / 待迭代

- T+3/T+5 回填完整度常落后于 T+1，需每周全量 `review`  
- 低吸样本仍近 0：本轮**只观察** diag，不放宽落盘  
- 影子桶达标后再开策略 Plan（收紧萌芽可买或另议过热）；未达标前禁止凭感觉改闸门  
- 计胜口径对低吸可能过严（F06 阶段 3 再议）  
