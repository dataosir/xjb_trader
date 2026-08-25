# F04 · 预审共振 / VETO / ATR / 身份

## 1. 背景与目标

对单票做「术」层完整评估：身份分、9 分共振、VETO、ATR 止损止盈与盈亏比。输出 PASS/观察/否决及结构化维度，供种子与 `eval`/`run` 共用。

## 2. 用户故事 / 场景

- `eval 600519`：只算不买，看清每一维得分与否决原因。  
- 种子预审：批量评估后交给 F03 硬闸。

## 3. 功能范围

**In**

- 身份判定（`identity`）  
- `score_nine` 共振维（板块、大盘趋势分级、消息、市值、量价、止损结构等）  
- VETO 硬/软否决；分时高位「强势豁免」  
- ATR 止损 clamp、含滑点 R:R、`min_odds`  
- `winrate_score` 计算（供闸门与影子通道）  

**Out**

- 写计划 / 门禁（F07/F02）  
- 改数据源（F14）  

## 4. 主流程与边界

1. 拉行情与 K 线指标（MA/ATR/量比/分时位置）。  
2. 身份分 → 共振维打分 → VETO。  
3. 结构止损止盈；R:R 不足则止损结构维不得分或整体不可买。  
4. 输出 `scoring_dims`、否决标签、建议仓位相关字段。  

**边界**：

- 非盘中可跳过分时相关误杀（历史修复）。  
- 缩量上涨（量比&lt;1.2）且非多头 → 量价 0 分；放量下跌可 -1。  
- 「乖离&gt;8%」扣分已从 `score_nine` 移除，改由 VETO/其他路径处理（见 CHANGELOG）。

## 5. 关键配置键

| 键 | 用途 |
|---|---|
| `strategy.pass_threshold` | 共振通过线 |
| `strategy.min_odds` | 含滑点最低 R:R（默认 2） |
| `scoring.*` | 各维满分/半满排名等 |
| `scoring.sl_struct_min_odds` | 止损结构维最低赔率 |
| VETO / 分时阈值 | 封顶、强势豁免开关等 |

## 6. 代码锚点

- `tea/screening/preflight.py` · `score_nine` / `winrate_score`  
- `tea/screening/veto.py`  
- `tea/analysis/identity.py`  
- `tea/analysis/expectancy.py`  
- `tea/data/indicators.py`（纯函数指标）  
- CLI：`tea eval <code> [--sl] [--tp] [--news]`

## 7. 验收标准

- [ ] `eval` 不落仓位  
- [ ] 公式变更必须同步 selftest 独立重算断言  
- [ ] 强势票分时高位可豁免软否决；弱势仍否决  
- [ ] R:R &lt; min_odds 不可当作结构过关  

## 8. 已知缺口 / 待迭代

- 消息面扫描常为 0/0（死维）  
- 共振维权重待阶段 3 用因子归因重构  
- `winrate_score` 权重已按种子校准，但仍可能需随样本更新  
