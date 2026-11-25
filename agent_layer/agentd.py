# -*- coding: utf-8 -*-
"""Windows 上的 agent 服务层 v0（用户级，不碰系统文件/注册表/服务）。

只做五件事：
  任务队列 / 认领锁 / 能力字段 / 审计 / STOP

启动：
    python agentd.py --port 8766

约束：
  · 只绑 127.0.0.1；非本机 Host 一律 403
  · 不装自启、不建 Windows 服务、不写注册表
  · STOP 文件存在时：拒绝新任务/认领，只留 health
  · 不执行工具；v0 只记录任务与状态（能力是声明，不是执行）
"""
import argparse
import datetime
import hashlib
import hmac
import secrets
import ipaddress
import json
import pathlib
import os
import sqlite3
import threading
import re
import uuid
from policy import authorize
from registry import Registry
import memory_tool as MT
try:
    import msvcrt
except ImportError:
    msvcrt = None
try:
    import fcntl
except ImportError:
    fcntl = None

import tools_exec as TX
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 64 * 1024
CAPS = ("read", "write", "network")
LOCK_OWNERS = ("qoder", "deepseek")


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def is_local_addr(addr):
    try:
        return ipaddress.ip_address(str(addr)).is_loopback
    except ValueError:
        return False


def is_local_host(host):
    if not host:
        return False
    h = host.split(":", 1)[0].strip().lower()
    return h in ("127.0.0.1", "localhost", "[::1]", "::1")


