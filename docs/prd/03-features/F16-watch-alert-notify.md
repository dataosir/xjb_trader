# F16 · 盘中提醒（观察池买点 + 持仓卖点，邮件）

## 1. 背景与目标

观察池（F10）标的入池后，用户需盯盘等待「回踩就绪」或「预审 PASS」再手动 `eval` / `run`。**本功能在盘中每分钟扫描一次观察池**，满足购买条件时向配置邮箱发送提醒，**不自动下单、不写计划**。

**2026-11 扩展（P0）**：只提醒买点、不提醒卖点，等于把「没有只涨不跌的票」当成不会发生。现在同一轮盘中扫描同时检查**当前持仓**：现价跌破止损或触及止盈时发卖点提醒，观察池为空也继续扫持仓；**只提醒，不自动平仓**。

**2026-11 扩展（P1）**：种子邮件此前只含可买/观察/前夕，近失票「看到了但没进池」，用户误以为收到邮件即已纳入跟踪。现在 `seed-plan` 默认把近失写入 `近失观察轨`，并纳入价格跟踪；达到回踩条件同样发买点提醒。邮件/报告会明确标注「已入观察池」或「仅复盘」。

与 F11 Out「不做进程内 cron」一致：调度走 **macOS launchd 外层**（同 F03 种子扫描方案 A），进程内只提供一次性扫描命令。

## 2. 用户故事 / 场景

- 14:30 `seed-plan` 后观察池新增 2 只，用户不想全天盯菜单 `7`。  
- 安装 launchd 任务后，盘中每分钟自动 `tea watch-alert`；某只回踩就绪 → 邮件提醒「600xxx 回踩就绪，请 eval/run」。  
- 用户运行 `tea setup-email` 引导配置 163 发件/收件邮箱与授权码；或手动 `config set`。未配置则扫描静默跳过发信。  
- 同一标的同一交易日只提醒一次（防刷屏）；次日可再次提醒。  
- 按观察提醒买入后，持仓现价跌破止损 → 邮件「止损触发」，正文含成本/现价/浮亏/止损价，提示手动 `tea close`。  
- 持仓触及止盈 → 邮件「止盈触发」；止损与止盈按不同条件分别去重（同日各最多一封）。  
- 手动录入的持仓没写止损/止盈时，止损按 `position.fallback_sl_pct`（默认 3%）兜底，并在正文标注「默认止损」；关闭兜底则跳过并记入 `missing_levels`。  

## 3. 功能范围

**In**

- 新 CLI：`tea watch-alert`（单次扫描，适合 launchd 每分钟触发）  
- 扫描对象：  
  - **买点**：`watch_pool.json` 中 `status=active` 的项，含 `近失观察轨`（观察池空时不打这部分行情）；`alert.include_near_miss=false` 可排除近失轨  
  - **卖点**：`capital_state.json` 中当前持仓（`alert.position_exit_enabled=true` 时；观察池空也继续扫）  
- **购买条件**（可配置 `alert.condition`，默认 `pullback_ready`）：  
  - **`pullback_ready`**（默认）：`watch_pool.pullback_ready()` 三项全满足（回撤 ≥ 配置%、分时 ≤ 配置%、入池天数 ≤ 保留天数），且当日预审**无硬否决**、身份未降级杂毛  
  - **`preflight_pass`**：实时 `preflight.evaluate` 裁决 `PASS`（9 分共振 + 无硬否决 + R:R 达标 + 非杂毛 reject）  
- **卖出条件**（持仓卖点，条件名固定）：  
  - **`stop_loss`**：现价 ≤ 止损价（优先读持仓 `stop`，缺失时按 `sl_pct` 反推，再缺失按 `position.fallback_sl_pct` 兜底）  
  - **`take_profit`**：现价 ≥ 止盈价（优先读持仓 `target`，缺失时按 `tp_pct` 反推）  
  - 同一根行情同时越线按**止损优先**，与 `followthrough._simulate_exit` 保守口径一致  
- 邮件通知：SMTP（标准库 `smtplib`），收件人/发件人/SMTP 均在配置  
- 邮箱引导：`tea setup-email`（163 授权码步骤说明 + 测试邮件）；`tea setup-email --test` 仅测连通  
- 去重状态：`data/watch_alert_state.json`（买点）与 `data/position_alert_state.json`（卖点），均按 `(date, code, condition)` 记录已发  
- 时段守卫：非交易日 / 非盘中（`timing.session_*`）→ 退出 0，不发信、不打行情（或仅打日志一行 skip）  
- 结构化日志：`logs/tea.log` 记 `tea.alert` 扫描摘要  

**Out**

- 自动下单 / 自动平仓 / 自动写计划 / 自动 `run`  
- 观察池直接升格可买（仍须 F07 计划 + F08 `run`）  
- 进程内常驻 daemon / 内置 cron 线程  
- 微信 / 短信 / 其他第三方推送（Bark/macOS 见 [F18](F18-push-notify.md)）  
- 对「仅 seed_records 影子」发买点提醒（影子记录不入观察池）
- 近失自动入池后再自动升格可买（仍走 F07/F08）

## 4. 主流程与边界

```
launchd 每 60s → watch-alert-cron.sh → tea watch-alert
    ↓
Timing：交易日 + 盘中？ ─否→ exit 0（skip）
    ↓
通知通道就绪？ ─否→ exit 0（skip）
    ↓
观察池 active 为空 且 持仓为空？ ─是→ exit 0（skip，不打行情）
    ↓
观察池非空 → 资格剔除 → 逐只 preflight.evaluate → 按 alert.condition 判定
    ↓
持仓非空 → 读成本/止损/止盈 → 现价触线判定 stop_loss / take_profit
    ↓
分别过滤 watch_alert_state / position_alert_state 当日已发
    ↓
notify.send_alert → 更新对应 state → 写 tea.log
```

