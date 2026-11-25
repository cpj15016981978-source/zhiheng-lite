# -*- coding: utf-8 -*-
"""任务台账：每个任务有 owner、状态、重试次数、失败原因、人工接管。

不是聊天记录；是“这件事现在到底在哪一步”。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import pathlib
import sqlite3
import threading
import urllib.parse
import urllib.request
import uuid

from . import profile as _profile

STATES = ("queued", "planning", "running", "waiting_approval",
          "succeeded", "failed", "cancelled", "needs_human")
TERMINAL = ("succeeded", "cancelled")
# 状态机：调用方自报的目标态只能走这张表里的边；表外一律拒，台账不接“我想当它做完了”。
LEGAL_NEXT = {
    "queued": ("planning", "running", "cancelled", "needs_human"),
    "planning": ("running", "cancelled", "needs_human"),
    "running": ("waiting_approval", "succeeded", "failed", "cancelled", "needs_human"),
    "waiting_approval": ("running", "cancelled", "needs_human"),
    "failed": ("queued", "needs_human", "cancelled"),
    "needs_human": ("queued", "cancelled"),
    "succeeded": (),
    "cancelled": (),
}
# 批准位只认这两个（板上规矩：时中永远 read，不给自己批自己的写）。
APPROVERS = ("boss", "qoder")

CAPABILITY = {
    "name": "任务台账",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "库打不开=抛错，不假装记账；非法状态迁移=ValueError；退单原因写 error，不另开表",
    "approval": "ADR-046：没有开关的既成常开登记，不发明新开关",
}

# ── Q-单-2（2026-11-15 P164）：「谁批的」从调用方打字变成机器验真 ──────────────
# ledger 档＝今天的行为，一字不变（默认关那一档按 ADR-044 让路）；
# token 档＝waiting_approval→running 那一步不再读 approver，而是拿令牌问本机 agentd。
APPROVAL_MODE_ENV = "PAI_APPROVAL_MODE"
AGENTD_URL_ENV = "PAI_AGENTD_URL"
AGENTD_TIMEOUT_ENV = "PAI_AGENTD_TIMEOUT"
DEFAULT_AGENTD_WHOAMI = "http://127.0.0.1:8766/whoami"
AGENTD_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def approval_mode():
    m = str(os.environ.get(APPROVAL_MODE_ENV) or "ledger").strip().lower()
    if m not in ("ledger", "token"):
        raise ValueError("PAI_APPROVAL_MODE 只认 ledger／token，现在给的是「%s」"
                         % (os.environ.get(APPROVAL_MODE_ENV) or ""))
    return m


def _agentd_whoami_url():
    """token 档「默认档不出网」的免检前提由这一刀守住：只许问回环（同 _ollama_url 那一族）。"""
    url = (os.environ.get(AGENTD_URL_ENV) or DEFAULT_AGENTD_WHOAMI).strip()
    host = (urllib.parse.urlsplit(url).hostname or "").strip().lower()
    if host not in AGENTD_LOOPBACK_HOSTS:
        raise ValueError("批准令牌只能问本机 agentd 验真，现在指向 %s —— "
                         "要接远端请先上板引 P 号并改能力登记的「出网」那一栏" % url)
    return url


def verify_token(token):
    """验一枚令牌，返回验出来的批准者全名（小写）。令牌明文绝不落库、绝不进日志。"""
    if not str(token or "").strip():
        raise ValueError("token 档必须有 approval_token：「谁批的」不再由调用方自报")
    req = urllib.request.Request(
        _agentd_whoami_url(), data=b"{}",
        headers={"X-Agent-Token": str(token), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(
                req, timeout=float(os.environ.get(AGENTD_TIMEOUT_ENV) or 3)) as resp:
            out = json.loads(resp.read().decode("utf-8") or "{}")
    except Exception as exc:
        raise ValueError("批准令牌验不了：问不到 agentd（%s）=> 状态不落地"
                         % type(exc).__name__) from None
    owner = str(out.get("owner") or "").strip().lower()
    caps = [str(c).strip().lower() for c in (out.get("caps") or [])]
    if owner not in APPROVERS:
        raise ValueError("验出来的批准者是「%s」，名单里只有 %s"
                         % (out.get("owner") or "", "/".join(APPROVERS)))
    if "write" not in caps:
        raise ValueError("这枚令牌 caps=%s，不含 write => 批不了写" % (",".join(caps) or "空"))
    return owner


def _grant_ref(token, task_id, from_state, to_state):
    return hashlib.sha256(("%s|%s|%s|%s" % (token, task_id, from_state, to_state))
                          .encode("utf-8")).hexdigest()[:16]


def _now():
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


class TaskLedger:
    def __init__(self, db_path=None):
        self.db_path = str(db_path or os.environ.get("PAI_TASK_DB")
                           or str(_profile.data_root() / "tasks.db"))
        pathlib.Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._lock = threading.RLock()
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        create table if not exists tasks(
          id text primary key,
          profile text not null,
          title text not null,
          goal text not null,
          state text not null,
          owner text not null,
          input_json text default '',
          plan_json text default '',
          result text default '',
          error text default '',
          attempts integer not null default 0,
          max_attempts integer not null default 2,
          idempotency_key text default '',
          parent_id text default '',
          human_takeover integer not null default 0,
          approved_by text not null default '',
          created_at text not null,
          updated_at text not null
        );
        create unique index if not exists ux_task_idem
          on tasks(profile, idempotency_key) where idempotency_key <> '';
        create index if not exists ix_task_state on tasks(profile, state, updated_at);
        create table if not exists approvals(
          ref text primary key,
          task_id text not null,
          from_state text not null,
          to_state text not null,
          approver text not null,
          used_at text not null
        );
        """)
        cols = {r["name"] for r in self.conn.execute("pragma table_info(tasks)")}
        if "approved_by" not in cols:
            self.conn.execute("alter table tasks add column approved_by text not null default ''")
        self.conn.commit()

    def close(self):
        self.conn.close()

    def create(self, profile, title, goal, owner="boss", idempotency_key="",
               parent_id="", max_attempts=2):
        if idempotency_key:
            row = self.conn.execute(
                "select * from tasks where profile=? and idempotency_key=?",
                (profile, idempotency_key)).fetchone()
            if row:
                return dict(row)
        tid = uuid.uuid4().hex[:12]
        now = _now()
        try:
            self.conn.execute(
                "insert into tasks(id,profile,title,goal,state,owner,idempotency_key,"
                "parent_id,max_attempts,created_at,updated_at) "
                "values(?,?,?,?,?,?,?,?,?,?,?)",
                (tid, profile, title, goal, "queued", owner, idempotency_key,
                 parent_id, max(0, int(max_attempts)), now, now))
            self.conn.commit()
        except sqlite3.IntegrityError:
            row = self.conn.execute(
                "select * from tasks where profile=? and idempotency_key=?",
                (profile, idempotency_key)).fetchone()
            return dict(row) if row else None
        return self.get(tid)

    def get(self, task_id):
        row = self.conn.execute("select * from tasks where id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list(self, profile=None, state=None, limit=50):
        sql = "select * from tasks where 1=1"
        args = []
        if profile:
            sql += " and profile=?"
            args.append(profile)
        if state:
            sql += " and state=?"
            args.append(state)
        sql += " order by updated_at desc limit ?"
        args.append(max(1, min(500, int(limit))))
        return [dict(r) for r in self.conn.execute(sql, args)]

    def transition(self, task_id, state, *, error=None, result=None, owner=None,
                   approver=None, token=None):
        if state not in STATES:
            raise ValueError("未知任务状态：%s" % state)
        row = self.get(task_id)
        if not row:
            raise KeyError("没有任务：%s" % task_id)
        here = row["state"]
        if state not in LEGAL_NEXT.get(here, ()):
            raise ValueError("非法迁移：%s → %s" % (here, state))
        sets = ["state=?", "updated_at=?"]
        args = [state, _now()]
        grant_ref = ""
        if state == "running":
            sets.append("attempts=attempts+1")
            if here == "waiting_approval":
                if approval_mode() == "token":
                    who = verify_token(token)        # approver 在这里不作数，agentd 说了算
                    grant_ref = _grant_ref(token, task_id, here, state)
                    if self.conn.execute("select 1 from approvals where ref=?",
                                         (grant_ref,)).fetchone():
                        raise ValueError("批准重放：这一枚批准（ref=%s）已经用过，"
                                         "要再批请重新取得令牌" % grant_ref)
                else:
                    who = str(approver or "").strip().lower()
                    if who not in APPROVERS:
                        raise ValueError("批准位只认 %s，现在给的是「%s」"
                                         % ("/".join(APPROVERS), approver or ""))
                sets.append("approved_by=?")
                args.append(who)
        if error is not None:
            sets.append("error=?")
            args.append(str(error))
        if result is not None:
            sets.append("result=?")
            args.append(str(result))
        if owner is not None:
            sets.append("owner=?")
            args.append(str(owner))
        args.append(task_id)
        self.conn.execute("update tasks set %s where id=?" % ",".join(sets), args)
        if grant_ref:
            # 与状态迁移同一个事务：批准要么连同留痕一起落，要么什么都不落（不留「批过了」的空壳）。
            self.conn.execute("insert into approvals(ref,task_id,from_state,to_state,"
                              "approver,used_at) values(?,?,?,?,?,?)",
                              (grant_ref, task_id, here, state, who, _now()))
        self.conn.commit()
        return self.get(task_id)

    def start(self, task_id, approver=None, token=None):
        return self.transition(task_id, "running", approver=approver, token=token)

    def fail(self, task_id, error):
        row = self.get(task_id)
        if not row:
            raise KeyError(task_id)
        max_attempts = int(row.get("max_attempts") or 0)
        attempts = int(row.get("attempts") or 0)
        state = "needs_human" if attempts > max_attempts else "failed"
        return self.transition(task_id, state, error=error)

    def retry(self, task_id):
        row = self.get(task_id)
        if not row:
            raise KeyError(task_id)
        if int(row.get("attempts") or 0) > int(row.get("max_attempts") or 0):
            return self.transition(task_id, "needs_human",
                                   error=row.get("error") or "超过最大重试次数")
        return self.transition(task_id, "queued")

    def take_over(self, task_id, owner="boss"):
        return self.transition(task_id, "needs_human", owner=owner)

    def cancel(self, task_id, reason="", owner=None):
        """退单/取消也留痕：状态、原因、操作人一起进台账。"""
        return self.transition(task_id, "cancelled",
                               error=str(reason or "未填写取消原因"),
                               owner=owner)

    def counts(self, profile=None):
        sql = "select state,count(*) n from tasks"
        args = []
        if profile:
            sql += " where profile=?"
            args.append(profile)
        sql += " group by state"
        return {r["state"]: r["n"] for r in self.conn.execute(sql, args)}



def _locked(fn):
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper


for _name in ("close", "create", "get", "list", "transition", "start", "fail",
              "retry", "take_over", "cancel", "counts"):
    setattr(TaskLedger, _name, _locked(getattr(TaskLedger, _name)))
