# F08 · 执行四阶段（run / eval）

## 1. 背景与目标

单标的准入走 Phase1→4 交互流水线：在门禁与预审通过后，给出是否登记试错仓的结论。`eval` 共用评分、不买不落仓。

## 2. 用户故事 / 场景

- T+1 14:00–14:45：`run 600519` 走完全流程。  
- 任意时刻：`eval` 演练评分。  
- 无人值守/自测：经 `prompt.IO`，禁止阶段内直接 `input()`/`print()`。

## 3. 功能范围

**In**

- `Session` 状态机；`OK/REJECT/ABORT` 统一结果  
- Phase1–4（采集/确认/评分/结论登记等，以 `phases/` 实现为准）  
- 与 F02 门禁、F04 预审、F09 持仓登记衔接  
- `--no-buy` / `--any-time` / `--force` 演练开关  

**Out**

- 券商下单  
- 种子批量扫描（F03）  

## 4. 主流程与边界

1. 建 Session → 会话门禁。  
2. 逐阶段推进；失败即 REJECT/ABORT 并归档报告。  
3. 通过且非 `--no-buy` → 登记灰度试错仓（F09）。  

**边界**：阶段之间不互相调用；返回值不得硬编码字面量替代 `results` 常量。

## 5. 关键配置键

| 键域 | 用途 |
|---|---|
| 门禁与窗口 | F02 |
| 评分与 min_odds | F04 |
| 仓位乘数 / 3-7 比例 | F09 |

## 6. 代码锚点

- `tea/phases/phase1.py` … `phase4.py`  
- `tea/phases/session.py` / `results.py` / `prompt.py`  
- `tea/runtime/runner.py` · `run_once`  
- CLI：`tea run` / `tea eval`

## 7. 验收标准

- [ ] `eval` 不改持仓  
- [ ] `run` 计划外代码失败  
- [ ] 报告写入 `reports/TRADE_CHECK_*.md`（按实现）  
- [ ] selftest 覆盖阶段结果枚举与关键门禁  

## 8. 已知缺口 / 待迭代

- README 与门禁关于 MA20 硬拦的历史描述需与实现保持同步  
- 交互文案与颜色语义遵循 CONTRIBUTING（种子品红 vs 盈亏绿红）  
