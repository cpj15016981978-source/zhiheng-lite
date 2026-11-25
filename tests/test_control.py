# -*- coding: utf-8 -*-
"""X6 控制通道：PID 身份核验 / proactive；不落用户文本。"""
import pathlib
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pai import voice, orchestrator as O  # noqa: E402


class ControlTest(unittest.TestCase):
    def setUp(self):
        voice.clear_voice_state()
        voice.clear_proactive()

    def tearDown(self):
        voice.clear_voice_state()
        voice.clear_proactive()

    def test_proactive_roundtrip(self):
        voice.set_proactive(False)
        self.assertFalse(O._policy_enabled())
        voice.set_proactive(True)
        self.assertTrue(O._policy_enabled())

    def test_stop_does_not_kill_mismatched_pid(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            voice.set_voice_state({"pid": proc.pid, "exe": "C:\\fake.exe",
                                   "cmdline": "fake", "created": "x"})
            self.assertFalse(voice.stop())
            self.assertIsNone(proc.poll(), "身份对不上时不许杀")
            self.assertIsNone(voice.get_voice_state())
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_stop_kills_matching_pid(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            info = voice._proc_identity(proc.pid)
            self.assertIsNotNone(info)
            voice.set_voice_state(info)
            self.assertTrue(voice.stop())
            proc.wait(timeout=5)
            self.assertIsNone(voice.get_voice_state())
        finally:
            if proc.poll() is None:
                proc.kill()


if __name__ == "__main__":
    unittest.main(verbosity=2)
