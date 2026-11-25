# -*- coding: utf-8 -*-
"""Muse 合并核心：agent_step 产出文字与文件提案。"""
import pathlib
import tempfile
import unittest
from unittest import mock

import os
from unittest import mock

from pai import job_runner as JR
from pai import job_worker as JW
from pai import unified as U


class AgentStepTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.old = JR.DB_PATH
        JR.DB_PATH = self.tmp / "jobs.db"

    def tearDown(self):
        JR.DB_PATH = self.old

    def test_plan_next_runs_step_and_waits_for_file_approval(self):
        os.environ["PAI_JOB_RUNNER"] = "on"
        os.environ["PAI_JOB_WORKER"] = "on"
        plan = JR.create_plan("写周报", ["输出周报文件"])
        U.handle("规划：执行 " + plan["id"])
        fake = [{"output": "结果\n[写文件: weekly.md]\n# 周报\n[/写文件]"}]
        with mock.patch.object(JW.AIP, "complete", return_value=fake):
            U.handle("规划：下一步 " + plan["id"])
        jobs = JR.plan_jobs(plan["id"])
        self.assertEqual(jobs[0]["status"], "done")
        self.assertEqual(jobs[0]["action_type"], "agent_step")
        files = JR.list_file_proposals("pending")
        self.assertTrue(any(x["filename"] == "weekly.md" and x["plan_id"] == plan["id"] for x in files))

    def test_step_without_tag_falls_back_to_pending_file(self):
        job = {"id": "j2", "title": "写周报文件", "action_type": "agent_step",
               "plan_id": "p2", "step_index": 0,
               "action_payload": {"goal": "写周报", "step": "输出周报文件"}}
        with mock.patch.object(JW.AIP, "complete", return_value=[{"output": "# 周报\n正文"}]):
            out = JW._execute(job)
        self.assertEqual(out["file_proposals"][0]["filename"], "plan-step-1.md")
        self.assertEqual(JR.list_file_proposals("pending")[0]["plan_id"], "p2")

    def test_step_creates_file_proposal(self):
        job = {"id": "j1", "title": "写周报", "action_type": "agent_step",
               "plan_id": "p1", "step_index": 2,
               "action_payload": {"goal": "写周报", "step": "输出周报文件"}}
        fake = [{"output": "完成。\n[写文件: 周报.md]\n# 周报\n[/写文件]"}]
        with mock.patch.object(JW.AIP, "complete", return_value=fake):
            out = JW._execute(job)
        self.assertTrue(out["ok"])
        self.assertEqual(out["action"], "agent_step")
        self.assertEqual(len(out["file_proposals"]), 1)
        rows = JR.list_file_proposals("pending")
        self.assertEqual(rows[0]["plan_id"], "p1")
        self.assertEqual(rows[0]["step_index"], 2)


if __name__ == "__main__":
    unittest.main()
