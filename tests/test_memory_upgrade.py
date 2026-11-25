# -*- coding: utf-8 -*-
"""记忆升级：聊天全量分类、混合召回、健康面板。"""
import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock

from pai import conversation_store as CS
from pai import memory as MEM
from pai import memory_candidates as MC


class ChatClassifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = CS.ConversationStore(pathlib.Path(self.tmp.name) / "c.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_append_classifies(self):
        self.store.append_message("work", "c1", "t", {"role": "user", "content": "客户订单报价要改"})
        self.store.append_message("work", "c1", "t", {"role": "ai", "content": "先核对尺寸"})
        st = self.store.message_stats("work")
        self.assertEqual(st["messages"], 2)
        self.assertEqual(st["unclassified"], 0)
        self.assertIn("work", st["kinds"])
        self.assertEqual(st["kinds"]["work"], 1)

    def test_backfill_old_rows(self):
        self.store.conn.execute(
            "insert into conversation_messages(profile,conversation_id,role,content,reasoning,created_at) "
            "values(?,?,?,?,?,?)", ("work", "c2", "user", "明天会议要待办", "", "2026-01-01"))
        self.store.conn.commit()
        n = self.store.backfill_classify("work")
        self.assertEqual(n, 1)
        self.assertEqual(self.store.message_stats("work")["unclassified"], 0)


class CoreMemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = MEM.Memory(str(pathlib.Path(self.tmp.name) / "m.db"))
        self.mem.upsert_fact("报价", "依赖", "尺寸", confidence=2)
        self.mem.write_episode("user", "客户报价要核对尺寸", "s1")

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def test_self_improve_cannot_write_facts(self):
        with self.assertRaises(ValueError):
            self.mem.upsert_fact("rsi", "改", "x", source="rsi")
        with self.assertRaises(ValueError):
            self.mem.write_episode("user", "self output", source="self_apply")

    def test_graph_is_core(self):
        out = self.mem.graph()
        self.assertEqual(len(out["nodes"]), 2)
        self.assertEqual(out["edges"][0]["label"], "依赖")

    def test_hybrid_search_is_core(self):
        store = CS.ConversationStore(pathlib.Path(self.tmp.name) / "c.db")
        try:
            store.append_message("work", "c1", "报价", {"role": "user", "content": "客户报价尺寸又变了"})
            out = self.mem.hybrid_search("报价 尺寸", store, "work")
            self.assertTrue(out["items"])
            sources = {x["source"] for x in out["items"]}
            self.assertIn("conversation", sources)
        finally:
            store.close()

    def test_health_is_core(self):
        self.mem.upsert_fact("报价", "依赖", "尺寸")  # same subject/predicate updates, not duplicate
        out = self.mem.health()
        self.assertTrue(out["ok"])
        self.assertIn("memory", out)

    def test_auto_scan_is_core(self):
        store = CS.ConversationStore(pathlib.Path(self.tmp.name) / "c.db")
        try:
            with mock.patch.object(MC, "PATH", pathlib.Path(self.tmp.name) / "cand.json"):
                store.append_message("work", "c1", "t",
                                     {"role": "user", "content": "记住：以后报价先核对尺寸"})
                out = self.mem.auto_scan(store, "work")
                self.assertEqual(out["scanned"], 1)
                self.assertEqual(out["added_count"], 1)
                self.assertEqual(len(MC.pending()), 1)
                out2 = self.mem.auto_scan(store, "work")
                self.assertEqual(out2["scanned"], 0)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
