# -*- coding: utf-8 -*-
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai import trace as T  # noqa: E402


class TraceTest(unittest.TestCase):
    def test_model_call_append_only(self):
        p = pathlib.Path(tempfile.mkdtemp()) / "trace.jsonl"
        tr = T.Trace(p)
        with mock.patch.dict(os.environ, {"PAI_TRACE": "on"}, clear=True):
            rid = tr.record_model_call("hello", [{"model_id": "mock", "output": "hi",
                                                  "cost": 0, "tokens": 2, "priced": True}])
        lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([x["kind"] for x in lines], ["model.request", "model.response"])
        self.assertEqual(lines[0]["request_id"], rid)
        self.assertEqual(lines[1]["request_id"], rid)
        self.assertEqual(lines[0]["prompt"], "hello")
        self.assertEqual(lines[0]["prompt_sha12"], __import__("hashlib").sha256(b"hello").hexdigest()[:12])

    def test_off_writes_nothing(self):
        p = pathlib.Path(tempfile.mkdtemp()) / "trace.jsonl"
        tr = T.Trace(p)
        with mock.patch.dict(os.environ, {"PAI_TRACE": "off"}, clear=True):
            tr.record_model_call("hello", [])
        self.assertFalse(p.exists())

    def test_orchestrator_wrapper_records(self):
        d = pathlib.Path(tempfile.mkdtemp())
        from pai import orchestrator as O
        from pai import trace as TT
        TT.TRACE._last_prompt_sha = None
        with mock.patch.dict(os.environ, {"PAI_TRACE": "on", "PAI_TRACE_DIR": str(d)}, clear=True), \
                mock.patch.object(O.M10, "complete", return_value=[{"model_id": "mock", "output": "hi"}]):
            O._complete("wired")
        files = list(d.glob("trace-*.jsonl"))
        self.assertEqual(len(files), 1)
        kinds = [json.loads(x)["kind"] for x in files[0].read_text(encoding="utf-8").splitlines()]
        self.assertEqual(kinds, ["model.request", "model.response"])

    def test_tool_and_approval_events(self):
        p = pathlib.Path(tempfile.mkdtemp()) / "trace.jsonl"
        tr = T.Trace(p)
        with mock.patch.dict(os.environ, {"PAI_TRACE": "on"}, clear=True):
            tr.record_tool_call("read_text", {"path": "a"}, result={"text": "x"})
            tr.record_approval("allow-once", subject="write", by="qoder", task_id="t1")
            tr.record_model_call("one", [])
            tr.record_model_call("two", [])
        kinds = [json.loads(x)["kind"] for x in p.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(kinds, ["tool.call", "tool.result", "approval.decision",
                                 "model.request", "prompt.changed", "model.request"])

    def test_tool_loop_wiring(self):
        d = pathlib.Path(tempfile.mkdtemp())
        from pai import tool_loop as TL
        from pai import trace as TT
        with mock.patch.dict(os.environ, {"PAI_TRACE": "on", "PAI_TRACE_DIR": str(d)}, clear=True), \
                mock.patch.object(TL.M8, "run", return_value={"ok": True}), \
                mock.patch.object(TL.M8, "backend", return_value="off"), \
                mock.patch.object(TL.M8, "set_enabled"):
            TL.run_tool(None, {"tool": "recall", "args": {"query": "x"}})
        files = list(d.glob("trace-*.jsonl"))
        kinds = [json.loads(x)["kind"] for x in files[0].read_text(encoding="utf-8").splitlines()]
        self.assertEqual(kinds, ["tool.call", "tool.result"])

    def test_error_is_recorded(self):
        p = pathlib.Path(tempfile.mkdtemp()) / "trace.jsonl"
        tr = T.Trace(p)
        with mock.patch.dict(os.environ, {"PAI_TRACE": "on"}, clear=True):
            tr.record_error("hello", RuntimeError("boom"))
        rec = json.loads(p.read_text(encoding="utf-8").strip())
        self.assertEqual(rec["kind"], "model.error")
        self.assertEqual(rec["error"], "RuntimeError")
        self.assertIn("boom", rec["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
