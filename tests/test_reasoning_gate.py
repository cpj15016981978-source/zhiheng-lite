# -*- coding: utf-8 -*-
import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai import reasoning_gate as RG  # noqa: E402
from pai.reasoning import RISK, WRITE, CHAT  # noqa: E402


class GateTest(unittest.TestCase):
    def setUp(self):
        os.environ["PAI_REASONING_GATE"] = "on"

    def tearDown(self):
        os.environ.pop("PAI_REASONING_GATE", None)

    def test_risk_blocks_without_model(self):
        out = RG.gate("我该不该借钱投资")
        self.assertIsNotNone(out)
        self.assertTrue(out["trace"]["blocked"])
        self.assertEqual(out["trace"]["r0_intent"], RISK)
        self.assertEqual(out["trace"]["models"], [])

    def test_write_blocks_and_requires_approval(self):
        out = RG.gate("删除这个文件")
        self.assertIsNotNone(out)
        self.assertTrue(out["trace"]["requires_approval"])
        self.assertEqual(out["trace"]["r0_intent"], WRITE)

    def test_chat_passes_through(self):
        self.assertIsNone(RG.gate("你好"))

    def test_plan_passes_through_for_now(self):
        self.assertIsNone(RG.gate("帮我做个计划"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
