# -*- coding: utf-8 -*-
"""可验证结果：文件存在/内容/退出码。只做检查，不执行命令。"""
from __future__ import annotations

import pathlib

from . import paths as PATHS

CAPABILITY = {
    "name": "可验证结果",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "文件/退出码检查失败=返回 ok:false，不执行命令、不伪造通过",
    "approval": "ADR-046：只读校验，不新增开关",
}


def _safe(rel):
    p = (PATHS.WORKSPACE / str(rel or "")).resolve()
    base = PATHS.WORKSPACE.resolve()
    if base != p and base not in p.parents:
        raise ValueError("路径越出 workspace")
    return p


def _exit_code(result):
    if isinstance(result, dict):
        for k in ("exit_code", "exit", "returncode", "code"):
            if k in result:
                return result.get(k)
    return None


def verify(spec, result=None):
    """spec: {file_exists, file_contains, file_not_contains, exit_code}。"""
    spec = spec if isinstance(spec, dict) else {}
    checks = []
    def _items(v):
        if not v:
            return []
        return list(v) if isinstance(v, (list, tuple)) else [v]
    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})
    for rel in _items(spec.get("file_exists")):
        try:
            p = _safe(rel)
            add("file_exists:" + str(rel), p.is_file(), str(p))
        except Exception as e:
            add("file_exists:" + str(rel), False, "%s: %s" % (type(e).__name__, e))
    for item in _items(spec.get("file_contains")):
        if isinstance(item, dict):
            rel, needle = item.get("path"), item.get("text")
        else:
            rel, needle = item
        try:
            text = _safe(rel).read_text(encoding="utf-8", errors="replace")
            add("file_contains:" + str(rel), str(needle) in text, "needle=%r" % str(needle)[:80])
        except Exception as e:
            add("file_contains:" + str(rel), False, "%s: %s" % (type(e).__name__, e))
    for item in _items(spec.get("file_not_contains")):
        if isinstance(item, dict):
            rel, needle = item.get("path"), item.get("text")
        else:
            rel, needle = item
        try:
            text = _safe(rel).read_text(encoding="utf-8", errors="replace")
            add("file_not_contains:" + str(rel), str(needle) not in text, "needle=%r" % str(needle)[:80])
        except Exception as e:
            add("file_not_contains:" + str(rel), False, "%s: %s" % (type(e).__name__, e))
    if "exit_code" in spec:
        got = _exit_code(result)
        want = spec.get("exit_code")
        add("exit_code", str(got) == str(want), "want=%s got=%s" % (want, got))
    return {"ok": all(c["ok"] for c in checks) and bool(checks), "checks": checks}


__all__ = ["verify"]
