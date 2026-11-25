# -*- coding: utf-8 -*-
"""C 面：服务断掉时给明确错误，不挂死、不伪造。

两个断点分别对应本机两条对外依赖：Ollama 11434 与外部 HTTP。
测试全程 monkeypatch，不真停服务；真停服务的现量证明在 README/回执里。
"""
import os
import unittest
import urllib.error
from unittest import mock

from pai import local_duo as D
from pai import vision as V
from pai import web_search as S


class OllamaDownTest(unittest.TestCase):
    def test_ollama_down_is_clear_error(self):
        calls = []

        def down(*a, **k):
            calls.append(1)
            raise RuntimeError("连不上本地 Ollama：Connection refused")

        with mock.patch.object(D, "_call", side_effect=down):
            with self.assertRaises(RuntimeError) as ctx:
                D.complete("任务", user_text="你好", smart=True)
        self.assertEqual(len(calls), 1, "断服务后不应继续重试后两步")
        self.assertIn("连不上本地 Ollama", str(ctx.exception))


class ExternalHttpDownTest(unittest.TestCase):
    def test_vision_and_search_down_are_clear_errors(self):
        old = {k: os.environ.get(k) for k in
               ("PAI_VISION_API_KEY", "TAVILY_API_KEY")}
        os.environ["PAI_VISION_API_KEY"] = "k"
        os.environ["TAVILY_API_KEY"] = "k"
        try:
            with mock.patch.object(V.urllib.request, "urlopen",
                                   side_effect=urllib.error.URLError("down")):
                with self.assertRaises(V.VisionError):
                    V.describe(b"x")
            with mock.patch.object(S.urllib.request, "urlopen",
                                   side_effect=urllib.error.URLError("down")):
                with self.assertRaises(S.SearchError):
                    S.search("q")
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main(verbosity=2)
