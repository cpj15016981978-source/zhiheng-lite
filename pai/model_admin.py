# -*- coding: utf-8 -*-
"""模型管理：列出本机 Ollama 模型，按用户输入拉取。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pai import paths
import urllib.request

CAPABILITY = {
    "name": "模型管理",
    "switch_env": "PAI_MODEL_ADMIN",
    "default_on": True,
    "stores_user_text": False,
    "network": True,
    "degrade": "Ollama 不可达=返回空列表并明确提示；pull 失败=保留原模型",
    "approval": "ADR-046：用户显式添加模型",
}


def _base():
    return os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def installed():
    try:
        with urllib.request.urlopen(_base() + "/api/tags", timeout=5) as r:
            d = json.loads(r.read().decode("utf-8"))
        return [{"name": x.get("name"), "size": x.get("size")} for x in d.get("models", [])]
    except Exception as e:
        return [{"error": "%s: %s" % (type(e).__name__, e)}]


def pull(name):
    name = str(name or "").strip()
    if not name or any(c in name for c in "&|;<>"):
        raise ValueError("模型名不合法")
    exe = (os.environ.get("OLLAMA_EXE") or shutil.which("ollama")
           or str(paths.APP_ROOT.parent / "ollama" / "ollama.exe"))
    if not os.path.exists(exe):
        raise RuntimeError("找不到 ollama 可执行文件，请先安装 Ollama")
    p = subprocess.run([exe, "pull", name], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    return {"ok": p.returncode == 0, "name": name, "tail": (p.stdout or p.stderr or "")[-1200:]}
