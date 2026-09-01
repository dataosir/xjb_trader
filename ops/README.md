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
./ops/install-launchd-seed-plan.sh
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
