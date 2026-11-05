"""TEA MCP Server（stdio）。"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from tea.mcp import handlers as h

mcp = MCPServer(
    "tea",
    instructions=(
        "TEA（XJB_TRADE）只读 MCP：市场天气、SEED 报告、今日状态、跟涨看板、观察池。"
        "写操作（seed-plan、run、review --update）未暴露；数据目录由 TEA_HOME 决定。"
    ),
)


@mcp.tool(description="检查 TEA 运行目录与 weather 探测。")
def tea_health() -> str:
    return h.tea_health_json()


@mcp.tool(description="列出可用的 tea_* MCP 工具说明（P0 只读集）。")
def tea_tools_catalog() -> str:
    return h.tea_tools_catalog_json()


@mcp.tool(description="市场天气（`tea weather`）。")
def tea_weather() -> str:
    return h.tea_tool_json("tea_weather")


@mcp.tool(description="最新 SEED 报告。")
def tea_seed_show() -> str:
    return h.tea_tool_json("tea_seed_show")


@mcp.tool(description="今日交易状态与门禁（`tea status --weather`）。")
def tea_status() -> str:
    return h.tea_tool_json("tea_status")


@mcp.tool(description="跟涨/缺口看板（`tea followthrough`，只读）。")
def tea_followthrough() -> str:
    return h.tea_tool_json("tea_followthrough")


@mcp.tool(description="观察池条数与代码列表。")
def tea_watch_summary() -> str:
    return h.tea_tool_json("tea_watch")
