# -*- coding: utf-8 -*-
import hashlib
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_layer"))
from tools import agent_write as W  # noqa: E402
import agentd  # noqa: E402
import tools_exec as TX  # noqa: E402


class AgentWriteTest(unittest.TestCase):
    def test_default_off_refuses(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(W.AgentWriteError):
                W.submit_text("a.txt", "x")

    def test_missing_token_refuses(self):
        with mock.patch.dict(os.environ, {"PAI_AGENT_WRITE": "on"}, clear=True):
            with self.assertRaises(W.AgentWriteError):
                W.submit_text("a.txt", "x")

    def test_payload_is_write_task(self):
        seen = {}

        def fake(path, payload):
            seen.update({"path": path, "payload": payload})
            return {"status": 200}

        env = {"PAI_AGENT_WRITE": "on", "PAI_AGENT_TOKEN": "tok"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(W, "_post", fake):
            W.submit_text("a.txt", "hello")
        self.assertEqual(seen["path"], "/task")
        self.assertEqual(seen["payload"]["capability"], "write")
        self.assertEqual(seen["payload"]["payload"]["tool"], "write_text")
        self.assertEqual(seen["payload"]["payload"]["args"]["path"], "a.txt")

    def test_path_escape_refused(self):
        for p in ("../a.txt", "a/../../b", "/abs.txt", "C:/x.txt", ""):
            with self.assertRaises(W.AgentWriteError):
                W._check_rel_path(p)

    def test_non_loopback_base_refused(self):
        with mock.patch.dict(os.environ, {"PAI_AGENT_BASE": "http://10.0.0.5:8766"}, clear=True):
            with self.assertRaises(W.AgentWriteError):
                W._base()


class AgentWriteE2ETest(unittest.TestCase):
    """真起一个回环 agentd，走 submit → approve → run。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.tokens = self.tmp / "tokens.json"
        self.tokens.write_text(json.dumps({
            "qoder": {"hash": hashlib.sha256(b"tok-q").hexdigest(),
                       "caps": ["read", "write"]},
        }), encoding="utf-8")
        self.store = agentd.Store(str(self.tmp), dev=False)
        self.srv = agentd.ThreadingHTTPServer(("127.0.0.1", 0), agentd.Handler)
        self.srv.store = self.store
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.old_env = {k: os.environ.get(k) for k in
                        ("PAI_AGENT_WRITE", "PAI_AGENT_TOKEN", "PAI_AGENT_BASE")}
        os.environ["PAI_AGENT_WRITE"] = "on"
        os.environ["PAI_AGENT_TOKEN"] = "tok-q"
        os.environ["PAI_AGENT_BASE"] = "http://127.0.0.1:%d" % self.port
        self.old_roots = (TX.ROOT, TX.SANDBOX)
        TX.ROOT = self.tmp / "root"
        TX.SANDBOX = self.tmp / "sandbox"
        TX.ROOT.mkdir(parents=True, exist_ok=True)
        TX.SANDBOX.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.store.con.close()
        TX.ROOT, TX.SANDBOX = self.old_roots
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_simulate_payload(self):
        seen = {}

        def fake(path, payload):
            seen.update({"path": path, "payload": payload})
            return {"ok": True, "simulation": {"cached": False}}

        env = {"PAI_AGENT_WRITE": "on", "PAI_AGENT_TOKEN": "tok"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(W, "_post", fake):
            out = W.simulate("t1")
        self.assertTrue(out["ok"])
        self.assertEqual(seen["path"], "/simulate")
        self.assertEqual(seen["payload"], {"id": "t1"})

    def test_submit_approve_run_writes_sandbox(self):
        out = W.submit_text("e2e.txt", "hello")
        tid = out["id"]
        sim = W.simulate(tid)
        self.assertTrue(sim["ok"])
        self.assertEqual(sim["simulation"]["decision"], "pending")
        self.assertEqual(sim["simulation"]["bytes_after"], 5)
        self.assertFalse((TX.SANDBOX / "e2e.txt").exists())
        self.assertEqual(W.approve(tid).get("ok"), True)
        self.assertEqual(W.run(tid).get("ok"), True)
        self.assertEqual((TX.SANDBOX / "e2e.txt").read_text(encoding="utf-8"), "hello")


if __name__ == "__main__":
    unittest.main(verbosity=2)
