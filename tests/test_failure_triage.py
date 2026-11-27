# -*- coding: utf-8 -*-
"""失败先验评分器：先问任务/标准/harness，再怪模型。"""
import unittest

from pai.self_review import difficulty_band, triage_failure


class FailureTriageTest(unittest.TestCase):
    def test_missing_criteria_is_not_model_fault(self):
        out = triage_failure("写周报", [], "正文", "没通过")
        self.assertEqual("criteria_bad", out["verdict"])
        self.assertFalse(out["ok"])

    def test_unvalidated_scorer_is_not_model_fault(self):
        out = triage_failure("写周报", ["文件存在"], "正文", "失败", known_good=False)
        self.assertEqual("criteria_bad", out["verdict"])

    def test_valid_task_criteria_and_output_is_model_fault(self):
        out = triage_failure("写周报", ["文件存在"], "正文", "", known_good=True)
        self.assertEqual("model_bad", out["verdict"])
        self.assertTrue(out["ok"])

    def test_difficulty_band(self):
        self.assertEqual("too_easy", difficulty_band(10, 10)["band"])
        self.assertEqual("too_hard", difficulty_band(0, 10)["band"])
        self.assertEqual("informative", difficulty_band(5, 10)["band"])

    def test_harness_error_is_harness_fault(self):
        out = triage_failure("写周报", ["文件存在"], "正文", "ImportError: missing", known_good=True)
        self.assertEqual("harness_bad", out["verdict"])


if __name__ == "__main__":
    unittest.main()
