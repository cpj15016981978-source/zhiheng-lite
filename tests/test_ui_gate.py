# -*- coding: utf-8 -*-
"""写接口统一门：无页面 nonce 的 /api 写请求必须 403。"""
import http.client
import json
import os
import re
import tempfile
import unittest

import webapp


class UiGateTest(unittest.TestCase):
    def setUp(self):
        self.httpd, self.thread = webapp.start(port=0, db_path=os.path.join(tempfile.mkdtemp(), "g.db"), quiet=True)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        webapp.stop(self.httpd, self.thread)

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        c.request(method, path, json.dumps(body or {}).encode(), h)
        r = c.getresponse(); data = r.read().decode("utf-8", "replace"); c.close()
        return r.status, data

    def test_write_without_nonce_is_403(self):
        status, _ = self.req("POST", "/api/tools/toggle", {"enabled": False})
        self.assertEqual(403, status)

    def test_write_with_page_nonce_passes(self):
        html = self.req("GET", "/chat")[1]
        m = re.search(r'window\.__DSH_NONCE__="([^"]+)"', html)
        self.assertTrue(m)
        status, _ = self.req("POST", "/api/tools/toggle", {"enabled": False},
                             {"Origin": "http://127.0.0.1:%d" % self.port, "X-DSH-Nonce": m.group(1)})
        self.assertEqual(200, status)


if __name__ == "__main__":
    unittest.main()
