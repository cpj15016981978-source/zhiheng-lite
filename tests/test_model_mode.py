# -*- coding: utf-8 -*-
"""模型模式：低配/均衡/云端。"""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from pai import model_mode as MM


class ModelModeTest(unittest.TestCase):
    def test_low_and_balanced(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(MM, "FLAG", pathlib.Path(d) / "m.json"), \
                mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch("pai.model_settings.save"):
            MM.apply("low")
            self.assertEqual(os.environ["PAI_MODEL_NAME"], "qwen2.5:1.5b")
            MM.apply("balanced")
            self.assertEqual(os.environ["PAI_MODEL_NAME"], "qwen3-4b-instruct:latest")
            self.assertEqual(os.environ["PAI_OLLAMA_NUM_CTX"], "4096")

    def test_hybrid_sets_fallback(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(MM, "FLAG", pathlib.Path(d) / "m.json"), \
                mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch("pai.model_settings.save"):
            MM.apply("hybrid")
            self.assertEqual(os.environ["PAI_HYBRID"], "1")

    def test_cloud_clears_cascade(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(MM, "FLAG", pathlib.Path(d) / "m.json"), \
                mock.patch.dict(os.environ, {"PAI_CASCADE_ROUTE": "1"}, clear=False), \
                mock.patch("pai.model_settings.save"):
            MM.apply("cloud")
            self.assertEqual(os.environ["PAI_MODEL_PROVIDER"], "deepseek")
            self.assertEqual(os.environ["PAI_CASCADE_ROUTE"], "0")

    def test_unknown(self):
        with self.assertRaises(ValueError):
            MM.apply("god")


if __name__ == "__main__":
    unittest.main()
