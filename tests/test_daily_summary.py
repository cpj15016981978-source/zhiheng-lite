# -*- coding: utf-8 -*-
"""每日发布记录摘要：08:00 建 pending，批准后 summarize_file。"""
import datetime
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from pai import job_runner as J
from pai import job_worker as W


class DailySummaryTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = pathlib.Path(self.td.name)
        self.file = self.root / "发布记录.md"
        self.file.write_text("2026-11-17 小红书已发；抖音待发。", encoding="utf-8")
        self.p = mock.patch.object(J, "DB_PATH", self.root / "jobs.db")
        self.p.start()
        self.env = mock.patch.dict(os.environ, {
            "PAI_JOB_RUNNER": "on", "PAI_JOB_WORKER": "on",
            "PAI_DAILY_SUMMARY": "on", "PAI_DAILY_SUMMARY_PATH": str(self.file),
            "PAI_JOB_ROOTS": str(self.root),
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.p.stop()
        self.td.cleanup()

    def test_daily_job_is_pending_and_once(self):
        now = datetime.datetime(2026, 11, 18, 8, 0).astimezone()
        a = W.ensure_daily_job(now)
        self.assertIsNotNone(a)
        self.assertEqual(a["status"], "pending")
        self.assertFalse(a["approved"])
        self.assertIsNone(W.ensure_daily_job(now))

    def test_summarize_file_uses_model_after_approval(self):
        job = J.create("摘要", action_type="summarize_file",
                       action_payload={"path": str(self.file)})
        J.approve(job["id"], "Boss")
        with mock.patch.object(W.M10, "complete", return_value=[{"output": "今天发抖音，别发收入数字"}]):
            out = W.run_once(dry_run=False)
        self.assertTrue(out["ok"])
        self.assertIn("今天发抖音", J.get(job["id"])["result"])

    def test_daily_before_8_does_nothing(self):
        now = datetime.datetime(2026, 11, 18, 7, 59).astimezone()
        self.assertIsNone(W.ensure_daily_job(now))

    def test_grace_until_10_and_no_late_evening(self):
        a = datetime.datetime(2026, 11, 18, 9, 59).astimezone()
        self.assertIsNotNone(W.ensure_daily_job(a))
        b = datetime.datetime(2026, 11, 19, 10, 1).astimezone()
        self.assertIsNone(W.ensure_daily_job(b))


if __name__ == "__main__":
    unittest.main()
