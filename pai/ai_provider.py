# -*- coding: utf-8 -*-
"""统一 AIProvider：聊天/流式/向量/健康/模型信息。"""
from __future__ import annotations

import json
import os
import urllib.request

CAPABILITY = {
    "name": "统一 AIProvider",
    "switch_env": "PAI_AI_PROVIDER",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "本地不可达=明确报错；远程缺 Key=不启用；不静默换模型",
    "approval": "ADR-046：统一接口层，不新增开关",
}


class ProviderError(RuntimeError):
    pass


class AIProvider:
    name = "base"

    def chat(self, messages, **opts):
        raise NotImplementedError

    def stream(self, messages, **opts):
        raise NotImplementedError

    def embeddings(self, texts, **opts):
        raise NotImplementedError

    def health_check(self):
        raise NotImplementedError

    def model_info(self):
        raise NotImplementedError


class OllamaProvider(AIProvider):
    name = "ollama"

    def __init__(self, model="qwen2.5:1.5b", base=None):
        self.model = model or "qwen2.5:1.5b"
        self.base = (base or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")

    def _req(self, path, payload, timeout=120):
        req = urllib.request.Request(self.base + path, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def chat(self, messages, **opts):
        d = self._req("/api/chat", {"model": self.model, "messages": messages, "stream": False,
                                    "options": {"num_ctx": int(opts.get("num_ctx") or 4096)}},
                      timeout=int(opts.get("timeout") or 300))
        return {"content": (d.get("message") or {}).get("content", ""), "model": self.model,
                "raw": d}

    def stream(self, messages, **opts):
        req = urllib.request.Request(self.base + "/api/chat",
                                     data=json.dumps({"model": self.model, "messages": messages,
                                                      "stream": True}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=int(opts.get("timeout") or 300)) as r:
            for line in r:
                if not line.strip():
                    continue
                d = json.loads(line.decode("utf-8"))
                if (d.get("message") or {}).get("content"):
                    yield d["message"]["content"]

    def embeddings(self, texts, **opts):
        d = self._req("/api/embed", {"model": opts.get("model") or "bge-m3", "input": texts},
                      timeout=int(opts.get("timeout") or 60))
        return d.get("embeddings") or []

    def health_check(self):
        try:
            with urllib.request.urlopen(self.base + "/api/tags", timeout=5) as r:
                return {"ok": r.status == 200, "provider": self.name}
        except Exception as e:
            return {"ok": False, "provider": self.name, "error": "%s: %s" % (type(e).__name__, e)}

    def model_info(self):
        return {"provider": self.name, "model": self.model, "base": self.base}


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self, key="", base="https://api.openai.com/v1", model="gpt-4o-mini"):
        self.key = key or os.environ.get("OPENAI_API_KEY", "")
        self.base = (base or os.environ.get("PAI_OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.model = model or os.environ.get("PAI_OPENAI_MODEL") or "gpt-4o-mini"

    def _req(self, path, payload, timeout=120):
        req = urllib.request.Request(self.base + path, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + self.key}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def chat(self, messages, **opts):
        if not self.key:
            raise ProviderError("OpenAI Key 未配置")
        d = self._req("/chat/completions", {"model": self.model, "messages": messages},
                      timeout=int(opts.get("timeout") or 120))
        return {"content": (d["choices"][0]["message"]["content"]), "model": self.model, "raw": d}

    def stream(self, messages, **opts):
        raise NotImplementedError("远程流式暂未接入统一接口")

    def embeddings(self, texts, **opts):
        d = self._req("/embeddings", {"model": opts.get("model") or "text-embedding-3-small",
                                      "input": texts})
        return [x["embedding"] for x in d.get("data", [])]

    def health_check(self):
        if not self.key:
            return {"ok": False, "provider": self.name, "error": "未配置 Key"}
        try:
            req = urllib.request.Request(self.base + "/models",
                                         headers={"Authorization": "Bearer " + self.key})
            with urllib.request.urlopen(req, timeout=8) as r:
                return {"ok": r.status == 200, "provider": self.name}
        except Exception as e:
            return {"ok": False, "provider": self.name, "error": "%s: %s" % (type(e).__name__, e)}

    def model_info(self):
        return {"provider": self.name, "model": self.model, "base": self.base}


class OfflineProvider(AIProvider):
    name = "offline"

    def chat(self, messages, **opts):
        return {"content": "本地离线模式：没有可用模型。请先加载本地模型。",
                "model": "offline", "raw": {}}

    def stream(self, messages, **opts):
        yield self.chat(messages, **opts)["content"]

    def embeddings(self, texts, **opts):
        return []

    def health_check(self):
        return {"ok": True, "provider": self.name}

    def model_info(self):
        return {"provider": self.name, "model": "offline"}


def local(model=None):
    return OllamaProvider(model or os.environ.get("PAI_MODEL_NAME") or "qwen2.5:1.5b")


def remote(key=None, base=None, model=None):
    return OpenAIProvider(key or os.environ.get("OPENAI_API_KEY") or "",
                          base or os.environ.get("PAI_OPENAI_BASE_URL"),
                          model or os.environ.get("PAI_OPENAI_MODEL"))


def selected():
    """当前主 provider；本地核心，自定义/远程仅显式选择时启用。"""
    p = (os.environ.get("PAI_MODEL_PROVIDER") or "ollama").strip().lower()
    name = os.environ.get("PAI_CUSTOM_MODEL") or os.environ.get("PAI_MODEL_NAME") or ""
    try:
        from pai import model_registry as MR
        cfg = MR.find(name) if name else None
        if cfg:
            if cfg.get("kind") == "local" or cfg.get("provider") == "ollama":
                return OllamaProvider(cfg.get("model") or name)
            return OpenAIProvider(cfg.get("key") or "", cfg.get("base_url") or "", cfg.get("model") or name)
    except Exception:
        pass
    if p in ("openai", "deepseek", "custom", "api"):
        return remote()
    return local()


def local_cascade(prompt, on_delta=None, user_text=None):
    from pai import local_cascade as LC
    return LC.complete(prompt, on_delta=on_delta, user_text=user_text)


def local_duo(prompt, on_delta=None, user_text=None, smart=False):
    from pai import local_duo as DUO
    return DUO.complete(prompt, on_delta=on_delta, user_text=user_text, smart=smart)


class CascadeProvider(AIProvider):
    name = "local_cascade"

    def chat(self, messages, **opts):
        text = messages[-1].get("content", "") if messages else ""
        d = local_cascade(text, on_delta=opts.get("on_delta"), user_text=text)
        return {"content": d.get("output", ""), "model": d.get("model", ""), "raw": d}

    def stream(self, messages, **opts):
        yield self.chat(messages, **opts)["content"]

    def embeddings(self, texts, **opts):
        return []

    def health_check(self):
        return {"ok": True, "provider": self.name}

    def model_info(self):
        return {"provider": self.name, "model": "cascade"}


class DuoProvider(AIProvider):
    name = "local_duo"

    def chat(self, messages, **opts):
        text = messages[-1].get("content", "") if messages else ""
        d = local_duo(text, on_delta=opts.get("on_delta"), user_text=text,
                      smart=bool(opts.get("smart")))
        return {"content": d.get("output", ""), "model": d.get("model", ""), "raw": d}

    def stream(self, messages, **opts):
        yield self.chat(messages, **opts)["content"]

    def embeddings(self, texts, **opts):
        return []

    def health_check(self):
        return {"ok": True, "provider": self.name}

    def model_info(self):
        return {"provider": self.name, "model": "duo"}


def complete(prompt, models=None, on_delta=None, on_reasoning=None):
    """业务层唯一文本补全入口；底层仍由 model_gateway 执行。"""
    from pai import model_gateway as MG
    kwargs = {"models": models}
    if on_delta is not None:
        kwargs["on_delta"] = on_delta
    if on_reasoning is not None:
        kwargs["on_reasoning"] = on_reasoning
    return MG.complete(prompt, **kwargs)
