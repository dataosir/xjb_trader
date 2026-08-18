# 选股策略与指标调整 · 2026-08-18

> 本次调整的结论先行：**可以调整，而且必须调整。** 复盘 08-08 ~ 08-18 共 30+ 次
> 种子扫描，引擎**从未产出过 1 只「可买」**（裁决全部 `PENDING`/`EMPTY`），
> `capital_state` 显示 0 持仓、0 交易历史——即 9 个交易日里系统处于「永远不买」。
> 根因不是市场差，而是三处过滤器互相锁死，存在数学上的硬死锁。

本文档是本次改动的完整留痕：背景 → 证据 → 根因 → 改动清单 → 留痕设计 → 验证 →
后续调整指引 → 回滚方法。摘要已同步进 `docs/CHANGELOG.md` 的 `[Unreleased]`。

---

## 1. 数据证据（改动前的事实盘点）

| 维度 | 实测值 | 说明 |
| --- | --- | --- |
| 扫描区间 | 2026-08-08 ~ 08-18（9 个交易日，30+ 次扫描） | `reports/SEED_*.md`、`data/accumulator.jsonl` |
| 可买标的 | **0 只** | 全部 `PENDING`（仅观察轨）或 `EMPTY` |
| 实盘交易 | **0 笔** | `capital_state.json` 持仓为空，`trades.json` 不存在 |
| 跟涨样本 | 84 条记录，去重后仅 **32** 个 (date,code)，`result` 全 `null` | T+1 回填从未跑过，跟涨胜率零样本 |
| 观察池 odds | 12 只全部 **2.08~2.18**，无一 ≥3 | 见 `data/watch_pool.json` |
| 候选分时位置 | 28 只里 18 只 ≥0.85（64%）、20 只被分时否决杀掉（71%） | `data/scan_details_*.json` |

**淘汰原因分布**（`data/seed_trace.jsonl`，8103 条）：
- 第 2 步「三档涨幅窗」：`涨幅不在窗口` 4394 条（占 65%，多档位重复 trace 致虚高）；
- 第 3 步 VETO：软否决 54 条、硬否决 19 条；
- 第 4 步预审：`差 1 分` 32 条、`近失` 29 条。

**候选明细实测**（28 只过第 2 步的候选）：
- 裁决分布：软否决 13、硬否决 7、观察轨 3、近失 5；
- 软/硬否决几乎全部来自 `分时高位/封顶`（20/28），其次 `MA20 乖离过热`（4）。

---

## 2. 三个系统性瓶颈（根因）

### 瓶颈 1（最根本）：R:R ≥ 3 在数学上不可达

- ATR 止损**硬顶 6%**（`atr_sl_hard_max_pct`）+ 止盈上限 15%（`atr_tp_cap_pct`）
  + 双边滑点 0.5% → **最大盈亏比 ≈ 2.08~2.18**。
- 反推：TP=15% 时，要达到 R:R≥3，止损必须 ≤3.85%，即 ATR% ≤2.57%。
- 但涨幅窗（3~7.5%）+ 龙头身份 + 强势板块选出的**必然是 ATR% 4~9% 的高波动票**，
  两个约束自相矛盾。
- 连锁后果：第 6 维「止损结构」要求 `odds≥3`，恒为 0 分 → 有效满分 9→8，门槛 6 更难到。

### 瓶颈 2：分时高位/封顶否决在收盘扫描时系统性误伤

- `分时位置 = (现价-最低)/(最高-最低)`。盘后扫描时现价≈当日最高 → 位置 0.9~1.0。
- 阈值：龙头 0.85、封顶 0.95。强势股「收在最高」是常态而非追高。
- 该否决本为「盘中防追高」，但 T+1 买入在 14:00–14:45 盘中会重新评估，收盘时的
  分时位置本不该是硬门槛。

### 瓶颈 3：MA20 乖离 20% 阈值偏严 + 共振门槛偏高（证据混合）

- 被「乖离>20%」软否决的 4 只，实测 T+1 +2.38%、T+3 +4.87%、T+5 +3.76%
  （成都先导 T+3 +9.57%、博腾 +3.28%）→ 龙头乖离 20% 偏严，误杀继续上涨的龙头。
- 但「差 1 分」进观察轨的票 T+5 均值 -6.87% → 门槛边际上不算错，故本次**不动
  `pass_threshold`**。

---

## 3. 改动清单

### 3.1 配置默认值（`tea/config/config_store.py` 的 `DEFAULTS`）

| 键 | 旧值 | 新值 | 理由 |
| --- | --- | --- | --- |
| `strategy.min_odds` | 3 | **2** | 解开 R:R 死锁（瓶颈 1） |
| `scoring.sl_struct_min_odds` | 3.0 | **2.0** | 同步，修复「止损结构」维度恒 0 |
| `strategy.veto_bias_leader_pct` | 20.0 | **25.0** | 龙头乖离阈值放宽（瓶颈 3） |
| `expectancy.insufficient_pass_bump` | 1 | **0** | 零样本时不再无依据抬门槛 |
| `veto.skip_intraday_check_off_session` | （新增） | **true** | 分时否决仅盘中生效（瓶颈 2） |

