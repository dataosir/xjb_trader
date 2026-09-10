# 07 · 每周选股周报邮件调度（launchd）

> **分工**：本页管「每周五收盘后发送选股周报邮件」的机器侧安装；产品语义见 [`../prd/03-features/F17-weekly-email-summary.md`](../prd/03-features/F17-weekly-email-summary.md)；SMTP 配置见 [`06-watch-alert-scheduler.md`](06-watch-alert-scheduler.md)。

---

## 1. 方案说明

| 项 | 说明 |
|---|---|
| 触发时间 | **每周五 17:00**（`StartCalendarInterval` Weekday=5） |
| 实际生效 | 命令内守卫：**交易日 + 周五** + `weekly_email.enabled` + `notify.email` 已配置 |
| 代码入口 | `python3 -m tea weekly-email`（一次性，非 daemon） |
| 报告内容 | 与 `tea weekly --write` 同源（F12） |
| 纪律 | **只发邮件，不自动下单** |

---

## 2. 前置：配置邮箱

若 F16 已配好 SMTP，只需开启周报开关：

```bash
tea config set weekly_email.enabled true
```

尚未配置邮箱时，先跑引导：

```bash
tea setup-email
```

---

## 3. 安装（macOS）

```bash
cd /path/to/tea
chmod +x ops/*.sh
./ops/install-launchd-weekly-email.sh
```

卸载：

```bash
./ops/uninstall-launchd-weekly-email.sh
```

---

## 4. 手动试跑

```bash
# 忽略周五/去重守卫（演练）
tea weekly-email --force

# 仅测不发（看 skip 原因）
tea weekly-email
```

日志：

```bash
tail -20 logs/daily/weekly_email/$(date +%Y-%m-%d).log   # 周五有发信才有
grep tea.weekly_email logs/tea.log | tail -10
```

---

## 5. 排障

| 现象 | 处理 |
|---|---|
| `skip: not_friday` | 非周五正常；用 `--force` 演练 |
| `skip: not_trading_day` | 节假日周五不发 |
| `skip: weekly_email_disabled` | `tea config set weekly_email.enabled true` |
| `skip: email_not_configured` | 跑 `tea setup-email` |
| `skip: already_sent` | 本周已发；`--force` 可重发 |
| SMTP 失败 | 查授权码 / SSL；见 F16 排障 |

---

## 6. 与 F16 共存

| launchd Label | 频率 | 命令 |
|---|---|---|
| `com.tea.watch-alert` | 每 60s（盘中） | `watch-alert` |
| `com.tea.weekly-email` | 周五 17:00 | `weekly-email` |
| `com.tea.seed-plan` | 周一至五 14:30 | `seed-plan` |

三者独立 plist，可分别安装/卸载。
