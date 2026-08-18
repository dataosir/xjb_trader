# 未提交代码逻辑审查报告 · 2026-08-18

> 审查范围：工作区未提交改动（`git diff`，9 个文件 + 1 个新文档）。
> 审查方法：逐 diff 块静态走查 + 全量调用方/引用点检索 + 动态自测（400/403）。
> 结论：**未发现崩溃级 / 数据破坏级 Bug**；发现 **1 个中等一致性缺口** 与
> **若干低风险观察点**，均不阻塞本次提交，但建议后续修复。

> **跟进（同日）**：P1 两项已修复 —— ① `phase1.py` 已传 `in_session=s.tm.in_session()`；
> ② 历史 `seed_records.jsonl` 已用 `followthrough.dedupe_records()` 去重（84 → 32）。
> 另新增运行日志与种子明细数据增强，见 `docs/CHANGELOG.md` 的「数据积累与运行日志」。

---

## 一、总体结论

| 严重度 | 数量 | 说明 |
| --- | --- | --- |
| 崩溃/数据破坏 | 0 | — |
| 中等（逻辑不一致） | 1 | `phase1.py` 未同步 `in_session` |
| 低（缺口/健壮性） | 5 | 历史重复未清理、全量读文件、format_veto 直接取键、留痕覆盖不全、兜底默认未同步 |

动态验证：`python3 -m tea selftest` → 400/403，本次改动相关的 2 个断言已通过；
剩余 3 个失败为改动前已存在（README 文案 ×2 + packaging spec ×1），与本次无关。

---

## 二、中等问题（建议修复）

### 1. `phase1.py` 未同步 `in_session` 判定 —— 一致性缺口

**位置**：`tea/phases/phase1.py:69`

```python
s.veto = veto_mod.check(s.quote, s.ind, s.identity, s.intraday, cfg)  # 未传 in_session
```

**现象**：本次把「分时否决仅盘中生效」只接到了 `preflight.evaluate`（种子扫描 /
`eval` / 观察池共用路径），但 `run` 命令的 Phase1 交互流仍无条件执行分时否决。

**影响**：
- 默认 `run` 走 `check_session_start(require_window=True)`，被买入窗口门禁限制在
  盘中（14:00–14:45），此时分时否决本就该生效 → 默认路径无影响；
- 但 `tea run --any-time <code>`（`cli.py:73` 传 `require_window=not args.any_time`）
  盘后执行时，Phase1 会基于失真的收盘分时位置误判「分时封顶/高位」，与 `eval` /
  种子扫描行为不一致（同样盘后，`eval` 跳过、`run --any-time` 不跳过）。

**修复建议**（Session 已有 `s.tm`，一行即可）：

```python
s.veto = veto_mod.check(s.quote, s.ind, s.identity, s.intraday, cfg,
                        in_session=s.tm.in_session())
```

---

## 三、低风险观察点

### 2. `record_seed` 去重只对新写入生效，历史重复未清理

`seed_records.jsonl` 现状：84 条记录 → 去重后仅 32 个 (date, code)，52 条重复
（08-10 的 300363/603108 各重复 8 次）。去重逻辑只防「未来重复」，已落盘的历史
重复仍在文件里，`update_results` / `aggregate` 跑起来仍会被历史重复污染跟涨胜率。

**建议**：一次性对 `data/seed_records.jsonl` 按 (date, code) 去重（保留首条）。

### 3. `record_seed` 每次调用全量读文件

`existing = {… for r in load_records(cfg) …}` 每次扫描都读一遍整个文件。文件小时
无感，长期累积是 O(n)/次。可接受，但可改为按 `date` 增量读（只读当日记录做去重）。

### 4. `format_veto` 新增分支对 `rejected`/`watchable` 直接取键

`format_veto` 当 `items` 为空但 `intraday_skipped=True` 时进入新分支，随后
`result["rejected"]` / `result["watchable"]` 是直接下标访问。实际调用方都传
`check()` 的完整返回（含这两个键），暂无触发；但属脆弱点，建议改 `.get(..., False)`。

