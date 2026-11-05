# MCP Server（stdio）

> 将 TEA 只读能力暴露为 [Model Context Protocol](https://modelcontextprotocol.io/) Tools，供 Cursor、goose、Claude Desktop 与 **enterprise-kb-py**（双 Server 配置）调用。与 `python -m tea` CLI **并行**：共用 `TEA_HOME` 下的 `data/`、`reports/`。

## 架构

```text
MCP 客户端 ──stdio JSON-RPC──► tea/mcp/server.py ──► runner / 只读 JSON
CLI        ──终端────────────► tea.runtime.cli     ──► 同上
```

- MCP 工具 **内调** `runner` / 读盘，不另起 `python -m tea` 子进程（P0）。
- **未暴露**写操作：`seed-plan`、`run`、`review --update` 等。

## 安装

```bash
cd /path/to/tea
python -m venv .venv && source .venv/bin/activate
pip install -e ".[mcp]"
# 或: pip install -r requirements-mcp.txt && pip install -e .
```

依赖：`mcp` 2.x（`MCPServer`）。

## Tools（P0）

| Tool | 说明 | CLI 等价 |
|------|------|----------|
| `tea_health` | 目录 + weather 探测 | — |
| `tea_tools_catalog` | 工具元数据 | — |
| `tea_weather` | 市场天气 | `tea weather` |
| `tea_seed_show` | 最新 SEED | `tea seed-show` |
| `tea_status` | 今日状态 | `tea status --weather` |
| `tea_followthrough` | 跟涨看板（只读） | `tea followthrough` |
| `tea_watch_summary` | 观察池摘要 | 读 `data/watch_pool.json` |

返回值：**JSON 字符串**，字段与 enterprise-kb 内嵌 `tea_*` 一致（`ok`、`exitCode`、`text`、`command`）。

## Cursor / goose 配置

与知识库 **并列** 两个 MCP Server（路径按本机修改）：

```json
{
  "mcpServers": {
    "enterprise-kb": {
      "command": "/path/to/enterprise-kb-py/.venv/bin/python",
      "args": ["/path/to/enterprise-kb-py/scripts/mcp_kb_server.py"],
      "env": {
        "PYTHONPATH": "/path/to/enterprise-kb-py"
      }
    },
    "tea": {
      "command": "/path/to/tea/.venv/bin/python",
      "args": ["/path/to/tea/scripts/mcp_tea_server.py"],
      "env": {
        "TEA_HOME": "/path/to/tea",
        "PYTHONPATH": "/path/to/tea"
      }
    }
  }
}
```

`TEA_HOME` 必须指向含 `tea_config.json`、`data/`、`reports/` 的运行目录（NAS 部署时为 tea 仓库根或数据卷挂载点）。

## 代码入口

| 路径 | 职责 |
|------|------|
| `scripts/mcp_tea_server.py` | 客户端启动脚本 |
| `tea/mcp/server.py` | `MCPServer` 与 `@mcp.tool` |
| `tea/mcp/tools.py` | 工具实现与 JSON payload |
| `tea/mcp/health.py` | `tea_health` 探测 |

## 测试

```bash
cd /path/to/tea
python -m unittest tests.test_mcp_tools -v
```

## 安全

- stdio MCP 无 HTTP 鉴权；仅本机或内网使用。
- P1 起可在 KB 侧通过 `TEA_TRANSPORT=mcp` 复用同一 Server（见 enterprise-kb `docs/enterprise/TEA_INTEGRATION.md`）。
