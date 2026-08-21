# 参与贡献

先说最重要的一条。

## 一条硬规矩：公式与断言同步

`tea/selftest.py` 里的每一条断言，都是**按规格文档独立重算一遍**再和引擎输出比对的。这是本项目唯一的正确性防线。

所以：

> **改动任何公式、阈值、评分维度或门禁规则，必须同步更新 `selftest.py` 中对应的断言，并让 `tea selftest` 保持全绿。**

不要为了让测试通过而把断言改成"引擎当前输出的值"——那样断言就退化成快照，失去了发现分叉的能力。正确做法是：先在断言里按新规格重算，再改实现去满足它。

## 提交前自查

```bash
python -m tea selftest      # 必须全绿
ruff check .                # 必须无告警
python -m compileall -q tea
```

三条全过再提 PR。CI 会在 Linux / macOS / Windows 与 Python 3.8 至 3.13 上重跑一遍。

## 代码风格

项目已有一套内部一致的写法，请跟随而不是改造：

- **取参缩写**：模块内统一用 `c = lambda k, d=None: cfg.get(f"xxx.{k}", d)`，这是有意保留的（ruff 已忽略 E731）。
- **所有可调数值走配置**：不要硬编码阈值。新增参数写进 `config_store.py` 的默认值表，用点号路径读取。
- **所有文件写入走 `utils`**：必须是原子写（先 `.tmp` 再 `rename`），不要直接 `open(...).write()`。
- **注释与文案用中文**，与现有代码保持一致；注释解释"为什么"而不是"做了什么"。
- **CLI 不放交易逻辑**：`cli.py` 只做参数解析与展示，逻辑一律下沉到 `runner.py` 及各模块，保证纪律规则只有一处定义。
- **子包从顶层引入**：写 `from .data import Market`，不要写 `from .data.market import Market`。子包的 `__init__.py` 是对外契约，只做再导出、不放实现。
- 行宽 110。

## 颜色方案（终端高亮）

所有控制台高亮必须走 `tea/core/utils.py` 的 `hl()` / `sign_color()` 与语义色常量，**禁止在业务代码里散落裸颜色名**（`"red"` / `"green"` 之类）。颜色由**含义**决定，不由出现位置决定：

| 语义常量 | 颜色 | 含义 |
| --- | --- | --- |
| `COLOR_PROFIT` | 绿 | 盈利 / 上涨 / 通过 |
| `COLOR_LOSS` | 红 | 亏损 / 下跌 / 拒绝 |
| `COLOR_WARN` | 黄 | 警告 / 注意 / 待定 / 数据缺失 |
| `COLOR_SEED` | 品红 | **种子选中 / 可买**（与盈亏色区分，单独标记） |
| `COLOR_INFO` | 青 | 信息 / 强调 / 候选明细 / 复盘 |
| `COLOR_NEUTRAL` | 白 | 中性 / 零值 |

三条硬规则：

1. 正负号类数值（盈亏、涨跌幅）一律用 `sign_color(v)`：`>0` 绿、`<0` 红、`=0` 白、`None` 黄。
2. 「种子选出来的票」——种子报告的可买桶、持仓对照里命中种子的标记——必须用 `COLOR_SEED`（品红），**不得复用**盈亏的绿/红，避免「命中种子」和「赚钱/亏钱」在视觉上混淆。
3. 新增语义色时先在这里登记，再到 `utils.py` 加常量，不要在调用处临时造色。

```python
utils.hl(utils.money(pnl), utils.sign_color(pnl))   # 盈亏按正负标色
utils.hl("可买", utils.COLOR_SEED)                    # 种子选中标品红
```

自测 `check_colors` 锁住 `sign_color` 的语义与 `COLOR_SEED` 的唯一性（不与盈亏/警告色重复）。

## 新增模块放哪里

`tea/` 下是九个分层子包，依赖方向严格单向向下：

