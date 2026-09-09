# F16 · 观察池盘中提醒（邮件）

## 1. 背景与目标

观察池（F10）标的入池后，用户需盯盘等待「回踩就绪」或「预审 PASS」再手动 `eval` / `run`。**本功能在盘中每分钟扫描一次观察池**，满足购买条件时向配置邮箱发送提醒，**不自动下单、不写计划**。

与 F11 Out「不做进程内 cron」一致：调度走 **macOS launchd 外层**（同 F03 种子扫描方案 A），进程内只提供一次性扫描命令。

## 2. 用户故事 / 场景

- 14:30 `seed-plan` 后观察池新增 2 只，用户不想全天盯菜单 `7`。  
- 安装 launchd 任务后，盘中每分钟自动 `tea watch-alert`；某只回踩就绪 → 邮件提醒「600xxx 回踩就绪，请 eval/run」。  
- 用户运行 `tea setup-email` 引导配置 163 发件/收件邮箱与授权码；或手动 `config set`。未配置则扫描静默跳过发信。  
- 同一标的同一交易日只提醒一次（防刷屏）；次日可再次提醒。

## 3. 功能范围

**In**

- 新 CLI：`tea watch-alert`（单次扫描，适合 launchd 每分钟触发）  
- 扫描对象：`watch_pool.json` 中 `status=active` 的项（**观察池非空才打行情**）  
- **购买条件**（可配置 `alert.condition`，默认 `pullback_ready`）：  
  - **`pullback_ready`**（默认）：`watch_pool.pullback_ready()` 三项全满足（回撤 ≥ 配置%、分时 ≤ 配置%、入池天数 ≤ 保留天数），且当日预审**无硬否决**、身份未降级杂毛  
  - **`preflight_pass`**：实时 `preflight.evaluate` 裁决 `PASS`（9 分共振 + 无硬否决 + R:R 达标 + 非杂毛 reject）  
- 邮件通知：SMTP（标准库 `smtplib`），收件人/发件人/SMTP 均在配置  
- 邮箱引导：`tea setup-email`（163 授权码步骤说明 + 测试邮件）；`tea setup-email --test` 仅测连通  
- 去重状态：`data/watch_alert_state.json`（按 `(date, code, condition)` 记录已发）  
- 时段守卫：非交易日 / 非盘中（`timing.session_*`）→ 退出 0，不发信、不打行情（或仅打日志一行 skip）  
- 结构化日志：`logs/tea.log` 记 `tea.alert` 扫描摘要  

**Out**

- 自动下单 / 自动写计划 / 自动 `run`  
- 观察池直接升格可买（仍须 F07 计划 + F08 `run`）  
- 进程内常驻 daemon / 内置 cron 线程  
- 微信 / 短信 / 其他第三方推送（Bark/macOS 见 [F18](F18-push-notify.md)）  
- 对「近失轨」「仅 seed_records 影子」发提醒（只扫观察池 active 项）

## 4. 主流程与边界

```
launchd 每 60s → watch-alert-cron.sh → tea watch-alert
    ↓
Timing：交易日 + 盘中？ ─否→ exit 0（skip）
    ↓
watch_pool.items(status=active) 为空？ ─是→ exit 0
    ↓
notify.email.enabled？ ─否→ exit 0
    ↓
逐只 preflight.evaluate → 按 alert.condition 判定
    ↓
过滤 watch_alert_state 当日已发
    ↓
notify.send_email → 更新 state → 写 tea.log
```

**边界**

1. **纪律不变**：邮件文案明确「请人工 eval/run，引擎不自动买入」。  
2. **行情频率**：每分钟 × 池内 N 只；`market.quote_cache_sec` 缓存减轻压力；池空则不请求。  
3. **密码安全**：SMTP 密码仅存 `tea_config.json`（gitignore），不进仓库、不进日志。  
4. **发信失败**：记录 ERROR，不更新 state（下一分钟可重试）；连续失败不崩溃。  
5. **低吸前夕轨**：默认参与扫描（与 F10 一致）；若 `alert.include_eve=false` 可排除 `前夕观察轨`。  
6. **与 F10 盘后 review 关系**：`review` 仍做收盘复核；本功能只管**盘中**提醒，不替代 `review`。

## 5. 关键配置键

| 键 | 默认 | 用途 |
|---|---|---|
| `alert.enabled` | `false` | 总开关（与 email 子开关联动） |
| `alert.condition` | `pullback_ready` | `pullback_ready` / `preflight_pass` |
| `alert.include_eve` | `true` | 是否扫描前夕观察轨 |
| `alert.require_no_hard_veto` | `true` | pullback 模式下仍要求无硬否决 |
| `alert.dedupe_per_day` | `true` | 同 code 同日只发一封 |
| `notify.email.enabled` | `false` | 邮件通道开关 |
| `notify.email.smtp_host` | `""` | SMTP 主机 |
| `notify.email.smtp_port` | `465` | 端口 |
| `notify.email.smtp_use_tls` | `true` | SSL/TLS |
| `notify.email.smtp_user` | `""` | 登录用户 |
| `notify.email.smtp_password` | `""` | 登录密码（敏感） |
| `notify.email.from_addr` | `""` | 发件人 |
| `notify.email.to_addrs` | `[]` | 收件人列表 |
| `notify.email.subject_prefix` | `[TEA观察]` | 主题前缀 |

复用 F10：`watch.pullback_*`、`watch.max_size` 等。

## 6. 代码锚点（规划）

| 模块 | 职责 |
|---|---|
| `tea/portfolio/watch_pool.py` | `scan_alerts()`：逐只评估 + 返回待提醒列表 |
| `tea/core/notify.py` | `send_email(cfg, subject, body)`：SMTP 发送 |
| `tea/runtime/runner.py` | `watch_alert()`：编排扫描 / 去重 / 发信 |
| `tea/runtime/cli.py` | `watch-alert` / `setup-email` 子命令 |
| `tea/config/email_setup.py` | 邮箱配置向导 |
| `tea/config/config_store.py` | `alert.*` / `notify.email.*` DEFAULTS |
| `ops/watch-alert-cron.sh` | launchd wrapper |
| `ops/com.tea.watch-alert.plist.template` | 每分钟 StartInterval=60 |

## 7. 验收标准

- [x] `alert.enabled=false` 时 `tea watch-alert` exit 0 且无 SMTP 连接  
- [x] selftest：mock SMTP + 假观察池 → 满足 `pullback_ready` 触发一次「待发」；state 去重后第二次不重复  
- [x] selftest：`preflight_pass` 模式 PASS 触发、REJECT 不触发  
- [x] 非盘中 / 非交易日：命令 exit 0，日志含 skip 原因  
- [x] 观察池为空：不打行情、不发信  
- [x] 发信失败写 ERROR，state 不 advance  
- [x] launchd 安装脚本可加载 `com.tea.watch-alert`  
- [x] 邮件正文含：代码、名称、轨道、条件类型、当前价、分时、回撤、提示「请手动 eval/run，不自动下单」

## 8. 已知缺口 / 待迭代

- 多收件人分组（可买 vs 观察）  
- 企业微信 / Bark 等通道  
- 与 `timing.buy_window` 联动：仅在 14:00–14:45 才发「可执行买入」级别提醒（可选后续）  
- 观察池项手动「暂停提醒」标记
