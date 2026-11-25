# -*- coding: utf-8 -*-
import json
import os
import pathlib
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai import model_gateway as M10  # noqa: E402


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8"))
        self.server.seen = body
        if not body.get("stream"):
            self.send_response(500)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        frames = [
            {"choices": [{"delta": {"content": "你"}}]},
            {"choices": [{"delta": {"content": "好"}}]},
            {"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 2}},
        ]
        for f in frames:
            self.wfile.write(("data: " + json.dumps(f) + "\n\n").encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class StreamTest(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.old = {k: os.environ.get(k) for k in (
            "PAI_MODEL_PROVIDER", "PAI_MODEL_NAME", "PAI_OPENAI_BASE_URL",
            "OPENAI_API_KEY")}
        os.environ["PAI_MODEL_PROVIDER"] = "openai"
        os.environ["PAI_MODEL_NAME"] = "gpt-4o-mini"
        os.environ["PAI_OPENAI_BASE_URL"] = "http://127.0.0.1:%d/v1" % self.port
        os.environ["OPENAI_API_KEY"] = "test-key"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_stream_callback_gets_chunks(self):
        chunks = []
        out = M10.complete("hi", on_delta=chunks.append)[0]
        self.assertEqual(out["output"], "你好")
        self.assertEqual(chunks, ["你", "好"])
        self.assertEqual(out["tokens"], 4)

    def test_stream_payload_has_stream_true(self):
        M10.complete("hi", on_delta=lambda s: None)
        self.assertTrue(self.srv.seen.get("stream"))

    def test_mock_callback_single_chunk(self):
        old = os.environ.pop("PAI_MODEL_PROVIDER", None)
        try:
            chunks = []
            out = M10.complete("hi", on_delta=chunks.append)[0]
            self.assertTrue(out["output"].startswith("[mock]"))
            self.assertEqual(len(chunks), 1)
        finally:
            if old is not None:
                os.environ["PAI_MODEL_PROVIDER"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
