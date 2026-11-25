# -*- coding: utf-8 -*-
"""代码模型分流。"""
import os
import unittest
from unittest import mock

from pai import code_route as C


class CodeRouteTest(unittest.TestCase):
    def test_code_detected(self):
        self.assertTrue(C.is_code("写一个 Python 函数"))
        self.assertTrue(C.is_code("这个报错怎么修"))
        self.assertFalse(C.is_code("今天天气怎么样"))

    def test_spec_env(self):
        with mock.patch.dict(os.environ, {"PAI_CODE_MODEL": "qwen2.5-coder:3b"}):
            self.assertEqual(C.spec(), "ollama:qwen2.5-coder:3b")


if __name__ == "__main__":
    unittest.main()
