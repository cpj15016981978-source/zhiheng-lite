# -*- coding: utf-8 -*-
"""知衡 Lite：一个本地模型、无记忆、无数据库的最小开源办公 AI。"""
from __future__ import annotations

import json
import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core_model import CoreModel, CoreModelError

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
CORE = CoreModel(CONFIG)
MODEL = CORE.name
HOST = str(CONFIG.get("host") or "127.0.0.1")
PORT = int(CONFIG.get("port") or 8760)
SYSTEM = str(CONFIG.get("system") or "你是知衡 Lite，一个本地办公助手。回答简洁、直接。")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, (ROOT / "static" / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/health":
            self._send(200, {"ok": True, "model": MODEL, "memory": False, **CORE.health()})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/chat":
            self._send(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 2_000_000:
                raise ValueError("bad body")
            payload = json.loads(self.rfile.read(n).decode("utf-8"))
            messages = payload.get("messages") or []
            if not isinstance(messages, list):
                raise ValueError("messages must be list")
            text = CORE.chat(messages)
            self._send(200, {"content": text, "model": MODEL, "provider": CORE.health().get("provider")})
        except Exception as e:
            self._send(400, {"error": "%s: %s" % (type(e).__name__, e)})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    print("Zhiheng Lite http://%s:%d model=%s memory=off" % (HOST, PORT, MODEL))
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
