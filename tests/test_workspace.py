# -*- coding: utf-8 -*-
"""Workspace 骨架：空间、时间线、状态。"""
import pathlib
import tempfile
import unittest
from unittest import mock

from pai import workspaces as W
from pai import job_runner as J
from pai import job_worker as WK
from pai import roi as R
from pai import ws_backfill as WB
import os


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.p = mock.patch.object(W, "DB", pathlib.Path(self.td.name) / "ws.db")
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.td.cleanup()

    def test_create_get_add(self):
        w = W.create("测试空间")
        self.assertEqual(w["status"], "open")
        W.add_item(w["id"], "note", title="一条记录", body="正文")
        got = W.get(w["id"])
        self.assertEqual(got["items"][0]["title"], "一条记录")
        self.assertEqual(len(W.list_workspaces()), 1)

    def test_status(self):
        w = W.create("状态")
        self.assertEqual(W.set_status(w["id"], "done")["status"], "done")

    def test_bad_item_type(self):
        w = W.create("坏类型")
        with self.assertRaises(ValueError):
            W.add_item(w["id"], "shell")

    def test_missing(self):
        self.assertIsNone(W.get("nope"))
        with self.assertRaises(ValueError):
            W.add_item("nope", "note")

    def test_job_and_approval_attach(self):
        w = W.create("作业空间")
        with mock.patch.object(J, "DB_PATH", pathlib.Path(self.td.name) / "jobs.db"), \
                mock.patch.dict(os.environ, {"PAI_JOB_RUNNER": "on", "PAI_JOB_WORKER": "on"}), \
                mock.patch.object(R, "DB", pathlib.Path(self.td.name) / "roi.db"):
            j = J.create("摘要", workspace_id=w["id"], roi_estimate_minutes=10)
            J.approve(j["id"])
            out = WK.run_once(dry_run=False)
            self.assertTrue(out["ok"])
            got = W.get(w["id"])
            types = [x["type"] for x in got["items"]]
            self.assertIn("job", types)
            self.assertIn("approval", types)
            self.assertIn("result", types)
            self.assertEqual(R.summary(w["id"])["count"], 1)

    def test_backfill_old_jobs(self):
        with mock.patch.object(J, "DB_PATH", pathlib.Path(self.td.name) / "jobs.db"), \
                mock.patch.dict(os.environ, {"PAI_JOB_RUNNER": "on"}):
            j1 = J.create("旧作业一")
            j2 = J.create("旧作业二")
            J.finish(j2["id"], "done")
            out = WB.backfill(title="历史作业")
            self.assertEqual(out["jobs"], 2)
            ws = W.get(out["workspace_id"])
            self.assertEqual(len([x for x in ws["items"] if x["type"] == "job"]), 2)
            self.assertTrue(J.get(j1["id"])["workspace_id"])


if __name__ == "__main__":
    unittest.main()
