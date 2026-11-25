# -*- coding: utf-8 -*-
"""A2 记忆写 v0：只写 facts，按 M2 入口签名。

由 agentd 的 memory_fact 工具调用；默认不启用，只有 --memory-db 指向临时库时才注册。
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pai.memory import Memory  # noqa: E402

SPEC = {
    "memory_fact": {"capability": "write", "write": True},
}

_PROJECT_DATA = ROOT / "data"


def validate_db(db_path, allow_real=False):
    p = Path(str(db_path)).resolve()
    if p == _PROJECT_DATA or _PROJECT_DATA in p.parents:
        if not allow_real:
            raise ValueError("memory_db 不能指向项目 data/ 下的真库：%s（确需真库请加 --allow-real-db）" % p)
        if not p.is_file():
            raise ValueError("--allow-real-db 只允许已存在的真库文件：%s" % p)
    return p


def canonical(args):
    a = args or {}
    return json.dumps({
        "subject": str(a.get("subject") or "").strip(),
        "predicate": str(a.get("predicate") or "").strip(),
        "object": a.get("object"),
        "confidence": float(a.get("confidence", 0.5)),
        "source_episode_id": a.get("source_episode_id"),
    }, ensure_ascii=False, sort_keys=True)


def before_sha(db_path, args):
    a = args or {}
    mem = Memory(str(db_path))
    try:
        row = mem.conn.execute(
            "SELECT * FROM facts WHERE subject=? AND predicate=?",
            (str(a.get("subject") or "").strip(),
             str(a.get("predicate") or "").strip())).fetchone()
        return hashlib.sha256(
            (json.dumps(dict(row), ensure_ascii=False, sort_keys=True) if row else "")
            .encode("utf-8")).hexdigest()[:12]
    finally:
        mem.conn.close()


def after_sha(args):
    return hashlib.sha256(canonical(args).encode("utf-8")).hexdigest()[:12]


def write_fact(db_path, args):
    subject = str((args or {}).get("subject") or "").strip()
    predicate = str((args or {}).get("predicate") or "").strip()
    obj = (args or {}).get("object")
    if not subject or not predicate or obj is None:
        raise ValueError("subject/predicate/object 必填")
    confidence = float((args or {}).get("confidence", 0.5))
    source = (args or {}).get("source_episode_id")
    mem = Memory(str(db_path))
    try:
        fid = mem.upsert_fact(subject, predicate, obj,
                              confidence=confidence,
                              source_episode_id=source)
        return {"fact_id": fid, "subject": subject, "predicate": predicate}
    finally:
        mem.conn.close()


def simulate_fact(db_path, args):
    subject = str((args or {}).get("subject") or "").strip()
    predicate = str((args or {}).get("predicate") or "").strip()
    obj = (args or {}).get("object")
    mem = Memory(str(db_path))
    try:
        row = mem.conn.execute(
            "SELECT * FROM facts WHERE subject=? AND predicate=?",
            (subject, predicate)).fetchone()
        return {
            "effects": ["update fact" if row else "insert fact"],
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "previous": (dict(row) if row else None),
            "before_sha12": hashlib.sha256(
                (json.dumps(dict(row), ensure_ascii=False, sort_keys=True) if row else "")
                .encode("utf-8")).hexdigest()[:12],
            "after_sha12": after_sha(args),
            "rollback": "restore previous fact" if row else "delete inserted fact",
            "unknowns": ["v0 只写 facts，不写原则/身份"],
            "confidence": 0.9,
        }
    finally:
        mem.conn.close()
