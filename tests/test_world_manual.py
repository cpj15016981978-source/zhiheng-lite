# -*- coding: utf-8 -*-
import json
import os
import pathlib
import sys
import tempfile
import unittest
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import webapp  # noqa: E402


def post(port, path, body):
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path),
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.status, json.loads(r.read().decode())


def get(port, path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=8) as r:
        return r.status, json.loads(r.read().decode())


class WorldManualTest(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("PAI_WORLD_WRITE")
        os.environ["PAI_WORLD_WRITE"] = "on"
        db = pathlib.Path(tempfile.mkdtemp()) / "world.db"
        self.httpd, self.thread = webapp.start(port=0, db_path=str(db), quiet=True, ready_timeout=40)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        webapp.stop(self.httpd, self.thread)
        if self.old is None:
            os.environ.pop("PAI_WORLD_WRITE", None)
        else:
            os.environ["PAI_WORLD_WRITE"] = self.old

    def test_add_entity_then_read(self):
        st, out = post(self.port, "/api/world/add", {
            "kind": "entity", "type": "person", "name": "世界测试", "aliases": "测试,world"})
        self.assertEqual(st, 200)
        self.assertTrue(out["ok"])
        st, world = get(self.port, "/api/world")
        self.assertEqual(st, 200)
        self.assertTrue(any(e["name"] == "世界测试" for e in world["entities"]))

    def test_off_is_403(self):
        os.environ["PAI_WORLD_WRITE"] = "off"
        try:
            post(self.port, "/api/world/add", {"kind": "entity", "type": "person", "name": "x"})
        except Exception as e:
            self.assertIn("403", str(getattr(e, "code", "")) + str(e))
        else:
            self.fail("应 403")


if __name__ == "__main__":
    unittest.main(verbosity=2)
