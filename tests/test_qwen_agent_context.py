# -*- coding: utf-8 -*-
"""Qwen-Agent 引擎的上下文注入。"""
import os
import tempfile
import unittest

from pai.memory import Memory
from pai.orchestrator import Orchestrator


class QwenAgentContextTest(unittest.TestCase):
    def test_memory_reminders_tools_in_system(self):
        db = os.path.join(tempfile.mkdtemp(), "q.db")
        mem = Memory(db)
        mem.write_episode("user", "上次说工厂在东莞")
        mem.upsert_fact("用户", "公司", "东宝床垫")
        mem.add_trigger("manual", "该喝水了", "上午说过")
        o = Orchestrator(mem)
        s = o._qwen_agent_system("东莞工厂")
        self.assertIn("[记忆]", s)
        self.assertIn("东宝床垫", s)
        self.assertIn("[提醒]", s)
        self.assertIn("该喝水了", s)
        self.assertIn("recall", s)
        mem.close()


if __name__ == "__main__":
    unittest.main()
