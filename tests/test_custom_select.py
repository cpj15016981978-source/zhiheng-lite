# -*- coding: utf-8 -*-
"""自定义模型选择。"""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from pai import custom_select as CS


class CustomSelectTest(unittest.TestCase):
    def test_select_api(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(CS, "FLAG", pathlib.Path(d) / "a.json"), \
                mock.patch.object(CS.MR, "find", return_value={
                    "name": "my", "kind": "api", "base_url": "https://x/v1",
                    "key": "k", "model": "m1"}), \
                mock.patch.dict(os.environ, {}, clear=False):
            CS.select("my")
            self.assertEqual(os.environ["PAI_CUSTOM_MODEL"], "my")
            self.assertEqual(os.environ["PAI_MODEL_PROVIDER"], "openai")
            self.assertEqual(CS.status()["name"], "my")
            CS.clear()
            self.assertNotIn("PAI_CUSTOM_MODEL", os.environ)

    def test_unknown(self):
        with mock.patch.object(CS.MR, "find", return_value=None):
            with self.assertRaises(ValueError):
                CS.select("nope")


if __name__ == "__main__":
    unittest.main()