**边界**

1. **纪律不变**：买点邮件明确「请人工 eval/run，引擎不自动买入」；卖点邮件明确「请手动 `tea close`，引擎不自动卖出」。  
2. **行情频率**：每分钟 × （池内 N 只 + 持仓 M 只）；`market.quote_cache_sec` 缓存减轻压力；两类都空则不请求。  
3. **密码安全**：SMTP 密码仅存 `tea_config.json`（gitignore），不进仓库、不进日志。  
4. **发信失败**：记录 ERROR，不更新对应 state（下一分钟可重试）；连续失败不崩溃。  
5. **低吸前夕轨 / 近失观察轨**：默认参与扫描（与 F10 一致）；`alert.include_eve=false` 排除 `前夕观察轨`，`alert.include_near_miss=false` 排除 `近失观察轨`。近失轨资格复检不走「观察轨须软否决或 PASS」的口径，避免入池即被剔除。  
6. **与 F10 盘后 review 关系**：`review` 仍做收盘复核；本功能只管**盘中**提醒，不替代 `review`。  
7. **持仓数据质量**：卖点只看 `capital_state.json` 里登记过的持仓；券商实盘已买卖但未 `pos-add` / `close` 的，系统看不到。没录止损时可用默认 3% 兜底，但正文会标注「默认止损」，不假装是原计划止损。  
8. **同条件同日一封**：止损、止盈是两个独立条件；同日先触止损再触止盈会各发一封，不互相抑制。

## 5. 关键配置键

| 键 | 默认 | 用途 |
|---|---|---|
| `alert.enabled` | `false` | 总开关（与 email 子开关联动） |
| `alert.condition` | `pullback_ready` | `pullback_ready` / `preflight_pass` |
| `alert.include_eve` | `true` | 是否扫描前夕观察轨 |
| `alert.include_near_miss` | `true` | 是否扫描近失观察轨 |
| `seed.near_miss_to_watch` | `true` | 种子扫描后近失是否自动写入近失观察轨（同时影响邮件/报告措辞与价格跟踪） |
| `alert.require_no_hard_veto` | `true` | pullback 模式下仍要求无硬否决 |
| `alert.dedupe_per_day` | `true` | 同 code 同日同条件只发一封（买点/止损/止盈各自去重） |
| `alert.position_exit_enabled` | `true` | 是否扫描持仓卖点（观察池空也扫） |
| `alert.position_fallback_stop` | `true` | 持仓没录止损时是否按默认止损兜底 |
| `position.fallback_sl_pct` | `3.0` | 默认止损百分比（兜底用） |
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
| `tea/portfolio/watch_pool.py` | `scan_alerts()`：观察池买点逐只评估 + 返回待提醒列表（`TRACK_NEAR` 近失轨同口径） |
| `tea/runtime/runner.py` | `seed_plan()`：近失自动入 `近失观察轨`，并把近失代码纳入 `price_track` |
| `tea/screening/seed_report.py` | 邮件/报告近失段落 + 主题「近失N只」；按 `seed.near_miss_to_watch` 切换已入池/仅复盘措辞 |
| `tea/portfolio/position_alert.py` | `scan_alerts()` / `exit_levels()`：持仓卖点触线判定 + 卖点 state 去重 |
| `tea/core/notify.py` | `send_alert(cfg, subject, body)`：多通道发送（email/macos/bark） |
| `tea/runtime/runner.py` | `watch_alert()`：编排观察池 + 持仓双扫描 / 去重 / 发信 |
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
- [x] 观察池与持仓都为空：不打行情、不发信  
- [x] 观察池为空但持仓非空：继续扫持仓卖点，不因池空早退  
- [x] selftest：持仓跌破止损 → `stop_loss` 提醒；触及止盈 → `take_profit` 提醒  
- [x] selftest：同日同条件去重；止损与止盈互不抑制  
- [x] selftest：持仓缺止损/止盈时默认止损兜底；关闭兜底后跳过并记 `missing_levels`  
- [x] selftest：卖点发信失败不写 state；提醒后持仓仍在（不自动卖出）  
- [x] 发信失败写 ERROR，state 不 advance  
- [x] launchd 安装脚本可加载 `com.tea.watch-alert`  
- [x] 买点邮件正文含：代码、名称、轨道、条件类型、当前价、**建议挂单价**（乖离自适应纪律回踩；高乖离 MA20 仅参考；止损/止盈按挂单价重算）、**计划止盈路径 T+1/T+2/T+3**（挂单价→止盈价线性拆分；复盘对照，非概率；核心数字用【】强调）、分时、回撤、提示「请手动 eval/run，不自动下单」  
- [x] 卖点邮件正文含：代码、名称、条件（止损/止盈）、成本、现价、浮动盈亏、R 倍数、止损/止盈价与来源、建仓日/持有天数，提示「请手动 `tea close`，不自动卖出」
- [x] 近失自动入 `近失观察轨`：资格复检不剔除、参与 `scan_alerts` 发信；`alert.include_near_miss=false` 可排除
- [x] 近失纳入 `price_track`；种子邮件含近失段落，主题不再把「仅近失」误标为空仓
- [x] `seed.near_miss_to_watch=false` 时邮件/报告明确「仅复盘，不入观察池」

## 8. 已知缺口 / 待迭代

- 多收件人分组（可买 vs 观察）  
- 企业微信 / Bark 等通道  
- 与 `timing.buy_window` 联动：仅在 14:00–14:45 才发「可执行买入」级别提醒（可选后续）  
- 观察池项手动「暂停提醒」标记
