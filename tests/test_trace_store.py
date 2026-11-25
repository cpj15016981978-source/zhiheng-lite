# -*- coding: utf-8 -*-
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tools import trace_store as TS  # noqa: E402


class TraceStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["PAI_TRACE_STORE"] = "on"
        os.environ["PAI_TRACE_DIR"] = self.dir

    def tearDown(self):
        os.environ.pop("PAI_TRACE_STORE", None)
        os.environ.pop("PAI_TRACE_DIR", None)

    def test_append_and_read(self):
        p = TS.append("你好", {"blocked": False, "models": []}, "test")
        self.assertTrue(p.exists())
        rows = TS.read(p)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["prompt"], "你好")
        self.assertEqual(rows[0]["source"], "test")

    def test_secret_prompt_not_stored(self):
        p = TS.append("sk-api-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
                      {"blocked_secret": True}, "test")
        rows = TS.read(p)
        self.assertIsNone(rows[0]["prompt"])
        self.assertNotIn("sk-api", json.dumps(rows[0], ensure_ascii=False))

    def test_off_writes_nothing(self):
        os.environ["PAI_TRACE_STORE"] = "off"
        self.assertIsNone(TS.append("x", {}, "test"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
