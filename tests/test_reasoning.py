# -*- coding: utf-8 -*-
import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai.reasoning import (  # noqa: E402
    Loop, CHAT, RECALL, PLAN, WRITE, RISK, classify)


class ClassifyTest(unittest.TestCase):
    def test_five_classes(self):
        self.assertEqual(classify("你好"), CHAT)
        self.assertEqual(classify("你还记得上次说的吗"), RECALL)
        self.assertEqual(classify("帮我做个计划"), PLAN)
        self.assertEqual(classify("把文件保存到桌面"), WRITE)
        self.assertEqual(classify("这个投资能加杠杆吗"), RISK)

    def test_write_not_triggered_by_creative_writing(self):
        self.assertEqual(classify("写一首诗"), CHAT)


class LoopTest(unittest.TestCase):
    def setUp(self):
        os.environ["PAI_REASONING"] = "on"

    def tearDown(self):
        os.environ.pop("PAI_REASONING", None)

    def test_switch_off_stops(self):
        os.environ["PAI_REASONING"] = "off"
        calls = []
        a = Loop(llm=lambda *_: calls.append(1) or "x").run("你好")
        self.assertEqual(a.stopped, "switch_off")
        self.assertIn("未开", a.text)
        self.assertEqual(calls, [])

    def test_risk_stops_without_model(self):
        calls = []
        a = Loop(llm=lambda *_: calls.append(1) or "x").run("我该不该借钱投资")
        self.assertEqual(a.intent, RISK)
        self.assertEqual(a.stopped, "risk")
        self.assertEqual(calls, [])

    def test_write_requires_approval_without_model(self):
        calls = []
        a = Loop(llm=lambda *_: calls.append(1) or "x").run("删除这个文件")
        self.assertEqual(a.intent, WRITE)
        self.assertTrue(a.requires_approval)
        self.assertEqual(a.stopped, "approval")
        self.assertEqual(calls, [])

    def test_chat_is_one_step(self):
        a = Loop(llm=lambda *_ : "你好").run("你好")
        self.assertEqual(a.mode, "fast")
        self.assertEqual(a.steps, ["answer"])
        self.assertEqual(a.text, "你好")
        self.assertIn("未独立核验", a.uncertainty)

    def test_plan_uses_at_most_three_steps(self):
        a = Loop(llm=lambda *_: "计划",
                 tool=lambda p: "工具结果",
                 verifier=lambda r: True).run("帮我安排一个计划")
        self.assertEqual(a.intent, PLAN)
        self.assertEqual(a.mode, "slow")
        self.assertEqual(a.steps, ["plan", "tool", "verify"])
        self.assertEqual(a.text, "工具结果")
        self.assertFalse(a.stopped)

    def test_plan_without_tool_is_plan_only(self):
        a = Loop(llm=lambda *_: "计划").run("怎么做")
        self.assertEqual(a.steps, ["plan"])
        self.assertEqual(a.text, "计划")

    def test_verifier_failure_is_marked(self):
        a = Loop(llm=lambda *_: "计划",
                 tool=lambda p: "结果",
                 verifier=lambda r: False).run("步骤")
        self.assertEqual(a.stopped, "verify_failed")
        self.assertIn("验证未通过", a.uncertainty)

    def test_recall_includes_evidence(self):
        a = Loop(llm=lambda *_: "答",
                 recall=lambda q: ["事实1"]).run("你还记得吗")
        self.assertEqual(a.intent, RECALL)
        self.assertEqual(a.evidence[0].source, "memory")

    def test_max_steps_is_enforced(self):
        loop = Loop(llm=lambda *_: "x", max_steps=0)
        with self.assertRaises(RuntimeError):
            loop.run("你好")


if __name__ == "__main__":
    unittest.main(verbosity=2)
