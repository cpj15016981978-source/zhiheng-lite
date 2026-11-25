# -*- coding: utf-8 -*-
"""关系层/毕业机制移植测试。"""
import unittest

from pai import graduation as G
from pai import relationship as R


class RelationshipTest(unittest.TestCase):
    def test_prompt_shape(self):
        text = R.prompt_block()
        self.assertIn("[关系层]", text)
        self.assertIn("认识第", text)


class GraduationTest(unittest.TestCase):
    def test_letter_contains_anchor_and_no_send_claim(self):
        text = G.letter({"name": "测试"}, [{"title": "喜欢喝茶"}])
        self.assertIn("测试", text)
        self.assertIn("喜欢喝茶", text)
        self.assertIn("不会把这里的话替你带给任何人", text)


if __name__ == "__main__":
    unittest.main()
