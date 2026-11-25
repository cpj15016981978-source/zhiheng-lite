# -*- coding: utf-8 -*-
"""B1 v2：接上 M2 分数源后，选择不再被原则垄断；无分条目进 dropped 并说明。"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai import workspace as W  # noqa: E402


def item(kind, i, text, ts="2026-10-21T12:00:00", score=None):
    d = {"kind": kind, "id": i, "text": text, "ts": ts}
    if score is not None:
        d["score"] = score
    return d


class B1v2Test(unittest.TestCase):
    def setUp(self):
        W.set_enabled(True)

    def tearDown(self):
        W.set_enabled(False)

    def _mixed(self):
        rows = []
        for i in range(10):
            rows.append(item("episode", "e%d" % i, "我爷爷1959年在挨饿 %d" % i))
            rows.append(item("fact", "f%d" % i, "用户 家族记忆 爷爷1959年挨饿 %d" % i))
            rows.append(item("principle", "p%d" % i, "爷爷挨饿的事要先核实 %d" % i))
            rows.append(item("culture", "c%d" % i, "论语 讲 爷爷 挨饿 %d" % i))
            rows.append(item("episode", "x%d" % i, "今天天气不错 %d" % i))
        return rows

    def test_selected_types_are_not_monopolized(self):
        res = W.select(self._mixed(), query="爷爷 挨饿", limit=7)
        kinds = [x["kind"] for x in res["selected"]]
        self.assertEqual(len(kinds), 7)
        self.assertLess(kinds.count("principle"), 7, "原则不许垄断")
        self.assertIn("episode", kinds)
        self.assertIn("fact", kinds)

    def test_explicit_score_bypasses_query(self):
        res = W.select([item("episode", 1, "任意", score=0.9)], query="", limit=3)
        self.assertEqual(len(res["selected"]), 1)
        self.assertAlmostEqual(res["selected"][0]["score"], 0.9, places=6)

    def test_zero_score_goes_to_dropped_with_reason(self):
        res = W.select([item("episode", 1, "无关", score=0)], query="爷爷")
        self.assertEqual(res["selected"], [])
        self.assertEqual(res["dropped"][0]["reason"], "no_score")

    def test_empty_query_marks_no_query(self):
        res = W.select([item("episode", 1, "爷爷挨饿")], query="")
        self.assertEqual(res["selected"], [])
        self.assertEqual(res["dropped"][0]["reason"], "no_query")

    def test_unrelated_query_yields_no_selection(self):
        res = W.select([item("episode", 1, "今天天气不错")], query="爷爷 挨饿")
        self.assertEqual(res["selected"], [])

    def test_deterministic(self):
        rows = self._mixed()
        self.assertEqual(W.select(rows, query="爷爷 挨饿", limit=7),
                         W.select(rows, query="爷爷 挨饿", limit=7))

    def test_budget_default_and_limit(self):
        self.assertEqual(W.select(self._mixed(), query="爷爷", limit=None)["budget"], 7)
        self.assertEqual(W.select(self._mixed(), query="爷爷", limit=3)["budget"], 3)

    def test_text_of_fact_shape(self):
        self.assertEqual(W.text_of({"subject": "用户", "predicate": "姓名", "object": "阿明"}),
                         "用户 姓名 阿明")

    def test_malformed_items_skipped(self):
        res = W.select([None, "x", 3], query="爷爷")
        self.assertEqual(res["selected"], [])
        self.assertEqual(res["dropped"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
