# -*- coding: utf-8 -*-
"""B0 世界模型 v0：可核对的实体/关系/事件/信念/矛盾。

同库、手写入口、不接 prompt、不自动抽取、不做模拟器。
"""
import datetime
import json
import uuid

CAPABILITY = {
    "name": "B0 世界模型 v0",
    "switch_env": "PAI_WORLD",
    "default_on": False,
    "stores_user_text": True,
    "network": False,
    "degrade": "未接 prompt；enabled()=False 时只是库函数，不自动读写、不阻塞对话",
    "probe_cost": "只读模块常量；不连库、不建表、不调模型、不联网",
    "approval": "未获单独批准：B0 v0 只做 schema/读写/矛盾检测，未接日常",
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
}


def enabled():
    import os
    return (os.environ.get("PAI_WORLD") or "").strip().lower() == "on"


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _nid(prefix):
    return "%s_%s" % (prefix, uuid.uuid4().hex[:12])


class World:
    def __init__(self, mem):
        self.mem = mem
        self.conn = mem.conn

    # -------- 写 --------
    def upsert_entity(self, kind, name, aliases=None, source_episode=None,
                      sensitivity="normal"):
        kind = str(kind or "").strip()
        name = str(name or "").strip()
        if kind not in ("person", "place", "org", "thing"):
            raise ValueError("entity type 只能是 person/place/org/thing")
        if not name:
            raise ValueError("entity name 不能为空")
        if sensitivity == "secret":
            raise ValueError("secret 不落库")
        if source_episode is None:
            raise ValueError("entity 必须带 source_episode")
        row = self.conn.execute(
            "SELECT * FROM world_entity WHERE type=? AND lower(name)=lower(?)"
            " AND status='active'", (kind, name)).fetchone()
        if row:
            old = set(json.loads(row["aliases"] or "[]"))
            old.update(aliases or [])
            self.conn.execute(
                "UPDATE world_entity SET aliases=?, source_episode=? WHERE id=?",
                (json.dumps(sorted(old), ensure_ascii=False),
                 source_episode, row["id"]))
            self.conn.commit()
            return row["id"]
        eid = _nid("ent")
        self.conn.execute(
            "INSERT INTO world_entity(id,type,name,aliases,sensitivity,created_at,"
            "source_episode,status) VALUES(?,?,?,?,?,?,?,?)",
            (eid, kind, name, json.dumps(aliases or [], ensure_ascii=False),
             sensitivity, _now(), source_episode, "active"))
        self.conn.commit()
        return eid

    def add_relation(self, subject_id, predicate, object_id=None, object_value=None,
                     valid_from=None, valid_to=None, confidence=0.5,
                     source_episode=None):
        if not subject_id:
            raise ValueError("subject_id 不能为空")
        if not str(predicate or "").strip():
            raise ValueError("predicate 不能为空")
        if (object_id is None) == (object_value is None):
            raise ValueError("object_id 与 object_value 必须二选一")
        if source_episode is None:
            raise ValueError("relation 必须带 source_episode")
        if not (0.0 <= float(confidence) <= 1.0):
            raise ValueError("confidence 必须在 0..1")
        sub = self.conn.execute(
            "SELECT id FROM world_entity WHERE id=? AND status='active'",
            (subject_id,)).fetchone()
        if not sub:
            raise ValueError("subject 不存在或已停用")
        rid = _nid("rel")
        self.conn.execute(
            "INSERT INTO world_relation(id,subject_id,predicate,object_id,object_value,"
            "valid_from,valid_to,confidence,source_episode,observed_at,status,retire_reason)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, subject_id, str(predicate), object_id, object_value,
             valid_from, valid_to, float(confidence), source_episode, _now(),
             "active", ""))
        self.conn.commit()
        return rid

    def add_event(self, at, kind, summary, participants=None, place_id=None,
                  source_episode=None, confidence=0.5, sensitivity="normal"):
        if not at or not str(kind or "").strip() or not str(summary or "").strip():
            raise ValueError("event at/kind/summary 不能为空")
        if source_episode is None:
            raise ValueError("event 必须带 source_episode")
        if sensitivity == "secret":
            raise ValueError("secret 不落库")
        eid = _nid("evt")
        self.conn.execute(
            "INSERT INTO world_event(id,at,kind,summary,participants,place_id,"
            "source_episode,confidence,sensitivity,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (eid, at, str(kind), str(summary),
             json.dumps(participants or [], ensure_ascii=False), place_id,
             source_episode, float(confidence), sensitivity, "active"))
        self.conn.commit()
        return eid

    def set_belief(self, holder, predicate, value, confidence=0.5, source=None,
                   subject_id=None):
        if holder not in ("self", "user"):
            raise ValueError("holder 只能是 self/user")
        if not str(predicate or "").strip():
            raise ValueError("predicate 不能为空")
        if source is None:
            raise ValueError("belief 必须带 source")
        bid = _nid("bel")
        self.conn.execute(
            "INSERT INTO world_belief(id,holder,subject_id,predicate,value,confidence,"
            "source,updated_at,status) VALUES(?,?,?,?,?,?,?,?,?)",
            (bid, holder, subject_id, str(predicate), value, float(confidence),
             str(source), _now(), "active"))
        self.conn.commit()
        return bid

    # -------- 读 --------
    def relations(self, subject_id, at=None):
        at = at or _now()
        rows = self.conn.execute(
            "SELECT * FROM world_relation WHERE subject_id=? AND status='active'"
            " AND (valid_from IS NULL OR valid_from<=?)"
            " AND (valid_to IS NULL OR valid_to>=?) ORDER BY observed_at",
            (subject_id, at, at)).fetchall()
        return [dict(r) for r in rows]

    def events_between(self, start, end):
        rows = self.conn.execute(
            "SELECT * FROM world_event WHERE status='active' AND at>=? AND at<=?"
            " ORDER BY at", (start, end)).fetchall()
        return [dict(r) for r in rows]

    def beliefs(self, holder="user"):
        rows = self.conn.execute(
            "SELECT * FROM world_belief WHERE holder=? AND status='active'"
            " ORDER BY updated_at", (holder,)).fetchall()
        return [dict(r) for r in rows]

    # -------- 矛盾 --------
    @staticmethod
    def _overlap(a1, a2, b1, b2):
        a1 = a1 or "0000-01-01"
        a2 = a2 or "9999-12-31"
        b1 = b1 or "0000-01-01"
        b2 = b2 or "9999-12-31"
        return a1 <= b2 and b1 <= a2

    def check_contradictions(self):
        rows = self.conn.execute(
            "SELECT * FROM world_relation WHERE status='active'"
            " ORDER BY subject_id, predicate, observed_at").fetchall()
        groups = {}
        for r in rows:
            groups.setdefault((r["subject_id"], r["predicate"]), []).append(r)
        found = []
        for (sub, pred), rs in groups.items():
            for i in range(len(rs)):
                for j in range(i + 1, len(rs)):
                    a, b = rs[i], rs[j]
                    va = (a["object_id"], a["object_value"])
                    vb = (b["object_id"], b["object_value"])
                    if va == vb:
                        continue
                    if not self._overlap(a["valid_from"], a["valid_to"],
                                         b["valid_from"], b["valid_to"]):
                        continue
                    exists = self.conn.execute(
                        "SELECT 1 FROM world_contradiction WHERE status='open'"
                        " AND ((a_id=? AND b_id=?) OR (a_id=? AND b_id=?))",
                        (a["id"], b["id"], b["id"], a["id"])).fetchone()
                    if exists:
                        continue
                    cid = _nid("cnt")
                    self.conn.execute(
                        "INSERT INTO world_contradiction(id,a_kind,a_id,b_kind,b_id,"
                        "detected_at,resolution,status) VALUES(?,?,?,?,?,?,?,?)",
                        (cid, "relation", a["id"], "relation", b["id"],
                         _now(), "", "open"))
                    found.append(cid)
        self.conn.commit()
        return found

    def retire(self, kind, obj_id, reason=""):
        table = {"entity": "world_entity", "relation": "world_relation",
                 "event": "world_event", "belief": "world_belief"}.get(kind)
        if not table:
            raise ValueError("kind 只能是 entity/relation/event/belief")
        if table == "world_relation":
            self.conn.execute(
                "UPDATE world_relation SET status='retired', retire_reason=? WHERE id=?",
                (str(reason or ""), obj_id))
        else:
            self.conn.execute(
                "UPDATE %s SET status='retired' WHERE id=?" % table, (obj_id,))
        self.conn.commit()
