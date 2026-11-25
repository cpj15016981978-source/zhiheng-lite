# -*- coding: utf-8 -*-
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai.memory import Memory  # noqa: E402
from pai.memory_brain import MemoryBrain  # noqa: E402


class BrainTest(unittest.TestCase):
    def setUp(self):
        os.environ["PAI_MEMORY_BRAIN"] = "on"
        self.mem = Memory(str(pathlib.Path(tempfile.mkdtemp()) / "m.db"))
        self.brain = MemoryBrain(self.mem)

    def tearDown(self):
        self.mem.close()
        os.environ.pop("PAI_MEMORY_BRAIN", None)

    def test_current_and_old_conflict(self):
        old = self.brain.remember(
            "当前主线是外贸 AI", subject="助手", predicate="当前主线",
            source="旧记录", importance=0.9, confidence=0.9)
        new = self.brain.remember(
            "当前主线是吹雪 Desktop", subject="助手", predicate="当前主线",
            source="新记录", importance=1.0, confidence=1.0, supersedes=old)
        out = self.brain.recall("当前主线", k=5)
        self.assertTrue(out["found"])
        self.assertEqual(out["items"][0]["id"], new)
        self.assertEqual(out["items"][0]["status"], "active")
        self.assertEqual(len(out["conflicts"]), 1)
        self.assertEqual(out["conflicts"][0]["old"][0]["id"], old)
        self.assertIn("现行", out["context"])

    def test_semantic_recall_without_http_falls_back(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {"PAI_MEMORY_SEMANTIC": "on", "PAI_EMBED_BASE_URL": ""}):
            self.brain.remember("工厂对账先统一订单号", source="test", importance=1.0)
            out = self.brain.recall("对账 订单号", k=3)
        self.assertTrue(out["found"])
        self.assertIn("订单号", out["context"])

    def test_notebook_roundtrip(self):
        self.assertEqual(self.brain.notebook("s1"), {})
        self.brain.update_notebook("s1", goal="修记忆", step="写测试")
        got = self.brain.notebook("s1")
        self.assertEqual(got["goal"], "修记忆")
        self.assertEqual(got["step"], "写测试")

    def test_forget_removes(self):
        i = self.brain.remember("临时密码 abc", source="test")
        self.assertTrue(self.mem.forget_item(i))
        self.assertIsNone(self.mem.open_item(i))
        self.assertFalse(self.brain.recall("临时密码")["found"])

    def test_no_match_returns_empty(self):
        out = self.brain.recall("完全不存在的档案 xyz")
        self.assertFalse(out["found"])
        self.assertEqual(out["context"], "")

    def test_purge_clears_new_tables(self):
        i = self.brain.remember("要被清除的秘密词", source="test")
        counts = self.mem.purge("秘密词")
        self.assertGreaterEqual(counts["memory_item"], 1)
        self.assertIsNone(self.mem.open_item(i))


if __name__ == "__main__":
    unittest.main(verbosity=2)
