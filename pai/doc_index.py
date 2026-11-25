# -*- coding: utf-8 -*-
"""文档分块与检索：本地 SQLite + 关键词打分，不出网。"""
from __future__ import annotations

from pai import paths

import datetime
import hashlib
import os
import pathlib
import re
import sqlite3
import uuid

CAPABILITY = {
    "name": "文档分块检索",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "空文档=ValueError；无命中=返回空；只读本机 SQLite，不调模型、不出网",
    "approval": "ADR-046：本地单用户检索登记，不发明新开关",
}

ROOT = paths.DATA_DIR
CHUNK = 1200
OVERLAP = 200


def _db():
    return pathlib.Path(os.environ.get("PAI_DOC_DB")
                        or ROOT / "doc_index.db")


def _conn():
    p = _db()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    c.executescript("""
    create table if not exists documents(
      id text primary key, profile text not null, name text not null,
      sha256 text not null, created_at text not null);
    create table if not exists chunks(
      id integer primary key autoincrement, doc_id text not null,
      seq integer not null, text text not null);
    create index if not exists ix_chunks_doc on chunks(doc_id, seq);
    """)
    return c


def _tokens(text):
    s = str(text or "").lower()
    words = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}", s)
    out = []
    for w in words:
        if w.isascii():
            out.append(w)
        else:
            out.extend(w[i:i + 2] for i in range(len(w) - 1))
    return out


def _chunks(text):
    t = str(text or "").strip()
    if not t:
        return []
    step = max(1, CHUNK - OVERLAP)
    return [t[i:i + CHUNK] for i in range(0, len(t), step) if t[i:i + CHUNK].strip()]


def index(profile, name, text):
    chunks = _chunks(text)
    if not chunks:
        raise ValueError("文档为空")
    doc_id = uuid.uuid4().hex[:12]
    ts = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    sha = hashlib.sha256(str(text).encode("utf-8")).hexdigest()
    c = _conn()
    try:
        old = c.execute("select id from documents where profile=? and sha256=? limit 1",
                        (str(profile), sha)).fetchone()
        if old:
            return {"doc_id": old["id"], "chunks": 0, "name": name, "skipped": True}
        c.execute("insert into documents(id,profile,name,sha256,created_at) values(?,?,?,?,?)",
                  (doc_id, profile, str(name or "文档")[:200], sha, ts))
        c.executemany("insert into chunks(doc_id,seq,text) values(?,?,?)",
                      [(doc_id, i, ch) for i, ch in enumerate(chunks)])
        c.commit()
    finally:
        c.close()
    return {"doc_id": doc_id, "chunks": len(chunks), "name": name}


def list_docs(profile):
    c = _conn()
    try:
        rows = c.execute("select id,name,created_at from documents where profile=? order by created_at desc",
                         (str(profile),)).fetchall()
    finally:
        c.close()
    return [dict(r) for r in rows]


def search_all(profile, query, k=5):
    q = set(_tokens(query))
    if not q:
        return []
    c = _conn()
    try:
        rows = c.execute(
            "select d.name, c.doc_id, c.seq, c.text from chunks c "
            "join documents d on d.id=c.doc_id where d.profile=? order by c.doc_id,c.seq",
            (str(profile),)).fetchall()
    finally:
        c.close()
    scored = []
    for r in rows:
        toks = _tokens(r["text"])
        if not toks:
            continue
        hits = sum(1 for t in toks if t in q)
        score = hits / max(1, len(toks)) ** 0.5
        if score > 0:
            scored.append({"doc_id": r["doc_id"], "name": r["name"],
                           "seq": r["seq"], "text": r["text"], "score": round(score, 4)})
    scored.sort(key=lambda x: -x["score"])
    return scored[:max(1, min(20, int(k)))]


def search(profile, doc_id, query, k=5):
    q = set(_tokens(query))
    if not q:
        return []
    c = _conn()
    try:
        rows = c.execute(
            "select seq,text from chunks where doc_id=? "
            "and doc_id in (select id from documents where profile=?) order by seq",
            (doc_id, profile)).fetchall()
    finally:
        c.close()
    scored = []
    for r in rows:
        toks = _tokens(r["text"])
        if not toks:
            continue
        hits = sum(1 for t in toks if t in q)
        score = hits / max(1, len(toks)) ** 0.5
        if score > 0:
            scored.append({"seq": r["seq"], "text": r["text"], "score": round(score, 4)})
    scored.sort(key=lambda x: -x["score"])
    return scored[:max(1, min(10, int(k)))]
