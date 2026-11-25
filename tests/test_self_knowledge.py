# -*- coding: utf-8 -*-
"""递归自我模型 v0：能力表、校准读写、关态拒绝、零新表。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai import self_knowledge as SK  # noqa: E402
from pai.memory import Memory          # noqa: E402


class SelfKnowledgeTest(unittest.TestCase):
    def setUp(self):
        self.mem = Memory(os.path.join(tempfile.mkdtemp(), "sk.db"))
        SK.set_enabled(True)

    def tearDown(self):
        self.mem.close()
        SK.set_enabled(False)

    def test_off_refuses(self):
        SK.set_enabled(False)
        res = SK.report(self.mem)
        self.assertFalse(res["ok"])
        self.assertIn("关", res["reason"])

    def test_capabilities_are_honest(self):
        res = SK.report(self.mem)
        self.assertTrue(res["ok"])
        names = {c["name"]: c for c in res["capabilities"]}
        self.assertEqual(names["有意识 / 有主观感受"]["status"], "没有")
        self.assertEqual(names["读懂产品/现场照片"]["status"], "没有")
        self.assertEqual(names["记住对话与事实"]["status"], "有")

    def test_calibration_unmeasured(self):
        res = SK.report(self.mem)
        self.assertFalse(res["calibration"]["measured"])

    def test_record_roundtrip(self):
        SK.record_calibration(self.mem, 0.18, 50, 0.82)
        cal = SK.calibration(self.mem)
        self.assertTrue(cal["measured"])
        self.assertEqual(cal["brier"], 0.18)
        self.assertEqual(cal["n"], 50)
        self.assertEqual(cal["accuracy"], 0.82)

    def test_record_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            SK.record_calibration(self.mem, 1.5, 50, 0.8)
        with self.assertRaises(ValueError):
            SK.record_calibration(self.mem, 0.2, 0, 0.8)

    def test_bad_meta_is_ignored(self):
        self.mem.set_meta(SK.CALIBRATION_KEY, "{not json")
        cal = SK.calibration(self.mem)
        self.assertFalse(cal["measured"])
        self.assertIn("坏", cal["reason"])

    def test_no_new_table(self):
        def tables():
            return {r[0] for r in self.mem.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        before = tables()
        SK.record_calibration(self.mem, 0.2, 10, 0.7)
        SK.report(self.mem)
        self.assertEqual(before, tables(), "不许新建表")

    def test_capability_declared(self):
        cap = SK.CAPABILITY
        self.assertEqual(cap["switch_env"], SK.ENV_BACKEND)
        self.assertIs(cap["default_on"], False)
        self.assertIs(cap["network"], False)
        self.assertIs(cap["stores_user_text"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