### 5. `record_param` 留痕未覆盖 `config reset` / `setup` 向导

参数变更留痕只接在 `config set`。通过 `tea config reset` 或重跑 `tea setup` 改配置
不会写 `param_change`，留痕覆盖不完整。属低优先级补全。

### 6. `min_odds` 兜底默认值未同步（纯文档一致性）

`gates.py:234`、`plan.py:235`、`phase2.py:30`、`preflight.py:72/324/329/356`、
`report.py:266`、`seed_report.py:214` 里 `cfg.s("min_odds", 3)` 的兜底默认仍是 3。
因 DEFAULTS 已含 `min_odds=2`，该兜底永不生效，无功能影响；但建议统一为 2 或去掉兜底。

---

## 四、已排查并排除的问题（无风险）

- 循环导入：`preflight → tea.core.timing` 无环。
- `veto.check` 新增 `in_session=None → True` 向后兼容，自测 14 处直接调用不受影响。
- `min_odds=2` 数学正确性：反推止盈 ≈14.44% ≤ 15% 上限，odds ≥2，`odds_ok=True`。
- `record_seed` 返回 int→dict 的唯一调用方 `runner.py` 已同步。
- 新增字段 `in_session`/`min_odds`/`intraday_skipped` 下游均 `.get()` 消费，无害。
- `has_veto` 语义：分时被跳过时 `items` 为空、`has_veto=False`，跳过≠否决。
- 自测 400/403，本次相关断言通过。

---

## 五、第二轮审查（同日，覆盖「数据积累与运行日志」改动）

结论：**仍未发现崩溃级 / 数据破坏级 Bug**。新增的运行日志、`phase1.py` 修复、
`dedupe_records`、`candidate_row` 数据增强均已逐行走查 + 自测（401/403）+ 功能冒烟。

已确认正确（无风险）：
- `tea/core/logger.py`：`_initialized` 幂等、失败静默降级、`TimedRotatingFileHandler`
  按日切割、子 logger（`tea.scan`/`tea.veto`/`tea.config`）经父 logger 正常落盘。
- `cli.py` 的 `init_logging` 接线：`getattr(logging, "INFO")` 级别映射正确。
- `phase1.py`：`s.tm.in_session()` 与 `preflight.evaluate` 行为一致。
- `candidate_row` 增强字段：`ev` 缺省为 `{}` 时（CAND_ERROR/SKIPPED）所有新字段回落
  None/空，不抛异常；`finalize_candidates` 只回填 verdict 不覆盖新字段；新增字段均
  JSON 可序列化。
- `record_seed`/`dedupe_records`：去重键 (date, code) 正确；`dedupe_records` 原子写，
  实测 84 → 32。

遗留低风险观察（不阻塞，可后续处理）：
1. **启动日志行的 `cfg.data_dir()`/`cfg.logs_dir()` 在 try 之外**：若日志目录创建失败，
   `init_logging` 会静默降级，但紧接着的启动日志行会因再次调用 `cfg.logs_dir()` 抛异常
   打断 `main()`。属边缘场景（数据目录同样假设可写），建议把启动日志包进 try 或改用
   不创建目录的取路径方式。
2. **`format_veto` 直接取 `result["rejected"]`/`result["watchable"]`**（上轮已提，未改）。
3. **`record_seed` 每次全量读文件 O(n)**（上轮已提，未改）。
4. **`dedupe_records` 未暴露 CLI 子命令**：只能脚本调用，未来可加 `tea` 子命令。
5. **文档陈旧**：README「410 参数」与实际 418 不符（本次新增 logs 段 + veto.skip 等）。

## 六、建议动作

| 优先级 | 动作 |
| --- | --- |
| P1 | 修复 `phase1.py` 传 `in_session=s.tm.in_session()`（1 行） |
| P1 | 历史 `seed_records.jsonl` 按 (date,code) 去重迁移 |
| P2 | `format_veto` 的 `rejected`/`watchable` 改 `.get()` |
| P2 | `record_param` 补覆盖 `config reset` / `setup` |
| P3 | `min_odds` 兜底默认统一为 2；`record_seed` 按日增量读 |
