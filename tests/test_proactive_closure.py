# -*- coding: utf-8 -*-
"""主动任务闭环：到期续线 → 提案 → 批准 → 写回 → 审计。"""
import datetime
import os
import pathlib
import tempfile
import unittest

from pai import continuity as C
from pai import exec_guard as EG
from pai import job_runner as J
from pai import proactive as PA


class ProactiveClosureTest(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.old = (C.PATH, PA.PATH, PA.LEDGER, J.DB_PATH)
        C.PATH = self.root / "continuity.json"
        PA.PATH = self.root / "proactive.json"
        PA.LEDGER = self.root / "ledger.json"
        J.DB_PATH = self.root / "jobs.db"

    def tearDown(self):
        C.PATH, PA.PATH, PA.LEDGER, J.DB_PATH = self.old

    def test_due_to_approved_writeback_and_audit(self):
        now = datetime.datetime.now().astimezone()
        C.save(profile="work", title="主动闭环", last_step="准备",
               next_step="检查本周任务", due=(now - datetime.timedelta(minutes=1)).isoformat())
        items = PA.refresh("work")
        self.assertEqual(len(items), 1)
        pid = items[0]["id"]
        out = PA.approve(pid, "work")
        self.assertTrue(out["ok"])
        rows = C.all_threads("work")
        self.assertEqual(rows[0]["last_step"], "已批准：检查本周任务")
        audit = EG.audit_recent(10)
        self.assertIn("proactive_execute", [x["action"] for x in audit])


if __name__ == "__main__":
    unittest.main()
