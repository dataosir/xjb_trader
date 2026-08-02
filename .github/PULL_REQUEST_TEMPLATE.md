## 这个 PR 改了什么

<!-- 一两句话说清动机。如果关联 Issue，写 Closes #123 -->

## 涉及哪一层

- [ ] 道 · 情绪与天气（sentiment / timing）
- [ ] 法 · 门禁与计划（gates / plan / portfolio）
- [ ] 术 · 评分与否决（identity / preflight / veto）
- [ ] 扫描与观察池（screener / watch_pool）
- [ ] 行情数据与防封（market）
- [ ] CLI / 报告输出 / 文档

## 是否改动了公式、阈值或纪律规则

- [ ] **没有改动**，纯重构 / 修 bug / 改文档
- [ ] **有改动**，且已同步更新 `selftest.py` 里对应的断言

> 如果勾了"有改动"，请在下面写清：原值 → 新值，以及为什么。
> 断言必须是**按新规格独立重算**的结果，不能直接填引擎当前的输出（见仓库根目录 `CONTRIBUTING.md`）。

改动明细：

<!-- 例：identity 板块内后 50% 扣分 -25 → -30，理由：… -->

## 提交前自查

- [ ] `python -m tea selftest` 全绿
- [ ] `ruff check .` 无告警
- [ ] `python -m compileall -q tea` 通过
- [ ] 新增配置参数已写进 `config_store.py` 的默认值表
- [ ] 影响决策的改动已在 README 对应小节补充说明
- [ ] 影响使用者的改动已记入 `CHANGELOG.md`

## 自测输出

<!-- 贴 `python -m tea selftest` 最后的统计行，例如：97/97 通过 -->

```text

```
