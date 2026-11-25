# -*- coding: utf-8 -*-
"""四层记忆：统一存进 memory.db，Markdown 只作旧数据迁移。"""
from __future__ import annotations

from pai import paths

import datetime
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = paths.DATA_DIR / "memory.db"
BASE = paths.DATA_DIR / "brain"

CAPABILITY = {
    "name": "四层记忆",
    "switch_env": "PAI_MEMORY_LAYERS",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "库缺失=自动建表；读失败=跳过该层，不编内容",
    "approval": "ADR-046：本地既成记忆层，不新增开关",
}

LAYERS = ("profile", "rules", "goals", "current_state")


def _conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB), timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS memory_layers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        layer TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS memory_layers_meta(
        key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
    c.commit()
    return c


def _ensure():
    c = _conn()
    try:
        n = c.execute("SELECT COUNT(*) FROM memory_layers").fetchone()[0]
        done = c.execute("SELECT value FROM memory_layers_meta WHERE key='migrated'").fetchone()
        if n == 0 and not done and BASE.is_dir():
            for p in BASE.glob("*.md"):
                c.execute("INSERT INTO memory_layers(layer,title,content,created_at) VALUES(?,?,?,?)",
                          (p.stem, "从 Markdown 迁移", p.read_text(encoding="utf-8", errors="replace"),
                           datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
            c.execute("INSERT OR REPLACE INTO memory_layers_meta(key,value) VALUES('migrated','1')")
            c.commit()
    finally:
        c.close()


def read(layer):
    _ensure()
    c = _conn()
    try:
        rows = c.execute("SELECT content FROM memory_layers WHERE layer=? ORDER BY id", (str(layer),)).fetchall()
    finally:
        c.close()
    if not rows:
        return "# %s\n" % layer
    return "\n\n".join(r[0] for r in rows)


def remember(layer, title, body):
    _ensure()
    if layer not in ("projects", "lessons", "profile", "rules", "goals", "current_state"):
        raise ValueError("未知记忆层：%s" % layer)
    c = _conn()
    try:
        c.execute("INSERT INTO memory_layers(layer,title,content,created_at) VALUES(?,?,?,?)",
                  (layer, str(title or ""), str(body or ""),
                   datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
        c.commit()
    finally:
        c.close()


def search(query, limit=8):
    _ensure()
    q = str(query or "").strip()
    if not q:
        return []
    c = _conn()
    try:
        rows = c.execute("SELECT layer,title,content FROM memory_layers WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
                         ("%" + q + "%", int(limit))).fetchall()
    finally:
        c.close()
    return [{"path": "%s/%s" % (r[0], r[1]), "text": r[2][:1200]} for r in rows]


def prompt_block(max_chars=1200):
    _ensure()
    parts = []
    for n in LAYERS:
        t = read(n).strip()
        if t and t != "# %s" % n:
            parts.append("[%s]\n%s" % (n, t))
    c = _conn()
    try:
        rows = c.execute("SELECT title,content FROM memory_layers WHERE layer='lessons' ORDER BY id DESC LIMIT 3").fetchall()
    finally:
        c.close()
    for title, content in reversed(rows):
        parts.append("[最近经验 · %s]\n%s" % (title, content[:400]))
    return "\n\n".join(parts)[:max_chars]
