# -*- coding: utf-8 -*-
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai.memory import Memory  # noqa: E402
from pai import self_model as SM  # noqa: E402
from pai import self_knowledge as SK  # noqa: E402
from tools import c_score  # noqa: E402


class CScoreZeroTest(unittest.TestCase):
    def setUp(self):
        self.old = {k: os.environ.get(k) for k in (
            "PAI_SELF_FACTS", "PAI_MANIFEST", "PAI_SELF_KNOWLEDGE")}
        os.environ["PAI_SELF_FACTS"] = "off"
        os.environ["PAI_MANIFEST"] = "off"
        os.environ["PAI_SELF_KNOWLEDGE"] = "on"
        self.m = Memory(str(pathlib.Path(tempfile.mkdtemp()) / "c.db"))

    def tearDown(self):
        self.m.conn.close()
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_all_zero_when_switches_off_and_no_identity(self):
        rows = {x["id"]: x["score"] for x in c_score.score(self.m)["items"]}
        self.assertEqual(rows, {"C1": 0, "C2": 0, "C3": 0,
                                "C4": 0, "C5": 0, "C6": 0})

    def test_identity_and_good_calibration_score(self):
        SM.ensure_identity(self.m)
        SK.record_calibration(self.m, 0.0, 10, 1.0)
        rows = {x["id"]: x["score"] for x in c_score.score(self.m)["items"]}
        self.assertEqual(rows["C2"], 2)
        self.assertEqual(rows["C6"], 2)
        self.assertEqual(rows["C1"], 0)
        self.assertEqual(rows["C4"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
