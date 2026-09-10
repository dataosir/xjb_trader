# TEA 外部运维脚本（ops/）

> 机器侧调度与安装脚本，**不在 TEA 进程内**做 cron。命令语义仍以 `docs/prd/02-daily-workflow.md` 为准。

## 种子扫描定时（方案 A）

| 文件 | 用途 |
|---|---|
| [`seed-plan-cron.sh`](seed-plan-cron.sh) | 交易日 wrapper：设 `TEA_HOME` → `python -m tea seed-plan` → 写 `logs/seed-cron.log` |
| [`com.tea.seed-plan.plist.template`](com.tea.seed-plan.plist.template) | macOS launchd 模板（`__TEA_HOME__` 占位） |
| [`install-launchd-seed-plan.sh`](install-launchd-seed-plan.sh) | 生成并加载 `~/Library/LaunchAgents/com.tea.seed-plan.plist` |
| [`uninstall-launchd-seed-plan.sh`](uninstall-launchd-seed-plan.sh) | 卸载 launchd 任务 |

详细 SOP、漏扫补救与验收清单见 [`docs/ops/05-seed-plan-scheduler.md`](../docs/ops/05-seed-plan-scheduler.md)。

### 快速安装（macOS）

```bash
cd /path/to/tea
chmod +x ops/*.sh
./ops/install-launchd-all.sh          # 一键安装四个任务
# 或单独：./ops/install-launchd-seed-plan.sh
```

仓库迁移 / Python 升级后自检：

```bash
tea launchd doctor
```

### 手动试跑

```bash
./ops/seed-plan-cron.sh
tail -20 logs/seed-cron.log
```

### 约束

- **只触发 `seed-plan`**：不自动 `plan-check` / `run` / 下单。
- **机器须 14:30 在线**：合盖休眠会漏扫；见 ops 文档 §漏扫补救。
- **周末脚本内跳过**；法定假日仍可能触发（exit 0），与 F11「不做进程内 cron」不冲突。

## 观察池盘中提醒（F16）

| 文件 | 用途 |
|---|---|
| [`watch-alert-cron.sh`](watch-alert-cron.sh) | wrapper：`python -m tea watch-alert` → `logs/watch-alert-cron.log` |
| [`com.tea.watch-alert.plist.template`](com.tea.watch-alert.plist.template) | launchd 模板（`StartInterval=60`） |
| [`install-launchd-watch-alert.sh`](install-launchd-watch-alert.sh) | 安装 `com.tea.watch-alert` |
| [`uninstall-launchd-watch-alert.sh`](uninstall-launchd-watch-alert.sh) | 卸载 |

SOP 见 [`docs/ops/06-watch-alert-scheduler.md`](../docs/ops/06-watch-alert-scheduler.md)。默认 SMTP 为 **163 邮箱**（`smtp.163.com:465`）。

```bash
chmod +x ops/*.sh
./ops/install-launchd-watch-alert.sh
```

## 盘后复核自动回填（F11 扩展）

| 文件 | 用途 |
|---|---|
| [`review-cron.sh`](review-cron.sh) | wrapper：`python -m tea review --scheduled` |
| [`_launchd-common.sh`](_launchd-common.sh) | launchd 安装公共函数（`tea launchd render-plist`） |
| [`com.tea.review.plist.template`](com.tea.review.plist.template) | 参考模板（安装脚本以 `scheduler.*` 动态生成为准） |
| [`install-launchd-review.sh`](install-launchd-review.sh) | 安装 `com.tea.review` |
| [`uninstall-launchd-review.sh`](uninstall-launchd-review.sh) | 卸载 |

SOP 见 [`docs/ops/08-review-scheduler.md`](../docs/ops/08-review-scheduler.md)。菜单 **8** 仍为手动全量复核；本任务自动补 T+3。

```bash
./ops/install-launchd-review.sh
```
