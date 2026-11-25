# -*- coding: utf-8 -*-
import unittest

from pai import model_gateway as MG


class ContextBudgetTest(unittest.TestCase):
    def test_short_prompt_unchanged(self):
        self.assertEqual(MG._fit_prompt("你好", 16384), "你好")

    def test_long_prompt_trims_middle_but_keeps_ends(self):
        text = "头" * 10000 + "中" * 20000 + "尾" * 10000
        out = MG._fit_prompt(text, 16384)
        self.assertLess(len(out), len(text))
        self.assertIn("长上下文中间已裁剪", out)
        self.assertTrue(out.startswith("头"))
        self.assertTrue(out.endswith("尾"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
