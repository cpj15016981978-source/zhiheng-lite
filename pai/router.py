# -*- coding: utf-8 -*-
"""记忆路由器 v1：按问题选 chat / wiki / code / skill。

只读检索；不调模型、不出网、不执行代码。
"""
from __future__ import annotations

import os
import re

from . import code_graph as CG
from . import memory_brain as MB
from . import skill_library as SL
from . import wiki as WK

CAPABILITY = {
    "name": "记忆路由器 v1",
    "switch_env": "PAI_ROUTER",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "关态不路由；开态只调用四个只读检索器，不执行、不调模型、不出网",
    "probe_cost": "只读模块常量与文件",
    "approval": "未获单独批准；由调用方显式开启",
}

CHAT_MARKERS = ("上次", "之前", "以前", "说过", "记得", "当前主线", "记忆", "历史")
WIKI_MARKERS = ("是什么", "协议", "设计", "怎么定", "知识", "文档", "为什么", "定义")
SKILL_MARKERS = ("怎么做", "步骤", "流程", "日报", "生成", "技能", "跑一遍")
CODE_MARKERS = ("函数", "类", "调用", "测试", "代码", "文件", "实现", "改这里", "影响")


def enabled():
    return (os.environ.get("PAI_ROUTER") or "").strip().lower() == "on"


class Router:
    def __init__(self, mem=None, wiki_root=None, code_db=None, skills_root=None):
        self.mem = mem
        self.brain = MB.MemoryBrain(mem) if mem is not None else None
        self.wiki = WK.Wiki(wiki_root) if WK.enabled() else None
        self.code = CG.CodeGraph(code_db) if (CG.enabled() and code_db) else None
        self.skills = SL.SkillLibrary(skills_root) if SL.enabled() else None

    def plan(self, question):
        q = str(question or "")
        picked = []
        if any(m in q for m in CHAT_MARKERS):
            picked.append("chat")
        if any(m in q for m in WIKI_MARKERS):
            picked.append("wiki")
        if any(m in q for m in SKILL_MARKERS):
            picked.append("skill")
        if any(m in q for m in CODE_MARKERS) or re.search(r"[A-Za-z_][A-Za-z0-9_]{2,}", q):
            picked.append("code")
        if not picked:
            picked = ["wiki", "chat"]
        return picked[:3]

    def route(self, question, k=3):
        if not enabled():
            return {"plan": [], "context": "", "sources": []}
        plan = self.plan(question)
        lines, sources = [], []

        if "chat" in plan and self.brain is not None:
            try:
                r = self.brain.recall(question, k=k)
                if r.get("context"):
                    lines.append("[chat]\n" + r["context"])
                    sources.append({"kind": "chat", "items": len(r.get("items") or [])})
            except Exception:
                pass

        if "wiki" in plan and self.wiki is not None:
            try:
                pages = self.wiki.search(question, limit=k)
                if pages:
                    lines.append("[wiki]\n" + self.wiki.context(question, limit=k))
                    sources.append({"kind": "wiki", "items": [p.get("slug") for p in pages]})
            except Exception:
                pass

        if "code" in plan and self.code is not None:
            try:
                names = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", str(question))
                names = [n for n in names if n.lower() not in {"the", "and", "what", "how"}][:4]
                code_lines = []
                for name in names:
                    hits = self.code.find(name, limit=3)
                    if hits:
                        code_lines.append("- %s: %s:%s" % (
                            name, hits[0]["file"], hits[0]["line"]))
                        tests = self.code.tests_for(name, limit=3)
                        for t in tests:
                            code_lines.append("  test: %s:%s" % (t["file"], t["line"]))
                if code_lines:
                    lines.append("[code]\n" + "\n".join(code_lines))
                    sources.append({"kind": "code", "items": len(code_lines)})
            except Exception:
                pass

        if "skill" in plan and self.skills is not None:
            try:
                found = self.skills.find(question, limit=2)
                if found:
                    lines.append("[skill]\n" + "\n".join(
                        "- %s: %s" % (s.get("name"), s.get("description")) for s in found))
                    sources.append({"kind": "skill", "items": [s.get("name") for s in found]})
            except Exception:
                pass

        return {"plan": plan, "context": "\n\n".join(lines), "sources": sources}
