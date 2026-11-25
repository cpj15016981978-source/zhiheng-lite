# -*- coding: utf-8 -*-
"""能力路由：把“点菜单”变成“说出来”，能力本身不新开页面。"""
from __future__ import annotations

import pathlib
import re

CAPABILITY = {
    "name": "能力路由",
    "switch_env": "PAI_CAPABILITY_ROUTER",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "无匹配=返回 None 交给普通对话；能力失败=明确报错，不伪造结果",
    "approval": "ADR-046：本地只读路由，不新增开关",
}

MEMORY = re.compile(r"(查记忆|记忆里)")
ROI = re.compile(r"(时间账本|省了多少|省了多少时间|作业统计|roi)", re.I)
BRIEF = re.compile(r"(今日简报|今天的简报|今日要事|今天要事)")
OFFICE = re.compile(r"(做|生成|写).{0,12}(Word|word|文档|PPT|ppt|演示|Excel|excel|表格)")
QUESTION = re.compile(r"(能不能|可不可以|会不会|能否|可以吗|怎么做|如何|吗[？?]?$)")
MIN_CONFIDENCE = 0.85
FILES = re.compile(r"(列文件|列目录|看看工作区|读文件|打开文件|写草稿|保存草稿|搜文件)")


def _hits(q):
    hits = []
    if OFFICE.search(q) and not QUESTION.search(q):
        hits.append({"name": "office", "query": q, "confidence": 0.88,
                     "reason": "明确要求生成 Word/PPT/Excel"})
    if BRIEF.search(q):
        hits.append({"name": "brief", "query": q, "confidence": 0.92,
                     "reason": "明确问今日简报"})
    if MEMORY.search(q):
        hits.append({"name": "memory", "query": q, "confidence": 0.90,
                     "reason": "明确查记忆"})
    if ROI.search(q):
        hits.append({"name": "roi", "query": q, "confidence": 0.85,
                     "reason": "明确问时间账本"})
    if FILES.search(q):
        hits.append({"name": "files", "query": q, "confidence": 0.86,
                     "reason": "明确操作工作区文件"})
    return hits


def route(text):
    """返回命中能力；拿不准时返回 abstain（先问一句），不直接抢。"""
    q = str(text or "").strip()
    if not q:
        return None
    hits = _hits(q)
    if not hits:
        return None
    if len(hits) > 1:
        for h in hits:
            h["confidence"] = round(h["confidence"] - 0.25, 2)
        return {"name": "abstain", "query": q, "confidence": max(h["confidence"] for h in hits),
                "reason": "多个能力同时命中", "options": hits}
    best = hits[0]
    if best["confidence"] < MIN_CONFIDENCE:
        return {"name": "abstain", "query": q, "confidence": best["confidence"],
                "reason": "置信度低于阈值", "options": hits}
    return best


def _ask(cap):
    names = {"memory": "查记忆", "roi": "时间账本",
             "brief": "今日简报", "office": "生成 Office 文件"}
    labels = [names.get(h["name"], h["name"]) for h in cap.get("options") or []]
    if not labels:
        return "我不太确定你要做什么。你可以说：查书、查记忆、时间账本、生成 Office。"
    if len(labels) == 1:
        return "你是想%s吗？还是普通聊天？" % labels[0]
    return "我不确定你要哪个：%s。你选一个，或者直接说普通聊天。" % "、".join(labels)


def _clean(q, words):
    s = str(q or "")
    for w in words:
        s = s.replace(w, " ")
    return re.sub(r"\s+", " ", s).strip("，。！？? ")


def _fmt_memory(mem, q):
    rows = mem.recall(q, k=5) if mem is not None else []
    if not rows:
        return "记忆里没有找到相关条目。"
    lines = ["记忆里找到 %d 条：" % len(rows)]
    for r in rows[:5]:
        it = r.get("item") or {}
        if it.get("type") == "episode":
            lines.append("- 你说过：%s" % str(it.get("content") or "")[:160])
        else:
            lines.append("- %s %s = %s" % (it.get("subject"), it.get("predicate"), it.get("object")))
    return "\n".join(lines)


def _fmt_roi():
    from pai import roi as R
    s = R.summary()
    return ("时间账本：作业 %d，成功 %d；估时 %.1f 分，实际 %.1f 分，节省 %.1f 分。"
            % (s["count"], s["success"], s["estimated_minutes"],
               s["actual_minutes"], s["saved_minutes"]))


def _fmt_brief():
    from pai import daily_brief as D
    p = sorted(D.OUT.glob("brief-*.md"))[-1:] if D.OUT.is_dir() else []
    if not p:
        return "今天还没有简报。"
    return p[0].read_text(encoding="utf-8", errors="replace")[:4000]


def dispatch(text, mem=None):
    cap = route(text)
    if not cap:
        return None
    try:
        if cap["name"] == "abstain":
            body = _ask(cap)
        elif cap["name"] == "office":
            from pai import office_gen as OG
            r = OG.generate(cap["query"])
            body = "已生成：\n%s\n类型：%s\n标题：%s" % (r["path"], r["kind"], r["title"])
        elif cap["name"] == "brief":
            body = _fmt_brief()
        elif cap["name"] == "memory":
            body = _fmt_memory(mem, cap["query"])
        elif cap["name"] == "roi":
            body = _fmt_roi()
        elif cap["name"] == "files":
            from pai import file_tools as FT
            q = cap["query"]
            if "写草稿" in q or "保存草稿" in q:
                raw = re.split(r"写草稿|保存草稿", q, 1)[-1].strip(" ：:")
                name, _, text = raw.partition("：")
                if not _:
                    name, _, text = raw.partition(":")
                body = "已写草稿：" + FT.write_draft(name or "draft.md", text)
            elif "读文件" in q or "打开文件" in q:
                rel = re.split(r"读文件|打开文件", q, 1)[-1].strip(" ：:")
                body = FT.read_file(rel)
            elif "搜文件" in q:
                qq = re.split(r"搜文件", q, 1)[-1].strip(" ：:")
                rows = FT.search(qq)
                body = "\n".join("- %s：%s" % (x["path"], x["snippet"][:120]) for x in rows) or "没有命中"
            else:
                rel = re.split(r"列文件|列目录|看看工作区", q, 1)[-1].strip(" ：:")
                body = "\n".join(FT.list_files(rel)) or "工作区为空"
        else:
            return None
    except Exception as e:
        body = "能力执行失败：%s: %s" % (type(e).__name__, e)
    return {"response": body, "trace": {
        "capability": cap["name"], "capability_query": cap["query"],
        "capability_confidence": cap.get("confidence"), "capability_reason": cap.get("reason"),
        "is_recall": cap["name"] == "memory", "is_reason": False,
        "models": [], "cost": 0.0, "tokens": 0,
        "recalled": [], "culture_refs": [], "principles_used": [],
        "reflection": {}, "affect": None}}
