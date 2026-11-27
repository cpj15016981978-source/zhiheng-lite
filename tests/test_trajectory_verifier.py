# -*- coding: utf-8 -*-
"""轨迹、verifier、resume 的最小回归。"""
import os
import pathlib
import tempfile
import unittest

from pai import paths as PATHS
from pai import job_runner as JR
from pai import verifier as VF
from pai import job_worker as JW
from unittest import mock
import webapp


class TrajectoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.old = JR.DB_PATH
        JR.DB_PATH = self.tmp / "jobs.db"
        self.old_ws = PATHS.WORKSPACE
        PATHS.WORKSPACE = self.tmp / "workspace"
        PATHS.WORKSPACE.mkdir()

    def tearDown(self):
        JR.DB_PATH = self.old
        PATHS.WORKSPACE = self.old_ws

    def test_trajectory_has_input_action_result_metrics(self):
        os.environ["PAI_JOB_RUNNER"] = "on"
        plan = JR.create_plan("测试", ["一步"])
        job = JR.create("一步", prompt="输入", action_type="noop", plan_id=plan["id"])
        JR.add_event(job["id"], "metrics", {"duration_ms": 12, "cost": 0.5})
        JR.finish(job["id"], "结果")
        tr = JR.trajectory(plan["id"])
        self.assertTrue(tr["ok"])
        step = tr["steps"][0]
        self.assertEqual("输入", step["input"]["prompt"])
        self.assertEqual("noop", step["input"]["action"])
        self.assertEqual("结果", step["result"])
        self.assertEqual(12, step["duration_ms"])
        self.assertEqual(0.5, step["cost"])

    def test_resume_failed_job(self):
        os.environ["PAI_JOB_RUNNER"] = "on"
        plan = JR.create_plan("测试", ["一步"])
        job = JR.create("一步", plan_id=plan["id"])
        JR.fail(job["id"], "坏掉")
        self.assertEqual("failed", JR.get(job["id"])["status"])
        out = JR.resume_plan(plan["id"])
        self.assertEqual(1, out["resumed"])
        self.assertEqual("pending", JR.get(job["id"])["status"])

    def test_only_explicit_read_steps_are_parallel(self):
        self.assertEqual("read_text", webapp._coord_step("read:a.md")[0])
        self.assertEqual("list_dir", webapp._coord_step("list:.")[0])
        self.assertEqual("agent_step", webapp._coord_step("写报告")[0])

    def test_agent_step_usage_is_recorded_in_result(self):
        job = {"id": "j", "title": "一步", "prompt": "一步", "action_type": "agent_step",
               "action_payload": {"goal": "g", "step": "s"}, "depends_on": "", "step_index": 0,
               "plan_id": ""}
        fake = [{"output": "做完了", "tokens": 7, "cost": 0.03}]
        with mock.patch.object(JW.AIP, "complete", return_value=fake), \
             mock.patch.object(JW.MU, "record") as rec:
            out = JW._execute(job)
        self.assertEqual(0.03, out["cost"])
        self.assertEqual(7, out["usage"]["tokens"])
        rec.assert_called_once()

    def test_verifier_file_and_exit(self):
        (PATHS.WORKSPACE / "a.md").write_text("hello world", encoding="utf-8")
        out = VF.verify({"file_exists": "a.md", "file_contains": [{"path": "a.md", "text": "hello"}],
                         "exit_code": 0}, {"exit_code": 0})
        self.assertTrue(out["ok"], out)
        bad = VF.verify({"file_exists": "missing.md"}, {})
        self.assertFalse(bad["ok"])


if __name__ == "__main__":
    unittest.main()