class Store:
    def __init__(self, data_dir, blackboard=None, dev=False, dev_write=False, trace_dir=None, memory_db=None, allow_real_db=False):
        self.dir = Path(data_dir)
        self.db = self.dir / "agentd.db"
        self.audit_path = self.dir / "audit.jsonl"
        self.audit_failures = 0
        self.trace_failures = 0
        self.degradation_count = 0
        self.degraded = None
        self.stop_path = self.dir / "STOP"
        self.lock_path = self.dir / "agentd.lock"
        self.tokens_path = self.dir / "tokens.json"
        self.approval_key_path = self.dir / "approval.key"
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.memory_db = MT.validate_db(memory_db, allow_real=allow_real_db) if memory_db else None
        self.dir.mkdir(parents=True, exist_ok=True)
        self.blackboard = Path(blackboard) if blackboard else None
        self.dev = bool(dev)
        self.dev_write = bool(dev_write)
        self.tools = Registry()
        self.tool_specs = dict(TX.TOOLS)
        for _name in TX.TOOLS:
            self.tools.register(_name, TX.execute)
        if self.memory_db:
            self.tool_specs.update(MT.SPEC)
            self.tools.register("memory_fact", lambda tool, args: MT.write_fact(self.memory_db, args))
        self.lock = threading.Lock()
        self.con = sqlite3.connect(str(self.db), check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, created TEXT, goal TEXT, capability TEXT,
                status TEXT, owner TEXT, budget INTEGER, calls_used INTEGER DEFAULT 0,
                payload TEXT, result TEXT, evidence TEXT, reason TEXT,
                claimed_at TEXT, lease_until TEXT, done_at TEXT,
                attempts INTEGER DEFAULT 0, max_attempts INTEGER DEFAULT 2,
                confirm INTEGER DEFAULT 0, approved INTEGER DEFAULT 0,
                priority INTEGER DEFAULT 0, depends TEXT DEFAULT '[]',
                lock_owner TEXT DEFAULT '', lock_key TEXT DEFAULT ''
            )""")
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(tasks)")}
        for name, ddl in (("attempts", "INTEGER DEFAULT 0"),
                          ("max_attempts", "INTEGER DEFAULT 2"),
                          ("confirm", "INTEGER DEFAULT 0"),
                          ("approved", "INTEGER DEFAULT 0"),
                          ("priority", "INTEGER DEFAULT 0"),
                          ("depends", "TEXT DEFAULT '[]'"),
                          ("lock_owner", "TEXT DEFAULT ''"),
                          ("lock_key", "TEXT DEFAULT ''"),
                          ("approved_by", "TEXT DEFAULT ''"),
                          ("approval_id", "TEXT DEFAULT ''"),
                          ("approval_sig", "TEXT DEFAULT ''"),
                          ("approved_at", "TEXT DEFAULT ''")):
            if name not in cols:
                self.con.execute("ALTER TABLE tasks ADD COLUMN %s %s" % (name, ddl))
        if not self.approval_key_path.exists():
            self.approval_key_path.write_text(secrets.token_hex(32), encoding="ascii")
        self.approval_key = self.approval_key_path.read_text(encoding="ascii").strip().encode("ascii")
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS simulations (
                key TEXT PRIMARY KEY, task_id TEXT, env_fp TEXT, created TEXT,
                result TEXT, confidence REAL, actual TEXT,
                verified INTEGER, invalidated_reason TEXT,
                last_invalidated_reason TEXT
            )""")
        scols = {r[1] for r in self.con.execute("PRAGMA table_info(simulations)")}
        if "last_invalidated_reason" not in scols:
            self.con.execute("ALTER TABLE simulations ADD COLUMN last_invalidated_reason TEXT DEFAULT ''")
        self.con.commit()

    def _degrade(self, kind, detail):
        self.degradation_count += 1
        if self.degraded is None:
            self.degraded = {"kind": str(kind), "detail": str(detail), "at": now()}

    def audit(self, event, **fields):
        rec = {"ts": now(), "event": event}
        rec.update(fields)
        try:
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as exc:
            self.audit_failures += 1
            self._degrade("audit", type(exc).__name__)

    def stopped(self):
        return self.stop_path.exists()

    def acquire(self):
        """单实例锁：用系统文件锁，进程死了锁自动释放。"""
        f = open(self.lock_path, "a+", encoding="utf-8")
        try:
            if msvcrt is not None:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            elif fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                f.close()
                return False
        except OSError:
            f.close()
            return False
        self._lock_file = f
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
        return True

    def release(self):
        f, self._lock_file = self._lock_file, None
        if not f:
            return
        try:
            if msvcrt is not None:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        try:
            f.close()
        except OSError:
            pass

    def tokens(self):
        if not self.tokens_path.exists():
            return {}
        try:
            return json.loads(self.tokens_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def token_required(self):
        return not self.dev

    def dev_caps(self):
        return ["read", "write"] if self.dev_write else ["read"]

    def auth(self, token):
        if not token:
            return None, None
        h = hashlib.sha256(token.encode()).hexdigest()
        for owner, rec in self.tokens().items():
            if rec.get("hash") == h:
                return owner, list(rec.get("caps") or [])
        return None, None

    def _backlog_lines(self):
        lines = self.blackboard.read_text(encoding="utf-8").splitlines()
        start = None
        for i, line in enumerate(lines):
            if line.startswith("---- ") and "Backlog & 认领锁" in line:
                start = i
                break
        if start is None:                 # 测试用小板：没有区段头，整板都算
            return lines
        for j in range(start + 1, len(lines)):
            if lines[j].startswith("---- "):
                return lines[start + 1:j]
        return lines[start + 1:]

    @staticmethod
    def _lock_title(line):
        title = re.split(r"\[", line.strip(), maxsplit=1)[0].strip()
        if not title:                      # 测试式 [锁:Q] G1
            title = re.sub(r"\[[^\]]*\]", "", line.strip()).strip()
        return title

    def locks_detail(self):
        if not self.blackboard or not self.blackboard.exists():
            return []
        out = []
        for line in self._backlog_lines():
            m = re.search(r"\[锁:\s*([^\]]+)\]", line)
            if not m:
                continue
            owner = m.group(1).strip()
            indent = len(line) - len(line.lstrip(" "))
            if indent > 6:
                continue
            if owner.lower() not in LOCK_OWNERS:
                continue
            closed = any(x in line for x in ("[已并入", "[结案", "[退回", "[已关闭"))
            out.append({"owner": owner, "line": line.strip(),
                        "title": self._lock_title(line), "active": not closed})
        return out

    def locks(self):
        return [x["line"] for x in self.locks_detail()]

    def lock_ok(self, owner, key):
        if not owner or not key:
            return False
        for x in self.locks_detail():
            if not x["active"]:
                continue
            if x["owner"].lower() == owner.lower() and key == x["title"]:
                return True
        return False

    def create(self, goal, capability, budget, owner, payload, priority=0, depends=None,
               lock_owner="", lock_key=""):
        if capability not in CAPS:
            return None, "capability 必须是 %s" % "/".join(CAPS)
        tid = uuid.uuid4().hex[:12]
        confirm = 1 if capability in ("write", "network") else 0
        deps = list(depends or [])
        if lock_owner:
            if str(lock_owner).lower() not in LOCK_OWNERS:
                return None, "锁主只允许 Qoder / DeepSeek"
            if not str(lock_key or "").strip():
                return None, "带锁任务必须给 lock_key（Backlog 行头标题）"
        with self.lock:
            for d in deps:
                if d == tid:
                    return None, "依赖不能是自身"
                if not self.con.execute("SELECT 1 FROM tasks WHERE id=?", (d,)).fetchone():
                    return None, "依赖不存在：%s" % d
            self.con.execute(
                "INSERT INTO tasks (id,created,goal,capability,status,owner,budget,payload,"
                "confirm,priority,depends,lock_owner,lock_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, now(), goal, capability, "queued", owner or "", int(budget),
                 json.dumps(payload or {}, ensure_ascii=False), confirm,
                 int(priority), json.dumps(deps, ensure_ascii=False),
                 str(lock_owner or ""), str(lock_key or "")))
            self.con.commit()
        self.audit("task.created", id=tid, owner=owner or "", capability=capability)
        return tid, None

    def list(self, status=None):
        with self.lock:
            if status:
                rows = self.con.execute(
                    "SELECT * FROM tasks WHERE status=? ORDER BY priority DESC, created", (status,))
            else:
                rows = self.con.execute("SELECT * FROM tasks ORDER BY priority DESC, created")
            return [dict(r) for r in rows]

    def get(self, tid):
        with self.lock:
            r = self.con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            return dict(r) if r else None

    def reap_expired(self):
        """把租约过期的 running 任务放回 queued；不计 attempt。"""
        with self.lock:
            rows = self.con.execute(
                "SELECT * FROM tasks WHERE status IN ('running','executing')"
                " AND lease_until IS NOT NULL AND lease_until < ?", (now(),)).fetchall()
            for r in rows:
                self.con.execute(
                    "UPDATE tasks SET status='queued', owner='', claimed_at=NULL,"
                    " lease_until=NULL, approved=0, approved_by='', approval_id='',"
                    " approval_sig='', approved_at='' WHERE id=?", (r["id"],))
                self.audit("task.lease_expired", id=r["id"], owner=r["owner"] or "")
            self.con.commit()
        return len(rows)

    def claim(self, tid, owner, lease_s=300, caps=None):
        self.reap_expired()
        if not owner:
            return False, "owner 必填"
        until = (datetime.datetime.now() + datetime.timedelta(seconds=max(5, lease_s))).isoformat(timespec="seconds")
        with self.lock:
            r = self.con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            if not r:
                return False, "任务不存在"
            decision = authorize(r["capability"], caps)
            if not decision.is_allowed():
                return False, decision.reason
            if r["status"] == "done" or r["status"] == "failed" or r["status"] == "cancelled":
                return False, "任务已结束（%s）" % r["status"]
            if int(r["confirm"] or 0) and not self._approval_valid(r):
                return False, "任务需要先批准（POST /approve），或批准标记无效"
            if r["lock_owner"]:
                if owner.lower() != r["lock_owner"].lower():
                    return False, "锁属于 %s，%s 不能认领" % (r["lock_owner"], owner)
                if not self.lock_ok(r["lock_owner"], r["lock_key"] or ""):
                    return False, "黑板锁不存在：%s" % r["lock_owner"]
            for d in json.loads(r["depends"] or "[]"):
                dep = self.con.execute("SELECT status FROM tasks WHERE id=?", (d,)).fetchone()
                if not dep or dep["status"] != "done":
                    return False, "依赖未完成：%s（%s）" % (d, dep["status"] if dep else "missing")
            if r["status"] == "running" and r["owner"] and r["owner"] != owner:
                if (r["lease_until"] or "") > now():
                    return False, "已被 %s 认领" % r["owner"]
            self.con.execute(
                "UPDATE tasks SET status='running', owner=?, claimed_at=?, lease_until=? WHERE id=?",
                (owner, now(), until, tid))
            self.con.commit()
        self.audit("task.claimed", id=tid, owner=owner, lease_until=until)
        return True, None

    def finish(self, tid, owner, ok, result, evidence, calls_used):
        with self.lock:
            r = self.con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            if not r:
                return False, "任务不存在"
            if r["owner"] != owner:
                return False, "只有认领者能提交结果"
            if int(calls_used or 0) > int(r["budget"]):
                return False, "超出预算 %d" % r["budget"]
            status = "done" if ok else "failed"
            attempts = int(r["attempts"] or 0) + 1
            retried = False
            if not ok and attempts < int(r["max_attempts"] or 2):
                status, retried = "queued", True
                cur = self.con.execute(
                    "UPDATE tasks SET status=?, result=?, evidence=?, calls_used=?, attempts=?,"
                    " owner='', lease_until=NULL, done_at=NULL, approved=0,"
                    " approved_by='', approval_id='', approval_sig='', approved_at=''"
                    " WHERE id=? AND status IN ('running','executing')",
                    (status, str(result or ""), str(evidence or ""), int(calls_used or 0), attempts, tid))
            else:
                cur = self.con.execute(
                    "UPDATE tasks SET status=?, result=?, evidence=?, calls_used=?, attempts=?, done_at=?"
                    " WHERE id=? AND status IN ('running','executing')",
                    (status, str(result or ""), str(evidence or ""), int(calls_used or 0), attempts, now(), tid))
            if cur.rowcount != 1:
                self.con.commit()
                return False, "任务已取消/结束，结果未写入"
            self.con.commit()
        self.audit("task." + ("retried" if retried else status), id=tid, owner=owner,
                   calls_used=int(calls_used or 0), attempts=attempts)
        return True, None

    def trace_approval(self, tid, owner, decision="allow-once", reason=""):
        if not self.trace_dir:
            return
        rec = {"at": now(), "kind": "approval.decision", "decision": decision,
               "subject": "write_task", "by": str(owner), "task_id": str(tid),
               "reason": str(reason), "source": "agentd"}
        try:
            p = self.trace_dir / ("trace-%s.jsonl" % datetime.date.today().isoformat())
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            self.trace_failures += 1
            self._degrade("trace", type(e).__name__)
            self.audit("trace.write_failed", id=str(tid), owner=str(owner),
                       error=type(e).__name__)

    def _approval_sig(self, tid, owner, nonce, at):
        raw = "%s|%s|%s|%s" % (tid, owner, nonce, at)
        return hmac.new(self.approval_key, raw.encode("utf-8"), hashlib.sha256).hexdigest()

    def _approval_valid(self, r):
        if not int(r["approved"] or 0):
            return False
        owner = str(r["approved_by"] or "")
        nonce = str(r["approval_id"] or "")
        sig = str(r["approval_sig"] or "")
        at = str(r["approved_at"] or "")
        if not (owner and nonce and sig and at):
            return False
        return hmac.compare_digest(sig, self._approval_sig(str(r["id"]), owner, nonce, at))

    def approve(self, tid, owner, caps=None):
        with self.lock:
            r = self.con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            if not r:
                self.trace_approval(tid, owner, "deny", "任务不存在")
                return False, "任务不存在"
            if not int(r["confirm"] or 0):
                self.trace_approval(tid, owner, "deny", "任务不需要批准")
                return False, "任务不需要批准"
            if caps is None:
                self.trace_approval(tid, owner, "deny", "缺少 caps")
                return False, "缺少 caps；拒绝"
            if r["capability"] not in caps:
                self.trace_approval(tid, owner, "deny", "批准者没有 %s 能力" % r["capability"])
                return False, "批准者没有 %s 能力" % r["capability"]
            nonce = secrets.token_hex(8)
            at = now()
            sig = self._approval_sig(str(tid), str(owner), nonce, at)
            self.con.execute(
                "UPDATE tasks SET approved=1, approved_by=?, approval_id=?, approval_sig=?, approved_at=? WHERE id=?",
                (str(owner), nonce, sig, at, tid))
            rows = self.con.execute("SELECT key,result FROM simulations WHERE task_id=?", (tid,)).fetchall()
            for row in rows:
                try:
                    sim = json.loads(row["result"])
                except (TypeError, ValueError):
                    continue
                if sim.get("decision") == "pending":
                    sim["decision"] = "allow-once"
                    self.con.execute("UPDATE simulations SET result=? WHERE key=?", (json.dumps(sim, ensure_ascii=False), row["key"]))
            self.con.commit()
        self.audit("task.approved", id=tid, owner=owner)
        self.trace_approval(tid, str(owner))
        return True, None

    def run_tool(self, tid, owner, caps=None):
        with self.lock:
            r = self.con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            if not r:
                return False, "任务不存在"
            decision = authorize(r["capability"], caps)
            if not decision.is_allowed():
                return False, decision.reason
            if r["status"] != "running":
                return False, "任务未在执行（%s）" % r["status"]
            if r["owner"] != owner:
                return False, "只有认领者能执行"
            if int(r["confirm"] or 0) and not self._approval_valid(r):
                return False, "任务需要先批准，或批准标记无效"
            payload = json.loads(r["payload"] or "{}")
            tool = str(payload.get("tool") or "")
            spec = self.tool_specs.get(tool)
            if not spec:
                return False, "未知工具：%s" % tool
            if spec["capability"] != r["capability"]:
                return False, "工具能力 %s 与任务 %s 不符" % (spec["capability"], r["capability"])
            if self.tools.get(tool) is None:
                return False, "工具未注册：%s" % tool
            if r["capability"] == "write":
                key = self._sim_key(payload)
                sim = self.con.execute(
                    "SELECT * FROM simulations WHERE key=? AND task_id=?", (key, tid)).fetchone()
                if sim is None or sim["invalidated_reason"]:
                    return False, "写任务需要先 /simulate（且缓存未失效）"
                try:
                    preview = json.loads(sim["result"])
                except (TypeError, ValueError):
                    return False, "写任务预览不可读"
                if preview.get("decision") != "allow-once":
                    return False, "写任务需要先批准后的 /simulate"
                if tool == "memory_fact":
                    if not self.memory_db:
                        return False, "记忆写未启用（缺 --memory-db）"
                    if preview.get("before_sha12") != MT.before_sha(self.memory_db, payload.get("args") or {}):
                        self.con.execute("UPDATE simulations SET invalidated_reason=?, last_invalidated_reason=? WHERE key=?", ("memory_before_mismatch", "memory_before_mismatch", key))
                        self.con.commit()
                        return False, "记忆在预览后已变化，请重新 /simulate"
                else:
                    expected = str((payload.get("args") or {}).get("text") or "").encode("utf-8")
                    bytes_after = preview.get("bytes_after")
                    if bytes_after is None or int(bytes_after) != len(expected) or \
                            preview.get("text_sha12") != hashlib.sha256(expected).hexdigest()[:12]:
                        self.con.execute("UPDATE simulations SET invalidated_reason=?, last_invalidated_reason=? WHERE key=?", ("preview_tampered", "preview_tampered", key))
                        self.con.commit()
                        return False, "预览与任务正文不一致"
                    try:
                        target = TX._resolve((payload.get("args") or {}).get("path"), True)
                        now_exists = target.is_file()
                        now_raw = target.read_bytes() if now_exists else b""
                        now_bytes = len(now_raw)
                        now_sha = hashlib.sha256(now_raw).hexdigest()[:12]
                    except Exception:
                        return False, "写目标不可读"
                    if bool(preview.get("exists")) != now_exists or preview.get("before_sha12") != now_sha:
                        self.con.execute("UPDATE simulations SET invalidated_reason=?, last_invalidated_reason=? WHERE key=?", ("run_before_mismatch", "run_before_mismatch", key))
                        self.con.commit()
                        return False, "写目标在预览后已变化，请重新 /simulate"
            used = int(r["calls_used"] or 0) + 1
            if used > int(r["budget"]):
                return False, "超出预算 %d" % r["budget"]
            cur = self.con.execute(
                "UPDATE tasks SET status='executing', calls_used=?, approved=0,"
                " approved_by='', approval_id='', approval_sig='', approved_at=''"
                " WHERE id=? AND status='running' AND owner=?",
                (used, tid, owner))
            if cur.rowcount != 1:
                return False, "任务已在执行"
            self.con.commit()
        try:
            fn = self.tools.get(tool)
            if fn is None:
                return False, "工具未注册：%s" % tool
            out = fn(tool, payload.get("args") or {})
        except Exception as e:
            self.finish(tid, owner, False, "tool_error: %s" % e, "", used)
            self.audit("task.tool_failed", id=tid, owner=owner, tool=tool,
                       error=type(e).__name__)
            return False, "工具失败：%s" % e
        self._record_actual(tid, payload, out)
        ok, err = self.finish(tid, owner, True, json.dumps(out, ensure_ascii=False), "", used)
        if ok:
            self.audit("task.tool_finished", id=tid, owner=owner, tool=tool)
        return ok, err

    def _record_memory_actual(self, tid, payload):
        key = self._sim_key(payload)
        row = self.con.execute("SELECT * FROM simulations WHERE key=? AND task_id=?", (key, tid)).fetchone()
        if row is None:
            return
        try:
            preview = json.loads(row["result"])
        except (TypeError, ValueError):
            return
        actual = MT.after_sha(payload.get("args") or {})
        ok = actual == preview.get("after_sha12")
        with self.lock:
            self.con.execute(
                "UPDATE simulations SET actual=?, verified=?, invalidated_reason=?, last_invalidated_reason=? WHERE key=?",
                (actual, 1 if ok else 0, "" if ok else "actual_mismatch",
                 "" if ok else "actual_mismatch", key))
            self.con.commit()

    def _env_fp(self):
        def file_sha(p):
            try:
                return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()[:12]
            except OSError:
                return "unknown"
        here = pathlib.Path(__file__).resolve()
        raw = "|".join([str(TX.ROOT), str(TX.SANDBOX),
                        file_sha(TX.__file__), file_sha(here),
                        file_sha(here.with_name("policy.py"))])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _sim_key(self, payload):
        args = (payload or {}).get("args") or {}
        if (payload or {}).get("tool") == "memory_fact":
            raw = "|".join(["memory_fact", MT.canonical(args), self._env_fp()])
        else:
            raw = "|".join([str(payload.get("tool") or ""), str(args.get("path") or ""),
                            hashlib.sha256(str(args.get("text") or "").encode("utf-8")).hexdigest(),
                            self._env_fp()])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _simulate_memory(self, tid, r, payload):
        key = self._sim_key(payload)
        cached = self.con.execute("SELECT * FROM simulations WHERE key=?", (key,)).fetchone()
        invalidated = (cached["invalidated_reason"] if cached else "") or ""
        if cached and int(cached["verified"] or 0) == 1 and not cached["invalidated_reason"]:
            out = json.loads(cached["result"])
            out["cached"] = True
            out["verified"] = True
            out["decision"] = "allow-once" if self._approval_valid(r) else "pending"
            return True, None, out
        info = MT.simulate_fact(self.memory_db, payload.get("args") or {})
        out = dict(info)
        out.update({"kind": "memory_fact", "decision": "allow-once" if self._approval_valid(r) else "pending",
                    "cached": False, "verified": False, "simulated_at": now(),
                    "target": "memory:facts", "exists": False,
                    "bytes_before": 0, "bytes_after": 0, "text_sha12": ""})
        self.con.execute(
            "INSERT OR REPLACE INTO simulations"
            " (key,task_id,env_fp,created,result,confidence,actual,verified,invalidated_reason,last_invalidated_reason)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (key, tid, self._env_fp(), now(), json.dumps(out, ensure_ascii=False),
             out["confidence"], "", 0, "", invalidated))
        self.con.commit()
        return True, None, out

    def simulate(self, tid, owner, caps=None):
        with self.lock:
            r = self.con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            if not r:
                return False, "任务不存在", None
            decision = authorize(r["capability"], caps)
            if not decision.is_allowed():
                return False, decision.reason, None
            payload = json.loads(r["payload"] or "{}")
            tool = str(payload.get("tool") or "")
            args = payload.get("args") or {}
            if tool == "memory_fact":
                if not self.memory_db:
                    return False, "记忆写未启用（缺 --memory-db）", None
                return self._simulate_memory(tid, r, payload)
            if tool != "write_text":
                return False, "dry-run 只支持 write_text/memory_fact", None
            try:
                target = TX._resolve(args.get("path"), True)
            except Exception as e:
                return False, "路径不可写：%s" % e, None
            key = self._sim_key(payload)
            cached = self.con.execute("SELECT * FROM simulations WHERE key=?", (key,)).fetchone()
            invalidated = (cached["invalidated_reason"] if cached else "") or ""
            if cached and int(cached["verified"] or 0) == 1 and not cached["invalidated_reason"]:
                out = json.loads(cached["result"])
                now_exists = target.is_file()
                now_raw = target.read_bytes() if now_exists else b""
                now_bytes = len(now_raw)
                now_sha = hashlib.sha256(now_raw).hexdigest()[:12]
                if out.get("exists") != now_exists or out.get("before_sha12") != now_sha:
                    invalidated = "before_mismatch"
                    self.con.execute(
                        "UPDATE simulations SET invalidated_reason=?, last_invalidated_reason=? WHERE key=?",
                        (invalidated, invalidated, key))
                    self.con.commit()
                else:
                    out["cached"] = True
                    out["verified"] = True
                    out["decision"] = "allow-once" if self._approval_valid(r) else "pending"
                    return True, None, out
            before = target.read_bytes() if target.is_file() else b""
            after = str(args.get("text") or "").encode("utf-8")
            out = {
                "decision": "allow-once" if self._approval_valid(r) else "pending",
                "target": str(target),
                "exists": target.is_file(),
                "bytes_before": len(before),
                "before_sha12": hashlib.sha256(before).hexdigest()[:12],
                "bytes_after": len(after),
                "text_sha12": hashlib.sha256(after).hexdigest()[:12],
                "effects": ["overwrite" if target.is_file() else "create"],
                "rollback": "restore old bytes" if target.is_file() else "delete created file",
                "unknowns": ["dry-run 只覆盖 write_text"],
                "confidence": 0.9 if self._approval_valid(r) else 0.5,
                "cached": False,
                "verified": False,
                "simulated_at": now(),
            }
            self.con.execute(
                "INSERT OR REPLACE INTO simulations"
                " (key,task_id,env_fp,created,result,confidence,actual,verified,invalidated_reason,last_invalidated_reason)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (key, tid, self._env_fp(), now(), json.dumps(out, ensure_ascii=False),
                 out["confidence"], "", 0, "", invalidated))
            self.con.commit()
        return True, None, out

    def _record_actual(self, tid, payload, out):
        if payload.get("tool") == "memory_fact":
            self._record_memory_actual(tid, payload)
            return
        try:
            target = TX._resolve((payload.get("args") or {}).get("path"), True)
            actual = target.read_bytes() if target.is_file() else b""
        except Exception:
            return
        key = self._sim_key(payload)
        with self.lock:
            row = self.con.execute("SELECT * FROM simulations WHERE key=?", (key,)).fetchone()
            if not row:
                return
            sim = json.loads(row["result"])
            sha12 = hashlib.sha256(actual).hexdigest()[:12]
            ok = len(actual) == int(sim.get("bytes_after") or 0) and sha12 == sim.get("text_sha12")
            if ok:
                sim["exists"] = target.is_file()
                sim["bytes_before"] = len(actual)
                sim["before_sha12"] = sha12
                sim["effects"] = ["overwrite"]
                sim["rollback"] = "restore old bytes"
            self.con.execute(
                "UPDATE simulations SET result=?, actual=?, verified=?, invalidated_reason=?, last_invalidated_reason=? WHERE key=?",
                (json.dumps(sim, ensure_ascii=False),
                 json.dumps({"bytes": len(actual), "sha12": sha12}, ensure_ascii=False),
                 1 if ok else 0, "" if ok else "actual_mismatch", "" if ok else "actual_mismatch", key))
            self.con.commit()

    def cancel(self, tid, reason):
        with self.lock:
            r = self.con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            if not r:
                return False, "任务不存在"
            if r["status"] in ("done", "failed", "cancelled"):
                return False, "任务已结束"
            self.con.execute("UPDATE tasks SET status='cancelled', reason=?, done_at=? WHERE id=?",
                             (str(reason or ""), now(), tid))
            self.con.commit()
        self.audit("task.cancelled", id=tid, reason=str(reason or ""))
        return True, None


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentLayer/0.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def _auth(self):
        st = self.server.store
        if not st.token_required():
            return (None, st.dev_caps())
        owner, caps = st.auth(self.headers.get("X-Agent-Token") or "")
        if not owner:
            self._send(401, {"error": "缺少/无效 X-Agent-Token"})
            return None
        return owner, caps

    def _blocked_host(self):
        if not is_local_addr(self.client_address[0]):
            self.close_connection = True
            self._send(403, {"error": "只允许本机来源"})
            return True
        if is_local_host(self.headers.get("Host")):
            return False
        self.close_connection = True
        self._send(403, {"error": "只允许本机访问"})
        return True

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, 400, "长度非法"
        if n < 0 or n > MAX_BODY:
            self.close_connection = True
            return None, 413, "内容过大"
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}"), None, None
        except (ValueError, UnicodeDecodeError):
            return None, 400, "不是合法 JSON"

    def do_GET(self):
        if self._blocked_host():
            return
        st = self.server.store
        if self.path == "/health":
            self._send(200, {"ok": True, "stopped": st.stopped(),
                             "tasks": len(st.list()),
                             "audit_failures": st.audit_failures,
                             "trace_failures": st.trace_failures,
                             "degradation_count": st.degradation_count,
                             "degraded": st.degraded})
        elif self.path == "/locks":
            a = self._auth()
            if a is None:
                return
            self._send(200, {"locks": st.locks()})
        elif self.path.startswith("/tasks"):
            a = self._auth()
            if a is None:
                return
            if st.stopped():
                self._send(503, {"error": "STOP 已置位"})
                return
            status = None
            if "?" in self.path:
                q = self.path.split("?", 1)[1]
                for part in q.split("&"):
                    if part.startswith("status="):
                        status = part.split("=", 1)[1]
            self._send(200, {"tasks": st.list(status)})
        else:
            self._send(404, {"error": "没有这个接口"})

    def do_POST(self):
        if self._blocked_host():
            return
        st = self.server.store
        payload, code, err = self._body()
        if code:
            self._send(code, {"error": err})
            return
        if st.stopped() and self.path not in ("/cancel", "/reap"):
            self._send(503, {"error": "STOP 已置位；删掉 data/STOP 才能继续"})
            return
        a = self._auth()
        if a is None:
            return
        owner, caps = a
        owner = owner if owner is not None else str(payload.get("owner") or "")
        if not owner and st.dev:
            owner = "dev"
        try:
            if self.path == "/task":
                cap = str(payload.get("capability") or "read")
                if cap not in ("read", "write"):
                    self._send(400, {"error": "capability 只能是 read 或 write"})
                    return
                if caps is not None and cap not in caps:
                    self._send(403, {"error": "令牌不允许 capability=%s" % cap})
                    return
                budget = payload.get("budget")
                tid, err = st.create(str(payload.get("goal") or "").strip(), cap,
                                     10 if budget is None else int(budget),
                                     owner, payload.get("payload"),
                                     int(payload.get("priority") or 0),
                                     payload.get("depends") or [],
                                     str(payload.get("lock_owner") or ""),
                                     str(payload.get("lock_key") or ""))
                if err:
                    self._send(400, {"error": err})
                else:
                    self._send(200, {"id": tid, "status": "queued"})
            elif self.path == "/simulate":
                ok, err, out = st.simulate(str(payload.get("id") or ""), owner, caps)
                code = 200 if ok else (403 if "令牌没有" in str(err) else 409)
                self._send(code, {"ok": ok, "error": err, "simulation": out})
            elif self.path == "/run":
                ok, err = st.run_tool(str(payload.get("id") or ""), owner, caps)
                code = 200 if ok else (403 if "令牌没有" in str(err) else 409)
                self._send(code, {"ok": ok, "error": err})
            elif self.path == "/reap":
                n = st.reap_expired()
                self._send(200, {"reaped": n})
            elif self.path == "/approve":
                ok, err = st.approve(str(payload.get("id") or ""), owner, caps)
                code = 200 if ok else (403 if "批准者没有" in str(err) else 409)
                self._send(code, {"ok": ok, "error": err})
            elif self.path == "/whoami":
                # Q-单-2（2026-11-15 P164）：给台账这一类外部调用方验一枚令牌的真伪。
                # 只回声【已经验过】的身份与 caps：0 状态变更、0 落盘、0 起进程；
                # 来源仍由 _blocked_host 挡在本机（非回环到不了这一行）。
                self._send(200, {"ok": True, "owner": owner or "dev",
                                 "caps": list(caps or []), "dev": bool(st.dev)})
            elif self.path == "/claim":
                ok, err = st.claim(str(payload.get("id") or ""), owner,
                                   int(payload.get("lease_s") or 300), caps)
                code = 200 if ok else (403 if "令牌没有" in str(err) else 409)
                self._send(code, {"ok": ok, "error": err})
            elif self.path == "/result":
                ok, err = st.finish(str(payload.get("id") or ""), owner,
                                    bool(payload.get("ok")),
                                    payload.get("result"), payload.get("evidence"),
                                    int(payload.get("calls_used") or 0))
                self._send(200 if ok else 409, {"ok": ok, "error": err})
            elif self.path == "/stop":
                if caps is None or "write" not in caps:
                    self._send(403, {"ok": False, "error": "停止需要 write 令牌"})
                    return
                st.audit("service.stop_requested", owner=owner)
                self._send(200, {"ok": True, "stopping": True})
                threading.Timer(0.2, self.server.shutdown).start()
                return
            elif self.path == "/cancel":
                ok, err = st.cancel(str(payload.get("id") or ""),
                                    str(payload.get("reason") or ""))
                self._send(200 if ok else 409, {"ok": ok, "error": err})
            else:
                self._send(404, {"error": "没有这个接口"})
        except (KeyError, ValueError, TypeError) as e:
            self._send(400, {"error": "参数不对：%s" % type(e).__name__})


class AgentHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--data", default=str(Path(__file__).with_name("data")))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--blackboard", default=None, help="只读黑板路径（可选）")
    ap.add_argument("--dev", action="store_true", help="本机开发态：允许无令牌；仅回环")
    ap.add_argument("--dev-write", action="store_true", help="本机开发态：无令牌时给 write（仅临时库用）")
    ap.add_argument("--trace-dir", default=None, help="I1 approval.decision 事件落盘目录")
    ap.add_argument("--memory-db", default=None, help="A2 记忆写 v0 的临时库；不传则不注册 memory_fact")
    ap.add_argument("--allow-real-db", action="store_true", help="显式允许 --memory-db 指向项目 data/ 真库（需 Boss 点头）")
    args = ap.parse_args(argv)
    if args.host not in ("127.0.0.1", "::1", "localhost"):
        print("拒绝启动：只允许回环 host，当前 %s" % args.host)
        return 2
    store = Store(args.data, blackboard=args.blackboard, dev=args.dev, dev_write=args.dev_write, trace_dir=args.trace_dir, memory_db=args.memory_db, allow_real_db=args.allow_real_db)
    if not store.acquire():
        print("拒绝启动：该 data 目录已有 agentd 在跑（单实例锁）")
        return 3
    srv = AgentHTTPServer((args.host, args.port), Handler)
    srv.store = store
    print("agentd 已启动 http://%s:%d  data=%s" % (args.host, args.port, args.data))
    print("STOP 文件：%s" % store.stop_path)
    if store.token_required():
        print("令牌已启用：需要 X-Agent-Token")
    if store.blackboard:
        print("黑板（只读）：%s" % store.blackboard)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        store.release()


if __name__ == "__main__":
    main()
