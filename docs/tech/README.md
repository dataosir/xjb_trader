# 技术层（tech）索引

> 全库清单见 [`../INDEX.md`](../INDEX.md)；入口与闭环见 [`../README.md`](../README.md)。

| 文档 | 职责 |
|---|---|
| [`../../RULES.md`](../../RULES.md) | **实现铁律权威源**（依赖 / KISS / 禁空 catch / 文档同步）；本目录 `RULES.md` 仅为指针 |
| [`00-engineering-standards.md`](00-engineering-standards.md) | 公式↔断言、风格、分层、颜色、禁止事项；文首摘要引用根 `RULES.md` |
| [`01-architecture.md`](01-architecture.md) | 分层、数据流、包↔PRD 映射 |
| [`02-api-specs.md`](02-api-specs.md) | CLI / 模块调用契约 |
| [`03-db-schema.md`](03-db-schema.md) | JSON/JSONL 持久化结构 |

改代码触及接口或落盘字段时，同迭代更新对应章节，并写 [`../CHANGELOG.md`](../CHANGELOG.md)。