> ⚠️ `save()` 落盘的是**全量配置**，DEFAULTS 不会覆盖既有 `tea_config.json`。
> 本次已同步修改 `tea_config.json`，但新机器/重装需重新确认这几个键。

### 3.2 代码改动

| 文件 | 改动 | 目的 |
| --- | --- | --- |
| `tea/screening/veto.py` | `check()` 新增 `in_session` 参数；盘前/午间/盘后跳过分时否决并留痕；`format_veto()` 显示跳过提示 | 瓶颈 2 |
| `tea/screening/preflight.py` | `evaluate()` 用 `Timing.in_session()` 判定会话并传入 veto；返回 `in_session`；`compute_levels()` 返回 `min_odds` | 瓶颈 2 + 留痕 |
| `tea/analysis/followthrough.py` | `record_seed()` 按 (date, code) 去重，返回 `{"added","skipped"}` | 修复样本重复 |
| `tea/runtime/runner.py` | 适配 `record_seed` 新返回值，提示去重数量 | 修复样本重复 |
| `tea/portfolio/accumulator.py` | 新增 `KIND_PARAM` + `record_param()` | 参数变更留痕 |
| `tea/runtime/cli.py` | `config set` 时调用 `record_param()` | 参数变更留痕 |
| `tea/selftest.py` | 同步 R:R 断言与龙头乖离断言到新规格 | 规格同步 |

### 3.3 未改动（刻意保留）

- `strategy.pass_threshold = 6`：共振门槛证据混合，暂不动。
- `atr_sl_hard_max_pct = 6` / `atr_tp_cap_pct = 15`：不改结构，只降 `min_odds`，
  避免把止盈目标推到不切实际的 22%+。
- `veto_bias_normal_pct = 15`：普通股乖离阈值证据不足，未动。

---

## 4. 关键节点日志与留痕设计

> 目标：**每一次「为什么被拒 / 被放行 / 谁改了什么」都能从落盘文件里回溯**，
> 后续调参不用再靠猜。

| 留痕点 | 载体 | 字段 |
| --- | --- | --- |
| R:R 判定门槛 | `scan_details_*.json`、观察池快照 | `levels.min_odds` |
| 分时否决是否跳过 | `scan_details_*.json`、观察池快照 | `veto.intraday_skipped` / `veto.intraday_note` |
| 评估时的会话状态 | `scan_details_*.json`、观察池快照 | `in_session` |
| 参数变更 | `data/accumulator.jsonl` | `kind=param_change`，`key`/`old`/`new` |
| 跟涨样本去重 | 控制台提示 + 返回值 | `{"added","skipped"}` |

---

## 5. 验证结果

`python3 -m tea selftest` → **400/403 通过**。

- 本次改动的 2 个相关断言（R:R≥min_odds、龙头乖离阈值）已同步到新规格并通过；
- 剩余 3 个失败为**改动前已存在**、与本次无关：
  1. README「五家域名清单」文案对齐；
  2. README「新浪 Referer 硬要求」文案对齐；
  3. `packaging/tea.spec` 的 `hiddenimports` 缺 `tea.reporting.retrospective`。

功能冒烟（隔离沙箱）已验证：
- `min_odds=2`、`sl_struct_min_odds=2.0`、`veto_bias_leader_pct=25.0`、
  `insufficient_pass_bump=0`、`skip_intraday_check_off_session=true` 正确加载；
- `veto.check` 盘中 `intraday=0.95` → 硬否决，盘后同值 → 跳过并留痕；
- `record_seed` 去重：首条落盘、重复跳过、新代码正常落盘。

---

## 6. 后续调整指引

1. **先回填 T+1**：跑 `tea review`（`close_review` → `followthrough.update_results`）
   把 `seed_records.jsonl` 里 32 个 (date,code) 的 `next_chg`/`result` 补上，跟涨胜率
   模块才有数据可用。
2. **看 `accumulator.jsonl` 的 `param_change`**：后续每次 `config set` 都会留痕，
   复盘「何时动了哪个阈值、改前是多少」直接查这里。
3. **看 `veto.intraday_skipped` / `in_session`**：确认盘后扫描是否按预期跳过分时否决。
4. **样本 <30 笔前不要动胜率类参数**：当前 T+5 只有 9 个样本，任何胜率结论都不稳。
5. 建议再空跑 2 周、累计 ≥30 条 T+1 样本后，再评估是否恢复 `pass_threshold`、
   `insufficient_pass_bump` 或收窄 `min_odds`。

---

## 7. 回滚方法

单键回滚（会写入 `param_change` 留痕）：

```bash
tea config set strategy.min_odds 3
tea config set scoring.sl_struct_min_odds 3.0
tea config set strategy.veto_bias_leader_pct 20.0
tea config set expectancy.insufficient_pass_bump 1
tea config set veto.skip_intraday_check_off_session false
```

代码级回滚：`git revert` 本次提交即可；`record_seed` 返回值已从 `int` 改为
`dict`，回滚时需连同 `tea/runtime/runner.py` 一并还原。

---

> 风险提示：本引擎仅用于个人学习与交易纪律研究，不构成投资建议。参数放宽只是
> 让「可买」信号能够产生，不代表必然盈利。
