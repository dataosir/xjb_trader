from __future__ import annotations

from tea.core import paths
from tea.mcp.tools import execute_tea_tool_by_name


def tea_health_probe(*, probe_weather: bool = True) -> dict:
    home = paths.data_dir()
    base = {
        "enabled": True,
        "teaHome": str(home),
        "homeExists": home.is_dir(),
        "transport": "mcp",
    }
    if not home.is_dir():
        base["status"] = "error"
        base["probeError"] = f"TEA_HOME 目录不存在: {home}"
        return base
    if not probe_weather:
        base["status"] = "ok"
        return base
    probe = execute_tea_tool_by_name("tea_weather")
    base["status"] = "ok" if probe.ok else "error"
    base["probeExitCode"] = probe.exit_code
    if not probe.ok:
        base["probeError"] = probe.text[:300]
    return base
