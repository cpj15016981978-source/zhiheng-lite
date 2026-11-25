# -*- coding: utf-8 -*-
"""M14 接主动推送：测试（不真出声，patch speak）。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.memory import Memory              # noqa: E402
from pai.orchestrator import Orchestrator  # noqa: E402

VOICE_TRACE = {"spoken": True, "backend": "windows", "chars": 8,
               "truncated": False, "rate": 0, "reason": ""}


class ProactiveVoiceTest(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "pv.db")
        self.mem = Memory(self.db)
        self.orch = Orchestrator(self.mem)
        self.mem.upsert_fact("用户", "姓名", "阿明")

    def tearDown(self):
        self.mem.close()

    def _manual(self):
        tid = self.mem.add_trigger("manual", "该喝水了", "你上午说过要提醒你")
        return self.mem.get_trigger(tid)

    def test_speaks_short_text_not_meta(self):
        trig = self._manual()
        with mock.patch("pai.orchestrator.M14.speak", return_value=dict(VOICE_TRACE)) as sp:
            res = self.orch.proactive(trig)
        sp.assert_called_once()
        said = sp.call_args[0][0]
        self.assertEqual(said, "阿明，该喝水了", "只念称呼+模板，不念元信息")
        self.assertTrue(res["voice"]["spoken"])
        self.assertIn("（我主动找你的理由", res["message"], "文字里仍保留理由")

    def test_voice_off_still_writes_outbox(self):
        trig = self._manual()
        off = dict(VOICE_TRACE, spoken=False, reason="语音开关是关的")
        with mock.patch("pai.orchestrator.M14.speak", return_value=off) as sp:
            res = self.orch.proactive(trig)
        sp.assert_called_once()
        self.assertFalse(res["voice"]["spoken"])
        self.assertEqual(len(self.mem.read_outbox()), 1, "关掉声音不影响落库")

    def test_write_failure_does_not_speak(self):
        trig = self._manual()
        with mock.patch.object(self.mem, "push_outbox", side_effect=RuntimeError("db down")):
            with mock.patch("pai.orchestrator.M14.speak") as sp:
                with self.assertRaises(RuntimeError):
                    self.orch.proactive(trig)
        sp.assert_not_called()

    def test_voice_trace_has_no_original_text(self):
        trig = self._manual()
        with mock.patch("pai.orchestrator.M14.speak", return_value=dict(VOICE_TRACE)):
            res = self.orch.proactive(trig)
        blob = json.dumps(res["voice"], ensure_ascii=False)
        self.assertNotIn("该喝水了", blob)
        self.assertNotIn("阿明", blob)

    def test_tick_speaks_and_still_claims(self):
        tid = self.mem.add_trigger(
            "date", "记得吃药", "医生说要按时吃", "2026-10-21 09:00")
        with mock.patch("pai.orchestrator.M14.speak", return_value=dict(VOICE_TRACE)) as sp:
            out = self.orch.tick(now="2026-10-21T09:30:00")
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["voice"]["spoken"])
        sp.assert_called_once()
        self.assertIsNotNone(self.mem.get_trigger(tid)["last_fired_ts"],
                             "出声失败/成功都不影响认领")

    def test_voice_failure_does_not_block_claim(self):
        tid = self.mem.add_trigger(
            "date", "记得吃药", "医生说要按时吃", "2026-10-21 09:00")
        fail = dict(VOICE_TRACE, spoken=False, reason="念不出来（已静默跳过）")
        with mock.patch("pai.orchestrator.M14.speak", return_value=fail):
            out = self.orch.tick(now="2026-10-21T09:30:00")
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0]["voice"]["spoken"])
        self.assertIsNotNone(self.mem.get_trigger(tid)["last_fired_ts"])

    def test_message_content_unchanged(self):
        trig = self._manual()
        with mock.patch("pai.orchestrator.M14.speak", return_value=dict(VOICE_TRACE)):
            res = self.orch.proactive(trig)
        self.assertIn("该喝水了", res["message"])
        self.assertIn("你上午说过要提醒你", res["message"])
        self.assertEqual(len(self.mem.read_outbox()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
