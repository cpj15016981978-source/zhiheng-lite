# -*- coding: utf-8 -*-
"""情绪小模型推理测试。"""
import os
import unittest
from unittest import mock

from pai import emotion_model as M


class EmotionModelTest(unittest.TestCase):
    def test_model_loads_and_returns_label(self):
        label, conf = M.classify("哈哈笑死")
        self.assertIn(label, ("playful", "happy", "warm"))
        self.assertGreaterEqual(conf, 0.0)
        self.assertLessEqual(conf, 1.0)

    def test_empty_is_safe(self):
        label, conf = M.classify("")
        self.assertIn(label, ("warm", "happy", "playful", "nostalgic", "melancholy", "distant"))
        self.assertEqual(conf, 0.0)

    def test_enabled_switch(self):
        with mock.patch.dict(os.environ, {"PAI_EMOTION_MODEL": "off"}):
            self.assertFalse(M.enabled())
        with mock.patch.dict(os.environ, {"PAI_EMOTION_MODEL": "on"}):
            self.assertTrue(M.enabled())


if __name__ == "__main__":
    unittest.main()
