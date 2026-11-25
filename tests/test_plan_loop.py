# -*- coding: utf-8 -*-
"""规划循环与文件提案：结构、联动字段、自我检查存储。"""
import os
import pathlib
import tempfile
import unittest

from pai import job_runner as JR


class PlanLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.old = JR.DB_PATH
        JR.DB_PATH = self.tmp / "jobs.db"

    def tearDown(self):
        JR.DB_PATH = self.old

    def test_plan_jobs_and_reflection(self):
        os.environ["PAI_JOB_RUNNER"] = "on"
        from pai import unified as U
        plan = JR.create_plan("写周报", ["读取 a.txt", "列出 目录"])
        U.handle("规划：执行 " + plan["id"])
        jobs = JR.plan_jobs(plan["id"])
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["action_type"], "read_text")
        self.assertEqual(jobs[1]["action_type"], "list_dir")
        self.assertEqual(jobs[1]["depends_on"], jobs[0]["id"])
        JR.set_plan_reflection(plan["id"], "通过")
        self.assertEqual(JR.get_plan(plan["id"])["reflection"], "通过")

    def test_file_proposal_links_plan(self):
        out = JR.create_file_proposal("周报.md", "内容", plan_id="p1", step_index=2)
        rows = JR.list_file_proposals("pending")
        row = next(x for x in rows if x["id"] == out["id"])
        self.assertEqual(row["plan_id"], "p1")
        self.assertEqual(row["step_index"], 2)

    def test_rejects_bad_file(self):
        for name in ("../x.txt", "x.exe", ".hidden"):
            with self.assertRaises(ValueError):
                JR.create_file_proposal(name, "x")


if __name__ == "__main__":
    unittest.main()
