# -*- coding: utf-8 -*-
"""当前自定义模型选择：场景之外的单模型入口。"""
from __future__ import annotations

import json
import os

from pai import atomic as AT
from pai import paths
from pai import model_registry as MR

FLAG = paths.DATA_DIR / "active_custom.json"

CAPABILITY = {
    "name": "自定义模型选择",
    "switch_env": "PAI_CUSTOM_MODEL",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "未知/已删除模型=清除选择；缺 Key=测试失败，不静默",
    "approval": "ADR-046：用户显式选择模型",
}


def status():
    try:
        return {"name": json.loads(FLAG.read_text(encoding="utf-8")).get("name", "")}
    except Exception:
        return {"name": ""}


def apply():
    name = status()["name"]
    cfg = MR.find(name) if name else None
    if not cfg:
        # 没有显式选择时，自动选已配置 Key 的 DeepSeek；不猜其他家。
        cand = next((x for x in MR.list_models()
                     if x.get("provider") == "deepseek" and x.get("kind") == "api"), None)
        if cand and any(x.get("name") == cand.get("name") and x.get("configured")
                        for x in MR.key_status()):
            try:
                AT.write_text(FLAG, json.dumps({"name": cand["name"]}, ensure_ascii=False), encoding="utf-8")
                name = cand["name"]; cfg = MR.find(name)
            except Exception:
                return status()
        else:
            return status()
    key = ""
    if cfg.get("key_enc"):
        try:
            from pai import providers as PR
            key = PR._unprotect(cfg.get("key_enc")) or ""
        except Exception:
            key = ""
    if cfg.get("kind") == "local" or cfg.get("provider") == "ollama":
        os.environ["PAI_MODEL_PROVIDER"] = "ollama"
        os.environ["PAI_MODEL_NAME"] = cfg.get("model") or name
    elif cfg.get("provider") == "deepseek":
        os.environ["PAI_MODEL_PROVIDER"] = "deepseek"
        os.environ["PAI_MODEL_NAME"] = cfg.get("model") or "deepseek-chat"
        if cfg.get("base_url"):
            os.environ["PAI_DEEPSEEK_BASE_URL"] = cfg.get("base_url")
        if key:
            os.environ["DEEPSEEK_API_KEY"] = key
    else:
        os.environ["PAI_MODEL_PROVIDER"] = "openai"
        os.environ["PAI_MODEL_NAME"] = cfg.get("model") or name
        if cfg.get("base_url"):
            os.environ["PAI_OPENAI_BASE_URL"] = cfg.get("base_url")
        if key:
            os.environ["OPENAI_API_KEY"] = key
    os.environ["PAI_CUSTOM_MODEL"] = name
    return status()

def select(name):
    cfg = MR.find(str(name or ""))
    if not cfg:
        raise ValueError("模型不存在：%s" % name)
    AT.write_text(FLAG, json.dumps({"name": name}, ensure_ascii=False), encoding="utf-8")
    return apply()


def clear():
    try:
        FLAG.unlink()
    except OSError:
        pass
    os.environ.pop("PAI_CUSTOM_MODEL", None)
    return {"name": ""}