```
runtime → phases → reporting / screening / portfolio → analysis → data → core / config
```

每个子包的 `__init__.py` 首行都标了它所在的层（如 `"""Layer 2 · 选股：…"""`），往里加东西前先对照层号：下层不许 import 上层，横向的同层包（`screening` ↔ `portfolio`）也别互相缠。

最常见的几类改动该落在哪：

- **新增数据源** → `tea/data/providers/`：继承 `base.IDataProvider`，只覆盖这家真支持的能力（不支持的静默跳过），再挂进 `ChainedProvider` 的降级链。
- **新增筛选门禁 / 一票否决规则** → `tea/screening/gates.py` / `tea/screening/veto.py`；共振评分与止损止盈在 `preflight.py`，种子四步流在 `screener.py`。
- **新增分析指标**（情绪、身份、期望值、跟涨）→ `tea/analysis/`：只做判断与打分，不碰落盘状态。
- **纯计算的行情指标**（MA / ATR / 分时位置）→ `tea/data/indicators.py`：必须是纯函数，不联网、不读配置。
- **跨日状态**（资金持仓、次日计划、观察池、流水、当日累积）→ `tea/portfolio/`。
- **新报告 / 新导出** → `tea/reporting/`：只做呈现，不放任何交易判断。
- **新命令 / 新菜单项** → `tea/runtime/cli.py` 只做参数解析与展示，逻辑下沉到 `runner.py` 或对应子包。
- **通用工具 / 时段判断 / 路径解析** → `tea/core/`（仅标准库）；**新参数默认值与向导问项** → `tea/config/`。

两条包内约定容易踩：

- `tea/data/` 内部依赖同样单向向下（`indicators` → `cache` → `fetcher` → `market`）。`fetcher` 只管取回 JSON、不懂行情语义，领域字段的翻译全部在 `market`。制造回边的改动不要提。
- `tea/phases/` 的阶段之间不互相调用，只读写 `session.Session`；返回值一律用 `results.OK / REJECT / ABORT`，不要在阶段里重新定义或硬编码字面量；要输入必须通过 `prompt.IO`，不直接调 `input()` / `print()`，否则 runner 的无人值守模式和自测会卡住。

新建**子包**时别忘了把包名加进 `pyproject.toml` 的 `[tool.setuptools] packages` 列表——那是一份显式清单，漏了不报错，只是构出的 wheel 里默默少了整个包。

动完收尾三条：`python -m tea selftest` 全绿、`ruff check .` 无告警、`git status` 干净（`data/`、`reports/`、`__pycache__/` 下的运行产物不要跟着提交）。

## 加新参数的顺序

1. 在 `config_store.py` 的 `DEFAULTS` 里加默认值（带注释说明含义与取值范围）。
2. 在用到的模块里通过 `cfg.get("段.键", 默认)` 或 `cfg.s("键", 默认)` 读取。
3. 在 `selftest.py` 里补一条断言，覆盖它生效与不生效两种情况。
4. 如果它影响决策，在 README 对应小节补一句说明。

## 提 Issue

- **Bug**：请附上 `tea --version` 的输出、复现步骤，以及 `tea selftest` 的结果（全绿还是有失败项）。
- **数据源问题**：东财接口可能改字段或限流。请贴出报错信息和你所在的网络环境（是否用代理）。
- **策略讨论**：欢迎，但请说清"改这条规则能避免哪一类亏损"，而不只是"我觉得这样更好"。纪律的每一条都应该有它想防住的具体错误。

## 不接受的改动

- 对接券商交易接口、实现自动下单。本项目刻意只做准入判断，下单必须由人手动完成。
- 去掉计划绑定、买入时间窗、单日限额这类纪律约束，或给它们加"一键跳过"。这些约束就是项目的全部意义。
- 引入重量级第三方依赖。运行时依赖必须保持为零（`requests` 是可选加速项，且必须保留 `urllib` 回退）。
