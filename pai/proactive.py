# -*- coding: utf-8 -*-
"""主动推进 v1：到期线 → 提案 → 人工批准 → 执行 → 台账。"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import threading
import uuid

from pai import continuity as CT
from pai import browser as BR

CAPABILITY = {
    "name": "主动推进 v1",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": True,
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
    "network": True,
    "degrade": "没有到期线=空；批准前不执行；未知动作=拒绝",
    "approval": "ADR-046：每条提案必须人工批准；浏览器动作沿用浏览器审批",
}

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATH = ROOT / "data" / "proactive.json"
LEDGER = ROOT / "data" / "proactive_ledger.json"
_LOCK = threading.Lock()
_URL = re.compile(r"https?://[^\s，。；]+")


def _read(path):
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _write(path, items):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _job_runner():
    try:
        from pai import job_runner as J
        return J if J.enabled() else None
    except Exception:
        return None


def ledger(limit=50):
    return _read(LEDGER)[-limit:][::-1]


def _record(kind, detail, ok=True):
    with _LOCK:
        items = _read(LEDGER)
        items.append({"at": _now(), "kind": kind, "ok": bool(ok), "detail": detail})
        _write(LEDGER, items[-500:])


def refresh(profile="work"):
    """为到期线补一条提案；同一线程只保留一条未处理提案。"""
    with _LOCK:
        items = _read(PATH)
        for t in CT.due(profile):
            tid = t.get("id")
            if any(x.get("thread_id") == tid and x.get("status") in ("pending", "approved", "executed") for x in items):
                continue
            nxt = str(t.get("next_step") or "")
            m = _URL.search(nxt)
            kind = "open_url" if m else "review"
            item = {
                "id": uuid.uuid4().hex[:10], "thread_id": tid,
                "title": t.get("title") or "未命名线",
                "next_step": nxt,
                "kind": kind,
                "url": m.group(0) if m else "",
                "risk": "会打开外部网页" if kind == "open_url" else "只记录，不自动执行",
                "status": "pending", "created": _now(),
            }
            J = _job_runner()
            if J:
                try:
                    job = J.create("待批准：" + item["title"], nxt,
                                   ttl_hours=72, agent="proactive", cwd="", access="read",
                                   agent_id="proactive")
                    J.wait(job["id"], "等待 Boss 批准主动提案")
                    item["job_id"] = job["id"]
                except Exception:
                    pass
            items.append(item)
            try:
                from pai import exec_guard as EG
                EG.audit("proactive", "low", "allow", item["id"], item.get("title") or "")
            except Exception:
                pass
        _write(PATH, items)
        return [x for x in items if x.get("status") == "pending"]


def run_background(stop=None, interval=300, profile="work"):
    """后台扫描到期续线，生成待批准提案；不自动执行。"""
    import time
    while stop is None or not stop.is_set():
        try:
            refresh(profile)
        except Exception:
            pass
        if stop is None:
            time.sleep(interval)
        else:
            stop.wait(interval)


def pending():
    return [x for x in _read(PATH) if x.get("status") == "pending"]


def proposals(profile="work"):
    refresh(profile)
    return [x for x in _read(PATH) if x.get("status") == "pending"]


def approve(pid, profile="work"):
    with _LOCK:
        items = _read(PATH)
        rec = next((x for x in items if x.get("id") == pid), None)
        if not rec or rec.get("status") != "pending":
            raise ValueError("没有待批准提案：%s" % pid)
        rec["status"] = "approved"
        rec["approved_at"] = _now()
        _write(PATH, items)
    detail = {"proposal": pid, "thread_id": rec.get("thread_id"), "kind": rec.get("kind")}
    J = _job_runner()
    job_id = rec.get("job_id") or ""
    if J and job_id:
        try:
            J.resume(job_id, "Boss 批准")
        except Exception:
            pass
    try:
        if rec.get("kind") == "open_url":
            url = rec.get("url") or ""
            if not url.startswith("http"):
                raise ValueError("URL 不合法")
            out = BR.task(url, [], confirm=True, headless=False)
            ok = bool(out.get("ok"))
            detail.update(url=url, result=out)
        else:
            out = {"ok": True, "kind": "review", "message": rec.get("next_step") or ""}
            ok = True
            detail.update(result=out)
    except Exception as e:  # noqa: BLE001
        ok = False
        out = {"ok": False, "reason": "%s: %s" % (type(e).__name__, e)}
        detail.update(result=out)
    if J and job_id:
        try:
            J.finish(job_id, "已执行：" + str(out)[:500]) if ok else J.fail(job_id, str(out)[:500])
        except Exception:
            pass
    with _LOCK:
        items = _read(PATH)
        rec = next((x for x in items if x.get("id") == pid), None)
        if rec:
            rec["status"] = "executed" if ok else "failed"
            rec["result"] = out
            rec["executed_at"] = _now()
            _write(PATH, items)
    # 把执行结果写回续线，避免“做过但线里没痕迹”。
    try:
        for t in CT.all_threads(profile, include_done=False):
            if t.get("id") != rec.get("thread_id"):
                continue
            last = ("已打开：" + (rec.get("url") or "")) if rec.get("kind") == "open_url" else \
                   ("已批准：" + (rec.get("next_step") or "")[:200])
            CT.save(profile=profile, title=t.get("title") or rec.get("title") or "未命名线",
                    last_step=last, next_step=t.get("next_step") or rec.get("next_step") or "继续",
                    evidence="审批台账：" + str(pid), due=t.get("due") or "", thread_id=t.get("id"))
            break
    except Exception:
        pass
    _record("proposal", detail, ok=ok)
    try:
        from pai import exec_guard as EG
        EG.audit("proactive_execute", "medium", "allow" if ok else "deny", str(pid), str(out)[:500])
    except Exception:
        pass
    return {"ok": ok, "proposal": pid, "result": out}


def reject(pid):
    with _LOCK:
        items = _read(PATH)
        rec = next((x for x in items if x.get("id") == pid), None)
        if not rec or rec.get("status") != "pending":
            raise ValueError("没有待批准提案：%s" % pid)
        rec["status"] = "rejected"
        rec["rejected_at"] = _now()
        _write(PATH, items)
    J = _job_runner()
    if J and rec.get("job_id"):
        try:
            J.fail(rec["job_id"], "Boss 忽略提案")
        except Exception:
            pass
    _record("proposal", {"proposal": pid, "rejected": True}, ok=True)
    try:
        from pai import exec_guard as EG
        EG.audit("proactive", "low", "allow", str(pid), "rejected")
    except Exception:
        pass
    return {"ok": True, "proposal": pid}
