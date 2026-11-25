# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import webapp


class CsrfOriginTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "memory.db"
        from pai.memory import Memory
        Memory(str(self.db)).close()
        self.httpd, self.thread = webapp.start(port=0, db_path=str(self.db), quiet=True)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_port

    def tearDown(self):
        webapp.stop(self.httpd, self.thread)

    def post(self, origin=None, referer=None):
        data = json.dumps({"enabled": False}).encode()
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        if referer:
            headers["Referer"] = referer
        req = urllib.request.Request(self.base + "/api/voice", data=data,
                                     method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())

    def test_external_origin_blocked(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post(origin="http://evil.example.com")
        self.assertEqual(cm.exception.code, 403)
        cm.exception.close()

    def test_external_referer_blocked(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post(referer="http://evil.example.com/page")
        self.assertEqual(cm.exception.code, 403)
        cm.exception.close()

    def test_local_origin_allowed(self):
        code, out = self.post(origin=self.base)
        self.assertEqual(code, 200)
        self.assertIn("enabled", out)

    def test_no_origin_allowed(self):
        code, out = self.post()
        self.assertEqual(code, 200)
        self.assertIn("enabled", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
