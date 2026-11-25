# -*- coding: utf-8 -*-
"""ModelRouter：把 chat/embed/rerank/asr/tts/vision/guard/judge 分角色接上。"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import urllib.request

from . import asr as ASR
from . import embedding as EMB
from . import model_gateway as MG
from . import rerank as RR
from . import roles_config as RC

ROLES = ("chat", "embed", "rerank", "asr", "tts", "vision", "guard", "judge")

CAPABILITY = {
    "name": "ModelRouter 角色路由",
    "switch_env": "PAI_ROUTER",
    "default_on": False,
    "stores_user_text": False,
    "network": True,
    "degrade": "角色关闭/网络失败时明确抛错；记忆检索另有本地兜底",
    "approval": "ADR-044 本地模型/HTTP 接入登记",
}


class RouterError(RuntimeError):
    pass


class ModelRouter:
    def _check(self, role):
        if not RC.enabled(role):
            raise RouterError("角色已关闭：%s（在桌面角色状态里打开）" % role)

    def chat(self, prompt: str, **kwargs) -> str:
        self._check("chat")
        return MG.complete(prompt, **kwargs)

    def embed(self, texts):
        self._check("embed")
        return EMB.embed(texts)

    def rerank(self, query, documents, top_k=None):
        self._check("rerank")
        return RR.rerank(query, documents, top_k=top_k)

    def asr(self, path) -> str:
        self._check("asr")
        return ASR.transcribe(path)

    def tts(self, text: str, **kwargs):
        self._check("tts")
        from . import voice
        return voice.speak(text, **kwargs)

    def vision(self, image_path, question="描述图片") -> str:
        self._check("vision")
        from . import offline as OFF
        OFF.assert_allowed("router_vision", RouterError, "模型视觉路由")
        base = (os.environ.get("PAI_VISION_BASE_URL") or "").strip().rstrip("/")
        if not base:
            raise RouterError("vision 未配置：设置 PAI_VISION_BASE_URL")
        data = base64.b64encode(pathlib.Path(image_path).read_bytes()).decode("ascii")
        body = json.dumps({"model": os.environ.get("PAI_VISION_MODEL", "qwen-vl"),
                           "image": data, "question": question}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(base + "/vision", data=body, method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return str(json.loads(r.read().decode("utf-8")).get("text") or "")

    def guard(self, text: str) -> dict:
        self._check("guard")
        low = str(text or "").lower()
        risky = [w for w in ("rm -rf", "format ", "del /", "drop table", "git reset --hard",
                             "curl ", "wget ", "powershell -enc") if w in low]
        if risky:
            return {"decision": "review", "reasons": risky}
        return {"decision": "allow", "reasons": []}

    def judge(self, task: str, a: str, b: str) -> str:
        self._check("judge")
        return self.chat("你是严格评审。按任务要求只回答 A/B/TIE，并给一句理由。\n"
                         "任务：%s\nA：%s\nB：%s" % (task, a, b))

    def status(self) -> dict:
        return {
            "chat": {"backend": MG.active_provider()},
            "embed": {"backend": EMB.backend()},
            "rerank": {"backend": RR.backend()},
            "asr": {"backend": ASR.backend(), "available": ASR.available()},
            "tts": {"backend": (os.environ.get("PAI_VOICE_BACKEND") or "windows-sapi")},
            "vision": {"backend": "http" if os.environ.get("PAI_VISION_BASE_URL") else "none"},
            "guard": {"backend": "rules"},
            "judge": {"backend": "chat"},
            "enabled": RC.all_roles(),
        }


def default() -> ModelRouter:
    return ModelRouter()
