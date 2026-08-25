# F09 · 持仓 / 资金 / 3-7 仓 / 平仓

## 1. 背景与目标

管理总资金与持仓状态：试错仓 → 确认仓 → 平仓回收，并写流水。引擎登记仓位是**账本**，实盘成交需用户手动完成后再对齐账本。

## 2. 用户故事 / 场景

- `run` 通过后登记约 30% 试错仓。  
- 突破确认：`add-confirm` 补约 70%。  
- 实盘已买：`pos-add` 补登；卖出：`close`。  
- `pos` / `capital` 查看盈亏与种子对照。

## 3. 功能范围

**In**

- 总资金读写  
- 持仓增删、确认仓、平仓（全平/部分）  
- 交易流水  
- 持仓与当日种子命中对照展示  

**Out**

- 自动下单  
- 复杂组合优化 / 多账户（可用 `TEA_HOME` 沙箱隔离）  

## 4. 主流程与边界

1. 资金乘数由天气姿态夹紧后计算建议股数。  
2. 开仓登记占用资金；平仓释放并记 PnL。  
3. 流水追加，供 `stats`/`weekly`。  

**边界**：股数/价格以用户输入或现价为准；行情延迟可能导致账本与实盘短暂不一致——以用户确认为准。

## 5. 关键配置键

| 键域 | 用途 |
|---|---|
| 总资金 / 单笔风险 | capital / strategy |
| 试错/确认比例 | 约 3/7 |
| 乘数上下限 | 如 [0.25, 1.0] |

## 6. 代码锚点

- `tea/portfolio/portfolio.py` / `trades.py`  
- `tea/runtime/runner.py` · `confirm_position` / `close_trade` / `buy_plan_item` / `holdings_review`  
- CLI：`pos` / `pos-add` / `pos-rm` / `capital` / `add-confirm` / `close` / `trades`

## 7. 验收标准

- [ ] 原子写 `capital_state.json` / `trades.json`  
- [ ] 平仓后资金回笼与流水一致  
- [ ] `pos` 展示含种子对照时用 `COLOR_SEED` 而非盈亏色  
- [ ] 相关 selftest 覆盖仓位算术  

## 8. 已知缺口 / 待迭代

- 低吸若上线，持仓周期与止损跟踪需单独设计（跨 F06）  
- 实盘全手动时，系统选票胜率与账本盈利可能长期脱节——归因应以 seed_records 为准  
