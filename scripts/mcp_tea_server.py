#!/usr/bin/env python3
"""启动 TEA MCP Server（stdio）。由 Cursor / goose 等客户端以子进程拉起。"""

from tea.mcp.server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio")
