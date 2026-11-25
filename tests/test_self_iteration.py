# -*- coding: utf-8 -*-
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai.self_iteration import SelfIteration  # noqa: E402


class SelfIterationTest(unittest.TestCase):
    def setUp(self):
        self.si = SelfIteration()

    def test_cannot_apply_without_approval(self):
        iid = self.si.propose("prompt", "system", "less verbose")
        with self.assertRaises(ValueError):
            self.si.apply(iid, lambda it: "x")

    def test_cannot_self_approve(self):
        iid = self.si.propose("prompt", "system", "less verbose")
        self.si.test(iid, lambda it: True)
        with self.assertRaises(ValueError):
            self.si.approve(iid, "self")
        with self.assertRaises(ValueError):
            self.si.approve(iid, "agent")

    def test_goal_identity_permission_are_forbidden(self):
        for kind in ("goal", "identity", "permission", "weights"):
            with self.assertRaises(ValueError):
                self.si.propose(kind, "x", "y")

    def test_code_requires_patch(self):
        with self.assertRaises(ValueError):
            self.si.propose("code", "agentd.py", "fix")
        iid = self.si.propose("code", "agentd.py", "fix", patch="--- a\n+++ b\n")
        self.assertEqual(self.si.get(iid)["level"], "L2")

    def test_full_l0_flow_with_rollback(self):
        iid = self.si.propose("prompt", "system", "less verbose")
        self.assertTrue(self.si.test(iid, lambda it: True))
        self.assertTrue(self.si.approve(iid, "Boss"))
        self.assertEqual(self.si.apply(iid, lambda it: "applied"), "applied")
        self.assertEqual(self.si.get(iid)["status"], "applied")
        self.assertEqual(self.si.rollback(iid, lambda it: "restored"), "restored")
        self.assertEqual(self.si.get(iid)["status"], "rolled_back")

    def test_failed_test_cannot_approve(self):
        iid = self.si.propose("tool", "search", "add timeout")
        self.assertFalse(self.si.test(iid, lambda it: False))
        self.assertEqual(self.si.get(iid)["status"], "test_failed")
        with self.assertRaises(ValueError):
            self.si.approve(iid, "Boss")

    def test_history_is_recorded(self):
        iid = self.si.propose("prompt", "system", "x")
        self.si.test(iid, lambda it: True)
        self.si.approve(iid, "Boss")
        ops = [h["op"] for h in self.si.get(iid)["history"]]
        self.assertEqual(ops, ["propose", "test", "approve"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
