# 01 · 整体架构

> 技术层：代码怎么分层、数据怎么流。产品边界见 [`../prd/`](../prd/README.md)。  
> 工程硬规矩见 [`00-engineering-standards.md`](00-engineering-standards.md)。

---

## 1. 定位

TEA（XJB_TRADE）是 **A 股交易准入引擎**：道 / 法 / 术串联否决。  
**不**对接券商、**不**自动下单；输出 `BUY` 仅表示纪律条件满足。

版本锚点：`tea.__version__`（当前见 [`../project-state.md`](../project-state.md)）。

---

## 2. 分层依赖（单向向下）

```
runtime (L5)  →  CLI / runner
phases  (L4)  →  Phase1–4 交互准入
reporting (L3)→  报告 / 周报 / 追溯（只呈现）
screening / portfolio (L2) → 筛选门禁 / 计划持仓观察
analysis (L1) → 情绪 / 身份 / 期望值 / 跟涨（不落盘状态）
data     (L1) → 行情源降级链 / 指标纯函数 / 缓存
core / config (L0) → 路径、原子写、日志、410 参数
```

**禁止**：下层 import 上层；同层 `screening` ↔ `portfolio` 互相缠绕。  
子包对外契约：各层 `__init__.py` 再导出，业务侧写 `from tea.data import Market`。

---

## 3. 核心数据流（日循环）

```
行情源(ChainedProvider)
    ↓
weather / seed-plan / winrate-scan
    ↓
Screener 四步流 → Preflight 9分+VETO → _winrate_gate
    ↓
可买 → trade_plan.json     观察 → watch_pool.json
低吸前夕 / 胜率影子 → 只落盘 seed_records（不写计划）
    ↓
T+1 窗口 run → gates + phases → 持仓 / trades
    ↓
review → followthrough 回填 T+n → accumulator / stats
```

串联否决：任一层（道/法/术）否决 → 整笔作废。

---

## 4. 包 ↔ 能力映射（PRD）

| 包 | 主要 PRD |
|---|---|
| `tea/analysis/sentiment.py` `core/timing.py` | F01 |
| `tea/screening/gates.py` | F02 |
| `tea/screening/screener.py` | F03 / F05 / F06 |
| `tea/screening/preflight.py` `veto.py` `analysis/identity.py` | F04 |
| `tea/portfolio/plan.py` | F07 |
| `tea/phases/*` `runtime/runner.py` | F08 |
| `tea/portfolio/portfolio.py` `trades.py` | F09 |
| `tea/portfolio/watch_pool.py` | F10 |
| `tea/analysis/followthrough.py` | F11 |
| `tea/reporting/*` | F12 |
| `tea/config/*` | F13 |
| `tea/data/*` | F14 |
| `tea/selftest.py` | F15 |

---

## 5. 运行时路径

| 优先级 | 基准目录 |
|---|---|
| 1 | `$TEA_HOME` |
| 2 | 打包版 `~/.tea/` |
| 3 | 源码版 `CWD` |

其下再拼 `data/`、`reports/`（见 `tea/core/paths.py`）。配置：`$TEA_CONFIG` 或基准目录下 `tea_config.json`（gitignore，勿提交私密项）。

---

## 6. 维护约定

- 改分层或新增跨层依赖 → **先改本文 + Plan**，再改代码。  
- 改可买口径 / 门禁 / 共振 → 同步对应 `prd/03-features/Fxx` 与 CHANGELOG。
