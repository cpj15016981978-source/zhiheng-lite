# -*- coding: utf-8 -*-
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import webapp  # noqa: E402
from tools import world_write_gate as WG  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ApprovalAuthTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.gate = pathlib.Path(tempfile.mkdtemp())
        self.old = {k: os.environ.get(k) for k in (
            "PAI_WORLD_SHADOW", "PAI_WORLD_SHADOW_DIR",
            "PAI_WORLD_APPROVAL_DIR", "PAI_WORLD_APPROVE_TOKEN")}
        os.environ["PAI_WORLD_SHADOW"] = "on"
        os.environ["PAI_WORLD_SHADOW_DIR"] = str(self.dir)
        os.environ["PAI_WORLD_APPROVAL_DIR"] = str(self.gate)
        os.environ["PAI_WORLD_APPROVE_TOKEN"] = "test-token"
        (self.dir / "shadow-20261030.jsonl").write_text(json.dumps({
            "source_episode": 1,
            "candidates": [{"kind": "entity", "type": "person",
                            "name": "测试人", "evidence": "我叫/我是"}],
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        from tools.review_world_shadow import _rows
        self.cid = _rows()[0]["id"]
        db = pathlib.Path(tempfile.mkdtemp()) / "a.db"
        self.httpd, self.thread = webapp.start(port=0, db_path=str(db),
                                               quiet=True, ready_timeout=40)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        webapp.stop(self.httpd, self.thread)
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def post(self, path, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path),
            data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())

    def prepare(self):
        return self.post("/api/world/approval/prepare",
                         {"candidate_id": self.cid})

    def test_approve_needs_token(self):
        p = self.prepare()
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/api/world/approval/approve",
                      {"request": p["request"], "by": "Boss"})
        self.assertEqual(cm.exception.code, 403)

    def test_unconfigured_token_is_403(self):
        os.environ.pop("PAI_WORLD_APPROVE_TOKEN", None)
        p = self.prepare()
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/api/world/approval/approve",
                      {"request": p["request"], "by": "Boss", "token": ""})
        self.assertEqual(cm.exception.code, 403)

    def test_approve_wrong_token(self):
        p = self.prepare()
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/api/world/approval/approve",
                      {"request": p["request"], "by": "Boss", "token": "bad"})
        self.assertEqual(cm.exception.code, 403)

    def test_approve_with_token(self):
        p = self.prepare()
        out = self.post("/api/world/approval/approve",
                        {"request": p["request"], "by": "Boss",
                         "token": "test-token"})
        self.assertTrue(out["ok"])
        self.assertFalse(out["used"])

    def test_web_ai_by_is_rejected(self):
        p = self.prepare()
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/api/world/approval/approve",
                      {"request": p["request"], "by": "AI",
                       "token": "test-token"})
        self.assertEqual(cm.exception.code, 403)

    def test_gate_refuses_ai_approver(self):
        p = self.prepare()
        req = self.gate / p["request"]
        with self.assertRaises(ValueError):
            WG.approve(str(req), "AI")

    def test_gate_refuses_true_db_prepare(self):
        with self.assertRaises(ValueError):
            WG.prepare(self.cid, str(ROOT / "data" / "memory.db"))

    def test_cli_by_is_required(self):
        p = self.prepare()
        req = self.gate / p["request"]
        out = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "world_write_gate.py"),
             "approve", "--request", str(req)],
            capture_output=True, text=True, timeout=20)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("--by", out.stderr)

    def test_used_approval_cannot_replay(self):
        p = self.prepare()
        req = self.gate / p["request"]
        ap, _ = WG.approve(str(req), "Boss")
        WG.execute(str(req), str(ap))
        with self.assertRaises(ValueError):
            WG.execute(str(req), str(ap))

    def test_batch_over_limit_is_rejected(self):
        db = pathlib.Path(tempfile.mkdtemp()) / "batch.db"
        with self.assertRaises(ValueError):
            WG.prepare_batch([self.cid] * 6, str(db))


if __name__ == "__main__":
    unittest.main(verbosity=2)
