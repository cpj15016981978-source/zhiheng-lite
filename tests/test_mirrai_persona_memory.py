# -*- coding: utf-8 -*-
"""Mirrai 记忆/人设蒸馏移植测试。"""
import datetime
import unittest

from pai import mirrai_memory as M
from pai import persona_distill as P
from unittest import mock
import pathlib
import tempfile


class MemoryTest(unittest.TestCase):
    def test_score_prefers_anchor_and_match(self):
        now = datetime.datetime.now()
        a = {"title": "生日是 5 月 2 日", "category": "anchor", "at": now.isoformat()}
        b = {"title": "今天吃了面", "category": "memory", "at": now.isoformat()}
        self.assertGreater(M.score("生日", a, now), M.score("生日", b, now))

    def test_recall_filters_zero(self):
        rows = M.recall("完全不存在的关键词", now=datetime.datetime.now())
        self.assertEqual(rows, [])


class PersonaTest(unittest.TestCase):
    def test_seven_layers(self):
        msgs = [{"content": "今天好累啊，想你了"}, {"content": "哈哈没事，我在"},
                {"content": "别走，你在吗"}]
        d = P.distill(msgs, name="测试")
        for k in ("personality", "speechStyle", "catchphrases", "attachmentStyle",
                  "loveLanguage", "conflictStyle", "triggeredMoments"):
            self.assertIn(k, d)
        self.assertTrue(d["catchphrases"])
        self.assertIn("焦虑", d["attachmentStyle"])

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            P.distill([], name="空")

    def test_llm_failure_falls_back(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(P, "OUT", pathlib.Path(d)):
            with mock.patch.object(P, "llm_distill", side_effect=RuntimeError("down")):
                out = P.import_text("今天很累\n想休息", name="测试", use_llm=True)
            self.assertTrue(out["ok"])
            self.assertEqual(out["data"].get("method"), "heuristic_fallback")

    def test_llm_incomplete_falls_back(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(P, "OUT", pathlib.Path(d)):
            with mock.patch.object(P, "llm_distill", return_value={"name": "x"}):
                out = P.import_text("今天很累\n想休息", name="测试", use_llm=True)
            self.assertEqual(out["data"].get("method"), "heuristic_fallback")


if __name__ == "__main__":
    unittest.main()
