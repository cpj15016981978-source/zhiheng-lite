# -*- coding: utf-8 -*-
"""Zhiheng Lite core model runtime.

One model, one local runtime. No model picker, no Ollama requirement in the
release build. A development fallback to Ollama is opt-in via dev_ollama.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent


class CoreModelError(RuntimeError):
    pass


class CoreModel:
    def __init__(self, config):
        self.cfg = config
        self.name = str(config.get("model") or "core-model")
        self.runtime = ROOT / str(config.get("runtime") or "runtime/llama-server.exe")
        self.model_path = ROOT / str(config.get("model_path") or "models/core.gguf")
        self.port = int(config.get("local_port") or 8761)
        self.num_ctx = int(config.get("num_ctx") or 4096)
        self.timeout = int(config.get("timeout") or 300)
        self.dev_ollama = bool(config.get("dev_ollama"))
        self.ollama = str(config.get("ollama") or "http://127.0.0.1:11434").rstrip("/")
        self._proc = None
        self._last_error = ""

    def _post(self, url, payload, timeout=None):
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _core_ready(self):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/health" % self.port, timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    def _start_core(self):
        if self._proc and self._proc.poll() is None:
            return
        if not self.runtime.is_file():
            raise CoreModelError("缺少核心运行时：%s" % self.runtime)
        if not self.model_path.is_file():
            raise CoreModelError("缺少核心模型：%s" % self.model_path)
        self._proc = subprocess.Popen(
            [str(self.runtime), "-m", str(self.model_path),
             "--host", "127.0.0.1", "--port", str(self.port),
             "-c", str(self.num_ctx), "--no-webui"],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 90
        while time.time() < deadline:
            if self._proc.poll() is not None:
                raise CoreModelError("核心运行时启动失败，退出码 %s" % self._proc.returncode)
            if self._core_ready():
                return
            time.sleep(0.5)
        raise CoreModelError("核心运行时启动超时")

    def _chat_core(self, messages):
        self._start_core()
        d = self._post("http://127.0.0.1:%d/v1/chat/completions" % self.port, {
            "model": "local",
            "messages": [{"role": "system", "content": str(self.cfg.get("system") or "")}] + messages,
            "stream": False,
            "max_tokens": int(self.cfg.get("max_tokens") or 2048),
        })
        try:
            return d["choices"][0]["message"]["content"]
        except Exception:
            raise CoreModelError("核心模型返回格式不对")

    def _chat_dev_ollama(self, messages):
        d = self._post(self.ollama + "/api/chat", {
            "model": self.name,
            "messages": [{"role": "system", "content": str(self.cfg.get("system") or "")}] + messages,
            "stream": False,
            "options": {"num_ctx": self.num_ctx},
        })
        return (d.get("message") or {}).get("content", "")

    def chat(self, messages):
        try:
            if self.dev_ollama:
                return self._chat_dev_ollama(messages)
            return self._chat_core(messages)
        except CoreModelError:
            raise
        except Exception as e:
            self._last_error = "%s: %s" % (type(e).__name__, e)
            raise CoreModelError(self._last_error)

    def health(self):
        if self.dev_ollama:
            return {"ok": bool(self.ollama), "provider": "ollama-dev",
                    "model": self.name, "runtime": "development fallback"}
        ok = self.runtime.is_file() and self.model_path.is_file()
        return {"ok": ok, "provider": "core", "model": self.name,
                "runtime": str(self.runtime), "model_path": str(self.model_path),
                "error": "" if ok else (self._last_error or "核心运行时或模型未随包提供")}
