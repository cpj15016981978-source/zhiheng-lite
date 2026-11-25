# -*- coding: utf-8 -*-
"""X7 v2：次序（先开关后政策）、默认 23:30、关态逐字、政策总开关。"""
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
from pai import orchestrator as O          # noqa: E402

TRACE = {"spoken": True, "backend": "windows", "chars": 8,
         "truncated": False, "rate": 0, "reason": ""}


def at(h, m=0):
    return datetime.datetime(2026, 10, 21, h, m)


class X7v2Test(unittest.TestCase):
    def setUp(self):
        self.mem = Memory(os.path.join(tempfile.mkdtemp(), "x7.db"))
        self.orch = Orchestrator(self.mem)
        self.mem.upsert_fact("用户", "姓名", "阿明")
        tid = self.mem.add_trigger("manual", "该喝水了", "你上午说过要提醒你")
        self.trig = self.mem.get_trigger(tid)

    def tearDown(self):
        self.mem.close()

    def test_default_quiet_is_2330(self):
        self.assertEqual(O._QUIET_DEFAULT, "23:30-07:00")

    def test_off_state_is_untouched_and_policy_not_consulted(self):
        with mock.patch("pai.orchestrator.M14.speaking_enabled", return_value=False):
            with mock.patch("pai.orchestrator.M14.speak", return_value=dict(TRACE)) as sp:
                with mock.patch("pai.orchestrator._voice_allowed",
                                side_effect=AssertionError("关着时不许问政策")):
                    res = self.orch.proactive(self.trig, now=at(23, 45))
        sp.assert_called_once()
        self.assertEqual(sp.call_args[0][0], "阿明，该喝水了")
        self.assertEqual(res["voice"], TRACE, "关态必须与未打补丁逐字相同")
        self.assertIn("该喝水了", res["message"])

    def test_on_state_quiet_blocks_at_2345(self):
        with mock.patch("pai.orchestrator.M14.speaking_enabled", return_value=True):
            with mock.patch("pai.orchestrator.M14.speak", return_value=dict(TRACE)) as sp:
                res = self.orch.proactive(self.trig, now=at(23, 45))
        sp.assert_not_called()
        self.assertIn("安静时段（23:30-07:00）", res["voice"]["reason"])

    def test_on_state_daytime_speaks(self):
        with mock.patch("pai.orchestrator.M14.speaking_enabled", return_value=True):
            with mock.patch("pai.orchestrator.M14.speak", return_value=dict(TRACE)) as sp:
                res = self.orch.proactive(self.trig, now=at(12, 0))
        sp.assert_called_once()
        self.assertTrue(res["voice"]["spoken"])

    def test_policy_off_allows_at_night(self):
        with mock.patch.dict(os.environ, {"PAI_PROACTIVE_POLICY": "off"}, clear=False):
            with mock.patch("pai.orchestrator.M14.speaking_enabled", return_value=True):
                with mock.patch("pai.orchestrator.M14.speak", return_value=dict(TRACE)) as sp:
                    self.orch.proactive(self.trig, now=at(23, 45))
        sp.assert_called_once()

    def test_quiet_off_allows_at_night(self):
        with mock.patch.dict(os.environ, {"PAI_PROACTIVE_QUIET": "off"}, clear=False):
            with mock.patch("pai.orchestrator.M14.speaking_enabled", return_value=True):
                with mock.patch("pai.orchestrator.M14.speak", return_value=dict(TRACE)) as sp:
                    self.orch.proactive(self.trig, now=at(23, 45))
        sp.assert_called_once()

    def test_daily_cap_blocks(self):
        self.mem.set_meta("proactive.voice.count.2026-10-21", 3)
        with mock.patch("pai.orchestrator.M14.speaking_enabled", return_value=True):
            with mock.patch("pai.orchestrator.M14.speak", return_value=dict(TRACE)) as sp:
                res = self.orch.proactive(self.trig, now=at(12, 0))
        sp.assert_not_called()
        self.assertIn("上限", res["voice"]["reason"])

    def test_min_gap_blocks(self):
        self.mem.set_meta("proactive.voice.last_ts", at(11, 45).isoformat(timespec="seconds"))
        with mock.patch("pai.orchestrator.M14.speaking_enabled", return_value=True):
            with mock.patch("pai.orchestrator.M14.speak", return_value=dict(TRACE)) as sp:
                res = self.orch.proactive(self.trig, now=at(12, 0))
        sp.assert_not_called()
        self.assertIn("不足", res["voice"]["reason"])

    def test_string_now_is_normalized(self):
        with mock.patch("pai.orchestrator.M14.speaking_enabled", return_value=True):
            with mock.patch("pai.orchestrator.M14.speak", return_value=dict(TRACE)) as sp:
                res = self.orch.proactive(self.trig, now="2026-10-21T23:45:00")
        sp.assert_not_called()
        self.assertIn("安静时段", res["voice"]["reason"])

    def test_counter_only_when_spoken(self):
        with mock.patch("pai.orchestrator.M14.speaking_enabled", return_value=True):
            with mock.patch("pai.orchestrator.M14.speak", return_value=dict(TRACE)):
                self.orch.proactive(self.trig, now=at(12, 0))
        self.assertEqual(self.mem.get_meta("proactive.voice.count.2026-10-21"), "1")
        self.assertEqual(self.mem.get_meta("proactive.voice.last_ts"), "2026-10-21T12:00:00")

    def test_invalid_quiet_spec_falls_back(self):
        with mock.patch.dict(os.environ, {"PAI_PROACTIVE_QUIET": "nonsense"}, clear=False):
            with mock.patch("pai.orchestrator.M14.speaking_enabled", return_value=True):
                with mock.patch("pai.orchestrator.M14.speak", return_value=dict(TRACE)) as sp:
                    self.orch.proactive(self.trig, now=at(12, 0))
        sp.assert_called_once()

    def test_capability_env_names_are_documented(self):
        src = Path(O.__file__).read_text(encoding="utf-8")
        for name in ("PAI_PROACTIVE_POLICY", "PAI_PROACTIVE_QUIET",
                     "PAI_PROACTIVE_VOICE_MAX", "PAI_PROACTIVE_VOICE_GAP_MIN"):
            self.assertIn(name, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
