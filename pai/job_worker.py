# -*- coding: utf-8 -*-
"""后台作业 worker（dry-run 优先）：只领 pending，不执行外部动作。"""
from __future__ import annotations

from pai import paths

import datetime
import json
import re
import os
import pathlib
import threading
from contextlib import closing

from pai import agent_registry as AR
from pai import roi as ROI
from pai import roi as ROI
from pai import job_runner as JR
from pai import verifier as VF
from pai import model_gateway as M10
from pai import ai_provider as AIP

STATE = JR.ROOT / "data" / "job_worker_state.json"
_STOP = threading.Event()
_THREAD = None
_SCHED = None

CAPABILITY = {
    "name": "后台作业 worker",
    "switch_env": "PAI_JOB_WORKER",
    "default_on": False,
    "stores_user_text": False,
    "network": True,
    "degrade": "开关关闭=不领活；dry-run=只写计划；summarize_file 只在批准后调模型",
    "approval": "ADR-046：默认关；dry-run 只改 jobs.db 状态",
}


def enabled():
    return os.environ.get("PAI_JOB_WORKER", "").strip().lower() in ("1", "on", "true", "yes")


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _roots():
    raw = os.environ.get("PAI_JOB_ROOTS") or (str(paths.DATA_DIR) + ";" + str(JR.ROOT))
    out = []
    for x in raw.split(";"):
        x = x.strip()
        if x:
            out.append(pathlib.Path(x).resolve())
    return out


def _under(path, root):
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _safe_path(raw):
    p = pathlib.Path(str(raw or "")).expanduser()
    if not p.is_absolute():
        raise ValueError("路径必须是绝对路径")
    if any(x in p.name.lower() for x in ("cloud.env", ".env", "credential", "secret", "id_rsa")):
        raise ValueError("敏感文件不在读取白名单")
    if not any(_under(p, r) for r in _roots()):
        raise ValueError("路径不在允许根目录")
    return p


def _execute(job):
    typ = job.get("action_type") or "noop"
    payload = job.get("action_payload") or {}
    if typ == "noop":
        return {"ok": True, "action": "noop", "message": "无外部动作"}
    if typ == "read_text":
        p = _safe_path(payload.get("path"))
        if not p.is_file():
            raise ValueError("不是文件：%s" % p)
        data = p.read_text(encoding="utf-8", errors="replace")[:20000]
        return {"ok": True, "action": "read_text", "path": str(p), "chars": len(data),
                "preview": data[:800]}
    if typ == "list_dir":
        p = _safe_path(payload.get("path"))
        if not p.is_dir():
            raise ValueError("不是目录：%s" % p)
        names = sorted(x.name for x in p.iterdir())[:200]
        return {"ok": True, "action": "list_dir", "path": str(p), "items": names}
    if typ == "agent_step":
        goal = str(payload.get("goal") or job.get("title") or "")
        step = str(payload.get("step") or job.get("prompt") or "")
        prior = str(payload.get("context") or "")
        if not prior and job.get("depends_on"):
            prev = JR.get(job.get("depends_on")) or {}
            prior = str(prev.get("result") or "")[:6000]
        prompt = (
            "你是知衡里的执行代理。按目标完成当前这一步，只输出结果，不要解释你是什么模型。\n"
            "目标：%s\n已完成上下文：%s\n当前步骤：%s\n"
            "如果需要写文件，必须只输出下面这种格式，不要输出额外正文：\n"
            "[写文件: report.md]\n文件内容\n[/写文件]\n"
            "文件名只能是英文/数字/下划线/横线，后缀限 txt/md/csv/json/html/py，不要带路径。"
            % (JR.untrusted_text("目标", goal), JR.untrusted_text("上下文", prior[-6000:]),
               JR.untrusted_text("步骤", step))
        )
        outs = AIP.complete(prompt)
        text = str(outs[0].get("output") or "") if outs else ""
        proposals = []
        for m in re.finditer(r"\[写文件:\s*([^\]]+)\]\s*\n(.*?)\[/写文件\]", text, re.S):
            try:
                proposals.append(JR.create_file_proposal(
                    m.group(1).strip(), m.group(2).strip("\n"),
                    plan_id=str(job.get("plan_id") or ""),
                    step_index=int(job.get("step_index") or 0)))
            except Exception as e:
                proposals.append({"error": "%s: %s" % (type(e).__name__, e)})
        if not proposals and re.search(r"(写|保存|生成|输出|整理).*(文件|报告|周报|文档|表格)|"
                                       r"(文件|报告|周报|文档|表格).*(写|保存|生成|输出|整理)", step):
            try:
                name = "plan-step-%s.md" % (int(job.get("step_index") or 0) + 1)
                proposals.append(JR.create_file_proposal(
                    name, text, plan_id=str(job.get("plan_id") or ""),
                    step_index=int(job.get("step_index") or 0)))
            except Exception as e:
                proposals.append({"error": "%s: %s" % (type(e).__name__, e)})
        return {"ok": bool(text), "action": "agent_step", "step": step,
                "result": text[:8000], "file_proposals": proposals}
    if typ == "summarize_file":
        p = _safe_path(payload.get("path"))
        if not p.is_file():
            raise ValueError("不是文件：%s" % p)
        text = p.read_text(encoding="utf-8", errors="replace")[:20000]
        prompt = ("你是知衡里的月白。只读下面这份发布记录，生成给 Boss 的简报。"
                  "只依据资料，不编造已发布/未发布状态。输出三段：\n"
                  "1. 最近一条记录\n2. 今天该做什么\n3. 有没有卡住/风险\n"
                  "不超过 220 字。\n" + JR.untrusted_text("发布记录", text))
        outs = AIP.complete(prompt)
        summary = str(outs[0].get("output") or "") if outs else ""
        return {"ok": bool(summary), "action": "summarize_file", "path": str(p),
                "chars": len(text), "summary": summary[:4000]}
    raise ValueError("动作不在白名单：%s" % typ)


