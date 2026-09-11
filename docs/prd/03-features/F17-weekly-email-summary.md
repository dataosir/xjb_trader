# F17 · 每周选股周报邮件

## 1. 背景与目标

用户已配置 SMTP（F16），希望**每周五收盘后**自动把当周选股复盘（F12 `weekly`）发到邮箱，无需手动跑菜单 `7` 或查本地 Markdown。

与 F16 分工：
- **F16**：盘中每分钟，观察池回踩就绪 → 即时提醒买入时机  
- **F17**：每周一次，汇总当周扫描漏斗 / 落选原因 / 成交 / 跟涨经验 / 观察池 → 周总结邮件

**纪律不变**：邮件只呈现报告，不自动下单、不写计划。

## 2. 用户故事 / 场景

- 周五 17:00 launchd 触发 `tea weekly-email`；若本周尚未发送，生成 `WEEKLY_*.md` 并 SMTP 投递。  
- 用户出差未开电脑，仍能在 Outlook / 手机邮箱收到「本周初筛 N 次 → 可买 M 只」摘要。  
- 与 `tea weekly` 内容一致；手动 `tea weekly-email --force` 可演练（忽略周五/去重守卫）。

## 3. 功能范围

**In**

- 新 CLI：`tea weekly-email`（单次发送，适合 launchd 周五触发）  
- 报告体：复用 `weekly.collect` + `weekly.render_md`（与 `tea weekly --write` 同源，**完整版仅落盘本地**）  
- 邮件正文：`format_email_digest` 精简摘要（核心一览 / 空仓对比 / 本周主线 / 选股名单 / 样本积累）；`weekly_email.include_full_report=true` 可恢复附完整 Markdown  
- 邮件：复用 `notify.send_email`（SMTP 配置与 F16 共用 `notify.email.*`）  
- 主题前缀：默认 `[TEA周报]`（与观察提醒 `[TEA观察]` 区分，可配置）  
- 去重：`data/weekly_email_state.json`，同 ISO 周只发一封（`weekly_email.dedupe_per_week`）  
- 守卫：非交易日 / 非周五（可 `--force` 跳过）/ `weekly_email.enabled` + `notify.email.enabled`  
- 结构化日志：`logs/tea.log` 记 `tea.weekly_email`  

**Out**

- 自动调参 / 自动写计划  
- 进程内 cron  
- 与 F16 合并为同一 launchd 任务（独立 plist，便于单独开关）  
- HTML 富文本邮件（本版纯文本 Markdown 正文）

## 4. 主流程与边界

```
launchd 周五 17:00 → weekly-email-cron.sh → tea weekly-email
    ↓
Timing：交易日 + 周五？ ─否→ exit 0（skip）
    ↓
weekly_email.enabled + notify.email 已配置？ ─否→ exit 0
    ↓
本周已发送（state）？ ─是→ exit 0
    ↓
weekly.collect → render_md → 落盘 WEEKLY_*.md
    ↓
notify.send_email → 更新 state → tea.log
```

**边界**

1. 发信失败不更新 state，下一触发可重试。  
2. 邮箱密码不进日志；与 F16 共用 `tea_config.json`。  
3. 节假日周五（非交易日）不发；顺延需手动 `--force` 或等下周。  
4. `weekly_email.days` 默认 7，与 `tea weekly` 一致。

## 5. 关键配置键

| 键 | 默认 | 用途 |
|---|---|---|
| `weekly_email.enabled` | `false` | 总开关 |
| `weekly_email.days` | `7` | 汇总天数 |
| `weekly_email.dedupe_per_week` | `true` | 同 ISO 周只发一封 |
| `weekly_email.require_friday` | `true` | 仅周五触发（`--force` 可跳过） |
| `weekly_email.subject_prefix` | `[TEA周报]` | 邮件主题前缀 |
| `weekly_email.include_full_report` | `false` | 邮件末尾附完整 Markdown（默认仅精简摘要） |
| `notify.email.*` | 见 F16 | SMTP 共用 |

## 6. 代码锚点

- `tea/reporting/weekly.py` — `send_email_report` / 去重 state  
- `tea/core/notify.py` — `smtp_ready` / `send_email(subject_prefix=...)`  
- `tea/runtime/runner.py` — `weekly_email()`  
- `tea/runtime/cli.py` — `weekly-email` 子命令  
- `ops/weekly-email-cron.sh` + launchd 安装脚本  

## 7. 验收标准

- [ ] 周五交易日 + 已配邮箱 → 收到含「核心一览 / 空仓对比 / 本周选股 / 样本积累」的精简周报邮件  
- [ ] 同周重复触发只发一封  
- [ ] `--force` 可忽略周五与去重（演练）  
- [ ] `selftest` mock SMTP 覆盖发送与去重  
- [ ] 不发交易指令、不写计划  

## 8. 已知缺口 / 待迭代

- 节假日周五顺延自动补发（可选，当前手动 `--force`）  
- 邮件正文 HTML 排版（可选）
