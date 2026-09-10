# 运营层（ops）索引

> 全库清单见 [`../INDEX.md`](../INDEX.md)；入口与闭环见 [`../README.md`](../README.md)。

| 文档 | 职责 |
|---|---|
| [`00-operator-hats.md`](00-operator-hats.md) | 两顶帽子：交易员 vs 策略研发；证据阶段时间占比 |
| [`01-growth-channels.md`](01-growth-channels.md) | 受众与推广渠道假设 |
| [`02-user-feedback.md`](02-user-feedback.md) | 反馈 / Bug 收集与进迭代规则 |
| [`03-operator-daily-sop.md`](03-operator-daily-sop.md) | 日 MIT + 晨晚间 checklist（命令时间线见 `prd/02-daily-workflow`） |
| [`04-evidence-scorecard.md`](04-evidence-scorecard.md) | 证据周 scorecard + 里程碑 / Kill 表（Ops-2） |
| [`05-seed-plan-scheduler.md`](05-seed-plan-scheduler.md) | 14:30 种子扫描 launchd 安装与漏扫 SOP（脚本在仓库 `ops/`） |
| [`09-promotion-one-pager.md`](09-promotion-one-pager.md) | 对外推广 one-pager 快捷引用 |
| [`06-watch-alert-scheduler.md`](06-watch-alert-scheduler.md) | 观察池盘中提醒 launchd（每分钟 + SMTP 配置） |
| [`07-weekly-email-scheduler.md`](07-weekly-email-scheduler.md) | 每周五选股周报邮件 launchd（17:00 + SMTP） |

**分工**：`prd/02-daily-workflow` = 机器命令 SOP；`ops/03` = 人怎么不逃避运营；`ops/05`/`06`/`07` + 仓库 `ops/` = 外部定时触发。  
产品与技术决策不在本层拍板；本层记录「谁在用、怎么分配注意力、说了什么、是否值得开 Plan」。
