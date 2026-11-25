# -*- coding: utf-8 -*-
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai.memory import Memory  # noqa: E402


class ForgetReasonTest(unittest.TestCase):
    def setUp(self):
        self.db = pathlib.Path(tempfile.mkdtemp()) / "m.db"
        self.m = Memory(str(self.db))

    def tearDown(self):
        self.m.conn.close()

    def test_forget_with_reason_keeps_audit_not_fact(self):
        self.m.upsert_fact("用户", "备注", "一条会被删掉的事实")
        fid = self.m.all_facts()[0]["id"]
        self.assertTrue(self.m.forget_fact(fid, "测试删除理由"))
        self.assertEqual(self.m.all_facts(), [])
        self.assertEqual(self.m.fact_history(), [])
        row = self.m.conn.execute(
            "SELECT value FROM meta WHERE key LIKE ?", ("forget:%",)).fetchone()
        self.assertIsNotNone(row)
        self.assertIn("测试删除理由", row["value"])
        self.assertNotIn("一条会被删掉的事实", row["value"])

    def test_forget_without_reason_still_removes(self):
        self.m.upsert_fact("用户", "备注", "无理由")
        fid = self.m.all_facts()[0]["id"]
        self.assertTrue(self.m.forget_fact(fid))
        self.assertEqual(self.m.all_facts(), [])
        row = self.m.conn.execute(
            "SELECT value FROM meta WHERE key LIKE ?", ("forget:%",)).fetchone()
        self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
