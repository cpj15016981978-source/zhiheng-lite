# -*- coding: utf-8 -*-
"""cascade shadow：只旁路，不改变云端答案。"""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from pai import cascade_shadow as CS


class CascadeShadowTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.log = pathlib.Path(self.td.name) / "shadow.jsonl"
        self.p = mock.patch.object(CS, "LOG", self.log)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.td.cleanup()

    def test_disabled_does_nothing(self):
        with mock.patch.dict(os.environ, {"PAI_CASCADE_MODE": "cloud_only"}):
            self.assertFalse(CS.enabled())
            self.assertIsNone(CS.observe_async("x", "y", lambda p: "z"))
        self.assertFalse(self.log.exists())

    def test_shadow_records_agree_and_does_not_change_answer(self):
        with mock.patch.dict(os.environ, {"PAI_CASCADE_MODE": "shadow"}):
            t = CS.observe_async("问题", "云端答案", lambda p: "云端答案")
            self.assertIsNotNone(t)
            t.join(timeout=2)
        text = self.log.read_text(encoding="utf-8")
        self.assertIn('"agree": true', text)

    def test_shadow_local_error_is_logged(self):
        with mock.patch.dict(os.environ, {"PAI_CASCADE_MODE": "shadow"}):
            t = CS.observe_async("问题", "云端", lambda p: (_ for _ in ()).throw(RuntimeError("down")))
            t.join(timeout=2)
        self.assertIn("RuntimeError", self.log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
