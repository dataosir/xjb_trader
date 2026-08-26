# 02 · 模块调用与接口规范

> 内部模块契约（非 HTTP API）。对外用户面是 CLI（`python -m tea` / `tea`）。  
> 详细命令表见根目录 `README.md`；功能验收见各 Fxx。

---

## 1. CLI 入口

| 入口 | 职责 |
|---|---|
| `tea/__main__.py` | `python -m tea` |
| `tea/runtime/cli.py` | 参数解析与菜单展示 —— **禁止放交易逻辑** |
| `tea/runtime/runner.py` | 命令编排，调用 screening / portfolio / phases |

硬规矩：纪律规则只在业务模块定义一处；CLI 只透传。

---

## 2. 决策主线命令 → 核心调用

| 命令 | Runner / 模块锚点 | 副作用 |
|---|---|---|
| `weather` | sentiment + timing | 只读 |
| `seed-plan` | `Screener` 四步流 → `plan` 写次日计划 → `maybe_auto_backfill(seed)` | 写计划 / 观察池 / 种子记录；可自动轻量回填 |
| `winrate-scan` | `Screener.winrate_scan`（菜单在复盘工具▸） | **只落盘观察，不写计划** |
| `plan-check` | `plan` 复核 | 变动则整单作废 |
| `run` / `eval` | gates → phases Phase1–4；`eval` 不落仓 | `run` 可登记灰度仓 |
| `review` | followthrough 回填 + watch 复核 | 写 seed_records / 报告 |
| （进菜单） | `maybe_auto_backfill(menu)` | 盘后/隔夜窗每天最多 1 次轻量回填 |
| `selftest` | `tea/selftest.py` | 临时 `$TEA_HOME` 沙箱，不碰真实数据 |

数字菜单顶层约 10 项（计划/准入/持仓/复盘/配置为子菜单）；改编号须同步 `check_menu`。

---

## 3. 关键内部契约（摘要）

### 3.1 行情

- `Market` / `ChainedProvider`：按 `market.data_sources` 降级；单源失败静默跳下一家。  
- `tea/data/indicators.py`：**纯函数**，不联网、不读配置。

### 3.2 预审

- `preflight`：身份 + 9 分共振 + ATR 止损止盈；产出分数与否决标签。  
- `veto`：硬否决列表，命中即不可买。  
- `winrate_score`：实证加权分；可买闸门见配置 `strategy.winrate_*`。

### 3.3 种子与闸门

- `Screener.screen_tier`：候选携带 `pick_sector_bk/name/rank`。  
- `_winrate_gate`：突破/过热禁买、胜率分门槛、入选板块一致性等（可配置开关）。  
- 低吸：`lowbuy_sector_pool` —— 阶段 1 **只攒样本、不写计划**。

### 3.4 计划状态机

`trade_plan.json`：`pending → ready_exec / invalid / executed / cleared`（见 `portfolio/plan.py`）。

### 3.5 配置

- 全部阈值经 `config_store` 点号路径；新增参数必须进 `DEFAULTS`。  
- 取参风格：模块内 `c = lambda k, d=None: cfg.get(f"xxx.{k}", d)`（见工程规范）。

---

## 4. 扩展点清单

| 扩展 | 落点 | 接口约定 |
|---|---|---|
| 新数据源 | `tea/data/providers/` | 继承 `IDataProvider`，不支持的能力静默跳过 |
| 新门禁 / VETO | `gates.py` / `veto.py` | 配置化阈值 + selftest 断言 |
| 新分析指标 | `tea/analysis/` | 不写跨日状态文件 |
| 新报告 | `tea/reporting/` | 只呈现，不判断交易 |

---

## 5. 待补全

- 按函数级签名展开（`screen_tier` / `record_seed` / `check_gates` 参数表）——有稳定对外脚本需求时再写。  
- 当前以「命令 + 模块锚点」为准，避免与实现漂移的大段伪代码。
