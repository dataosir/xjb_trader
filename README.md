# XJB_TRADE (TEA) — A 股交易准入引擎

[![CI](https://github.com/dataosir/xjb_trader/actions/workflows/ci.yml/badge.svg)](https://github.com/dataosir/xjb_trader/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![自测](https://img.shields.io/badge/selftest-352%2F352-brightgreen.svg)](#离线自测)

> 计划你的交易，交易你的计划。宁可空仓，不强行凑票。

---

## 项目简介

这不是选股软件，是**准入引擎**。它的默认答案是"不买"，只有当道（市场天气）、法（纪律门禁）、术（个股共振）三层同时点头，才会放行一次开仓。

引擎不预测涨跌，只回答一个问题：**今天这一笔，够不够资格下手？**

### 三层框架

| 层 | 职责 | 模块 |
|---|---|---|
| **道** | 市场天气：情绪分 → 周期 → 交易姿态 → 仓位乘数 | `sentiment.py` `timing.py` |
| **法** | 纪律门禁：计划绑定、时间窗、单日限额、连亏冷却、仓位与期望值 | `gates.py` `plan.py` `portfolio.py` `expectancy.py` |
| **术** | 个股评分：身份判定、9 分共振、ATR 止损止盈、VETO 一票否决 | `identity.py` `preflight.py` `veto.py` `screener.py` |

三层是**串联否决**关系，不是加权投票 —— 任一层否决，整笔作废。

### 核心公式

- **情绪分**（基准 50 / 上限 100）：涨跌比、最高连板、上证 MA20 位置与涨跌幅、热点板块数、前 5 板块均涨 —— 六项加减得分。
- **9 分共振**：板块强度、大盘趋势、消息面、市值区间、量价结构、止损结构 —— 任一项为 0 不减分，但总分需过动态门槛。
- **ATR 止损止盈**：止损 clamp(ATR%×1.5, 2%, 8%) 硬顶 6%，含滑点 R:R ≥ 3。
- **3/7 灰度仓位**：30% 试错仓 + 突破确认后 70% 确认仓，乘数夹紧 [0.25, 1.0]。

### 每日时间线

| 时刻 | 动作 | 命令 |
|---|---|---|
| 13:30 | 观察扫描，看盘面强弱 | `weather` |
| **14:30** | **种子四步流 → 写次日计划** | `seed-plan` |
| 14:35 | 计划复核（任一要素变动 → 整单作废） | `plan-check` |
| 收盘后 | 跟涨回填 + 观察池复核 + 当日累积 | `review` |
| T+1 14:00–14:45 | **唯一买入窗口，执行计划** | `run <代码>` |
| 每周五 | 周复盘：纪律自查 + 归因 | `weekly` |

### 纪律硬约束

1. **计划绑定** —— 今日无有效计划，或代码不在计划内 → 禁止评估、禁止买入。
2. **买入窗口** —— 非 14:00–14:45 → 禁止新开。
3. **单日限额** —— 每日最多新开 1 笔、评估 5 次、同一标的复筛 2 次。
4. **上证 MA20 下方** → 禁止评估新开。
5. **姿态空仓 / 高潮易分歧** → 禁止买入。
6. **连亏冷却** —— 连续亏损达阈值 → 强制冷却期。
7. **VETO 硬否决 / 共振分不足 / R:R <3** → 禁止买入。

---

## 快速开始

### 系统要求

零第三方依赖，只需 Python 3.8+（仅用标准库 `urllib` / `json` / `concurrent.futures`）。已在 Linux / macOS / Windows 与 Python 3.8 至 3.13 上通过 CI。

### 方式一：一键启动脚本（推荐）

```bash
git clone https://github.com/dataosir/xjb_trader.git
cd xjb_trader

./run.sh                    # 进入数字菜单
./run.sh selftest           # 离线自测
./run.sh eval 600519        # 子命令原样透传
```

Windows 用 `run.bat`，可直接双击：

```bat
run.bat
run.bat selftest
```

脚本只做三件事：找到 3.8+ 的解释器、把数据目录锁在仓库根目录、把参数原样交给引擎。不装包，不建虚拟环境。

### 方式二：安装为命令

```bash
git clone https://github.com/dataosir/xjb_trader.git
cd xjb_trader
pip install .

tea                         # 进入数字菜单
tea --help                  # 查看全部子命令
```

想让抓取更稳（连接复用）可装上可选的 `requests`：

```bash
pip install ".[fast]"
```

### 方式三：直接跑源码

```bash
git clone https://github.com/dataosir/xjb_trader.git
cd xjb_trader

python3 -m tea              # 进入数字菜单
python3 -m tea --help       # 查看全部子命令
python3 -m tea selftest     # 离线自测
```

### 打包发布

在**自己电脑上调试完**，一键打成可分发的桌面程序：

```bash
python3 packaging/build.py
```

脚本按 `sys.platform` 自动选形态：macOS 出 `.app` bundle，Windows 出 `.exe` 单文件。PyInstaller 不能交叉编译，要哪个平台的就到哪个平台上跑。

---

## 配置文件说明

### tea_config.json

引擎所有可调参数集中在一个 JSON 配置文件中，包含 **410 个参数**，覆盖策略阈值、数据源、网络超时、风控等全部维度。

配置文件在**首次保存配置时自动落盘**，之后每次启动读取。它已写入 `.gitignore`（含本金、代理池等私有信息），**不要提交到公开仓库**。

### 初始配置

**第一次进菜单会自动弹配置向导**，把因人而异的那几项问一遍（总资金、选股与风控阈值、板块交易权限、数据源降级链），其余参数留默认。每项都可直接回车取默认。完成后写 `meta.initialized`，不再重弹。

```bash
tea setup             # 重跑配置向导（当前值作为默认值）
tea setup --defaults  # 不提问，直接采用推荐默认值
```

### 手动管理配置

```bash
tea config count      # 参数总数与配置文件路径
tea config list       # 全部参数（点号路径）
tea config set strategy.pass_threshold 7
```

资金也可单独改：

```bash
tea capital 100000
tea status            # 确认门禁计数 / 计划 / 持仓
```

### 数据目录

数据落 `data/`，报告落 `reports/`。数据目录可用环境变量改写，便于多账户或沙箱：

```bash
TEA_HOME=~/my_trade tea
```

### 输出文件

所有写入都是原子的（先写 `.tmp` 再 `rename`）：

| 文件 | 内容 |
|---|---|
| `data/trade_plan.json` | 交易计划（纪律锚点） |
| `data/daily_state.json` | 单日门禁计数 |
| `data/capital_state.json` | 资金与持仓 |
| `data/watch_pool.json` | 观察池 |
| `data/trades.json` | 交易流水 |
| `data/seed_trace.jsonl` | 落选追溯（结构化） |
| `data/accumulator.jsonl` | 当日累积事件 |
| `reports/TRADE_CHECK_*.md` | 单标的准入报告 |
| `reports/SEED_*.md` | 种子扫描报告 |

### 多数据源与降级

引擎支持从五家数据源按顺序降级取数。默认全开（东财 → 腾讯 → 新浪 → 网易 → 凤凰），前一家取不到自动问下一家。每家的实现只覆盖它真有的能力，其余静默跳过。

切换数据源组合：

```bash
python3 -m tea config set market.data_sources '["eastmoney","tencent","sina"]'
```

境外环境如需走代理取东财，置 `market.use_env_proxy: true`。

### 网络要求

数据源走公开行情接口，内网 / 防火墙环境需放通以下域名（按五家数据源分列）：

| 数据源 | 需放通的域名 |
| --- | --- |
| 东财 | `push2.eastmoney.com` |
| 腾讯 | `qt.gtimg.cn`、`web.ifzq.gtimg.cn` |
| 新浪 | `hq.sinajs.cn`、`money.finance.sina.com.cn` |
| 网易 | `api.money.126.net` |
| 凤凰 | `api.finance.ifeng.com` |

- 新浪的报价 / K 线接口有 **Referer 硬要求**：请求头不带 `Referer: https://finance.sina.com.cn` 会被直接 403。放通域名时必须同时允许该 Referer，否则新浪这一级始终不可用。
- 若内网只放通了东财，其余四家反而会拖慢降级链（每级都要等超时）。此时建议退回单源：`python3 -m tea config set market.data_sources '["eastmoney"]'`。

> **风险与免责声明**：本项目仅用于个人学习与交易纪律研究，不构成任何投资建议。引擎输出的 `BUY` 只表示符合预设纪律条件，不是盈利保证。行情数据来自第三方公开接口，可能延迟、出错或随时停服。引擎不对接任何券商交易接口，不会自动下单。首次使用建议先用 `eval` 空跑至少两周。

---

## 命令速查

### 决策主线

| 命令 | 说明 |
|---|---|
| `run [代码]` | 单标的准入评估，走完 Phase1→4，通过则登记灰度仓 |
| `eval <代码>` | 只算不买：出完整评分与理由，不落任何仓位 |
| `seed-plan` | 种子扫描四步流 + 写次日计划 + 观察池入池 |
| `plan-check` | 计划复核，任一变动整单作废 |
| `plan` | 查看 / 清空 / 作废交易计划 |
| `review` | 盘后复核：T+1 跟涨回填 + 观察池 + 当日累积 |

### 状态查询

| 命令 | 说明 |
|---|---|
| `weather` | 市场天气：情绪分 / 周期 / 姿态 / 仓位乘数 |
| `status` | 今日状态：门禁计数 / 计划 / 持仓 |
| `gate` | 单日门禁明细 |
| `watch` | 观察池：查看 / 复核 / 纳入 / 剔除 |
| `pos` / `capital` | 持仓与资金 |
| `trades` | 交易流水 |

### 复盘归因

| 命令 | 说明 |
|---|---|
| `stats` | 统计与归因（胜率 / 期望值 / R 分布） |
| `weekly` | 周复盘（纪律自查 + 归因） |
| `accum` | 当日累积 —— 回答"为什么今天没交易" |
| `trace` | 落选追溯 —— 每一步淘汰了谁、因为什么 |
| `followthrough` | 跟涨经验（T+1 胜率样本） |

### 仓位与配置

| 命令 | 说明 |
|---|---|
| `add-confirm <代码>` | 突破确认后补足 70% 确认仓 |
| `close <代码>` | 平仓登记（写流水 + 回收资金） |
| `config list/get/set/reset/count` | 配置读写 |
| `setup [--defaults]` | 配置向导 |
| `selftest` | 离线自测（不联网） |

---

## 离线自测

```bash
python3 -m tea selftest
```

自测**不联网**，用构造行情跑全链路。每一项断言都在测试内**按规格独立重算一遍**再和引擎比对，能抓住"实现和规格分叉"。当前：**352/352 通过**。自测在临时 `TEA_HOME` 沙箱中运行，不会碰真实数据与报告。

---

## 详细文档索引

| 文档 | 说明 |
|---|---|
| [docs/README.md](docs/README.md) | 文档总入口：分层说明 + **4 步迭代闭环** |
| [docs/INDEX.md](docs/INDEX.md) | **全库文件清单**（增删改 docs 必维护） |
| [docs/project-state.md](docs/project-state.md) | 当前全局状态（版本 / 进行中 / 下一步） |
| [docs/prd/README.md](docs/prd/README.md) | 产品需求（PRD）：定位、日常流程、F01–F15 与 backlog |
| [docs/tech/00-engineering-standards.md](docs/tech/00-engineering-standards.md) | 工程规范：公式与断言同步、代码风格、模块归属 |
| [docs/tech/01-architecture.md](docs/tech/01-architecture.md) | 技术架构与数据流 |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 版本变更记录（格式参考 Keep a Changelog） |
| [docs/archive/](docs/archive/) | 历史策略路线图与评审（只读归档） |
| [LICENSE](LICENSE) | MIT 许可证 |
| [.github/ISSUE_TEMPLATE/](.github/ISSUE_TEMPLATE/) | Bug 与规则讨论模板 |
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | CI 流水线（自测矩阵 / lint / 构包） |

### 模块结构

```
tea/
├── __init__.py         包版本号 __version__
├── __main__.py         python -m tea 入口
├── selftest.py         离线自测
├── core/               Layer 0：无依赖公共工具
├── config/             Layer 0：配置层（410 参数 + 首次向导）
├── data/               Layer 1：行情数据层（五源降级 + 防封）
├── analysis/           Layer 1：数学与统计（情绪 / 身份 / 期望值）
├── screening/          Layer 2：筛选与门禁（共振 / VETO / 门禁 / 扫描）
├── portfolio/          Layer 2：持仓与观察闭环
├── reporting/          Layer 3：报告（准入 / 追溯 / 周复盘）
├── phases/             Layer 4：四阶段交互流程
└── runtime/            Layer 5：入口（CLI + runner）
```

分层自 Layer 0 排到 Layer 5，只允许高层依赖低层，禁止反向依赖与跨层回边。

---

本项目以 [MIT 许可证](LICENSE) 开源。你可以自由使用、修改、分发，但请保留版权声明，并自行承担使用风险。

如果这个项目让你少做了一笔冲动交易，它就已经回本了。
