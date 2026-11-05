# 12 · 交接：2026-11-05 本机修复 → NAS 同步与后续 AI 跟进

> **读者**：回家后操作者 + 接续开发的 AI。  
> **状态**：本机已改代码并 **push 至 `origin/main`**（截至 commit `51ca3b7`）；NAS **尚未**拉取/对齐。  
> **闭环**：改完 NAS 后更新本文件「验收」勾选，并追加 `docs/CHANGELOG.md` + `docs/project-state.md`。

---

## 1. 背景（2026-11-05 发生了什么）

| 现象 | 根因 | 是否策略 bug |
|---|---|---|
| 种子扫描 / 天气屏 `fund_flow: 'Market' object has no attribute 'get_sector_fund_flow_top'` | NAS 或本机曾 **sentiment 新、market 旧**（或未 `pip install -e .`） | 否；缺路只影响展示，不参与情绪分 |
| 日终运维摘要 **20 条** `ERROR [_] weather-retry-cron` | ① 盘中 **板块/涨跌 stale**，重试仍缺口 → exit 2（预期）；② **`error_log_append` 参数写反**，正文丢失 | ② 是真 bug，已修 |
| 种子屏「板块排名磁盘兜底」「涨跌家数缓存回退」 | 东财/网络抖动 | 否；共振已对 stale 降权，但 14:30 选股质量可能偏 |

**本机已落地（在 `origin/main`）**：

- `ops/_log-daily.sh`：`error_log_append` source/正文顺序修正。  
- `tea/analysis/sentiment.py`：无 `get_sector_fund_flow_top` 时跳过，友好提示，不抛 AttributeError。  
- `tea/data/market.py`（此前已有）：`get_sector_fund_flow_top`。  
- MCP 只读 server + selftest hiddenimports（与 NAS 生产 cron **无硬依赖**，可不同步 optional 依赖）。

---

## 2. P0 · 今晚 NAS 必做（操作清单）

在 NAS 仓库根（与 **cron 同款** `python3` / `TEA_HOME`）：

### 2.1 同步代码（二选一）

**推荐：git**

```bash
cd /path/to/tea   # NAS 上的 TEA_HOME
git fetch origin
git checkout main
git pull origin main   # 应包含 51ca3b7 及更早 2ab3f4b 等
```

**或 rsync（不覆盖数据）**

```bash
# 在本机执行；路径按实际改
rsync -av --exclude .venv --exclude xjb_trade.egg-info \
  --exclude data/ --exclude reports/ --exclude logs/ \
  --exclude tea_config.json \
  /Users/renwentao/Downloads/code/tea/ user@nas:/path/to/tea/
```

### 2.2 解释器与 editable 安装

若 cron 用的是 site-packages 里的旧包，必须对齐源码：

```bash
cd /path/to/tea
python3 -c "from tea.data import Market; print('fund_flow', hasattr(Market(), 'get_sector_fund_flow_top'))"
# 期望: fund_flow True

pip install -e .    # 用 cron 同款 python3 -m pip install -e .
python3 -m tea.selftest   # 期望全绿（当前基线 904/904）
```

### 2.3 冒烟

```bash
python3 -m tea weather --refresh
python3 -m tea launchd doctor   # Linux 上若仅 cron，可跳过 launchd 项，改查 crontab -l
```

### 2.4 验收 error.log 可读性（下一交易日或手动触发 retry）

同步 **后**，若 weather-retry 仍 exit 2，新写入应类似：

```text
ERROR [weather-retry-cron] market weather still has gaps …
```

**不应再**只有 `ERROR [_] weather-retry-cron`（无正文）。

查账：

```bash
grep "$(date +%Y-%m-%d)" logs/error.log | tail -5
grep "$(date +%Y-%m-%d)" logs/daily/weather_retry/$(date +%Y-%m-%d).log | tail -10
```

---

## 3. P1 · 不必改代码，但要心里有数

| 项 | 说明 |
|---|---|
| weather-retry 仍频繁 ERROR | 东财长期补不齐时 **exit 2 是设计**；看 `weather_retry` 日录里 `still_gap` / `gaps=`，不是 cron 坏了 |
| 14:30 已扫且板块 stale | 文档建议网好或缺口消除后 **手动** `tea seed-plan --force`（或按 SOP 补跑） |
| `ops_summary.only_on_issues` | 全绿日可能不发运维信；与 DEFAULTS 不一致时在 **本机/NAS config** 改，非改闸 |
| NAS 09:00 前在线 | 覆盖 14:30–15:10 + weather-retry 窗口（见 `project-state`） |

---

## 4. P2 · 可选代码增强（未做，后续 AI 需先 Plan 再改）

用户曾讨论、**未实现**；若要做，走 4 步闭环（Sync → Plan → 用户确认 → Code & Doc → Log）：

1. **weather-retry 对 `error.log` 限频**  
   - 仍 exit 2、仍写 `logs/daily/weather_retry/`；  
   - `error.log` 例如 30 分钟最多 1 条，避免日终邮件永远「需关注」。  
   - 改：`ops/weather-retry-cron.sh` + [`11-weather-retry-scheduler.md`](11-weather-retry-scheduler.md) + selftest/ops 断言若有。

2. **运维摘要 `ops_summary`**  
   - 单独一行：`weather-retry：今日 N 次仍缺口`；  
   - 与 seed/review **失败** 分轨计 warn。

3. **`project-state` 日期**  
   - 当前仍 2026-11-03；NAS 验收后可更新至验收日，并删或归档本 handoff 的「未完成」节。

---

## 5. 后续 AI 跟进指令（复制即用）

```text
请读 docs/project-state.md 与 docs/ops/12-handoff-2026-11-05-nas-sync.md。
确认 NAS 是否已完成 §2 验收勾选；若未完成，只输出 NAS 操作步骤，不改策略闸。
若用户要求做 §4 限频/摘要增强，先输出 Plan 等确认，再改代码并更新 11-weather-retry、CHANGELOG、INDEX、project-state。
```

---

## 6. 验收记录（人工 / AI 填）

| 步骤 | 完成 | 备注 |
|---|---|---|
| NAS `git pull` 或 rsync | ☐ | commit: ______ |
| `hasattr(Market(), 'get_sector_fund_flow_top')` True | ☐ | |
| `pip install -e .`（若曾用旧包） | ☐ | |
| `python3 -m tea.selftest` 全绿 | ☐ | |
| `weather --refresh` 无 fund_flow AttributeError | ☐ | |
| 下一交易日 error.log 含 `[weather-retry-cron]` 正文 | ☐ | |

**验收人 / 日期**：________________

---

## 7. 相关文档

- 天气重试：[`11-weather-retry-scheduler.md`](11-weather-retry-scheduler.md)  
- 种子调度：[`05-seed-plan-scheduler.md`](05-seed-plan-scheduler.md)  
- 日 SOP：[`03-operator-daily-sop.md`](03-operator-daily-sop.md)  
- 变更明细：`docs/CHANGELOG.md` · `[Unreleased]` 运维 / 道 · 市场天气
