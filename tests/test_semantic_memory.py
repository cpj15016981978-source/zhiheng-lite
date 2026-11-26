# -*- coding: utf-8 -*-
"""语义记忆搜索：local-hash 兜底可用，混合搜索包含 semantic 来源。"""
import os
import pathlib
import tempfile
import unittest

from pai.memory import Memory


class SemanticMemoryTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("PAI_EMBED_BASE_URL", None)
        self.mem = Memory(str(pathlib.Path(tempfile.mkdtemp()) / "m.db"))
        self.mem.remember_item("周报：本周完成审批链修复", kind="note")
        self.mem.remember_item("合同：甲方是东宝床垫厂", kind="note")

    def tearDown(self):
        self.mem.close()

    def test_semantic_recall(self):
        rows = self.mem.recall_items_semantic("周报进展", 5)
        self.assertTrue(rows)
        self.assertIn("周报", rows[0].get("text", ""))

    def test_hybrid_does_not_return_irrelevant_semantic_junk(self):
        out = self.mem.hybrid_search("周报", limit=10)
        texts = [x.get("text", "") for x in out["items"]]
        self.assertTrue(any("周报" in t for t in texts))
        self.assertFalse(any("合同" in t for t in texts), "无关的语义行不应混进来")


if __name__ == "__main__":
    unittest.main()
