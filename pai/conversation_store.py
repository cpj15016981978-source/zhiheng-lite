# -*- coding: utf-8 -*-
"""会话同步：把青鸟 Chat 的会话快照存进本机 SQLite。

不是聊天记忆库；只是前端会话列表的服务端副本，用于换浏览器/换设备后恢复。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import re
import sqlite3
import threading


def _locked(fn):
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    return wrapper

MAX_BYTES = 4 * 1024 * 1024

CAPABILITY = {
    "name": "会话同步快照",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "库打不开=抛错；快照非法/超 4MB=ValueError，不静默丢会话",
    "approval": "ADR-046：没有开关的既成常开登记，不发明新开关",
}


def _now():
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


_RULES = (
    ("research", ("假设", "实验", "验证", "研究", "复盘", "证据", "对照")),
    ("decision", ("决定", "选择", "放弃", "定稿", "批准", "不做了")),
    ("task", ("待办", "任务", "安排", "截止", "提醒", "下一步", "要做")),
    ("work", ("客户", "订单", "报价", "工厂", "生产", "会议", "项目", "报表", "发票")),
    ("personal", ("家里", "孩子", "身体", "吃饭", "睡觉", "周末", "老婆", "父母")),
    ("emotion", ("烦", "累", "焦虑", "开心", "生气", "难受", "压力")),
    ("question", ("?", "？", "怎么", "为什么", "能不能", "是否")),
)


def classify(text):
    t = str(text or "")
    tags = [name for name, words in _RULES if any(w in t for w in words)]
    if not tags:
        return "other", []
    return tags[0], tags


class ConversationStore:
    def __init__(self, db_path=None):
        self.db_path = str(db_path or os.environ.get("PAI_CONVERSATIONS_DB")
                           or "conversations.db")
        pathlib.Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
        create table if not exists conversation_snapshots(
          profile text primary key,
          data_json text not null,
          updated_at text not null
        )""")
        self.conn.execute("""
        create table if not exists conversation_messages(
          id integer primary key autoincrement,
          profile text not null,
          conversation_id text not null,
          role text not null,
          content text,
          reasoning text,
          created_at text not null
        )""")
        self.conn.execute("""
        create table if not exists conversations(
          profile text not null,
          id text not null,
          title text not null default '',
          pinned integer not null default 0,
          updated_at integer not null default 0,
          primary key(profile, id)
        )""")
        self.conn.execute("""
        create index if not exists ix_conversation_messages
          on conversation_messages(profile, conversation_id, id)""")
        for col, ddl in (("kind", "text not null default ''"),
                         ("tags", "text not null default '[]'"),
                         ("classified_at", "text not null default ''")):
            try:
                self.conn.execute("alter table conversation_messages add column %s %s" % (col, ddl))
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    def close(self):
        self.conn.close()

    @_locked
    def load(self, profile):
        row = self.conn.execute(
            "select data_json, updated_at from conversation_snapshots where profile=?",
            (profile,)).fetchone()
        if not row:
            return {"sessions": [], "active": None, "updatedAt": 0}
        try:
            data = json.loads(row["data_json"])
        except Exception:
            return {"sessions": [], "active": None, "updatedAt": 0}
        if not isinstance(data, dict):
            return {"sessions": [], "active": None, "updatedAt": 0}
        sessions = data.get("sessions") if isinstance(data.get("sessions"), list) else []
        return {"sessions": sessions[:50], "active": data.get("active"),
                "updatedAt": int(data.get("updatedAt") or 0), "saved_at": row["updated_at"]}

    @_locked
    def save(self, profile, data):
        sessions = data.get("sessions") if isinstance(data, dict) else None
        if not isinstance(sessions, list) or len(sessions) > 50:
            raise ValueError("sessions 必须是最多 50 条的数组")
        payload = {
            "sessions": sessions,
            "active": data.get("active"),
            "updatedAt": int(data.get("updatedAt") or 0),
        }
        raw = json.dumps(payload, ensure_ascii=False)
        if len(raw.encode("utf-8")) > MAX_BYTES:
            raise ValueError("会话快照超过 4MB")
        self.conn.execute(
            "insert into conversation_snapshots(profile,data_json,updated_at) "
            "values(?,?,?) on conflict(profile) do update set "
            "data_json=excluded.data_json, updated_at=excluded.updated_at",
            (profile, raw, _now()))
        for sess in sessions:
            if not isinstance(sess, dict) or not sess.get("id"):
                continue
            self._upsert_conv(profile, sess)
        self.conn.commit()
        return {"ok": True, "updatedAt": payload["updatedAt"], "saved_at": _now()}

    def _upsert_conv(self, profile, sess):
        cid = str(sess.get("id") or "").strip()
        if not cid:
            return
        self.conn.execute(
            "insert into conversations(profile,id,title,pinned,updated_at) "
            "values(?,?,?,?,?) on conflict(profile,id) do update set "
            "title=excluded.title, pinned=excluded.pinned, updated_at=excluded.updated_at",
            (profile, cid, str(sess.get("title") or "新对话")[:30],
             1 if sess.get("pinned") else 0,
             int(sess.get("updated") or _dt.datetime.now().timestamp() * 1000)))

    @_locked
    def append_message(self, profile, conversation_id, title, message):
        """逐条写：消息日志 + 顺手把快照更新一格，旧前端仍能整份恢复。"""
        cid = str(conversation_id or "").strip()
        if not cid:
            raise ValueError("缺 conversation_id")
        if not isinstance(message, dict):
            raise ValueError("message 必须是对象")
        role = str(message.get("role") or "").strip()
        if role not in ("user", "ai"):
            raise ValueError("role 必须是 user/ai")
        content = str(message.get("content") or "")
        reasoning = str(message.get("reasoning") or "")
        ts = _now()
        kind, tags = classify(content)
        self.conn.execute(
            "insert into conversation_messages"
            "(profile,conversation_id,role,content,reasoning,created_at,kind,tags,classified_at) "
            "values(?,?,?,?,?,?,?,?,?)",
            (profile, cid, role, content[:200000], reasoning[:200000], ts,
             kind, json.dumps(tags, ensure_ascii=False), ts))
        snap = self.load(profile)
        sessions = snap.get("sessions") or []
        sess = next((s for s in sessions if s.get("id") == cid), None)
        if sess is None:
            sess = {"id": cid, "title": str(title or "新对话")[:30],
                    "messages": [], "updated": 0}
            sessions.insert(0, sess)
        elif title:
            sess["title"] = str(title)[:30]
        sess.setdefault("messages", []).append(
            {"role": role, "content": content, "reasoning": reasoning})
        sess["updated"] = int(_dt.datetime.now().timestamp() * 1000)
        snap["sessions"] = sessions[:50]
        snap["active"] = snap.get("active") or cid
        snap["updatedAt"] = int(_dt.datetime.now().timestamp() * 1000)
        self.save(profile, snap)
        self.conn.commit()
        return {"ok": True, "conversation_id": cid, "created_at": ts}

    @_locked
    def search_messages(self, profile, query, limit=10):
        q = str(query or "").strip()
        if not q:
            return []
        toks = [x for x in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{1,4}", q) if x][:6]
        if not toks:
            return []
        where = " and ".join(["content like ?"] * len(toks))
        args = [profile] + ["%" + x + "%" for x in toks] + [int(limit)]
        rows = self.conn.execute(
            "select id,conversation_id,role,content,created_at,kind,tags "
            "from conversation_messages where profile=? and " + where +
            " order by id desc limit ?", args).fetchall()
        return [dict(r) for r in rows]

    @_locked
    def backfill_classify(self, profile):
        rows = self.conn.execute(
            "select id,content from conversation_messages "
            "where profile=? and (kind='' or classified_at='')", (profile,)).fetchall()
        n = 0
        for r in rows:
            kind, tags = classify(r["content"])
            self.conn.execute(
                "update conversation_messages set kind=?,tags=?,classified_at=? where id=?",
                (kind, json.dumps(tags, ensure_ascii=False), _now(), r["id"]))
            n += 1
        self.conn.commit()
        return n

    @_locked
    def all_messages(self, profile, after_id=0, limit=500):
        rows = self.conn.execute(
            "select id,conversation_id,role,content,created_at,kind,tags "
            "from conversation_messages where profile=? and id>? order by id limit ?",
            (profile, int(after_id), int(limit))).fetchall()
        return [dict(r) for r in rows]

    @_locked
    def message_stats(self, profile):
        total = self.conn.execute(
            "select count(*) from conversation_messages where profile=?", (profile,)).fetchone()[0]
        unclassified = self.conn.execute(
            "select count(*) from conversation_messages where profile=? and (kind='' or classified_at='')",
            (profile,)).fetchone()[0]
        kinds = {r["kind"]: r["n"] for r in self.conn.execute(
            "select kind,count(*) as n from conversation_messages where profile=? group by kind",
            (profile,)).fetchall()}
        return {"messages": total, "unclassified": unclassified, "kinds": kinds}

    def _import_session_messages(self, profile, sess):
        cid = str(sess.get("id") or "").strip()
        if not cid:
            return
        n = self.conn.execute(
            "select count(*) from conversation_messages where profile=? and conversation_id=?",
            (profile, cid)).fetchone()[0]
        if n:
            return
        for m in (sess.get("messages") or []):
            if not isinstance(m, dict) or m.get("role") not in ("user", "ai"):
                continue
            self.conn.execute(
                "insert into conversation_messages"
                "(profile,conversation_id,role,content,reasoning,created_at) "
                "values(?,?,?,?,?,?)",
                (profile, cid, m.get("role"), str(m.get("content") or "")[:200000],
                 str(m.get("reasoning") or "")[:200000], _now()))

    def _migrate_snapshot(self, profile):
        snap = self.load(profile)
        for sess in (snap.get("sessions") or []):
            if isinstance(sess, dict):
                self._upsert_conv(profile, sess)
                self._import_session_messages(profile, sess)
        self.conn.commit()
        return snap

    @_locked
    def load_from_messages(self, profile):
        snap = self._migrate_snapshot(profile)
        convs = [dict(r) for r in self.conn.execute(
            "select id,title,pinned,updated_at from conversations "
            "where profile=? order by pinned desc, updated_at desc", (profile,))]
        msgs = [dict(r) for r in self.conn.execute(
            "select conversation_id,role,content,reasoning from conversation_messages "
            "where profile=? order by id", (profile,))]
        by = {}
        for m in msgs:
            by.setdefault(m["conversation_id"], []).append(
                {"role": m["role"], "content": m["content"] or "",
                 "reasoning": m["reasoning"] or ""})
        sessions = []
        for c in convs:
            sessions.append({
                "id": c["id"],
                "title": c["title"] or "新对话",
                "pinned": bool(c["pinned"]),
                "updated": int(c["updated_at"] or 0),
                "messages": by.get(c["id"], []),
            })
        active = snap.get("active")
        if active not in {s["id"] for s in sessions}:
            active = sessions[0]["id"] if sessions else None
        return {"sessions": sessions[:50], "active": active,
                "updatedAt": max([s["updated"] for s in sessions] or [0])}

    @_locked
    def update_meta(self, profile, conversation_id, title=None, pinned=None):
        cid = str(conversation_id or "").strip()
        if not cid:
            raise ValueError("缺 conversation_id")
        self.conn.execute(
            "insert into conversations(profile,id,title,pinned,updated_at) "
            "values(?,?,?,?,?) on conflict(profile,id) do update set "
            "title=coalesce(?,title), pinned=coalesce(?,pinned), updated_at=excluded.updated_at",
            (profile, cid, str(title or "新对话")[:30],
             1 if pinned else 0, int(_dt.datetime.now().timestamp() * 1000),
             None if title is None else str(title)[:30],
             None if pinned is None else (1 if pinned else 0)))
        snap = self.load(profile)
        sess = next((s for s in (snap.get("sessions") or []) if s.get("id") == cid), None)
        if sess is None:
            sess = {"id": cid, "title": str(title or "新对话")[:30],
                    "messages": [], "updated": 0}
            snap.setdefault("sessions", []).insert(0, sess)
        if title is not None:
            sess["title"] = str(title)[:30]
        if pinned is not None:
            sess["pinned"] = bool(pinned)
        sess["updated"] = int(_dt.datetime.now().timestamp() * 1000)
        snap["sessions"] = (snap.get("sessions") or [])[:50]
        snap["active"] = snap.get("active") or cid
        snap["updatedAt"] = sess["updated"]
        self.save(profile, snap)
        self.conn.commit()
        return {"ok": True, "conversation_id": cid,
                "title": sess["title"], "pinned": bool(sess.get("pinned"))}

    @_locked
    def delete_conversation(self, profile, conversation_id):
        cid = str(conversation_id or "").strip()
        if not cid:
            raise ValueError("缺 conversation_id")
        self.conn.execute(
            "delete from conversation_messages where profile=? and conversation_id=?",
            (profile, cid))
        self.conn.execute(
            "delete from conversations where profile=? and id=?", (profile, cid))
        snap = self.load(profile)
        snap["sessions"] = [s for s in (snap.get("sessions") or []) if s.get("id") != cid]
        if snap.get("active") == cid:
            snap["active"] = (snap["sessions"][0].get("id")
                              if snap.get("sessions") else None)
        self.save(profile, snap)
        self.conn.commit()
        return {"ok": True, "conversation_id": cid}

    @_locked
    def replace_messages(self, profile, conversation_id, title, messages):
        cid = str(conversation_id or "").strip()
        if not cid or not isinstance(messages, list):
            raise ValueError("缺 conversation_id/messages")
        clean = []
        for m in messages:
            if not isinstance(m, dict) or m.get("role") not in ("user", "ai"):
                continue
            clean.append({"role": m["role"],
                          "content": str(m.get("content") or "")[:200000],
                          "reasoning": str(m.get("reasoning") or "")[:200000]})
        self.conn.execute(
            "delete from conversation_messages where profile=? and conversation_id=?",
            (profile, cid))
        for m in clean:
            self.conn.execute(
                "insert into conversation_messages"
                "(profile,conversation_id,role,content,reasoning,created_at) "
                "values(?,?,?,?,?,?)",
                (profile, cid, m["role"], m["content"], m["reasoning"], _now()))
        snap = self.load(profile)
        sessions = snap.get("sessions") or []
        sess = next((s for s in sessions if s.get("id") == cid), None)
        if sess is None:
            sess = {"id": cid, "title": str(title or "新对话")[:30],
                    "messages": [], "updated": 0}
            sessions.insert(0, sess)
        elif title:
            sess["title"] = str(title)[:30]
        sess["messages"] = clean
        sess["updated"] = int(_dt.datetime.now().timestamp() * 1000)
        snap["sessions"] = sessions[:50]
        snap["active"] = cid
        snap["updatedAt"] = sess["updated"]
        self.save(profile, snap)
        self.conn.commit()
        return {"ok": True, "conversation_id": cid, "count": len(clean)}

