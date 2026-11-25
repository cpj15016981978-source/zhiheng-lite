# -*- coding: utf-8 -*-
"""job_worker：dry-run 只领 pending、只写计划，不碰外部。"""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from pai import job_runner as J
from pai import agent_registry as AR
from pai import job_worker as W


class JobWorkerTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.p = mock.patch.object(J, "DB_PATH", pathlib.Path(self.td.name) / "jobs.db")
        self.p.start()
        self.env = mock.patch.dict(os.environ, {"PAI_JOB_RUNNER": "on", "PAI_JOB_WORKER": "on"})
        self.env.start()
        self.p2 = mock.patch.object(AR, "PATH", pathlib.Path(self.td.name) / "agents.json")
        self.p2.start()

    def tearDown(self):
        self.env.stop()
        self.p2.stop()
        self.p.stop()
        self.td.cleanup()

    def test_dry_run_does_not_finish_or_approve(self):
        job = J.create("整理台账", "把未排产单整理一下", agent="codex", cwd="D:/tmp")
        out = W.run_once(dry_run=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["claimed"], 1)
        self.assertEqual(out["mode"], "dry-run")
        row = J.get(job["id"])
        self.assertEqual(row["status"], "pending")
        self.assertFalse(row["approved"])
        self.assertIn("不写工作目录", out["plan"]["would_not"])
        types = [e["type"] for e in J.events(job["id"])]
        self.assertIn("dry_run", types)
        self.assertNotIn("claimed", types)

    def test_dependency_chain(self):
        a = J.create("第一步")
        b = J.create("第二步", depends_on=a["id"])
        J.approve(a["id"]); J.approve(b["id"])
        self.assertEqual(W.run_once(dry_run=False)["job_id"], a["id"])
        self.assertEqual(J.get(b["id"])["status"], "pending")
        self.assertEqual(W.run_once(dry_run=False)["job_id"], b["id"])
        self.assertIn("upstream", J.get(b["id"])["result"])

    def test_no_pending_is_clean(self):
        out = W.run_once(dry_run=True)
        self.assertEqual(out["claimed"], 0)

    def test_worker_disabled_refuses(self):
        J.create("x")
        with mock.patch.dict(os.environ, {"PAI_JOB_WORKER": "off"}):
            with self.assertRaises(RuntimeError):
                W.run_once(dry_run=True)

    def test_real_execute_requires_approval(self):
        job = J.create("x")
        self.assertEqual(W.run_once(dry_run=False)["claimed"], 0)
        J.approve(job["id"])
        out = W.run_once(dry_run=False)
        self.assertTrue(out["ok"])
        self.assertEqual(J.get(job["id"])["status"], "done")

    def test_allowlisted_read_text(self):
        job = J.create("读文件", action_type="read_text",
                       action_payload={"path": str(pathlib.Path(__file__).resolve())})
        J.approve(job["id"], "Boss")
        out = W.run_once(dry_run=False)
        self.assertTrue(out["ok"])
        self.assertEqual(out["result"]["action"], "read_text")
        self.assertIn("job_worker", J.get(job["id"])["result"])

    def test_path_outside_root_rejected(self):
        job = J.create("越界", action_type="read_text",
                       action_payload={"path": r"C:\Windows\win.ini"})
        J.approve(job["id"], "Boss")
        for _ in range(3):
            out = W.run_once(dry_run=False)
            self.assertFalse(out["ok"])
        self.assertEqual(J.get(job["id"])["status"], "failed")
        self.assertEqual(J.get(job["id"])["attempts"], 3)

    def test_revoked_agent_cannot_claim(self):
        a = AR.register("runner", "Boss", ["job:create", "job:claim"])
        job = J.create("x", agent_id=a["id"])
        J.approve(job["id"])
        AR.revoke(a["id"])
        out = W.run_once(dry_run=False)
        self.assertFalse(out["ok"])
        self.assertIn("agent", out["error"])

    def test_unknown_action_rejected_at_create(self):
        with self.assertRaises(ValueError):
            J.create("坏动作", action_type="shell")


if __name__ == "__main__":
    unittest.main()
