# -*- coding: utf-8 -*-
"""Mirrai 六态情绪层移植测试。"""
import unittest

from pai import mirrai_emotion as E


class MirraiEmotionTest(unittest.TestCase):
    def test_six_states(self):
        self.assertEqual(E.compute_state("好久没见，想你了"), "nostalgic")
        self.assertEqual(E.compute_state("哈哈笑死"), "playful")
        self.assertEqual(E.compute_state("今天很难过"), "melancholy")
        self.assertEqual(E.compute_state("太好了，开心"), "happy")
        self.assertEqual(E.compute_state("随便吧，无所谓"), "distant")
        self.assertEqual(E.compute_state("今天做报表"), "warm")

    def test_negation_does_not_fake_sadness(self):
        self.assertEqual(E.compute_state("我不难过"), "warm")
        self.assertEqual(E.compute_state("别难过"), "warm")
        self.assertEqual(E.compute_state("我不开心"), "melancholy")
        self.assertNotEqual(E.compute_state("开心不起来"), "happy")

    def test_prompt_block(self):
        text = E.prompt_block("想你了")
        self.assertIn("[镜像情绪 · Mirrai 六态]", text)
        self.assertIn("nostalgic", text)
        self.assertIn("内部估计", text)


if __name__ == "__main__":
    unittest.main()
