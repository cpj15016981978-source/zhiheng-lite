# -*- coding: utf-8 -*-
"""LLM Wiki v1：可读、可引用、可回溯的 Markdown 知识页。

不是聊天记录，是“确认过的知识”。
只读；不自动改页、不调模型、不出网。
"""
from __future__ import annotations

from pai import paths

import os
import pathlib
import re

CAPABILITY = {
    "name": "LLM Wiki v1",
    "switch_env": "PAI_WIKI",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "关态返回空；开态只读 wiki/*.md，不执行、不调模型、不出网",
    "probe_cost": "只读模块常量与文件",
    "approval": "未获单独批准；由调用方显式开启，不自动加载",
}

ROOT = pathlib.Path(os.environ.get(
    "PAI_WIKI_DIR",
    str(paths.APP_ROOT / "wiki")))


def enabled():
    return (os.environ.get("PAI_WIKI") or "").strip().lower() == "on"


def set_enabled(on):
    os.environ["PAI_WIKI"] = "on" if on else "off"
    return enabled()


def _tokens(text):
    out = []
    for part in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", str(text or "")):
        if part.isascii():
            out.append(part.lower())
        elif len(part) <= 2:
            out.append(part)
        else:
            out.extend(part[i:i + 2] for i in range(len(part) - 1))
    return list(dict.fromkeys(out))


def parse_page(text):
    meta, body = {}, text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            body = parts[2].lstrip("\n")
            for line in parts[1].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
    return meta, body


class WikiError(ValueError):
    pass


class Wiki:
    def __init__(self, root=None):
        self.root = pathlib.Path(root or ROOT)

    def _paths(self):
        if not self.root.exists():
            return []
        return sorted(self.root.glob("*.md"))

    def list(self):
        if not enabled():
            return []
        out = []
        for p in self._paths():
            try:
                meta, body = parse_page(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            meta["_path"] = str(p)
            meta["_body"] = body
            out.append(meta)
        return out

    def get(self, slug):
        for page in self.list():
            if page.get("slug") == slug:
                return page
        return None

    def search(self, query, limit=5):
        if not enabled():
            return []
        q = _tokens(query)
        scored = []
        for page in self.list():
            text = " ".join(str(page.get(k) or "") for k in ("title", "tags", "slug"))
            title_hits = sum(1 for t in q if t in _tokens(text))
            body_hits = sum(1 for t in q if t in _tokens(page.get("_body", "")))
            score = title_hits * 3 + body_hits
            if score:
                scored.append((score, page))
        scored.sort(key=lambda x: -x[0])
        return [p for _n, p in scored[:limit]]

    def context(self, query, limit=3):
        pages = self.search(query, limit=limit)
        lines = []
        for p in pages:
            day = p.get("updated") or ""
            src = p.get("source") or "未标来源"
            body = re.sub(r"\s+", " ", p.get("_body", ""))[:600]
            lines.append("- [wiki:%s｜%s｜%s] %s" % (p.get("slug"), src, day, body))
        return "\n".join(lines)

    def links(self, slug):
        page = self.get(slug)
        if not page:
            return []
        return list(dict.fromkeys(re.findall(r"\[\[([^\]]+)\]\]", page.get("_body", ""))))

    def _slug_for(self, target):
        target = str(target or "").strip()
        for p in self.list():
            if p.get("slug") == target or p.get("title") == target:
                return p.get("slug")
        return target

    def backlinks(self, slug):
        out = []
        for p in self.list():
            targets = [self._slug_for(t) for t in self.links(p.get("slug"))]
            if slug in targets:
                out.append(p.get("slug"))
        return out

    def validate(self):
        problems = []
        seen = set()
        for p in self.list():
            for key in ("title", "slug", "status", "source", "updated"):
                if not p.get(key):
                    problems.append("%s 缺 %s" % (p.get("_path"), key))
            slug = p.get("slug")
            if slug in seen:
                problems.append("slug 重复：%s" % slug)
            seen.add(slug)
        return problems
