# F11 · 复盘 / 跟涨落盘 / T+n 回填

## 1. 背景与目标

把「选过的票」变成可归因样本：落盘 → T+1/T+3/T+5 回填 → 胜率与因子统计。没有稳定 review，所有策略迭代都是猜。

## 2. 用户故事 / 场景

- 收盘后 `review`：回填跟涨、观察池、当日累积。  
- `followthrough --update`：专注跟涨回填与胜率。  
- 开发者检查 `seed_records.jsonl` 是否含 `lowbuy`/`winrate_score`/`pick_sector_*`。

## 3. 功能范围

**In**

- `record_seed`：候选全字段落盘（含因子、闸门、模式）  
- T+n 回填与胜负口径（默认 T+1 涨≥3% 计胜）  
- 低吸样本进度统计  
- 与 `close_review` 编排（跟涨 + 观察池 + accumulator）  

**Out**

- 根据回填自动改权重上线（须人工/阶段门控）  
- 删除历史样本「美化胜率」  

## 4. 主流程与边界

1. 种子结束：`_ft_entries` 收集可买/观察/低吸/胜率影子。  
2. `record_seed` 追加 jsonl（原子友好追加策略以实现为准）。  
3. `review`/`followthrough --update`：按交易日取后续行情填 T+n。  
4. 聚合输出胜率、分桶（供路线图与未来先验）。  

**边界**：字段缺失会导致阶段 2 归因失败——新字段必须双端（entries + record_seed）同时写。

## 5. 关键配置键

| 键 | 用途 |
|---|---|
| `followthrough.min_samples` | 聚合最小样本（先验另用更严阈值） |
| 计胜阈值 | T+1 涨幅阈值等 |

## 6. 代码锚点

- `tea/analysis/followthrough.py`  
- `tea/portfolio/accumulator.py`  
- `tea/runtime/runner.py` · `close_review` / `_ft_entries`  
- CLI：`review` / `followthrough`

## 7. 验收标准

- [ ] 新种子记录含：`winrate_score`、`lowbuy`、`mode`、`pick_sector_*`、因子字段  
- [ ] `review` 后 T+1 可回填（交易日与停牌边界正确）  
- [ ] 低吸进度可打印  
- [ ] selftest 覆盖 record/回填关键路径  

## 8. 已知缺口 / 待迭代

- 路线图仍列待补字段：`amount_yi`/`cap_yi`/`rank_pct`/`veto_labels` 等  
- T+3/T+5 回填完整度常落后于 T+1，需每日跑  
- 计胜口径对低吸可能过严（F06 阶段 3 再议）  