def plan(job):
    """不调用模型、不执行任何外部动作的确定性计划。"""
    title = str(job.get("title") or "")
    agent = str(job.get("agent") or "unset")
    cwd = str(job.get("cwd") or "")
    access = str(job.get("access") or "read")
    return {
        "job_id": job.get("id"),
        "title": title,
        "mode": "dry-run",
        "action": job.get("action_type") or "noop",
        "approved": bool(job.get("approved")),
        "would": [
            "读取作业标题和指令（只读）",
            "确认 agent=%s、cwd=%s、access=%s" % (agent, cwd, access),
            "动作=%s；批准后才执行白名单动作" % (job.get("action_type") or "noop"),
        ],
        "would_not": [
            "不打开浏览器",
            "不写工作目录",
            "不执行 shell",
            "不调用外部 API",
        ],
        "note": "dry-run 只写 jobs.db，不产生外部副作用",
    }






def _daily_path():
    from pai import paths
    return os.environ.get("PAI_DAILY_SUMMARY_PATH") or str(paths.DATA_DIR / "daily_summary.md")


def ensure_daily_job(now=None):
    """每天 >=08:00 建一条 pending 摘要作业；只建一次，必须人工批准。"""
    if os.environ.get("PAI_DAILY_SUMMARY", "").strip().lower() not in ("1", "on", "true", "yes"):
        return None
    now = now or datetime.datetime.now().astimezone()
    # 只补 08:00-09:59 这一档；晚上启动不再补当天的
    if not (8 <= now.hour < 10):
        return None
    day = now.strftime("%Y-%m-%d")
    title = "每日发布记录摘要 " + day
    for j in JR.list_jobs():
        if j.get("title") == title:
            return None
    return JR.create(title, "只读发布记录并生成摘要", ttl_hours=24,
                     agent="daily", cwd="", access="read",
                     action_type="summarize_file",
                     action_payload={"path": _daily_path()}, agent_id="daily")


def run_daily_brief():
    """08:00 生成工厂只读简报；不调模型、不改 ERP。"""
    from pai import daily_brief as DB
    return DB.run()


def ensure_daily_brief(now=None):
    if os.environ.get("PAI_FACTORY_BRIEF", "").strip().lower() not in ("1", "on", "true", "yes"):
        return None
    now = now or datetime.datetime.now().astimezone()
    if not (8 <= now.hour < 10):
        return None
    from pai import daily_brief as DB
    p = DB.OUT / ("brief-%s.md" % now.date().isoformat())
    if p.is_file():
        return None
    return DB.run(now.date())


def run_backup():
    """每日 03:00 备份 data；保留最近 7 份。"""
    from pai import backup_task as BT
    return BT.run()


