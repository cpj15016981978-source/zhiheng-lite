# -*- coding: utf-8 -*-
"""本地模型包管理：登记、检测、按角色选择、0.6B 路由。"""
from __future__ import annotations

import json
import os
import urllib.request

CAPABILITY = {
    "name": "本地模型包管理",
    "switch_env": "PAI_MODEL_PACKS",
    "default_on": False,
    "stores_user_text": False,
    "network": True,
    "degrade": "模型缺失=明确不可用；路由失败=回退规则；不伪造模型结果",
    "approval": "ADR-044 本地模型接入登记",
}

BASE = "http://127.0.0.1:11434"
PACKS = (
    {"id": "qwen3-0.6b", "ollama": "qwen3:0.6b", "params": "0.6B", "size_mb": 450,
     "roles": ["route", "classify"], "license": "Apache-2.0", "ram_mb": 1000},
    {"id": "qwen3-4b", "ollama": "qwen3-4b-instruct:latest", "params": "4B", "size_mb": 2500,
     "roles": ["chat", "summarize"], "license": "Apache-2.0", "ram_mb": 4000},
    {"id": "deepseek-r1-1.5b", "ollama": "deepseek-r1:1.5b", "params": "1.5B", "size_mb": 1900,
     "roles": ["chat", "summarize"], "license": "MIT", "ram_mb": 2200},
)


def _get(path, timeout=5):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def installed():
    try:
        data = _get("/api/tags")
    except Exception:
        return []
    names = {str(m.get("name") or "") for m in (data.get("models") or [])}
    return [dict(p, installed=p["ollama"] in names) for p in PACKS]


def get(pack_id):
    for p in installed():
        if p["id"] == pack_id:
            return p
    return None


def pick(role="route"):
    for p in installed():
        if p["installed"] and role in p["roles"]:
            return p
    return None


def chat(pack_id, prompt, timeout=180, max_tokens=300):
    p = get(pack_id)
    if not p or not p["installed"]:
        raise RuntimeError("模型包未安装：%s" % pack_id)
    body = json.dumps({
        "model": p["ollama"], "prompt": str(prompt), "stream": False,
        "think": False,
        "options": {"temperature": 0.1, "num_predict": max_tokens},
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + "/api/generate", data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    return str(data.get("response") or "").strip()


LABELS = ("chat", "workspace", "job", "memory", "code", "other")


def route(text, pack_id="qwen3-0.6b"):
    """只做分类；模型不可用/输出越界时回退规则，不猜。"""
    q = str(text or "").strip()
    if not q:
        return {"ok": False, "label": "other", "source": "empty"}
    p = get(pack_id)
    if not p or not p["installed"]:
        return {"ok": False, "label": _rules(q), "source": "rules"}
    prompt = ("把下面这句话分到且只分到一个类别。类别："
              "chat=聊天；workspace=工作区/文件；job=作业/任务/执行；"
              "memory=记忆/上次；code=代码/报错；other=其他。"
              "只输出类别英文单词，不要标点。\n句子：%s\n类别：" % q[:800])
    try:
        raw = chat(pack_id, prompt, timeout=30, max_tokens=8).strip().lower()
    except Exception:
        return {"ok": False, "label": _rules(q), "source": "rules"}
    clean = "".join(ch for ch in raw.lower() if "a" <= ch <= "z")
    for label in LABELS:
        if label in clean:
            return {"ok": True, "label": label, "source": pack_id}
    return {"ok": False, "label": _rules(q), "source": "rules"}


def _rules(q):
    if any(x in q for x in ("作业", "任务", "跑一下", "执行")):
        return "job"
    if any(x in q for x in ("记得", "上次", "之前", "记忆")):
        return "memory"
    if any(x in q for x in ("代码", "函数", "报错", "测试")):
        return "code"
    if any(x in q for x in ("工作区", "空间", "文件")):
        return "workspace"
    if any(x in q for x in ("你好", "谢谢", "在吗")):
        return "chat"
    return "other"
