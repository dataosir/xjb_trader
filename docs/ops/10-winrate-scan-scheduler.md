# 胜率影子扫描自动调度（launchd / cron）

> 关联：F05 / B-P1-01 rule vs winrate 影子对照

## 1. 做什么

工作日 **14:35**（可配置 `scheduler.winrate_scan.*`，默认 seed-plan 后 5 分钟）自动跑 `tea winrate-scan --scheduled`：

1. 全市场胜率通道扫描（`winrate_score` 分档）
2. 落盘 `mode=winrate` 影子样本 + `scan_details` sidecar
3. 有候选时发 `[TEA胜率]` 邮件（与菜单 4 相同）

**不写计划、不入观察池、不自动下单**；与 14:30 `seed-plan`（rule 主样本）并行对照。

## 2. 与 seed-plan 的关系

| 维度 | `seed-plan`（14:30） | `winrate-scan`（14:35） |
|---|---|---|
| 通道 | rule（9 分共振） | winrate（数据型） |
| 写计划 | 有可买则写 | **否** |
| 样本 mode | rule（主样本） | winrate（影子） |
| 去重 | scan_anchor primary | `(date,code)` 不覆盖 rule |
| 增量 | — |  mainly「rule 未选、winrate 选到」的差异集 |

同 `(date, code)` 已有 rule 记录时，winrate 扫描**不会**再写一条 winrate 记录（设计使然，见 `followthrough._should_replace_record`）。

## 3. 安装

### macOS launchd

```bash
cd /path/to/tea
chmod +x ops/*.sh
./ops/install-launchd-winrate-scan.sh
# 或一键重装全部：
./ops/install-launchd-all.sh
tea config set winrate.scheduled_enabled true
```

### Linux cron（NAS）

```bash
./ops/install-cron-linux.sh   # 含 14:35 winrate-scan-cron.sh
```

改触发时刻（改完须重装 launchd / cron）：

```bash
tea config set scheduler.winrate_scan.hour 14
tea config set scheduler.winrate_scan.minute 35
./ops/install-launchd-winrate-scan.sh
tea launchd list
```

手动试跑（忽略去重）：

```bash
tea winrate-scan --scheduled --force
# 或
./ops/winrate-scan-cron.sh
```

菜单 **4 · 胜率选股** 仍可随时手动跑；与自动任务共用扫描逻辑。

## 4. 守卫与去重

- 非交易日 → `skip: not_trading_day`
- 同日已跑 → `skip: already_done`（state：`data/winrate_scheduled_state.json`）
- `winrate.scheduled_enabled=false` → 跳过
- `--force` → 忽略去重（漏扫补救）

## 5. 日志

- `logs/daily/winrate/YYYY-MM-DD.log`：**胜率影子日录**
- `logs/tea.log`：搜 `tea.winrate`
- `logs/launchd-winrate.{stdout,stderr}.log`：launchd 直出（stderr 增量同步到 `logs/error.log`）
- **`[TEA运维]` 日终摘要**：15:01 review 后邮件含 `winrate-scan` 心跳

## 6. 排障

| 现象 | 处理 |
|---|---|
| 运维邮件报 winrate 未跑 | `launchctl list \| grep com.tea.winrate-scan`；重装 `./ops/install-launchd-winrate-scan.sh` |
| winrate 样本仍慢 | 正常——大量票与 rule 重叠会被去重；看 `added` 而非扫描总数 |
| 与菜单 4 重复 | 自动任务有日去重；手动 `--force` 可补跑 |
| launchd `Operation not permitted` | plist 直调 `python3 -m tea winrate-scan --scheduled`；重装 install 脚本 |

## 7. Phase 0 验收

- [ ] `tea launchd list` 含 `winrate_scan: 周一至五 14:35`
- [ ] 交易日 14:35 后 `logs/daily/winrate/` 有当日文件
- [ ] `seed_records` 新增 `mode=winrate`（与 rule 不重叠的增量）
- [ ] 15:01 运维邮件 `winrate-scan` 行显示正常