def run_due_reminders():
    """每分钟检查主库 reminders；到点用本地模型执行并记录。"""
    from pai import unified as U
    for rid, name, prompt in U.due():
        try:
            from pai.memory import Memory
            from pai.profile import db_path
            from pai.orchestrator import Orchestrator
            out = Orchestrator(Memory(str(db_path()))).handle(prompt, channel="reminder")
            text = out.get("response") or ""
            U.mark_done(rid)
            print("[提醒 %s] %s" % (name, text[:120]))
        except Exception as e:
            print("[提醒 %s] 失败：%s: %s" % (name, type(e).__name__, e))


def start_scheduler():
    """APScheduler 08:00 准点；错过 2 小时内由启动补建。"""
    global _SCHED
    if _SCHED and getattr(_SCHED, "running", False):
        return _SCHED
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        tz = datetime.datetime.now().astimezone().tzinfo
        sched = BackgroundScheduler(timezone=tz)
        sched.add_job(ensure_daily_job, "cron", hour=8, minute=0,
                      id="daily-summary", replace_existing=True,
                      coalesce=True, max_instances=1, misfire_grace_time=7200)
        sched.add_job(run_daily_brief, "cron", hour=8, minute=0,
                      id="daily-factory-brief", replace_existing=True,
                      coalesce=True, max_instances=1, misfire_grace_time=7200)
        sched.add_job(run_backup, "cron", hour=3, minute=0,
                      id="daily-backup", replace_existing=True,
                      coalesce=True, max_instances=1, misfire_grace_time=7200)
        sched.add_job(run_due_reminders, "cron", minute="*",
                      id="user-reminders", replace_existing=True,
                      coalesce=True, max_instances=1, misfire_grace_time=120)
        sched.start()
        _SCHED = sched
        return sched
    except Exception:
        return None


def scheduler_status():
    try:
        jobs = _SCHED.get_jobs() if _SCHED else []
        return {"running": bool(_SCHED and _SCHED.running),
                "next": [j.next_run_time.isoformat() if j.next_run_time else "" for j in jobs]}
    except Exception:
        return {"running": False, "next": []}


def _write_state(**kw):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    d = {"at": _now(), "pid": os.getpid(), "alive": _THREAD.is_alive() if _THREAD else False}
    d.update(kw)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE)


def status():
    try:
        d = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    d["thread_alive"] = bool(_THREAD and _THREAD.is_alive())
    d["enabled"] = enabled()
    d["scheduler"] = scheduler_status()
    return d


def run_forever(interval=60, dry_run=False):
    _STOP.clear()
    scans = claims = failures = 0
    _write_state(running=True, scans=0, claims=0, failures=0, interval=interval, dry_run=dry_run)
    while not _STOP.is_set():
        try:
            out = run_once(dry_run=dry_run)
            scans += 1
            claims += int(out.get("claimed") or 0)
            if not out.get("ok"):
                failures += 1
            _write_state(running=True, scans=scans, claims=claims, failures=failures,
                         last=out, interval=interval, dry_run=dry_run)
        except Exception as e:
            failures += 1
            _write_state(running=True, scans=scans, claims=claims, failures=failures,
                         error="%s: %s" % (type(e).__name__, e), interval=interval, dry_run=dry_run)
        if _STOP.wait(max(5, int(interval))):
            break
    _write_state(running=False, scans=scans, claims=claims, failures=failures,
                 interval=interval, dry_run=dry_run)


def start_background(interval=60, dry_run=False):
    global _THREAD
    if not enabled() or not JR.enabled():
        return False
    if _THREAD and _THREAD.is_alive():
        return True
    start_scheduler()
    try:
        ensure_daily_job()
    except Exception:
        pass
    try:
        ensure_daily_brief()
    except Exception:
        pass
    _THREAD = threading.Thread(target=run_forever, kwargs={"interval": interval, "dry_run": dry_run},
                               daemon=True, name="job-worker")
    _THREAD.start()
    return True


def stop_background():
    _STOP.set()


