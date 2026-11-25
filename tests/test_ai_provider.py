# -*- coding: utf-8 -*-
"""统一 AIProvider 基础测试。"""
import os
import unittest
from unittest import mock

from pai import ai_provider as A


class ProviderTest(unittest.TestCase):
    def test_offline(self):
        p = A.OfflineProvider()
        self.assertTrue(p.health_check()["ok"])
        self.assertIn("离线", p.chat([{"role": "user", "content": "hi"}])["content"])

    def test_local_info(self):
        with mock.patch.dict(os.environ, {"PAI_MODEL_NAME": "qwen2.5:1.5b"}, clear=False):
            p = A.local()
            self.assertEqual(p.model_info()["model"], "qwen2.5:1.5b")
            self.assertEqual(p.name, "ollama")

    def test_custom_selected(self):
        import pathlib, tempfile
        from pai import model_registry as MR
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(MR, "PATH", pathlib.Path(d) / "m.json"), \
                mock.patch.dict(os.environ, {"PAI_CUSTOM_MODEL": "my-api"}, clear=False):
            MR.save({"name": "my-api", "kind": "api", "provider": "custom",
                     "base_url": "https://example.com/v1", "key": "k", "model": "m1"})
            p = A.selected()
            self.assertEqual(p.model_info()["model"], "m1")
            self.assertEqual(p.name, "openai")

    def test_remote_requires_key(self):
        p = A.remote(key="", base="https://example.com/v1")
        self.assertFalse(p.health_check()["ok"])


if __name__ == "__main__":
    unittest.main()
