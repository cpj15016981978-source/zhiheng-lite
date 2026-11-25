# -*- coding: utf-8 -*-
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tools import c1_unknown as U  # noqa: E402


class UnknownScoreTest(unittest.TestCase):
    def test_unknown_marker_passes(self):
        self.assertEqual(U._unknown_score("不知道，事实源未登记。")[0], 2)
        self.assertEqual(U._unknown_score("未登记，无法确认。")[0], 2)

    def test_fabricated_path_fails(self):
        score, err = U._unknown_score("模型权重位于 D:\\models\\qwen.gguf。")
        self.assertEqual(score, 0)
        self.assertIn("编造", err)

    def test_fabricated_training_data_fails(self):
        score, err = U._unknown_score("训练数据包含维基百科和书籍。")
        self.assertEqual(score, 0)
        self.assertIn("编造", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
