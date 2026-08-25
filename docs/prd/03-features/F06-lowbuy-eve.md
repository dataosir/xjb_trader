# F06 · 低吸前夕（只攒样本）

## 1. 背景与目标

追涨通道天然错过「启动前夕」。低吸通道在 1%~3% 涨幅窗、升温中游板块池中选「鱼身」候选。  
**阶段 1 已落地：只落盘积累，不写计划、不买入。**

## 2. 用户故事 / 场景

- `seed-plan` 在追涨扫描之外，对低吸板块池跑 `eve_scan`。  
- `review` 打印低吸样本进度（目标 ≥30 条再验证）。  
- 空池时打印逐关归因，而不是静默「无」。

## 3. 功能范围

**In**

- `lowbuy_sector_pool`：rank 3~10、板块涨幅 2~4%、涨停≤1  
- `TIER_EVE` 扫描；标签 `lowbuy=True`  
- 因子字段随种子落盘（bias/vol/intraday/ma 等）  
- `lowbuy_pool_diag` 空池归因  
- 文案：低吸观察（启动前夕）  

**Out**

- `TIER_LOWBUY` 可写计划 / 买入（阶段 3）  
- 独立低吸形态门槛（阶段 2 验证后）  
- 上调低吸专用 `min_odds` 并实盘（阶段 3）  

## 4. 主流程与边界

1. 板块排序后过滤低吸池。  
2. 池空 → 诊断字符串进 notes。  
3. 池非空 → `eve_scan`，预审可算 winrate_score，但**永不进可买写计划**。  
4. `_ft_entries` → `record_seed` 必须写入 `lowbuy` 等字段。  

**边界**：低吸胜率天然偏低，未来上线必须靠更高 R:R 与更长持仓周期，不能复用追涨 T+1≥3% 单一计胜而不评估。

## 5. 关键配置键

| 键 | 默认意图 |
|---|---|
| `seed.lowbuy_rank_min/max` | 3 / 10 |
| `seed.lowbuy_chg_min/max` | 2.0 / 4.0 |
| `seed.lowbuy_limit_up_max` | 1 |
| 前夕涨幅窗 | `TIER_EVE` 约 1.0~3.0% |

## 6. 代码锚点

- `tea/screening/screener.py` · `lowbuy_sector_pool` / `lowbuy_pool_diag` / `eve_scan`  
- `tea/analysis/followthrough.py` · `lowbuy_sample_stats` / `record_seed`  
- 文档：`docs/archive/LOWBUY_PLAN_2026-08-25.md`

## 7. 验收标准

- [ ] 低吸候选不写入可买交易计划  
- [ ] `seed_records` 含 `lowbuy=true` 及关键因子字段  
- [ ] `review` 可见低吸样本计数/胜率进度  
- [ ] 空池有逐关剩余数说明  

## 8. 已知缺口 / 待迭代

- 历史低吸条数曾长期为 0（落盘 bug 已修，需持续跑日循环）  
- 阶段 2：≥30 条后验证量能/位置/乖离/板块假设  
- 阶段 3：独立档位与持仓周期（见 LOWBUY_PLAN）  
