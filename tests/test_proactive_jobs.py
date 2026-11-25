# -*- coding: utf-8 -*-
"""主动推进 → 作业：提案时挂起，批准后恢复并结项。"""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from pai import job_runner as J
from pai import proactive as P


class ProactiveJobsTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        d = pathlib.Path(self.td.name)
        self.p = mock.patch.object(J, "DB_PATH", d / "jobs.db")
        self.p.start()
        self.p2 = mock.patch.object(P, "PATH", d / "proactive.json")
        self.p2.start()
        self.p3 = mock.patch.object(P, "LEDGER", d / "ledger.json")
        self.p3.start()
        self.env = mock.patch.dict(os.environ, {"PAI_JOB_RUNNER": "on"})
        self.env.start()
        self.thread = {"id": "t1", "title": "冒烟线", "next_step": "检查订单",
                       "due": "2000-01-01T00:00:00+08:00"}
        self.due = mock.patch.object(P.CT, "due", return_value=[self.thread])
        self.due.start()
        self.all = mock.patch.object(P.CT, "all_threads", return_value=[self.thread])
        self.all.start()
        self.save = mock.patch.object(P.CT, "save", return_value=None)
        self.save.start()

    def tearDown(self):
        for x in (self.save, self.all, self.due, self.env, self.p3, self.p2, self.p):
            x.stop()
        self.td.cleanup()

    def test_proposal_creates_waiting_job_and_approve_finishes_it(self):
        props = P.proposals("work")
        self.assertEqual(len(props), 1)
        self.assertTrue(props[0].get("job_id"))
        jobs = J.list_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["status"], "waiting")
        out = P.approve(props[0]["id"], "work")
        self.assertTrue(out["ok"])
        job = J.get(props[0]["job_id"])
        self.assertEqual(job["status"], "done")
        types = [e["type"] for e in J.events(job["id"])]
        self.assertIn("resumed", types)
        self.assertIn("finished", types)

    def test_reject_fails_job(self):
        props = P.proposals("work")
        P.reject(props[0]["id"])
        self.assertEqual(J.get(props[0]["job_id"])["status"], "failed")


if __name__ == "__main__":
    unittest.main()
