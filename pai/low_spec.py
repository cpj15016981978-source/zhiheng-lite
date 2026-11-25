# -*- coding: utf-8 -*-
"""低配模式：8GB/无独显只加载 1.5B，不加载 4B。"""
from __future__ import annotations

from pai import paths

import ctypes
import json
import os
import pathlib
import threading
import time
import urllib.request

from pai import atomic as AT

ROOT = pathlib.Path(__file__).resolve().parent.parent
FLAG = paths.DATA_DIR / "low_spec.json"

CAPABILITY = {
    "name": "低配模式",
    "switch_env": "PAI_LOW_SPEC",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "模型缺失=提示先拉取；切换失败=保留原设置，不静默降级",
    "approval": "ADR-046：本地单用户性能档，默认关，用户显式开启",
}

PRESET = {"provider": "ollama", "model": "qwen2.5:1.5b", "offline": True,
          "cascade_router": "qwen3:0.6b", "cascade_small": "qwen2.5:1.5b",
          "cascade_main": "qwen2.5:1.5b", "num_ctx": "2048"}
MEMORY_BUDGET_MB = int(os.environ.get("PAI_LOW_SPEC_BUDGET_MB") or 3072)
MAX_CONCURRENT = int(os.environ.get("PAI_LOW_SPEC_CONCURRENCY") or 1)
_SEM = threading.BoundedSemaphore(max(1, MAX_CONCURRENT))


def _win_memory():
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MEMORYSTATUSEX(); m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        raise OSError("GlobalMemoryStatusEx failed")
    return int(m.ullTotalPhys // (1024 * 1024)), int(m.ullAvailPhys // (1024 * 1024))


def memory_status():
    try:
        total, free = _win_memory()
    except Exception:
        total, free = 0, 0
    return {"total_mb": total, "free_mb": free, "budget_mb": MEMORY_BUDGET_MB,
            "over_budget": bool(total and total < MEMORY_BUDGET_MB),
            "can_load": bool(total and free >= 1024 and total >= MEMORY_BUDGET_MB),
            "concurrency": MAX_CONCURRENT}


def acquire(timeout=30):
    if not _SEM.acquire(timeout=timeout):
        raise TimeoutError("低配并发已满（%d）" % MAX_CONCURRENT)
    return True


def release():
    try:
        _SEM.release()
    except ValueError:
        pass


def loaded_models():
    base = (os.environ.get("PAI_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
    try:
        with urllib.request.urlopen(base + "/api/ps", timeout=3) as r:
            d = json.loads(r.read().decode("utf-8") or "{}")
        return [{"name": m.get("name"), "size_mb": round((m.get("size") or 0) / 1048576, 1)}
                for m in d.get("models", [])]
    except Exception:
        return []


def unload_models():
    base = (os.environ.get("PAI_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
    before = loaded_models()
    out = []
    for m in before:
        name = m.get("name")
        if not name:
            continue
        try:
            req = urllib.request.Request(base + "/api/generate",
                                         data=json.dumps({"model": name, "keep_alive": 0}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                out.append({"model": name, "ok": r.status == 200})
        except Exception as e:
            out.append({"model": name, "ok": False, "error": type(e).__name__})
    after = loaded_models()
    for _ in range(10):
        if not after:
            break
        time.sleep(0.5)
        after = loaded_models()
    return {"before": before, "unloaded": out, "after": after,
            "reclaimed": len(after) == 0, "ts": time.time()}


def status():
    try:
        d = json.loads(FLAG.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    return {"enabled": bool(d.get("enabled")), **PRESET,
            "resources": memory_status(), "loaded": loaded_models()}


def enable():
    os.environ["PAI_MODEL_PROVIDER"] = PRESET["provider"]
    os.environ["PAI_MODEL_NAME"] = PRESET["model"]
    os.environ["PAI_CASCADE_ROUTER_MODEL"] = PRESET["cascade_router"]
    os.environ["PAI_CASCADE_SMALL_MODEL"] = PRESET["cascade_small"]
    os.environ["PAI_CASCADE_MAIN_MODEL"] = PRESET["cascade_main"]
    os.environ["PAI_OLLAMA_NUM_CTX"] = PRESET["num_ctx"]
    AT.write_text(FLAG, json.dumps({"enabled": True}, ensure_ascii=False), encoding="utf-8")
    from pai import model_settings as MS
    MS.save(PRESET["provider"], PRESET["model"], True)
    return status()


def disable():
    AT.write_text(FLAG, json.dumps({"enabled": False}, ensure_ascii=False), encoding="utf-8")
    return status()


def apply():
    if status().get("enabled"):
        return enable()
    return status()
