# -*- coding: utf-8 -*-
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai import wiki as W  # noqa: E402


class WikiTest(unittest.TestCase):
    def setUp(self):
        os.environ["PAI_WIKI"] = "on"
        self.w = W.Wiki()

    def tearDown(self):
        os.environ.pop("PAI_WIKI", None)

    def test_mainline_loaded(self):
        p = self.w.get("mainline")
        self.assertIsNotNone(p)
        self.assertEqual(p["title"], "主线")
        self.assertIn("C1 已过 Q 复评", p["_body"])

    def test_search_c1(self):
        got = self.w.search("C1 判分尺")
        self.assertTrue(got)
        self.assertIn("c1", [p["slug"] for p in got])

    def test_context_has_citation(self):
        ctx = self.w.context("记忆系统")
        self.assertIn("[wiki:", ctx)
        self.assertIn("记忆", ctx)

    def test_backlinks(self):
        self.assertIn("mainline", self.w.backlinks("c1"))

    def test_validate(self):
        self.assertEqual(self.w.validate(), [])

    def test_off_returns_empty(self):
        os.environ["PAI_WIKI"] = "off"
        self.assertEqual(self.w.list(), [])
        self.assertEqual(self.w.search("C1"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
