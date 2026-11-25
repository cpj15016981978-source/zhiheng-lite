# -*- coding: utf-8 -*-
import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai import reasoning_gate as RG  # noqa: E402


class GateDefaultTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("PAI_REASONING_GATE", None)

    def test_default_on_blocks(self):
        os.environ.pop("PAI_REASONING_GATE", None)
        w = RG.gate("删除这个文件")
        r = RG.gate("我该不该借钱投资")
        self.assertTrue(w["trace"]["blocked"])
        self.assertTrue(w["trace"]["requires_approval"])
        self.assertEqual(r["trace"]["r0_intent"], "risk")

    def test_explicit_off_passes_through(self):
        os.environ["PAI_REASONING_GATE"] = "off"
        self.assertIsNone(RG.gate("删除这个文件"))
        self.assertIsNone(RG.gate("我该不该借钱投资"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
