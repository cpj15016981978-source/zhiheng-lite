# -*- coding: utf-8 -*-
"""B1 全局工作空间 v2：接上分数源（复用 M2 的 recall 打分尺子）。

v1 的问题（守门人实测）：原则/事实/文化没有分数源，类型先验让原则垄断，
50 题里 50 次选出的 7 条全是原则，记忆/事实/文化 = 0。

v2 的做法：
  · 条目可以带显式 score；没有 score 时，用 M2._score(query, text, ts, now, 1.0) 现算；
  · 排序用【原始分数】，不再乘类型先验（先验只会重新制造垄断）；
  · score<=0 的条目进 dropped 并带 reason（"no_query"/"no_score"），不再当场消失；
  · 默认关；本步仍不接 prompt——分数源先做对，消融之后再说。

条目形状：
    {"kind": "episode|fact|principle|culture", "id": 1, "text": "...",
     "ts": "2026-...", "score": 0.8（可选）}
"""
import datetime
import os

from . import memory as M2

ENV_BACKEND = "PAI_WORKSPACE"
ENV_BUDGET = "PAI_WORKSPACE_BUDGET"
BACKEND_OFF = "off"
BACKEND_ON = "on"
DEFAULT_BUDGET = 7
MAX_BUDGET = 20

CAPABILITY = {
    "name": "B1 全局工作空间 v2",
    "switch_env": ENV_BACKEND,
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "关=不选择、不记录；无 query/无分数=进 dropped 并说明；预算非法=回默认 7",
    "probe_cost": "只读环境变量，不起进程、不联网、不写库",
    "approval": "未获单独批准：由 DeepSeek 在 Boss『把这个也加上』下自选；默认关；接 prompt 前先做消融",
}


def backend():
    raw = (os.environ.get(ENV_BACKEND) or "").strip().lower()
    return raw if raw in (BACKEND_OFF, BACKEND_ON) else BACKEND_OFF


def set_enabled(on):
    os.environ[ENV_BACKEND] = BACKEND_ON if on else BACKEND_OFF


def budget():
    raw = os.environ.get(ENV_BUDGET)
    try:
        n = int(raw) if raw is not None else DEFAULT_BUDGET
    except (TypeError, ValueError):
        n = DEFAULT_BUDGET
    return max(1, min(MAX_BUDGET, n))


def text_of(item):
    """从不同形状的条目里取可打分的文本；取不到返回空串。"""
    if not isinstance(item, dict):
        return ""
    if item.get("subject") or item.get("predicate"):
        parts = [item.get("subject"), item.get("predicate"), item.get("object")]
        return " ".join(str(p) for p in parts if p)
    for key in ("text", "content", "object", "ref"):
        val = item.get(key)
        if val:
            return str(val)
    return ""


def _ts_rank(ts):
    try:
        return -datetime.datetime.fromisoformat(str(ts)).timestamp()
    except (TypeError, ValueError):
        return float("inf")


def score_of(item, query, now_dt):
    """显式 score 优先；否则用 M2 的尺子现算。返回 (score, reason)。"""
    raw = item.get("score")
    if raw is not None:
        try:
            s = float(raw)
        except (TypeError, ValueError):
            s = 0.0
        return (s, "" if s > 0 else "no_score")
    q = (query or "").strip()
    if not q:
        return 0.0, "no_query"
    text = text_of(item)
    if not text:
        return 0.0, "no_text"
    s = float(M2._score(q, text, item.get("ts"), now_dt, 1.0))
    return s, ("" if s > 0 else "no_score")


def select(items, query="", now=None, limit=None):
    """返回 {"selected","dropped","budget"}；不抛异常，不改入参。"""
    if limit is None:
        n = budget()
    else:
        try:
            n = max(1, min(MAX_BUDGET, int(limit)))
        except (TypeError, ValueError):
            n = budget()
    now_dt = M2._as_dt(now) or datetime.datetime.now()

    scored, dropped = [], []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        s, reason = score_of(item, query, now_dt)
        item["score"] = round(s, 6)
        if s <= 0:
            item["reason"] = reason
            dropped.append(item)
        else:
            scored.append(item)

    scored.sort(key=lambda x: (-x["score"], _ts_rank(x.get("ts")), str(x.get("id") or "")))

    # 类型配额只是排序偏好：配额内先收，超配额进 rest，最后仍按分数截 n；
    # 它不硬封顶（只有一类有分时，照样可能占满 n）。
    # 目的不是“平均主义”，而是防止某类文本因分数并列/短语命中而垄断工作区。
    per_kind = max(1, n // 3)
    picked, rest, counts = [], [], {}
    for it in scored:
        kind = str(it.get("kind") or "episode")
        if counts.get(kind, 0) < per_kind:
            picked.append(it)
            counts[kind] = counts.get(kind, 0) + 1
        else:
            rest.append(it)
    selected = (picked + rest)[:n]
    chosen = {id(x) for x in selected}
    not_chosen = [x for x in scored if id(x) not in chosen]
    return {"selected": selected, "dropped": dropped + not_chosen, "budget": n}
