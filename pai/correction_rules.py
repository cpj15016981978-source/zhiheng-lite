# -*- coding: utf-8 -*-
"""纠错规则桥 v0.1：把协议里的 Correction/Rule 做成可接主系统的层。

默认关；只有 PAI_CORRECTION_RULES=on 才读写。
不自动改回复行为：先提案，Boss 批准后才 active。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib

from pai import atomic as AT
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
STORE = ROOT / "data" / "corrections.jsonl"
ENV = "PAI_CORRECTION_RULES"

_PATTERNS = (
    re.compile(r"^\s*以后(.{1,80})$"),
    re.compile(r"^\s*记住[：: ]?(.{1,80})$"),
    re.compile(r"^\s*别再(.{1,80})$"),
    re.compile(r"^\s*不要(.{1,80})$"),
    re.compile(r"^\s*纠正[：: ]?(.{1,80})$"),
)


def enabled():
    return (os.environ.get(ENV) or "").strip().lower() == "on"


def detect(text):
    if not enabled():
        return None
    raw = str(text or "").strip()
    for p in _PATTERNS:
        m = p.match(raw)
        if m:
            return {"quote": raw, "kind": "style", "candidate": m.group(1).strip()}
    return None


def _read():
    if not STORE.exists():
        return []
    rows = []
    for line in STORE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _write(rows):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    AT.write_text(STORE, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def propose(text, kind="style"):
    if not enabled():
        return None
    hit = detect(text)
    if hit is None:
        return None
    rows = _read()
    cid = "cor-%04d" % (len(rows) + 1)
    row = {
        "correction_id": cid,
        "quote": hit["quote"],
        "candidate": hit["candidate"],
        "kind": kind,
        "status": "pending",
        "created_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "approved_at": None,
    }
    rows.append(row)
    _write(rows)
    return row


def list_all():
    return _read()


def get(cid):
    for r in _read():
        if r["correction_id"] == cid:
            return r
    return None


def approve(cid):
    rows = _read()
    for r in rows:
        if r["correction_id"] == cid:
            r["status"] = "active"
            r["approved_at"] = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
            _write(rows)
            return r
    raise KeyError(cid)


def disable(cid):
    rows = _read()
    for r in rows:
        if r["correction_id"] == cid:
            r["status"] = "disabled"
            _write(rows)
            return r
    raise KeyError(cid)


def active_rule():
    for r in reversed(_read()):
        if r["status"] == "active":
            return r
    return None


def prompt_block():
    rule = active_rule()
    if not rule:
        return ""
    return "[本轮行为规则]\n- %s\n" % rule["candidate"]


def purge_text(needle):
    """清除含 needle 的纠正原文（只清 corrections.jsonl，不碰记忆库）。"""
    needle = str(needle or "")
    if not needle:
        return 0
    rows = _read()
    keep = [r for r in rows if needle not in str(r.get("quote") or "")]
    removed = len(rows) - len(keep)
    if removed:
        _write(keep)
    return removed


def trace_fields():
    rule = active_rule()
    if not rule:
        return {}
    return {"correction_id": rule["correction_id"],
            "rule": rule["candidate"],
            "applied_at": rule["approved_at"]}

CAPABILITY = {
    "name": "纠错规则桥 v0.1",
    "switch_env": "PAI_CORRECTION_RULES",
    "default_on": False,
    "stores_user_text": True,
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
    "network": False,
    "degrade": "关时不读不写；开时把纠正原文写进 data/corrections.jsonl，可用 purge_text() 清除；不写记忆库、不调模型",
    "probe_cost": "只读模块常量与本机 JSONL 路径；不连库、不调模型、不联网",
    "approval": "未获单独批准：v0.1 只做提案/审批，未接自动改原则",
}
