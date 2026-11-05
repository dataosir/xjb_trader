from __future__ import annotations

import json

from tea.mcp.health import tea_health_probe
from tea.mcp.tools import P0_TOOL_CATALOG, tea_tool_result_payload, tea_tools_catalog_payload


def tea_health_json() -> str:
    return json.dumps(tea_health_probe(), ensure_ascii=False)


def tea_tool_json(tool_name: str) -> str:
    return json.dumps(tea_tool_result_payload(tool_name), ensure_ascii=False)


def tea_tools_catalog_json() -> str:
    return json.dumps(tea_tools_catalog_payload(), ensure_ascii=False)


def tool_names() -> frozenset[str]:
    return frozenset(spec.name for spec in P0_TOOL_CATALOG)
