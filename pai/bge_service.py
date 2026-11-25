# -*- coding: utf-8 -*-
"""本地 BGE 服务：Ollama bge-m3 + 余弦重排。

端点：
  GET  /health
  POST /v1/embeddings   OpenAI 兼容
  POST /v1/rerank       {query, documents}

这是“BGE-M3 dense embedding + 余弦 rerank”，不是 bge-reranker 交叉编码器。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OLLAMA = (os.environ.get("BGE_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
MODEL = (os.environ.get("BGE_MODEL") or "bge-m3").strip()

CAPABILITY = {
    "name": "本地 BGE 服务",
    "switch_env": "BGE_MODEL",
    "default_on": False,
    "stores_user_text": False,
    "network": True,
    "degrade": "Ollama/bge-m3 不可用时 health 返回 503，调用方回退本地 hash/词面",
    "approval": "ADR-044 本地模型/HTTP 接入登记",
}


def _embed(texts):
    body = json.dumps({"model": MODEL, "input": list(texts)}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(OLLAMA + "/api/embed", data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read().decode("utf-8"))
    out = data.get("embeddings")
    if out is None:
        one = data.get("embedding")
        out = [one] if one else []
    return out


def _cos(a, b):
    n = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (n or 1e-9)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            try:
                _embed(["health"])
                self._send(200, {"ok": True, "model": MODEL, "backend": "ollama"})
            except Exception as exc:
                self._send(503, {"ok": False, "model": MODEL, "error": str(exc)})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            if self.path == "/v1/embeddings":
                texts = data.get("input") or []
                if isinstance(texts, str):
                    texts = [texts]
                vecs = _embed(texts)
                self._send(200, {"data": [{"index": i, "embedding": v} for i, v in enumerate(vecs)],
                                 "model": MODEL})
                return
            if self.path == "/v1/rerank":
                q = str(data.get("query") or "")
                docs = [str(d) for d in (data.get("documents") or [])]
                if not docs:
                    self._send(200, {"results": []})
                    return
                vecs = _embed([q] + docs)
                qv, dv = vecs[0], vecs[1:]
                out = [{"index": i, "relevance_score": _cos(qv, v)} for i, v in enumerate(dv)]
                out.sort(key=lambda x: x["relevance_score"], reverse=True)
                self._send(200, {"results": out, "model": MODEL})
                return
            self._send(404, {"error": "not found"})
        except Exception as exc:
            self._send(503, {"error": str(exc)})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8780)
    ns = ap.parse_args(argv)
    srv = ThreadingHTTPServer(("127.0.0.1", ns.port), Handler)
    print("BGE service http://127.0.0.1:%d model=%s" % (ns.port, MODEL), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
