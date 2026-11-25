# -*- coding: utf-8 -*-
"""召回升级 stage 0：全量候选 + 别名表（零依赖，recall 签名不变）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.memory import Memory  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.m = Memory(os.path.join(tempfile.mkdtemp(), "r0.db"))

    def tearDown(self):
        self.m.close()

    def eps(self, query, k=5):
        return [r for r in self.m.recall(query, k=k) if r["item"]["type"] == "episode"]


class TestFullScan(_Base):
    def test_old_memory_beyond_500_is_recalled(self):
        cur = self.m.conn.execute(
            "INSERT INTO episodes(ts, role, content, session_id) VALUES(?,?,?,?)",
            ("2015-01-01T00:00:00", "user", "爷爷1959年挨饿", "ancient"))
        ancient = cur.lastrowid
        for i in range(505):
            self.m.conn.execute(
                "INSERT INTO episodes(ts, role, content, session_id) VALUES(?,?,?,?)",
                ("2026-10-20T12:00:00", "user", "无关内容%d" % i, "s"))
        self.m.conn.commit()
        ids = [r["item"]["id"] for r in self.eps("爷爷挨饿")]
        self.assertIn(ancient, ids, "stage 0 起，窗口外的最老记忆也必须召回")

    def test_recent_still_wins_when_content_same(self):
        now = "2026-10-20T12:00:00"
        new = self.m.write_episode("user", "爷爷讲过挨饿那年")
        cur = self.m.conn.execute(
            "INSERT INTO episodes(ts, role, content, session_id) VALUES(?,?,?,?)",
            ("2019-01-01T12:00:00", "user", "爷爷讲过挨饿那年", "old"))
        old = cur.lastrowid
        self.m.conn.commit()
        ids = [r["item"]["id"] for r in self.eps("爷爷挨饿")]
        self.assertEqual(ids[0], new, "同等相关时新近的在前")
        self.assertEqual(len(ids), 2, "旧的不能被抹掉")


class TestAlias(_Base):
    def test_alias_query_hits_original_wording(self):
        self.m.write_episode("user", "我爷爷1959年在挨饿")
        rows = self.eps("祖父")
        self.assertTrue(rows, "查「祖父」应命中记忆里的「爷爷」")

    def test_original_query_hits_alias_wording(self):
        self.m.write_episode("user", "我祖父1959年在挨饿")
        rows = self.eps("爷爷")
        self.assertTrue(rows, "查「爷爷」应命中记忆里的「祖父」")

    def test_alias_applies_to_facts(self):
        self.m.upsert_fact("用户", "职业", "床垫厂管生产")
        hits = [r for r in self.m.recall("工作") if r["item"]["type"] == "fact"]
        self.assertTrue(hits, "别名表对 fact 也要生效")

    def test_alias_does_not_invent_hits(self):
        self.m.write_episode("user", "今天看床垫弹簧")
        self.assertEqual(self.eps("祖父"), [], "别名只能扩展同义，不能凭空召回")

    def test_unrelated_query_still_empty(self):
        self.m.write_episode("user", "今天天气不错")
        self.assertEqual(self.m.recall("床垫弹簧软硬"), [])


class TestContract(_Base):
    def test_signature_and_bounds(self):
        self.m.write_episode("user", "爷爷1959年挨饿")
        rows = self.m.recall("爷爷挨饿", k=3, now="2026-10-20T12:00:00")
        self.assertLessEqual(len(rows), 3)
        for r in rows:
            self.assertGreaterEqual(r["relevance"], 0.0)
            self.assertLessEqual(r["relevance"], 1.0)
            self.assertIn(r["item"]["type"], ("episode", "fact"))

    def test_empty_query_returns_nothing(self):
        self.m.write_episode("user", "爷爷1959年挨饿")
        self.assertEqual(self.m.recall("   "), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
