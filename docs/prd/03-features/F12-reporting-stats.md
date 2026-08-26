# F12 · 报告 / 统计 / 周报 / 追溯

## 1. 背景与目标

把决策与否决过程变成可读报告与可查询状态，回答：「今天为什么没买？谁在哪一步被淘汰？本周纪律如何？」

## 2. 用户故事 / 场景

- `status`：门禁计数 / 计划 / 持仓一屏。  
- `accum`：为何没交易。  
- `trace`：落选链路。  
- `stats` / `weekly`：胜率、期望值、纪律自查。

## 3. 功能范围

**In**

- 控制台状态与 Markdown 报告（SEED / TRADE_CHECK / STATS / 周报）  
- 落选追溯 jsonl 读取展示  
- 当日累积事件  
- 统计归因（胜率、R 分布、期望值）  

**Out**

- 在 reporting 层做交易决策（禁止；CONTRIBUTING）  
- BI 大盘 Web UI（非目标）  

## 4. 主流程与边界

1. 决策链路写结构化事件 / 报告。  
2. 查询命令只读聚合与呈现。  
3. `--write` / `--no-write` 控制落盘。  

**边界**：颜色语义固定——盈亏绿红、种子品红、警告黄；禁止裸颜色字符串。

## 5. 关键配置键

| 键域 | 用途 |
|---|---|
| 报告目录 / TEA_HOME | paths |
| weekly 默认天数 | CLI `--days` |

## 6. 代码锚点

- `tea/reporting/report.py` / `weekly.py` / `seed_trace.py` / `retrospective.py`  
- `tea/screening/seed_report.py`  
- `tea/analysis/stats.py`  
- `tea/runtime/runner.py` · `daily_status` / `stats_report` / `weekly_report`  
- CLI：`status` / `stats` / `weekly` / `accum` / `trace` / `trades`

## 7. 验收标准

- [ ] reporting 不包含买卖判定逻辑  
- [ ] `trace` 能按日展示淘汰步骤  
- [ ] `weekly` 含纪律自查项  
- [ ] 颜色自测 `check_colors` 通过  

## 8. 已知缺口 / 待迭代

- 顶层菜单已压到约 10 项（计划/准入/持仓/复盘子菜单）；改编号须同步 `check_menu`  
- 周报可链到 PRD backlog 完成度（可选）  
