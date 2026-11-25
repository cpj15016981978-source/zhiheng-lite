# -*- coding: utf-8 -*-
import json
import os
import pathlib
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import webapp  # noqa: E402


class DailyMemoryTest(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("PAI_MEMORY_WRITE")
        os.environ["PAI_MEMORY_WRITE"] = "on"
        db = pathlib.Path(tempfile.mkdtemp()) / "m.db"
        self.httpd, self.thread = webapp.start(port=0, db_path=str(db),
                                               quiet=True, ready_timeout=40)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        webapp.stop(self.httpd, self.thread)
        if self.old is None:
            os.environ.pop("PAI_MEMORY_WRITE", None)
        else:
            os.environ["PAI_MEMORY_WRITE"] = self.old

    def post(self, path, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path),
            data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())

    def test_save_and_list(self):
        out = self.post("/api/memory/card", {
            "kind": "事", "person": "阿明",
            "what": "阿明说下周交样品", "why": "客户在等",
            "next_step": "周五前确认"})
        self.assertTrue(out["ok"])
        self.assertTrue(out["event_id"])
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/memory/cards" % self.port,
                timeout=8) as r:
            data = json.loads(r.read().decode())
        self.assertEqual(len(data["items"]), 1)
        self.assertIn("阿明说下周交样品", data["items"][0]["summary"])

    def test_off_is_403(self):
        os.environ["PAI_MEMORY_WRITE"] = "off"
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/api/memory/card", {"kind": "事", "what": "x"})
        self.assertEqual(cm.exception.code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
