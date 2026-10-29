# 11 · 市场天气缺口自动重试（cron）

> **分工**：东财 `clist` 盘中抖动导致 `sector_stale` / `hard` 缺口时，用 **外部 cron** 每隔几分钟刷新天气；不自动 `seed-plan`、不下单。命令语义见 [`../prd/02-daily-workflow.md`](../prd/02-daily-workflow.md) 与 F01/F14。

---

## 1. 行为说明

| 项 | 说明 |
|---|---|
| 脚本 | `ops/weather-retry-cron.sh` |
| 触发建议 | 交易日 **每 3 分钟**（脚本内默认仅 **09:25–15:10** 本地时间执行，其余时刻 exit 0） |
| 逻辑 | 先读 120s 缓存；若仍有 `sector_stale` 或 `data_gap_summary` 非空 → `weather(refresh=True)` 强制重拉 |
| 成功 | exit 0，写入 `logs/daily/weather_retry/YYYY-MM-DD.log` |
| 仍缺口 | exit 2，并追加 `logs/error.log`（下一档 cron 继续重试） |
| 与 seed | **不替代** 14:30 `seed-plan`；缺口消除后请按需手动重跑 `tea seed-plan` |

---

## 2. 安装（Linux / NAS）

已并入一键安装：

```bash
cd /path/to/tea
chmod +x ops/weather-retry-cron.sh
./ops/install-cron-linux.sh
crontab -l | grep weather-retry
```

等价 cron 行（`install-cron-linux.sh` 会写入 TEA 块）：

```cron
*/3 * * * 1-5 cd /path/to/tea && TEA_HOME=/path/to/tea TEA_PYTHON=python3 /path/to/tea/ops/weather-retry-cron.sh
```

---

## 3. macOS（crontab，可选）

launchd 未装本任务时，可用用户 crontab（需本机 **cron 有权限**、且交易日机器在线）：

```bash
crontab -e
```

加入（路径换成你的仓库）：

```cron
*/3 * * * 1-5 cd /Users/you/tea && TEA_HOME=/Users/you/tea ./ops/weather-retry-cron.sh
```

试跑：

```bash
./ops/weather-retry-cron.sh
tail -20 logs/daily/weather_retry/$(date +%Y-%m-%d).log
```

---

## 4. 环境变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `TEA_HOME` | 仓库根 | 数据 / 配置 |
| `TEA_PYTHON` | `python3` | 解释器 |
| `TEA_WEATHER_RETRY_WINDOW` | `0925-1510` | 允许执行时段（`HHMM-HHMM`） |

东财仍长期失败时，配合 `tea config set market.*_retries` 与网络/代理排查（见 F14 README），cron 只能**反复尝试**，不能替代可用出口。
