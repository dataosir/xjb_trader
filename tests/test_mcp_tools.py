"""TEA MCP 工具单元测试（不依赖 mcp 包 import server）。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from tea.mcp.health import tea_health_probe
from tea.mcp.tools import execute_tea_tool_by_name, tea_tool_result_payload, tea_tools_catalog_payload


class TestMcpTools(unittest.TestCase):
    def test_catalog_has_p0_tools(self):
        data = tea_tools_catalog_payload()
        names = {t["name"] for t in data["tools"]}
        self.assertIn("tea_weather", names)
        self.assertIn("tea_watch", names)
        self.assertEqual(len(names), 5)

    def test_payload_shape(self):
        from tea.mcp.result import TeaResult

        with patch(
            "tea.mcp.tools.execute_tea_tool_by_name",
            return_value=TeaResult(ok=True, stdout="x", stderr="", exit_code=0, command=["weather"]).normalized(),
        ):
            payload = tea_tool_result_payload("tea_weather")
        self.assertEqual(payload["tool"], "tea_weather")
        self.assertTrue(payload["ok"])
        self.assertIn("text", payload)
        self.assertIn("exitCode", payload)

    def test_watch_pool_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "data")
            os.makedirs(data_dir)
            path = os.path.join(data_dir, "watch_pool.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps([{"code": "600000"}, {"code": "000001"}]))
            with patch.dict(os.environ, {"TEA_HOME": tmp}, clear=False):
                result = execute_tea_tool_by_name("tea_watch")
        self.assertTrue(result.ok)
        self.assertIn("600000", result.text)

    def test_health_without_weather_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"TEA_HOME": tmp}, clear=False):
                probe = tea_health_probe(probe_weather=False)
        self.assertEqual(probe["status"], "ok")
        self.assertTrue(probe["homeExists"])


if __name__ == "__main__":
    unittest.main()
