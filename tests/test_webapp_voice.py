# -*- coding: utf-8 -*-
import json
import os
import shutil
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import webapp
from pai import voice as M14


class WebVoiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "memory.db"
        from pai.memory import Memory
        Memory(str(self.db)).close()
        self.old = os.environ.get("PAI_VOICE_BACKEND")
        self.httpd, self.thread = webapp.start(port=0, db_path=str(self.db), quiet=True)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_port

    def tearDown(self):
        webapp.stop(self.httpd, self.thread)
        if self.old is None:
            os.environ.pop("PAI_VOICE_BACKEND", None)
        else:
            os.environ["PAI_VOICE_BACKEND"] = self.old

    def req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method,
                                   headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=5) as resp:
            return json.loads(resp.read().decode())

    def test_chat_speaks_only_when_enabled(self):
        class FakeOrch:
            def handle(self, text, channel="web", **kwargs):
                return {"response": "hi", "trace": {}}

        old = webapp.orch
        webapp.orch = FakeOrch()
        try:
            with mock.patch.object(M14, "speaking_enabled", return_value=False), \
                    mock.patch.object(M14, "speak") as sp:
                self.req("POST", "/api/chat", {"text": "x"})
                sp.assert_not_called()
            with mock.patch.object(M14, "speaking_enabled", return_value=True), \
                    mock.patch.object(M14, "speak") as sp:
                self.req("POST", "/api/chat", {"text": "x"})
                sp.assert_called_once_with("hi")
        finally:
            webapp.orch = old

    def test_voice_toggle(self):
        out = self.req("GET", "/api/voice")
        self.assertIn("enabled", out)
        self.assertEqual(self.req("POST", "/api/voice", {"enabled": True})["enabled"], True)
        self.assertEqual(self.req("GET", "/api/voice")["enabled"], True)
        self.assertEqual(self.req("POST", "/api/voice", {"enabled": False})["enabled"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
