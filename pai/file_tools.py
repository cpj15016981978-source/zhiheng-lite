# -*- coding: utf-8 -*-
"""安全文件工具：只读工作区，写入只允许 drafts，不执行、不越界。"""
from __future__ import annotations

from pai import paths

import os
import pathlib

from pai import atomic as AT

ROOT = pathlib.Path(os.environ.get("PAI_WORKSPACE") or paths.WORKSPACE)

CAPABILITY = {
    "name": "安全文件工具",
    "switch_env": "PAI_FILE_TOOLS",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "越界路径=拒绝；超限=拒绝；写入只到 drafts，不覆盖已有文件",
    "approval": "ADR-046：本地工作区只读为主，写草稿固定目录",
}


def _root():
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "drafts").mkdir(exist_ok=True)
    (ROOT / "output").mkdir(exist_ok=True)
    return ROOT


def _safe(rel):
    p = (_root() / str(rel or "")).resolve()
    root = _root().resolve()
    if p != root and root not in p.parents:
        raise ValueError("路径越界：%s" % rel)
    return p


def list_files(sub=""):
    p = _safe(sub)
    if not p.exists():
        return []
    if p.is_file():
        return [str(p.relative_to(_root()))]
    return [str(x.relative_to(_root())) for x in sorted(p.rglob("*")) if x.is_file()][:200]


def read_file(rel, limit=20000):
    p = _safe(rel)
    if not p.is_file():
        raise ValueError("不是文件：%s" % rel)
    if p.stat().st_size > 2_000_000:
        raise ValueError("文件过大")
    return p.read_text(encoding="utf-8", errors="replace")[:limit]


def write_draft(name, text):
    rel = pathlib.Path("drafts") / pathlib.Path(str(name or "draft.md")).name
    p = _safe(rel)
    if p.exists():
        raise ValueError("草稿已存在，不覆盖：%s" % p.name)
    data = str(text or "")
    if len(data.encode("utf-8")) > 200_000:
        raise ValueError("草稿过大")
    AT.write_text(p, data, encoding="utf-8")
    return str(p.relative_to(_root()))


def search(query, limit=20):
    q = str(query or "").lower()
    if not q:
        return []
    out = []
    for p in _root().rglob("*"):
        if not p.is_file() or p.suffix.lower() not in (".md", ".txt", ".py", ".json", ".csv"):
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if q in t.lower():
            out.append({"path": str(p.relative_to(_root())), "snippet": t[:500]})
        if len(out) >= limit:
            break
    return out
