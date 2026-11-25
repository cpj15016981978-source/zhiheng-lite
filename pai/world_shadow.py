# -*- coding: utf-8 -*-
"""B0 世界模型 · 影子抽取 v0。

默认关；开着也只写候选文件，不写 world 表、不写 memory。
规则抽取是 v0，不调模型、不宣称“理解”。
"""
import datetime
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CAPABILITY = {
    "name": "B0 世界影子抽取 v0",
    "switch_env": "PAI_WORLD_SHADOW",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "默认关；开=只把候选写 data/world_shadow/，不写 world 表、不写 memory、不调模型",
    "probe_cost": "只读文本；不连库、不调模型、不联网",
    "approval": "未获单独批准：影子只记候选，不落世界表。",
}

_NAME = re.compile(r"(?:我叫|我的名字是|我是)(?!什么|啥|谁|哪|不是|不)\s*([\u4e00-\u9fa5A-Za-z0-9]{1,12})")
_REL = re.compile(r"我(?:的)?(妻子|老婆|丈夫|老公|朋友|同事|师父|师傅)是\s*([\u4e00-\u9fa5A-Za-z0-9]{1,12})")
_WORK = re.compile(r"我在\s*([\u4e00-\u9fa5A-Za-z0-9]{1,20})\s*(?:工作|上班)")
_LIKE = re.compile(r"我喜欢\s*([^\s，。！？,.!?]{1,20})")


def enabled():
    return (os.environ.get("PAI_WORLD_SHADOW") or "").strip().lower() == "on"


def extract(text):
    t = str(text or "")
    out = []
    for m in _NAME.finditer(t):
        out.append({"kind": "entity", "type": "person", "name": m.group(1),
                    "evidence": "我叫/我是"})
    for m in _REL.finditer(t):
        out.append({"kind": "entity", "type": "person", "name": m.group(2),
                    "evidence": m.group(1)})
        out.append({"kind": "relation", "subject": "用户",
                    "predicate": m.group(1), "object": m.group(2),
                    "evidence": m.group(1)})
    for m in _WORK.finditer(t):
        out.append({"kind": "entity", "type": "org", "name": m.group(1),
                    "evidence": "我在…工作"})
    for m in _LIKE.finditer(t):
        out.append({"kind": "belief", "holder": "user", "predicate": "喜欢",
                    "value": m.group(1), "evidence": "我喜欢…"})
    return out


def observe(text, source_episode=None, source="chat"):
    """只记候选；不开=什么都不做。"""
    if not enabled():
        return None
    cands = extract(text)
    if not cands:
        return None
    d = Path(os.environ.get("PAI_WORLD_SHADOW_DIR") or (ROOT / "data" / "world_shadow"))
    d.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": str(source or "chat"),
        "source_episode": source_episode,
        "candidates": cands,
    }
    p = d / ("shadow-%s.jsonl" % datetime.datetime.now().strftime("%Y%m%d"))
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return p