def run_once(dry_run=True):
    if not JR.enabled():
        raise RuntimeError("PAI_JOB_RUNNER 未开启")
    if not enabled():
        raise RuntimeError("PAI_JOB_WORKER 未开启")
    with closing(JR._conn()) as c, c:
        where = ("status='pending'" if dry_run else
                 "status='pending' AND approved=1 AND attempts < 3 "
                 "AND (depends_on='' OR EXISTS(SELECT 1 FROM jobs p WHERE p.id=jobs.depends_on AND p.status='done'))")
        row = c.execute("SELECT * FROM jobs WHERE " + where + " ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            return {"ok": True, "claimed": 0, "mode": "dry-run" if dry_run else "execute",
                    "message": "没有 pending 作业"}
        job = JR._row(row)
        p = plan(job)
        if dry_run:
            # 试运行只看计划，不改作业状态、不消耗批准
            JR._add(c, job["id"], "dry_run", p)
            return {"ok": True, "claimed": 1, "mode": "dry-run", "job_id": job["id"], "plan": p}
        # 原子领活，避免两个 worker 同时拿同一条
        cur = c.execute("UPDATE jobs SET status='running', updated_at=? WHERE id=? AND status='pending' AND approved=1",
                        (_now(), job["id"]))
        if cur.rowcount != 1:
            return {"ok": True, "claimed": 0, "message": "被别的 worker 领走或未批准"}
        JR._add(c, job["id"], "claimed", {"mode": "execute"})
        JR._add(c, job["id"], "planned", p)
    aid = str(job.get("agent_id") or "")
    if aid and not AR.allowed(aid, "job:claim"):
        JR.fail(job["id"], "agent 无 job:claim 权限或已停用：%s" % aid)
        return {"ok": False, "claimed": 1, "mode": "execute", "job_id": job["id"],
                "error": "agent revoked or missing scope"}
    import time
    t0 = time.time()
    try:
        out = _execute(job)
        verify_spec = (job.get("action_payload") or {}).get("verify")
        if verify_spec:
            ver = VF.verify(verify_spec, out)
            out = dict(out) if isinstance(out, dict) else {"result": out}
            out["verification"] = ver
            JR.add_event(job["id"], "verified", ver)
            if not ver.get("ok"):
                raise ValueError("验收未通过：" + str(ver.get("checks"))[:500])
        dep = job.get("depends_on") or ""
        if dep:
            prev = JR.get(dep) or {}
            out = dict(out) if isinstance(out, dict) else {"ok": True, "result": out}
            out["upstream"] = {"id": dep, "status": prev.get("status"),
                               "result": str(prev.get("result") or "")[:2000]}
        JR.finish(job["id"], json.dumps(out, ensure_ascii=False)[:4000])
        JR.add_event(job["id"], "metrics", {"duration_ms": int((time.time() - t0) * 1000),
                                            "cost": float((out or {}).get("cost") or 0)})
        try:
            ROI.record(job_id=job["id"], workspace_id=job.get("workspace_id") or "",
                       agent_id=job.get("agent_id") or "",
                       estimated_minutes=job.get("roi_estimate_minutes") or 0,
                       actual_seconds=time.time() - t0, success=True, note="done")
        except Exception:
            pass
        return {"ok": True, "claimed": 1, "mode": "execute", "job_id": job["id"], "result": out}
    except Exception as e:
        JR.fail_or_retry(job["id"], "%s: %s" % (type(e).__name__, e))
        try:
            cur = JR.get(job["id"]) or {}
            if cur.get("status") == "failed":
                fail_dependents(job["id"], str(e))
        except Exception:
            pass
        try:
            ROI.record(job_id=job["id"], workspace_id=job.get("workspace_id") or "",
                       agent_id=job.get("agent_id") or "",
                       estimated_minutes=job.get("roi_estimate_minutes") or 0,
                       actual_seconds=time.time() - t0, success=False, note=str(e)[:200])
        except Exception:
            pass
        return {"ok": False, "claimed": 1, "mode": "execute", "job_id": job["id"],
                "error": str(e)}


def fail_dependents(job_id, reason=""):
    """上游失败：把直接/间接下游 pending 作业标失败，避免永远等。"""
    seen = set()
    cur = [str(job_id)]
    while cur:
        nxt = []
        for parent in cur:
            with closing(JR._conn()) as c, c:
                rows = c.execute("SELECT id FROM jobs WHERE depends_on=? AND status='pending'", (parent,)).fetchall()
            for r in rows:
                jid = r["id"]
                if jid in seen:
                    continue
                seen.add(jid)
                try:
                    JR.fail(jid, "上游失败，停止：%s" % (reason or parent))
                except Exception:
                    pass
                nxt.append(jid)
        cur = nxt
    return sorted(seen)
