# -*- coding: utf-8 -*-
"""今日一记：把一张卡写进 world_event + 人物实体。

不调模型；写入由调用方显式触发。
"""
import datetime
import json

from pai.world import World

KINDS = {"事": "memory", "承诺": "promise", "故事": "story",
         "memory": "memory", "promise": "promise", "story": "story"}



CAPABILITY = {
    "name": "今日一记",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": True,
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
    "network": False,
    "degrade": "调用方不触发=不写；kind 非法=ValueError",
    "approval": "ADR-046：显式调用才写世界记忆",
}

def save(mem, kind, person, what, why="", next_step="", at=None):
    k = KINDS.get(str(kind or "").strip())
    if not k:
        raise ValueError("kind 只能是 事/承诺/故事")
    if not str(what or "").strip():
        raise ValueError("what 不能为空")
    at = str(at or datetime.datetime.now().isoformat(timespec="minutes"))
    text = str(what).strip()
    if why:
        text += "\n为什么：" + str(why).strip()
    if next_step:
        text += "\n下一步：" + str(next_step).strip()
    ep = mem.write_episode("user", text)
    w = World(mem)
    pid = None
    if str(person or "").strip():
        pid = w.upsert_entity("person", str(person).strip(), source_episode=ep)
    eid = w.add_event(at, k, text, participants=[pid] if pid else [],
                      source_episode=ep, confidence=0.9)
    return {"event_id": eid, "episode_id": ep, "person_id": pid, "at": at, "kind": k}


def cards(mem, limit=20):
    rows = mem.conn.execute(
        "SELECT * FROM world_event WHERE status='active' AND kind IN "
        "('memory','promise','story') ORDER BY at DESC, rowid DESC LIMIT ?",
        (int(limit),)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["participants"] = json.loads(d.get("participants") or "[]")
        except ValueError:
            d["participants"] = []
        out.append(d)
    return out
