# -*- coding: utf-8 -*-
"""记忆去重与出网白名单收口。"""
import os
import pathlib
import tempfile
import unittest

from pai import network_policy as NP
from pai.memory import Memory


class DedupeEgressTest(unittest.TestCase):
    def test_memory_dedupe_marks_items(self):
        m = Memory(os.path.join(tempfile.mkdtemp(), "m.db"))
        for _ in range(2):
            m.conn.execute(
                "INSERT INTO memory_item(kind,text,status,created_at,valid_from,hash) "
                "VALUES('note','重复记忆','active','2026-01-01','2026-01-01','h')")
        m.conn.commit()
        out = m.dedupe()
        self.assertGreaterEqual(out["items_superseded"], 1)
        active = m.conn.execute(
            "SELECT count(*) FROM memory_item WHERE status='active'").fetchone()[0]
        self.assertEqual(active, 1)
        m.close()

    def test_allow_host_adds(self):
        old = NP.FLAG
        NP.FLAG = pathlib.Path(tempfile.mkdtemp()) / "network.json"
        try:
            NP.allow_host("example.com")
            self.assertTrue(NP.is_host_allowed("https://example.com/x"))
            self.assertFalse(NP.is_host_allowed("https://evil.com/x"))
        finally:
            NP.FLAG = old


if __name__ == "__main__":
    unittest.main()
