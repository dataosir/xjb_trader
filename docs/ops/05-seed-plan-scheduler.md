# 05 · 种子扫描外部调度（launchd）

> **分工**：本页管「14:30 别忘跑 seed-plan」的机器侧安装与漏扫 SOP；命令语义见 [`../prd/02-daily-workflow.md`](../prd/02-daily-workflow.md)；人侧 checklist 见 [`03-operator-daily-sop.md`](03-operator-daily-sop.md)。

---

## 1. 方案说明（方案 A）

采用 **macOS launchd 外层调度**，调用仓库内 `ops/seed-plan-cron.sh`，内部执行现有 `tea seed-plan`。

| 项 | 说明 |
|---|---|
| 触发时刻 | 周一至周五 **14:30**（东八区，与 `timing.seed_scan` 对齐） |
| 代码改动 | **零**——复用 `runner.seed_plan()` 全链路 |
| 与 F11 Out | 「不做进程内 cron」≠ 禁止系统 launchd；仍不自动下单 |
| 幂等 | 同日多次跑：计划写入有 `active_codes_equal`；`seed_records` 有去重 |

---

## 2. 安装（macOS）

```bash
cd /path/to/tea          # 换成你的仓库路径
chmod +x ops/*.sh
./ops/install-launchd-seed-plan.sh
```

验证：

```bash
launchctl list | grep com.tea.seed-plan
./ops/seed-plan-cron.sh    # 手动试跑
tail -30 logs/seed-cron.log
ls -lt reports/SEED_*.md | head -3
```

卸载：

```bash
./ops/uninstall-launchd-seed-plan.sh
```

### 环境变量

与 `run.sh` 一致，安装前可 export：

| 变量 | 默认 | 用途 |
|---|---|---|
| `TEA_HOME` | 仓库根 | 数据 / 配置 / 报告目录 |
| `TEA_PYTHON` | `python3` | 指定解释器（多版本机器） |

---

## 3. 日志与产出

| 路径 | 内容 |
|---|---|
| `logs/tea.log` | **主审计源**：结构化运行日志（`tea.scan` / `tea.data` / `tea.score`） |
| `logs/seed-cron.log` | 手动 `./ops/seed-plan-cron.sh` 的终端输出；launchd 直调时由程序末尾补一行摘要 |
| `logs/launchd-seed.stdout.log` | launchd 标准输出 |
| `logs/launchd-seed.stderr.log` | launchd 标准错误（权限/路径问题先看这里） |
| `reports/SEED_*.md` | 种子报告 |

### 3.1 查日志速查

```bash
# 今日种子扫描摘要
grep -E "种子扫描开始|种子漏斗|扫描完成" logs/tea.log | tail -20

# 共振分逐只明细（有候选时才有）
grep "共振预审" logs/tea.log | tail -30

# 行情/K 线取数
grep -E "行情就绪|K线就绪|行情取数失败|K线取数失败" logs/tea.log | tail -30
```

晚间 SOP §4 检查：`tea.log` 或 `seed-cron.log` 当日是否有扫描完成记录，并扫一眼 SEED 报告。

### 3.2 launchd 报 `Operation not permitted`

旧版 plist 通过 `/bin/bash ops/seed-plan-cron.sh` 触发时，若仓库在 **Downloads** 目录，macOS 可能拦截脚本执行（`launchd-seed.stderr.log` 可见）。

**已修复**：新版 plist 直接 `python3 -m tea seed-plan`，需重装：

```bash
./ops/install-launchd-seed-plan.sh
```

若仍失败：将仓库移出 Downloads，或给 Terminal / `python3` 开「完全磁盘访问权限」。

---

## 4. 漏扫补救

| 场景 | 动作 |
|---|---|
| 笔记本合盖 / 休眠错过 14:30 | 收盘前手动 `tea seed-plan` 或 `./ops/seed-plan-cron.sh` |
| 连续 2 个交易日未产出 SEED | 次日 MIT-1 强制种子（见 [`03-operator-daily-sop.md`](03-operator-daily-sop.md) §4） |
| launchd 未加载 | `launchctl list \| grep com.tea.seed-plan`；无则重装 install 脚本 |
| 法定假日误触发 | 脚本周末会 skip；假日若触发可忽略（`seed-plan` 仍安全 exit） |

**成功定义不变**：自动扫描只替代「忘记点菜单 3」；`plan-check` 与 `run` 仍须人工。

---

## 5. 验收清单（Phase 0）

- [ ] `install-launchd-seed-plan.sh` 执行成功，`launchctl list` 可见 `com.tea.seed-plan`
- [ ] 手动 `./ops/seed-plan-cron.sh` 后 `logs/seed-cron.log` 有 `done seed-plan exit=0`
- [ ] `reports/SEED_*.md` 正常产出
- [ ] 晚间 SOP 已把「是否自动跑通」纳入 §4 checklist
- [ ] 知晓漏扫时手动补跑路径

---

## 6. 纪律链（不变）

```
launchd 14:30 → seed-plan → trade_plan.json（有可买时）
                          ↘ seed_records / watch_pool / 自动轻量回填
次日 09:35    → plan-check（人工）
T+1 14:00-14:45 → run（人工，不自动下单）
```

---

## 7. 相关文档

| 文档 | 用途 |
|---|---|
| [`../../ops/README.md`](../../ops/README.md) | 脚本清单与快速命令 |
| [`../prd/03-features/F03-seed-screening.md`](../prd/03-features/F03-seed-screening.md) | 种子四步流 |
| [`../prd/03-features/F11-review-followthrough.md`](../prd/03-features/F11-review-followthrough.md) | 自动回填 / Out 边界 |
| [`03-operator-daily-sop.md`](03-operator-daily-sop.md) | 人侧日 SOP |
