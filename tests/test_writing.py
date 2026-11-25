# -*- coding: utf-8 -*-
import unittest

from pai import writing as W


class WritingTest(unittest.TestCase):
    def test_build_draft(self):
        p = W.build_prompt("draft", "给客户解释涨价", "客户", "克制", "500 字", "成本上涨",
                           style_block="[长期写作风格]\n风格名：克制专业")
        self.assertIn("给客户解释涨价", p)
        self.assertIn("成本上涨", p)
        self.assertIn("直接给完整初稿", p)
        self.assertIn("克制专业", p)

    def test_unknown_task_and_empty_topic(self):
        with self.assertRaises(ValueError):
            W.build_prompt("unknown", "x")
        with self.assertRaises(ValueError):
            W.build_prompt("draft", "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
