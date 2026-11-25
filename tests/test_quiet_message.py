# -*- coding: utf-8 -*-
"""安静时段的坏值提示：必须印“实际生效的时段”，不印用户写的乱码。"""
import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.memory import Memory              # noqa: E402
from pai.orchestrator import Orchestrator  # noqa: E402


class QuietMessageTest(unittest.TestCase):
    def setUp(self):
        self.mem = Memory(os.path.join(tempfile.mkdtemp(), "qm.db"))
        self.orch = Orchestrator(self.mem)
        self.mem.upsert_fact("用户", "姓名", "阿明")
        tid = self.mem.add_trigger("manual", "该喝水了", "你上午说过")
        self.trig = self.mem.get_trigger(tid)

    def tearDown(self):
        self.mem.close()

    def test_bad_spec_prints_effective_default(self):
        with mock.patch.dict(os.environ, {"PAI_PROACTIVE_QUIET": "nonsense"}, clear=False):
            with mock.patch("pai.orchestrator.M14.speaking_enabled", return_value=True):
                with mock.patch("pai.orchestrator.M14.speak") as sp:
                    res = self.orch.proactive(
                        self.trig, now=datetime.datetime(2026, 10, 21, 23, 45))
        sp.assert_not_called()
        reason = res["voice"]["reason"]
        self.assertIn("23:30-07:00", reason)
        self.assertNotIn("nonsense", reason)

    def test_off_is_not_a_quiet_hour(self):
        with mock.patch.dict(os.environ, {"PAI_PROACTIVE_QUIET": "off"}, clear=False):
            with mock.patch("pai.orchestrator.M14.speaking_enabled", return_value=True):
                with mock.patch("pai.orchestrator.M14.speak",
                                return_value={"spoken": True, "backend": "windows",
                                              "chars": 4, "truncated": False,
                                              "rate": 0, "reason": ""}) as sp:
                    self.orch.proactive(
                        self.trig, now=datetime.datetime(2026, 10, 21, 23, 45))
        sp.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
