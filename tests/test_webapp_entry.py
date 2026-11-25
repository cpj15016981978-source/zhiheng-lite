# -*- coding: utf-8 -*-
import json
import pathlib
import sys
import tempfile
import unittest
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import webapp  # noqa: E402


class FakeOrch:
    calls = 0

    def __init__(self, mem):
        FakeOrch.calls = 0

    def handle(self, text, **kw):
        FakeOrch.calls += 1
        return {"response": "SHOULD_NOT_RUN", "trace": {}}


class EntryGuardTest(unittest.TestCase):
    def setUp(self):
        self.old = webapp.Orchestrator
        webapp.Orchestrator = FakeOrch
        self.db = pathlib.Path(tempfile.mkdtemp()) / "entry.db"
        self.httpd, self.thread = webapp.start(port=0, db_path=str(self.db), quiet=True)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        webapp.Orchestrator = self.old

    def post(self, text):
        data = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/chat" % self.port,
            data=data, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def test_write_is_blocked_and_orch_not_called(self):
        code, out = self.post("删除这个文件")
        self.assertEqual(code, 200)
        self.assertTrue(out["trace"]["blocked"])
        self.assertTrue(out["trace"]["requires_approval"])
        self.assertEqual(FakeOrch.calls, 0)

    def test_risk_is_blocked_and_orch_not_called(self):
        code, out = self.post("我该不该借钱投资")
        self.assertEqual(code, 200)
        self.assertTrue(out["trace"]["blocked"])
        self.assertEqual(out["trace"]["r0_intent"], "risk")
        self.assertEqual(FakeOrch.calls, 0)

    def test_chat_passes_through(self):
        code, out = self.post("你好")
        self.assertEqual(code, 200)
        self.assertEqual(FakeOrch.calls, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
