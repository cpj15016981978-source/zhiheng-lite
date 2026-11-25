# -*- coding: utf-8 -*-
import unittest
from unittest import mock

from pai import local_duo as D


class LocalDuoTest(unittest.TestCase):
    def test_needs_review(self):
        self.assertFalse(D.needs_review("你好"))
        self.assertFalse(D.needs_review("今天有什么"))
        self.assertTrue(D.needs_review("帮我分析这个方案的风险"))
        self.assertTrue(D.needs_review("请设计一个长期计划"))

    def test_three_steps_and_stream_final(self):
        deltas = []
        calls = []

        def fake(prompt, model, max_tokens=700, on_delta=None):
            calls.append((prompt, model, max_tokens))
            if len(calls) == 1:
                return "草稿", 10, 5
            if len(calls) == 2:
                return "审稿意见", 8, 4
            if on_delta:
                on_delta("最终回答")
            return "最终回答", 9, 6

        with mock.patch.object(D, "_call", side_effect=fake):
            out = D.complete("原始任务", on_delta=deltas.append)
        self.assertEqual(len(calls), 3)
        self.assertIn("草稿", calls[1][0])
        self.assertEqual(deltas, ["最终回答"])
        self.assertEqual(out["output"], "最终回答")
        self.assertEqual(out["model_id"], "local_duo")
        self.assertEqual(out["tokens"], 42)

    def test_review_failure_still_final(self):
        calls = []

        def fake(prompt, model, max_tokens=700, on_delta=None):
            calls.append(prompt)
            if len(calls) == 1:
                return "草稿", 1, 1
            if len(calls) == 2:
                raise RuntimeError("r1 down")
            return "最终", 1, 1

        with mock.patch.object(D, "_call", side_effect=fake):
            out = D.complete("任务")
        self.assertEqual(out["output"], "最终")

    def test_smart_simple_uses_one_call(self):
        calls = []

        def fake(prompt, model, max_tokens=700, on_delta=None):
            calls.append(prompt)
            if on_delta:
                on_delta("单脑")
            return "单脑", 1, 1

        with mock.patch.object(D, "_call", side_effect=fake):
            out = D.complete("原始任务", user_text="你好", smart=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(out["model_id"], "local_smart")
        self.assertEqual(out["output"], "单脑")

    def test_smart_complex_uses_three_calls(self):
        calls = []

        def fake(prompt, model, max_tokens=700, on_delta=None):
            calls.append(prompt)
            if len(calls) == 1:
                return "草稿", 1, 1
            if len(calls) == 2:
                return "审稿", 1, 1
            return "最终", 1, 1

        with mock.patch.object(D, "_call", side_effect=fake):
            out = D.complete("原始任务", user_text="帮我分析这个方案的风险", smart=True)
        self.assertEqual(len(calls), 3)
        self.assertEqual(out["model_id"], "local_smart")


if __name__ == "__main__":
    unittest.main(verbosity=2)
