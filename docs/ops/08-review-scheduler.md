# 盘后复核自动调度（launchd）

> 关联：F11 review / 跟涨 T+N 回填 / 方案 E 周报 T+3

## 1. 做什么

工作日 **15:01**（可配置 `scheduler.review.*`）自动跑 `tea review --scheduled`：

1. 回填种子 `seed_records` 的 T+1/T+2/T+3/T+5
2. 观察池复核 + 超时清理
3. 打印样本缺口看板 + **T+3 归因**（方案 E）

**不自动下单**；与 F16/F18 盘中提醒无关。

## 2. 与现有机制的关系

| 机制 | 时机 | 深度 |
|---|---|---|
| `seed-plan` 收尾 `maybe_auto_backfill` | 14:30 | 轻量 `update_results`（后台） |
| 进菜单 `maybe_auto_backfill(menu)` | 盘后/隔夜，每天 1 次 | 轻量（后台） |
| **本 launchd `review --scheduled`** | **15:01**（`scheduler.review`） | **全量 `close_review`** |
| 菜单 **8 · 盘后复核** | 手动 | 全量（随时） |
| F17 `weekly-email` | 周五 17:00 | 发信前 `force` 再跑一遍 review |

轻量回填补 T+1；**T+3 要靠全量 review**（或周五周报前强制 review）。

## 3. 安装

```bash
cd /path/to/tea
chmod +x ops/*.sh
./ops/install-launchd-review.sh
tea config set review.scheduled_enabled true
```

改触发时刻（改完须重装 launchd）：

```bash
tea config set scheduler.review.hour 15
tea config set scheduler.review.minute 1
./ops/install-launchd-review.sh
tea launchd list    # 核对四个任务的 scheduler.* 时刻
```

手动试跑（忽略时段/去重）：

```bash
tea review --scheduled --force
```

## 4. 守卫与去重

- 非交易日 → `skip: not_trading_day`
- 15:00 前 → `skip: before_close`
- 同日已跑 → `skip: already_done`（state：`data/review_scheduled_state.json`）
- `review.scheduled_enabled=false` → 跳过

## 5. 日志

- `logs/tea.log`：搜 `tea.review`
- `logs/review-cron.log`：wrapper 输出
- `logs/launchd-review.{stdout,stderr}.log`：launchd 直出

## 6. 排障

| 现象 | 处理 |
|---|---|
| 待 T+3 一直不降 | 确认 launchd 已装；手动 `tea review --force` |
| 周五周报 T+3 仍旧 | `weekly_email.run_review_before` 默认 true；查 `tea.weekly_email pre_review` 日志 |
| 与菜单 8 重复 | 正常；去重保证每天自动只跑 1 次，菜单 8 可随时补跑 |
