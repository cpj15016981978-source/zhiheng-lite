# -*- coding: utf-8 -*-
"""修复复核：续线读失败不覆盖、purge 同步清 FTS、token 默认开且可绑定。"""
import os
import pathlib
import tempfile
import unittest

from pai import approval_token as T
from pai import continuity as C
from pai.memory import Memory


class ReadFailureTest(unittest.TestCase):
    def test_corrupt_continuity_is_not_overwritten(self):
        p = pathlib.Path(tempfile.mkdtemp()) / "continuity.json"
        p.write_text("{broken", encoding="utf-8")
        old = C.PATH
        C.PATH = p
        try:
            with self.assertRaises(C.ContinuityReadError):
                C.save(title="x", next_step="y")
            self.assertEqual(p.read_text(encoding="utf-8"), "{broken")
        finally:
            C.PATH = old

    def test_purge_removes_fts_orphan(self):
        mem = Memory(os.path.join(tempfile.mkdtemp(), "m.db"))
        mem.remember_item("绝密暗号甲乙丙")
        mem.purge("绝密暗号甲乙丙")
        ids = {r[0] for r in mem.conn.execute(
            "SELECT rowid FROM memory_item_fts WHERE memory_item_fts MATCH ?", ("绝密",))}
        self.assertEqual(ids, set())
        mem.close()

    def test_token_default_on_and_bind(self):
        old = os.environ.get("PAI_REQUIRE_APPROVAL_TOKEN")
        os.environ.pop("PAI_REQUIRE_APPROVAL_TOKEN", None)
        try:
            self.assertTrue(T.required())
            t = T.issue("code", "x", bind="abc")
            self.assertFalse(T.consume(t["token"], "code", bind="wrong"))
            self.assertTrue(T.consume(t["token"], "code", bind="abc"))
        finally:
            if old is None:
                os.environ.pop("PAI_REQUIRE_APPROVAL_TOKEN", None)
            else:
                os.environ["PAI_REQUIRE_APPROVAL_TOKEN"] = old


if __name__ == "__main__":
    unittest.main()
