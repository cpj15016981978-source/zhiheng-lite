# -*- coding: utf-8 -*-
"""Qwen-Agent 的 MCP 工具注册。"""
import os
import tempfile
import unittest
from unittest import mock

from pai import orchestrator as O
from pai.memory import Memory


class QwenMCPToolsTest(unittest.TestCase):
    def test_mcp_tool_is_registered(self):
        mem = Memory(os.path.join(tempfile.mkdtemp(), "q.db"))
        o = O.Orchestrator(mem)
        seen = []

        class FakeAssistant:
            def __init__(self, function_list=None, llm=None, system_message=None):
                seen.extend(t.name for t in (function_list or []))
            def run(self, messages):
                return [{"role": "assistant", "content": "ok"}]

        with mock.patch("pai.mcp_client.servers", return_value=[{"name": "demo"}]), \
             mock.patch("pai.mcp_client.list_tools", return_value=[
                 {"name": "echo", "description": "echo",
                  "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]), \
             mock.patch("qwen_agent.agents.Assistant", FakeAssistant):
            self.assertEqual(o.qwen_agent("hi"), "ok")
        self.assertIn("recall", seen)
        self.assertIn("mcp__demo__echo", seen)
        mem.close()


if __name__ == "__main__":
    unittest.main()
