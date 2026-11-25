# -*- coding: utf-8 -*-
"""模型包管理：安装检测、角色选择、0.6B 路由回退。"""
import unittest
from unittest import mock

from pai import model_packs as MP
from pai import model_settings as MS
import os, pathlib, tempfile


class ModelPacksTest(unittest.TestCase):
    def test_installed_marks_missing(self):
        tags = {"models": [{"name": "qwen3:0.6b"}]}
        with mock.patch.object(MP, "_get", return_value=tags):
            rows = MP.installed()
        self.assertTrue([r for r in rows if r["id"] == "qwen3-0.6b"][0]["installed"])
        self.assertFalse([r for r in rows if r["id"] == "qwen3-4b"][0]["installed"])

    def test_pick_role(self):
        tags = {"models": [{"name": "qwen3:0.6b"}]}
        with mock.patch.object(MP, "_get", return_value=tags):
            self.assertEqual(MP.pick("route")["id"], "qwen3-0.6b")
            self.assertIsNone(MP.pick("chat"))

    def test_route_uses_model_and_validates(self):
        tags = {"models": [{"name": "qwen3:0.6b"}]}
        with mock.patch.object(MP, "_get", return_value=tags), \
                mock.patch.object(MP, "chat", return_value="job"):
            out = MP.route("帮我跑个任务")
        self.assertTrue(out["ok"])
        self.assertEqual(out["label"], "job")

    def test_route_falls_back_when_missing(self):
        with mock.patch.object(MP, "_get", return_value={"models": []}):
            out = MP.route("记得上次的代码")
        self.assertFalse(out["ok"])
        self.assertEqual(out["source"], "rules")

    def test_route_rejects_out_of_vocab(self):
        tags = {"models": [{"name": "qwen3:0.6b"}]}
        with mock.patch.object(MP, "_get", return_value=tags), \
                mock.patch.object(MP, "chat", return_value="随便"):
            out = MP.route("你好")
        self.assertFalse(out["ok"])
        self.assertEqual(out["source"], "rules")


class ModelSettingsTest(unittest.TestCase):
    def test_save_load_apply(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(MS, "PATH", pathlib.Path(d) / "m.json"):
            MS.save("ollama", "qwen3-4b-instruct:latest", True)
            self.assertEqual(MS.load()["provider"], "ollama")
            with mock.patch.dict(os.environ, {}, clear=False), \
                    mock.patch.object(__import__("pai.network_policy", fromlist=["NP"]), "FLAG",
                                      pathlib.Path(d) / "network.json"):
                MS.apply()
                self.assertEqual(os.environ["PAI_MODEL_PROVIDER"], "ollama")
                # PAI_OFFLINE 只归 network_policy；model_settings 不再写它。
                self.assertNotIn("PAI_OFFLINE", os.environ)
                self.assertEqual(MS.current()["offline"], True)


if __name__ == "__main__":
    unittest.main()
