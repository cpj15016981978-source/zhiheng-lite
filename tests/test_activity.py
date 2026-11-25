# -*- coding: utf-8 -*-
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai import activity as A  # noqa: E402


class ActivityTest(unittest.TestCase):
    def test_record_chat_metadata_only(self):
        d = pathlib.Path(tempfile.mkdtemp())
        with mock.patch.dict(os.environ, {"PAI_A1_ACTIVITY": "on",
                                          "PAI_A1_ACTIVITY_DIR": str(d)}, clear=True):
            A.record_chat("s1", "e1", channel="web", facts=2, principles=4, model="mock")
        lines = (d / "a1.activity.jsonl").read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[0])
        self.assertEqual(rec["kind"], "user_activity")
        self.assertEqual(rec["action"], "chat.turn")
        self.assertEqual(rec["session"], "s1")
        self.assertEqual(rec["db_write"], True)
        self.assertEqual(rec["facts"], 2)
        self.assertNotIn("text", rec)

    def test_off_writes_nothing(self):
        d = pathlib.Path(tempfile.mkdtemp())
        with mock.patch.dict(os.environ, {"PAI_A1_ACTIVITY": "off",
                                          "PAI_A1_ACTIVITY_DIR": str(d)}, clear=True):
            A.record_chat("s1", "e1")
        self.assertFalse((d / "a1.activity.jsonl").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
