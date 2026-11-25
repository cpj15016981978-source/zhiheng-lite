# -*- coding: utf-8 -*-
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agent_layer"))
import policy as P  # noqa: E402


class PolicyTest(unittest.TestCase):
    def test_deny_is_terminal(self):
        d = P.Decision().deny("no")
        d.allow_once("try override")
        self.assertEqual(d.value, P.DENY)
        self.assertFalse(d.is_allowed())
        self.assertEqual(d.reason, "no")

    def test_pipeline_stops_after_deny(self):
        seen = []

        def first(d, ctx):
            seen.append("first")
            d.deny("stop")

        def second(d, ctx):
            seen.append("second")
            d.allow_once("should not run")

        d = P.run_checks([first, second])
        self.assertEqual(seen, ["first"])
        self.assertEqual(d.value, P.DENY)

    def test_no_decision_is_unavailable(self):
        d = P.run_checks([])
        self.assertEqual(d.value, P.UNAVAILABLE)
        self.assertFalse(d.is_allowed())

    def test_missing_caps_is_unavailable(self):
        d = P.authorize("write", None)
        self.assertEqual(d.value, P.UNAVAILABLE)
        self.assertFalse(d.is_allowed())

    def test_authorize_caps(self):
        self.assertTrue(P.authorize("write", ["read", "write"]).is_allowed())
        d = P.authorize("write", ["read"])
        self.assertEqual(d.value, P.DENY)
        self.assertIn("write", d.reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
