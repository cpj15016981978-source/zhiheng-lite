# -*- coding: utf-8 -*-
"""M8 第二批 v0：模型选只读工具（协议解析 + 编排层一次续调 + 成本可见）。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai import tool_loop as TL      # noqa: E402
from pai.memory import Memory        # noqa: E402
from pai.orchestrator import Orchestrator  # noqa: E402

CALL = 'TOOL_CALL: {"tool": "recall", "args": {"query": "阿明", "k": 3}}'


def prop(text, cost=0.01, tokens=10):
    return [{"model_id": "mock", "output": text, "cost": cost,
             "tokens": tokens, "priced": True}]


class ParseTest(unittest.TestCase):
    def test_parse_valid(self):
        got = TL.parse("先查一下\n" + CALL)
        self.assertEqual(got, {"tool": "recall", "args": {"query": "阿明", "k": 3}})

    def test_parse_missing_or_bad(self):
        self.assertIsNone(TL.parse("直接回答，没有工具"))
        self.assertIsNone(TL.parse('TOOL_CALL: {"tool": "recall", "args": '))
        self.assertIsNone(TL.parse('TOOL_CALL: {"tool": "nope", "args": {}}'))
        self.assertIsNone(TL.parse('TOOL_CALL: {"tool": "reminders", "args": []}'))

    def test_instruction_lists_registry(self):
        text = TL.instruction()
        for name in ("recall",):
            self.assertIn(name, text)
        self.assertIn("TOOL_CALL", text)

    def test_strip_call(self):
        self.assertEqual(TL.strip_call("答案\n" + CALL), "答案")
        self.assertEqual(TL.strip_call(CALL), "")

    def test_disabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(TL.enabled())


class OrchestratorToolUseTest(unittest.TestCase):
    def setUp(self):
        self.mem = Memory(os.path.join(tempfile.mkdtemp(), "tu.db"))
        self.mem.upsert_fact("用户", "姓名", "阿明")
        self.orch = Orchestrator(self.mem)
        self.env = mock.patch.dict(os.environ, {"PAI_TOOL_USE": "1"}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.mem.close()

    def test_tool_call_then_answer(self):
        prompts = []

        def complete(prompt, **kw):
            prompts.append(prompt)
            if len(prompts) == 1:
                return prop(CALL, cost=0.01, tokens=10)
            return prop("阿明，你之前说过你在床垫厂。", cost=0.02, tokens=20)

        with mock.patch("pai.orchestrator.M10.complete", side_effect=complete):
            out = self.orch.handle("我叫什么？")
        self.assertIn("阿明", out["response"])
        tu = out["trace"]["tool_use"]
        self.assertTrue(tu["used"])
        self.assertEqual(tu["tool"], "recall")
        self.assertEqual(tu["calls"], 1)
        self.assertAlmostEqual(out["trace"]["cost"], 0.03)
        self.assertEqual(out["trace"]["tokens"], 30)
        self.assertIn("工具协议", prompts[0], "开关打开时 prompt 应带工具说明")
        self.assertIn("工具结果", prompts[1], "第二次调用应带工具结果")

    def test_disabled_keeps_prompt_untouched(self):
        prompts = []

        def complete(prompt, **kw):
            prompts.append(prompt)
            return prop("直接回答", cost=0.01, tokens=10)

        with mock.patch.dict(os.environ, {"PAI_TOOL_USE": "0"}, clear=False):
            with mock.patch("pai.orchestrator.M10.complete", side_effect=complete):
                out = self.orch.handle("你好")
        self.assertFalse(out["trace"]["tool_use"]["used"])
        self.assertNotIn("工具协议", prompts[0])

    def test_bad_tool_name_falls_back(self):
        with mock.patch("pai.orchestrator.M10.complete",
                        return_value=prop('TOOL_CALL: {"tool": "rm_rf", "args": {}}')):
            out = self.orch.handle("你好")
        self.assertFalse(out["trace"]["tool_use"]["used"])
        self.assertNotIn("TOOL_CALL", out["response"])

    def test_second_call_failure_keeps_original(self):
        from pai.model_gateway import ModelGatewayError
        effects = [prop(CALL), ModelGatewayError("boom")]
        with mock.patch("pai.orchestrator.M10.complete", side_effect=effects):
            out = self.orch.handle("我叫什么？")
        self.assertFalse(out["trace"]["tool_use"]["used"])
        self.assertIn("二次调用失败", out["trace"]["tool_use"]["reason"])
        self.assertIn("没查成", out["response"])

    def test_m8_switch_is_restored(self):
        with mock.patch.dict(os.environ, {"PAI_TOOLS": "off"}, clear=False):
            res = TL.run_tool(self.mem, {"tool": "recall", "args": {"query": "阿明"}})
            self.assertTrue(res["ok"])
            self.assertEqual(os.environ.get("PAI_TOOLS"), "off")

    def _second_fails(self, second):
        effects = [prop(CALL), second]
        with mock.patch("pai.orchestrator.M10.complete", side_effect=effects):
            return self.orch.handle("我叫什么？")

    def test_second_call_empty_list_falls_back(self):
        out = self._second_fails([])
        self.assertFalse(out["trace"]["tool_use"]["used"])
        self.assertIn("IndexError", out["trace"]["tool_use"]["reason"])
        self.assertIn("没查成", out["response"])

    def test_second_call_oserror_falls_back(self):
        out = self._second_fails(OSError("断流"))
        self.assertFalse(out["trace"]["tool_use"]["used"])
        self.assertIn("OSError", out["trace"]["tool_use"]["reason"])

    def test_second_call_missing_output_falls_back(self):
        out = self._second_fails([{"model_id": "x", "cost": 0.0, "tokens": 0}])
        self.assertFalse(out["trace"]["tool_use"]["used"])
        self.assertIn("KeyError", out["trace"]["tool_use"]["reason"])

    def test_second_call_bad_cost_falls_back(self):
        out = self._second_fails([{"model_id": "x", "output": "好", "cost": "abc", "tokens": 0}])
        self.assertFalse(out["trace"]["tool_use"]["used"])
        self.assertIn("ValueError", out["trace"]["tool_use"]["reason"])

    def test_second_call_priced_is_merged(self):
        effects = [prop(CALL), prop("阿明", cost=0.02, tokens=20)]
        with mock.patch("pai.orchestrator.M10.complete", side_effect=effects):
            out = self.orch.handle("我叫什么？")
        self.assertTrue(out["trace"]["tool_use"]["used"])
        self.assertTrue(out["trace"]["tool_use"]["priced"])

    def test_council_does_not_get_tool_protocol(self):
        prompts = []
        def complete(prompt, **kw):
            prompts.append(prompt)
            return prop("议会回答")
        with mock.patch("pai.orchestrator.M10.complete", side_effect=complete):
            out = self.orch.handle("你好", council=("deepseek", "openai"))
        self.assertNotIn("工具协议", prompts[0])
        self.assertFalse(out["trace"]["tool_use"]["used"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
