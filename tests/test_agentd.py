# -*- coding: utf-8 -*-
"""agent 服务层：队列/认领/结果/预算/STOP/本机限制/审计。"""
import json
import os
import sys
import datetime
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent_layer"))

import agentd  # noqa: E402
import tools_exec as TX  # noqa: E402
import memory_tool as MT  # noqa: E402
from pai.memory import Memory  # noqa: E402


@unittest.skipIf(os.environ.get("PAI_CI") == "1", "agentd 本机外部进程，CI 分层跳过")
class AgentdTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.memdb = Path(self.dir) / "memory.db"
        self.store = agentd.Store(self.dir, dev=True, dev_write=True,
                                  memory_db=str(self.memdb))
        self.tool_root = Path(self.dir) / "toolroot"
        self.sandbox = Path(self.dir) / "sandbox"
        self.tool_root.mkdir(parents=True, exist_ok=True)
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self._orig_roots = (TX.ROOT, TX.SANDBOX)
        TX.ROOT, TX.SANDBOX = self.tool_root, self.sandbox
        self.srv = agentd.ThreadingHTTPServer(("127.0.0.1", 0), agentd.Handler)
        self.srv.store = self.store
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.store.con.close()
        TX.ROOT, TX.SANDBOX = self._orig_roots

    def req(self, method, path, body=None, host=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        hd = {"Content-Type": "application/json"}
        if host:
            hd["Host"] = host
        r = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                   data=data, headers=hd, method=method)
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def submit(self, **kw):
        body = {"goal": "test", "capability": "read", "budget": 5}
        body.update(kw)
        return self.req("POST", "/task", body)

    def test_full_flow_and_audit(self):
        code, out = self.submit()
        self.assertEqual(code, 200)
        tid = out["id"]
        code, out = self.req("POST", "/claim", {"id": tid, "owner": "deepseek"})
        self.assertEqual(code, 200)
        code, out = self.req("POST", "/result", {"id": tid, "owner": "deepseek",
                                                 "ok": True, "result": "done",
                                                 "calls_used": 1})
        self.assertEqual(code, 200)
        code, out = self.req("GET", "/tasks")
        self.assertEqual(code, 200)
        self.assertEqual(out["tasks"][0]["status"], "done")
        audit = Path(self.dir, "audit.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertGreaterEqual(len(audit), 3)

    def test_claim_conflict(self):
        tid = self.submit()[1]["id"]
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "a"})[0], 200)
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "b"})[0], 409)

    def test_budget_is_enforced(self):
        tid = self.submit(budget=1)[1]["id"]
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        code, out = self.req("POST", "/result", {"id": tid, "owner": "a",
                                                 "ok": True, "calls_used": 2})
        self.assertEqual(code, 409)
        self.assertIn("预算", out["error"])

    def test_stop_blocks_new_tasks(self):
        Path(self.dir, "STOP").write_text("stop", encoding="utf-8")
        self.assertEqual(self.submit()[0], 503)
        code, out = self.req("GET", "/health")
        self.assertEqual(code, 200)
        self.assertTrue(out["stopped"])

    def test_foreign_host_rejected(self):
        code, _ = self.req("GET", "/health", host="evil.example")
        self.assertEqual(code, 403)

    def test_bad_capability(self):
        code, out = self.submit(capability="root")
        self.assertEqual(code, 400)

    def test_health(self):
        code, out = self.req("GET", "/health")
        self.assertEqual(code, 200)
        self.assertTrue(out["ok"])


    def test_write_needs_approval(self):
        tid = self.submit(capability="write")[1]["id"]
        code, out = self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.assertEqual(code, 409)
        self.assertIn("批准", out["error"])
        self.assertEqual(self.req("POST", "/approve", {"id": tid})[0], 200)
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "a"})[0], 200)

    def test_failure_retries_then_fails(self):
        tid = self.submit()[1]["id"]
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        code, _ = self.req("POST", "/result", {"id": tid, "owner": "a",
                                               "ok": False, "result": "boom"})
        self.assertEqual(code, 200)
        row = self.store.get(tid)
        self.assertEqual((row["status"], row["attempts"]), ("queued", 1))
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.req("POST", "/result", {"id": tid, "owner": "a", "ok": False, "result": "boom2"})
        row = self.store.get(tid)
        self.assertEqual((row["status"], row["attempts"]), ("failed", 2))


    def test_expired_lease_is_reaped(self):
        tid = self.submit()[1]["id"]
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.store.con.execute("UPDATE tasks SET lease_until='2000-01-01T00:00:00' WHERE id=?", (tid,))
        self.store.con.commit()
        code, out = self.req("POST", "/reap", {})
        self.assertEqual((code, out["reaped"]), (200, 1))
        self.assertEqual(self.store.get(tid)["status"], "queued")
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "b"})[0], 200)

    def test_live_lease_blocks_other_owner(self):
        tid = self.submit()[1]["id"]
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.store.con.execute("UPDATE tasks SET lease_until='2999-01-01T00:00:00' WHERE id=?", (tid,))
        self.store.con.commit()
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "b"})[0], 409)


    def test_run_read_tool(self):
        p = (self.tool_root / "tool_read.txt")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("hello", encoding="utf-8")
        tid = self.submit(capability="read",
                          payload={"tool": "read_text", "args": {"path": str(p)}})[1]["id"]
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "a"})[0], 200)
        code, out = self.req("POST", "/run", {"id": tid, "owner": "a"})
        self.assertEqual(code, 200)
        self.assertIn("hello", self.store.get(tid)["result"])

    def test_write_tool_needs_approval_and_stays_in_sandbox(self):
        p = (self.sandbox / "run_test.txt")
        tid = self.submit(capability="write",
                          payload={"tool": "write_text",
                                   "args": {"path": str(p), "text": "ok"}})[1]["id"]
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "a"})[0], 409)
        self.assertEqual(self.req("POST", "/approve", {"id": tid})[0], 200)
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "a"})[0], 200)
        self.req("POST", "/simulate", {"id": tid})
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 200)
        self.assertEqual(p.read_text(encoding="utf-8"), "ok")

    def test_run_rejects_path_escape(self):
        tid = self.submit(capability="read",
                          payload={"tool": "read_text", "args": {"path": "C:/Windows/win.ini"}})[1]["id"]
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        code, out = self.req("POST", "/run", {"id": tid, "owner": "a"})
        self.assertEqual(code, 409)
        self.assertIn("越界", out["error"])
        self.assertEqual(self.store.get(tid)["status"], "queued")


    def test_dependency_blocks_until_done(self):
        a = self.submit(goal="a")[1]["id"]
        b = self.submit(goal="b", depends=[a])[1]["id"]
        code, out = self.req("POST", "/claim", {"id": b, "owner": "x"})
        self.assertEqual(code, 409)
        self.assertIn("依赖未完成", out["error"])
        self.req("POST", "/claim", {"id": a, "owner": "x"})
        self.req("POST", "/result", {"id": a, "owner": "x", "ok": True})
        self.assertEqual(self.req("POST", "/claim", {"id": b, "owner": "x"})[0], 200)

    def test_priority_orders_list(self):
        low = self.submit(goal="low", priority=1)[1]["id"]
        high = self.submit(goal="high", priority=9)[1]["id"]
        code, out = self.req("GET", "/tasks")
        self.assertEqual(code, 200)
        ids = [t["id"] for t in out["tasks"]]
        self.assertLess(ids.index(high), ids.index(low))

    def test_missing_dependency_rejected(self):
        code, out = self.submit(depends=["nope"])
        self.assertEqual(code, 400)
        self.assertIn("依赖不存在", out["error"])


    def test_stop_still_allows_cancel_and_reap(self):
        tid = self.submit()[1]["id"]
        Path(self.dir, "STOP").write_text("stop", encoding="utf-8")
        self.assertEqual(self.req("POST", "/cancel", {"id": tid, "reason": "stop"})[0], 200)
        self.assertEqual(self.req("POST", "/reap", {})[0], 200)

    def test_read_truncation_flag(self):
        p = (self.tool_root / "big_test.txt")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x" * 120000, encoding="utf-8")
        out = TX.execute("read_text", {"path": str(p)})
        self.assertTrue(out["truncated"])
        self.assertEqual(out["bytes"], 100000)

    def test_tool_capability_is_rejected(self):
        code, out = self.submit(capability="tool")
        self.assertEqual(code, 400)
        self.assertIn("capability", out["error"])


    def test_run_success_records_attempt(self):
        p = (self.tool_root / "tool_read.txt")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("hello", encoding="utf-8")
        tid = self.submit(capability="read",
                          payload={"tool": "read_text", "args": {"path": str(p)}})[1]["id"]
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 200)
        row = self.store.get(tid)
        self.assertEqual((row["status"], row["attempts"], row["calls_used"]), ("done", 1, 1))

    def test_reap_executing(self):
        tid = self.submit()[1]["id"]
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.store.con.execute(
            "UPDATE tasks SET status='executing', lease_until='2000-01-01T00:00:00' WHERE id=?",
            (tid,))
        self.store.con.commit()
        self.assertEqual(self.req("POST", "/reap", {})[1]["reaped"], 1)
        self.assertEqual(self.store.get(tid)["status"], "queued")

    def test_deny_journal_and_case_variants(self):
        for name in ("memory.db-journal", "Tokens.json", "keys.Json"):
            with self.assertRaises(PermissionError):
                TX._resolve("D:/DSH/" + name, False)


    def test_cancel_during_run_does_not_become_done(self):
        import time
        tid = self.submit(capability="read",
                          payload={"tool": "read_text", "args": {"path": "x"}})[1]["id"]
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        orig = TX.execute

        def slow(tool, args):
            time.sleep(0.4)
            return {"ok": True}

        TX.execute = slow
        out = {}
        try:
            t = threading.Thread(target=lambda: out.update(
                {"r": self.req("POST", "/run", {"id": tid, "owner": "a"})}))
            t.start()
            time.sleep(0.1)
            self.assertEqual(self.req("POST", "/cancel", {"id": tid, "reason": "stop"})[0], 200)
            t.join(timeout=5)
        finally:
            TX.execute = orig
        self.assertEqual(self.store.get(tid)["status"], "cancelled")


    def test_concurrent_run_only_once(self):
        import time
        p = self.tool_root / "slow.txt"
        p.write_text("x", encoding="utf-8")
        tid = self.submit(capability="read",
                          payload={"tool": "read_text", "args": {"path": str(p)}})[1]["id"]
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        orig = TX.execute
        def slow(tool, args):
            time.sleep(0.5)
            return {"ok": True}

        TX.execute = slow
        out = []
        try:
            def go():
                out.append(self.req("POST", "/run", {"id": tid, "owner": "a"})[0])
            ts = [threading.Thread(target=go) for _ in range(2)]
            [t.start() for t in ts]
            [t.join(5) for t in ts]
        finally:
            TX.execute = orig
        self.assertEqual(sorted(out), [200, 409])
        row = self.store.get(tid)
        self.assertEqual((row["status"], row["calls_used"], row["attempts"]), ("done", 1, 1))

    def test_approval_is_consumed_by_run(self):
        p = self.sandbox / "approved.txt"
        tid = self.submit(capability="write",
                          payload={"tool": "write_text",
                                   "args": {"path": str(p), "text": "x"}})[1]["id"]
        self.req("POST", "/approve", {"id": tid})
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.req("POST", "/simulate", {"id": tid})
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 200)
        self.assertEqual(int(self.store.get(tid)["approved"]), 0)

    def test_budget_counts_failed_tool(self):
        tid = self.submit(capability="read", budget=1,
                          payload={"tool": "read_text",
                                   "args": {"path": str(self.tool_root / "missing.txt")}})[1]["id"]
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 409)
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 409)
        row = self.store.get(tid)
        self.assertEqual((row["calls_used"], row["attempts"]), (1, 1))


    def test_single_instance_lock(self):
        d = tempfile.mkdtemp()
        st = agentd.Store(d)
        try:
            self.assertTrue(st.acquire())
            self.assertFalse(st.acquire())
            st.release()
            self.assertTrue(st.acquire())
            st.release()
            Path(d, "agentd.lock").write_text("999999", encoding="utf-8")
            self.assertTrue(st.acquire())
            st.release()
        finally:
            st.con.close()

    def test_server_does_not_reuse_address(self):
        self.assertFalse(agentd.AgentHTTPServer.allow_reuse_address)


    def test_second_server_on_same_port_is_refused(self):
        s1 = agentd.AgentHTTPServer(("127.0.0.1", 0), agentd.Handler)
        try:
            port = s1.server_address[1]
            with self.assertRaises(OSError):
                agentd.AgentHTTPServer(("127.0.0.1", port), agentd.Handler)
        finally:
            s1.server_close()


    def test_reaped_write_requires_new_approval(self):
        tid = self.submit(capability="write",
                          payload={"tool": "write_text",
                                   "args": {"path": "reap.txt", "text": "x"}})[1]["id"]
        self.assertEqual(self.req("POST", "/approve", {"id": tid})[0], 200)
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "a"})[0], 200)
        self.store.con.execute(
            "UPDATE tasks SET lease_until='2000-01-01T00:00:00' WHERE id=?", (tid,))
        self.store.con.commit()
        self.assertEqual(self.req("POST", "/reap", {})[1]["reaped"], 1)
        self.assertEqual(int(self.store.get(tid)["approved"]), 0)
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "a"})[0], 409)
        self.assertEqual(self.req("POST", "/approve", {"id": tid})[0], 200)
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "a"})[0], 200)


    def test_retry_clears_approval(self):
        tid = self.submit(capability="write",
                          payload={"tool": "write_text",
                                   "args": {"path": "retry.txt", "text": "x"}})[1]["id"]
        self.assertEqual(self.req("POST", "/approve", {"id": tid})[0], 200)
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "a"})[0], 200)
        self.assertEqual(self.req("POST", "/result", {"id": tid, "owner": "a",
                                                      "ok": False, "result": "fail"})[0], 200)
        row = self.store.get(tid)
        self.assertEqual((row["status"], int(row["approved"])), ("queued", 0))
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "a"})[0], 409)


    def test_simulate_no_side_effect(self):
        p = self.sandbox / "sim.txt"
        tid = self.submit(capability="write",
                          payload={"tool": "write_text",
                                   "args": {"path": "sim.txt", "text": "hello"}})[1]["id"]
        code, out = self.req("POST", "/simulate", {"id": tid})
        self.assertEqual(code, 200)
        sim = out["simulation"]
        self.assertEqual(sim["decision"], "pending")      # 未批准
        self.assertEqual(sim["bytes_after"], 5)
        self.assertFalse(p.exists())

    def test_simulate_cache(self):
        tid = self.submit(capability="write",
                          payload={"tool": "write_text",
                                   "args": {"path": "cache.txt", "text": "x"}})[1]["id"]
        self.req("POST", "/approve", {"id": tid})
        self.assertEqual(self.req("POST", "/simulate", {"id": tid})[1]["simulation"]["cached"], False)
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 200)
        sim = self.req("POST", "/simulate", {"id": tid})[1]["simulation"]
        self.assertEqual(sim["cached"], True)
        self.assertEqual(sim["verified"], True)
        n = self.store.con.execute("SELECT COUNT(*) FROM simulations").fetchone()[0]
        self.assertEqual(n, 1)

    def test_simulate_before_state_mismatch_invalidates(self):
        tid = self.submit(capability="write",
                          payload={"tool": "write_text",
                                   "args": {"path": "before.txt", "text": "x"}})[1]["id"]
        self.req("POST", "/approve", {"id": tid})
        self.req("POST", "/simulate", {"id": tid})
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.req("POST", "/run", {"id": tid, "owner": "a"})
        (self.sandbox / "before.txt").write_text("changed-by-other", encoding="utf-8")
        sim = self.req("POST", "/simulate", {"id": tid})[1]["simulation"]
        self.assertFalse(sim["cached"])
        self.assertEqual(sim["bytes_before"], len("changed-by-other"))
        row = self.store.con.execute("SELECT last_invalidated_reason AS invalidated_reason FROM simulations").fetchone()
        self.assertEqual(row["invalidated_reason"], "before_mismatch")

    def test_re_simulate_recovers_from_before_mismatch(self):
        tid = self.submit(capability="write",
                          payload={"tool": "write_text",
                                   "args": {"path": "recover.txt", "text": "x"}})[1]["id"]
        self.req("POST", "/approve", {"id": tid})
        self.req("POST", "/simulate", {"id": tid})
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        (self.sandbox / "recover.txt").write_text("other", encoding="utf-8")
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 409)
        sim = self.req("POST", "/simulate", {"id": tid})[1]["simulation"]
        self.assertFalse(sim["cached"])
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 200)

    def test_re_simulate_memory_recovers_from_before_mismatch(self):
        tid = self.submit(capability="write",
                          payload={"tool": "memory_fact",
                                   "args": {"subject": "用户", "predicate": "测试", "object": "x"}})[1]["id"]
        self.req("POST", "/approve", {"id": tid})
        self.req("POST", "/simulate", {"id": tid})
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        m = Memory(str(self.memdb))
        m.upsert_fact("用户", "测试", "changed")
        m.close()
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 409)
        sim = self.req("POST", "/simulate", {"id": tid})[1]["simulation"]
        self.assertFalse(sim["cached"])
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 200)

    def test_simulate_actual_mismatch_invalidates(self):
        payload = {"tool": "write_text", "args": {"path": "mm.txt", "text": "one"}}
        tid = self.submit(capability="write", payload=payload)[1]["id"]
        self.req("POST", "/approve", {"id": tid})
        self.req("POST", "/simulate", {"id": tid})
        (self.sandbox / "mm.txt").write_text("different", encoding="utf-8")
        self.store._record_actual(tid, payload, {})
        row = self.store.con.execute("SELECT verified,invalidated_reason FROM simulations").fetchone()
        self.assertEqual((row["verified"], row["invalidated_reason"]), (0, "actual_mismatch"))
        self.assertEqual(self.req("POST", "/simulate", {"id": tid})[1]["simulation"]["cached"], False)

    def test_simulate_path_escape_denied(self):
        tid = self.submit(capability="write",
                          payload={"tool": "write_text",
                                   "args": {"path": "../escape.txt", "text": "x"}})[1]["id"]
        self.assertEqual(self.req("POST", "/simulate", {"id": tid})[0], 409)


    def test_simulate_env_change_is_not_cache_hit(self):
        tid = self.submit(capability="write",
                          payload={"tool": "write_text",
                                   "args": {"path": "env.txt", "text": "x"}})[1]["id"]
        self.req("POST", "/approve", {"id": tid})
        self.assertFalse(self.req("POST", "/simulate", {"id": tid})[1]["simulation"]["cached"])
        old = TX.SANDBOX
        try:
            TX.SANDBOX = self.sandbox / "other"
            TX.SANDBOX.mkdir(parents=True, exist_ok=True)
            self.assertFalse(self.req("POST", "/simulate", {"id": tid})[1]["simulation"]["cached"])
        finally:
            TX.SANDBOX = old


    def test_dev_default_is_read_only(self):
        st = agentd.Store(tempfile.mkdtemp(), dev=True)
        try:
            self.assertEqual(st.dev_caps(), ["read"])
            self.assertFalse(agentd.authorize("write", st.dev_caps()).is_allowed())
        finally:
            st.con.close()

    def test_unregistered_tool_is_refused(self):
        tid = self.submit(capability="read",
                          payload={"tool": "list_dir", "args": {"path": "."}})[1]["id"]
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "a"})[0], 200)
        self.store.tools.dispose("list_dir")
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 409)
        self.store.tools.register("list_dir", TX.execute)
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 200)

    def test_tool_capability_mismatch_is_409(self):
        tid = self.submit(capability="read",
                          payload={"tool": "write_text",
                                   "args": {"path": "mis.txt", "text": "x"}})[1]["id"]
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "a"})[0], 200)
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 409)


    def test_write_run_requires_simulate(self):
        p = self.sandbox / "gate.txt"
        tid = self.submit(capability="write",
                          payload={"tool": "write_text",
                                   "args": {"path": "gate.txt", "text": "x"}})[1]["id"]
        self.req("POST", "/approve", {"id": tid})
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 409)
        self.assertFalse(p.exists())
        self.req("POST", "/simulate", {"id": tid})
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 200)
        self.assertTrue(p.exists())


    def test_manual_approved_bit_is_rejected(self):
        tid = self.submit(capability="write")[1]["id"]
        self.store.con.execute("UPDATE tasks SET approved=1 WHERE id=?", (tid,))
        self.store.con.commit()
        code, out = self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.assertEqual(code, 409)
        self.assertIn("批准", out["error"])
        self.assertEqual(self.req("POST", "/approve", {"id": tid})[0], 200)
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "a"})[0], 200)

    def test_approval_signature_tamper_is_rejected(self):
        tid = self.submit(capability="write")[1]["id"]
        self.assertEqual(self.req("POST", "/approve", {"id": tid})[0], 200)
        self.store.con.execute("UPDATE tasks SET approved_by='mallory' WHERE id=?", (tid,))
        self.store.con.commit()
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "a"})[0], 409)

    def test_pending_preview_is_refused(self):
        p = self.sandbox / "pending.txt"
        tid = self.submit(capability="write",
                          payload={"tool": "write_text",
                                   "args": {"path": "pending.txt", "text": "x"}})[1]["id"]
        self.req("POST", "/approve", {"id": tid})
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.req("POST", "/simulate", {"id": tid})
        row = self.store.con.execute("SELECT key,result FROM simulations").fetchone()
        sim = json.loads(row["result"])
        sim["decision"] = "pending"
        self.store.con.execute("UPDATE simulations SET result=? WHERE key=?",
                               (json.dumps(sim, ensure_ascii=False), row["key"]))
        self.store.con.commit()
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 409)
        self.assertFalse(p.exists())

    def test_run_rechecks_before_state(self):
        p = self.sandbox / "drift.txt"
        tid = self.submit(capability="write",
                          payload={"tool": "write_text",
                                   "args": {"path": "drift.txt", "text": "x"}})[1]["id"]
        self.req("POST", "/approve", {"id": tid})
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.req("POST", "/simulate", {"id": tid})
        p.write_text("external-write", encoding="utf-8")
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 409)
        self.assertEqual(p.read_text(encoding="utf-8"), "external-write")
        row = self.store.con.execute("SELECT last_invalidated_reason AS invalidated_reason FROM simulations").fetchone()
        self.assertEqual(row["invalidated_reason"], "run_before_mismatch")

    def test_forged_preview_is_refused(self):
        p = self.sandbox / "forged.txt"
        tid = self.submit(capability="write",
                          payload={"tool": "write_text",
                                   "args": {"path": "forged.txt", "text": "x"}})[1]["id"]
        self.req("POST", "/approve", {"id": tid})
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.req("POST", "/simulate", {"id": tid})
        row = self.store.con.execute("SELECT key,result FROM simulations").fetchone()
        sim = json.loads(row["result"])
        sim["bytes_after"] = 999
        sim["text_sha12"] = "deadbeefdead"
        self.store.con.execute("UPDATE simulations SET result=? WHERE key=?",
                               (json.dumps(sim, ensure_ascii=False), row["key"]))
        self.store.con.commit()
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 409)
        self.assertFalse(p.exists())
        row = self.store.con.execute("SELECT last_invalidated_reason AS invalidated_reason FROM simulations").fetchone()
        self.assertEqual(row["invalidated_reason"], "preview_tampered")


    def test_denied_approval_writes_trace(self):
        d = Path(tempfile.mkdtemp())
        st = agentd.Store(str(d), dev=True, dev_write=True, trace_dir=d / "trace")
        try:
            tid, err = st.create("w", "write", 5, "", {"tool": "write_text",
                                                       "args": {"path": "t.txt", "text": "x"}})
            self.assertIsNone(err)
            ok, msg = st.approve(tid, "qoder", [])
            self.assertFalse(ok)
            self.assertIn("能力", msg or "")
            p = Path(d) / "trace" / ("trace-%s.jsonl" % datetime.date.today().isoformat())
            rec = json.loads(p.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(rec["kind"], "approval.decision")
            self.assertEqual(rec["decision"], "deny")
            self.assertEqual(rec["task_id"], tid)
            self.assertIn("能力", rec["reason"])
        finally:
            st.con.close()

    def test_approval_writes_trace(self):
        d = Path(tempfile.mkdtemp())
        st = agentd.Store(str(d), dev=True, dev_write=True, trace_dir=d / "trace")
        try:
            tid, err = st.create("w", "write", 5, "", {"tool": "write_text",
                                                       "args": {"path": "t.txt", "text": "x"}})
            self.assertIsNone(err)
            self.assertEqual(st.approve(tid, "qoder", ["write"]), (True, None))
            p = Path(d) / "trace" / ("trace-%s.jsonl" % datetime.date.today().isoformat())
            rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(rec["kind"], "approval.decision")
            self.assertEqual(rec["task_id"], tid)
            self.assertEqual(rec["by"], "qoder")
        finally:
            st.con.close()



    def test_resimulate_after_external_change_recovers(self):
        p = self.sandbox / "recover.txt"
        p.write_text("AAAA", encoding="utf-8")
        tid = self.submit(capability="write",
                          payload={"tool": "write_text",
                                   "args": {"path": "recover.txt", "text": "x"}})[1]["id"]
        self.req("POST", "/approve", {"id": tid})
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.req("POST", "/simulate", {"id": tid})
        p.write_text("CCCC", encoding="utf-8")
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 409)
        sim = self.req("POST", "/simulate", {"id": tid})[1]["simulation"]
        self.assertFalse(sim["cached"])
        self.assertEqual(sim["bytes_before"], 4)
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 200)
        self.assertEqual(p.read_text(encoding="utf-8"), "x")

    def test_audit_unwritable_does_not_break_task(self):
        d = Path(tempfile.mkdtemp())
        self.store.audit_path = d / "audit-dir"
        self.store.audit_path.mkdir()
        self.store.audit("forced", x=1)
        self.assertEqual(self.submit()[0], 200)
        code, health = self.req("GET", "/health")
        self.assertEqual(code, 200)
        self.assertGreaterEqual(health["audit_failures"], 1)
        self.assertEqual(health["degraded"]["kind"], "audit")

    def test_audit_failures_are_counted(self):
        d = Path(tempfile.mkdtemp())
        self.store.audit_path = d / "audit-dir"
        self.store.audit_path.mkdir()
        for _ in range(3):
            self.store.audit("forced")
        code, health = self.req("GET", "/health")
        self.assertEqual(code, 200)
        self.assertEqual(health["audit_failures"], 3)
        self.assertEqual(health["degradation_count"], 3)

    def test_audit_and_trace_unwritable_degrade(self):
        d = Path(tempfile.mkdtemp())
        self.store.audit_path = d / "audit-dir"
        self.store.audit_path.mkdir()
        blocker = d / "trace-file"
        blocker.write_text("x", encoding="utf-8")
        self.store.trace_dir = blocker
        self.store.audit("forced", x=1)
        self.store.trace_approval("t1", "qoder")
        self.assertEqual(self.submit()[0], 200)
        code, health = self.req("GET", "/health")
        self.assertEqual(code, 200)
        self.assertGreaterEqual(health["audit_failures"], 1)
        self.assertGreaterEqual(health["trace_failures"], 1)
        self.assertIsNotNone(health["degraded"])
        self.assertIn(health["degraded"]["kind"], ("audit", "trace"))

    def test_same_length_rewrite_is_detected(self):
        p = self.sandbox / "same.txt"
        p.write_text("AAAA", encoding="utf-8")
        tid = self.submit(capability="write",
                          payload={"tool": "write_text",
                                   "args": {"path": "same.txt", "text": "x"}})[1]["id"]
        self.req("POST", "/approve", {"id": tid})
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.req("POST", "/simulate", {"id": tid})
        p.write_text("CCCC", encoding="utf-8")
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 409)
        self.assertEqual(p.read_text(encoding="utf-8"), "CCCC")

    def test_empty_text_is_allowed(self):
        p = self.sandbox / "empty.txt"
        tid = self.submit(capability="write",
                          payload={"tool": "write_text",
                                   "args": {"path": "empty.txt", "text": ""}})[1]["id"]
        self.req("POST", "/approve", {"id": tid})
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        sim = self.req("POST", "/simulate", {"id": tid})[1]["simulation"]
        self.assertEqual(sim["bytes_after"], 0)
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 200)
        self.assertEqual(p.read_bytes(), b"")

    def test_multiline_write_self_verifies(self):
        p = self.sandbox / "multi.txt"
        tid = self.submit(capability="write",
                          payload={"tool": "write_text",
                                   "args": {"path": "multi.txt", "text": "a\nb"}})[1]["id"]
        self.req("POST", "/approve", {"id": tid})
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.req("POST", "/simulate", {"id": tid})
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 200)
        self.assertEqual(p.read_bytes(), b"a\nb")
        row = self.store.con.execute("SELECT verified FROM simulations").fetchone()
        self.assertEqual(int(row["verified"]), 1)

    def test_trace_failure_does_not_block_approval(self):
        d = Path(tempfile.mkdtemp())
        blocker = d / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        st = agentd.Store(str(d), dev=True, dev_write=True, trace_dir=blocker)
        try:
            tid, _ = st.create("w", "write", 5, "", {"tool": "write_text",
                                                      "args": {"path": "t.txt", "text": "x"}})
            self.assertEqual(st.approve(tid, "qoder", ["write"]), (True, None))
        finally:
            st.con.close()


    def _memory_task(self, obj="x"):
        return self.submit(capability="write",
                           payload={"tool": "memory_fact",
                                    "args": {"subject": "用户", "predicate": "备注",
                                             "object": obj, "confidence": 0.8,
                                             "source_episode_id": 7}})[1]["id"]

    def test_memory_fact_dry_run_does_not_write(self):
        tid = self._memory_task()
        self.req("POST", "/approve", {"id": tid})
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        sim = self.req("POST", "/simulate", {"id": tid})[1]["simulation"]
        self.assertEqual(sim["effects"], ["insert fact"])
        m = Memory(str(self.memdb))
        self.assertEqual(m.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0], 0)
        m.conn.close()

    def test_memory_fact_requires_approval(self):
        tid = self._memory_task()
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "a"})[0], 409)
        self.req("POST", "/simulate", {"id": tid})
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 409)

    def test_memory_fact_writes_through_m2(self):
        tid = self._memory_task("阿里")
        self.req("POST", "/approve", {"id": tid})
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.req("POST", "/simulate", {"id": tid})
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 200)
        m = Memory(str(self.memdb))
        row = m.conn.execute(
            "SELECT object, confidence FROM facts WHERE subject=? AND predicate=?",
            ("用户", "备注")).fetchone()
        self.assertEqual(row["object"], "阿里")
        self.assertEqual(row["confidence"], 0.8)
        m.conn.close()

    def test_memory_fact_not_registered_without_memory_db(self):
        st = agentd.Store(tempfile.mkdtemp(), dev=True, dev_write=True)
        try:
            self.assertNotIn("memory_fact", st.tool_specs)
        finally:
            st.con.close()



    def test_memory_fact_budget_zero_is_refused(self):
        tid = self.submit(capability="write", budget=0,
                          payload={"tool": "memory_fact",
                                   "args": {"subject": "用户", "predicate": "备注",
                                            "object": "x", "source_episode_id": 7}})[1]["id"]
        self.req("POST", "/approve", {"id": tid})
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.req("POST", "/simulate", {"id": tid})
        code, out = self.req("POST", "/run", {"id": tid, "owner": "a"})
        self.assertEqual(code, 409)
        self.assertIn("超出预算 0", out["error"])


    def test_memory_fact_run_rechecks_before_sha(self):
        tid = self._memory_task("新值")
        self.req("POST", "/approve", {"id": tid})
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.req("POST", "/simulate", {"id": tid})
        m = Memory(str(self.memdb))
        m.upsert_fact("用户", "备注", "外部改", confidence=0.8, source_episode_id=7)
        m.conn.close()
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 409)
        sim = self.req("POST", "/simulate", {"id": tid})[1]["simulation"]
        self.assertTrue(sim["before_sha12"])
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 200)

    def test_memory_fact_after_check_sets_verified(self):
        tid = self._memory_task("核对面")
        self.req("POST", "/approve", {"id": tid})
        self.req("POST", "/claim", {"id": tid, "owner": "a"})
        self.req("POST", "/simulate", {"id": tid})
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "a"})[0], 200)
        row = self.store.con.execute("SELECT verified, actual FROM simulations ORDER BY created DESC LIMIT 1").fetchone()
        self.assertEqual(int(row["verified"]), 1)
        self.assertTrue(row["actual"])

    def test_memory_fact_sim_key_includes_fact_fields(self):
        a = {"tool": "memory_fact", "args": {"subject": "用户", "predicate": "备注", "object": "A"}}
        b = {"tool": "memory_fact", "args": {"subject": "用户", "predicate": "备注", "object": "B"}}
        self.assertNotEqual(self.store._sim_key(a), self.store._sim_key(b))

    def test_memory_db_rejects_project_data(self):
        root = Path(agentd.__file__).resolve().parents[1]
        with self.assertRaises(ValueError):
            agentd.Store(tempfile.mkdtemp(), dev=True, dev_write=True,
                         memory_db=str(root / "data" / "memory.db"))

    def test_memory_simulate_without_db_is_refused(self):
        st = agentd.Store(tempfile.mkdtemp(), dev=True, dev_write=True)
        try:
            tid, _ = st.create("m", "write", 3, "",
                               {"tool": "memory_fact", "args": {"subject": "用户",
                                "predicate": "备注", "object": "x", "source_episode_id": 1}})
            st.approve(tid, "a", ["write"])
            st.claim(tid, "a")
            ok, err, out = st.simulate(tid, "a", ["write"])
            self.assertFalse(ok)
            self.assertIn("未启用", err)
            self.assertFalse((Path(st.dir) / "None").exists())
        finally:
            st.con.close()


    def test_old_simulations_schema_is_migrated(self):
        d = tempfile.mkdtemp()
        st = agentd.Store(d, dev=True, dev_write=True)
        st.con.close()
        import sqlite3
        c = sqlite3.connect(str(Path(d) / "agentd.db"))
        c.execute("ALTER TABLE simulations DROP COLUMN last_invalidated_reason")
        c.commit()
        c.close()
        st2 = agentd.Store(d, dev=True, dev_write=True)
        try:
            cols = {r[1] for r in st2.con.execute("PRAGMA table_info(simulations)")}
            self.assertIn("last_invalidated_reason", cols)
        finally:
            st2.con.close()


    def test_memory_db_reject_does_not_create_data_dir(self):
        d = Path(tempfile.mkdtemp()) / "brand-new"
        root = Path(agentd.__file__).resolve().parents[1]
        with self.assertRaises(ValueError):
            agentd.Store(str(d), dev=True, dev_write=True,
                         memory_db=str(root / "data" / "memory.db"))
        self.assertFalse(d.exists())


    def test_memory_validate_real_requires_existing_file(self):
        root = Path(agentd.__file__).resolve().parents[1]
        missing = root / "data" / "definitely_missing_a2.db"
        with self.assertRaises(ValueError):
            MT.validate_db(missing, allow_real=True)

    def test_memory_validate_allows_real_with_explicit_flag(self):
        d = Path(tempfile.mkdtemp())
        real = d / "memory.db"
        real.write_text("", encoding="utf-8")
        old = MT._PROJECT_DATA
        MT._PROJECT_DATA = d
        try:
            with self.assertRaises(ValueError):
                MT.validate_db(real)
            self.assertEqual(MT.validate_db(real, allow_real=True), real.resolve())
        finally:
            MT._PROJECT_DATA = old



    def test_stop_endpoint_stops_server(self):
        code, out = self.req("POST", "/stop", {})
        self.assertEqual(code, 200)
        self.assertTrue(out["stopping"])
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

@unittest.skipIf(os.environ.get("PAI_CI") == "1", "agentd 本机外部进程，CI 分层跳过")
class TokenTest(unittest.TestCase):
    def setUp(self):
        import hashlib
        self.dir = tempfile.mkdtemp()
        Path(self.dir, "tokens.json").write_text(json.dumps({
            "deepseek": {"hash": hashlib.sha256(b"tok-d").hexdigest(), "caps": ["read"]},
            "qoder": {"hash": hashlib.sha256(b"tok-q").hexdigest(), "caps": ["read", "write"]},
        }), encoding="utf-8")
        self.board = Path(self.dir, "board.txt")
        self.board.write_text("[锁:Qoder] G1 游戏线\n", encoding="utf-8")
        self.store = agentd.Store(self.dir, blackboard=self.board)
        self.tool_root = Path(self.dir) / "toolroot"
        self.tool_root.mkdir(parents=True, exist_ok=True)
        self._orig_root = TX.ROOT
        TX.ROOT = self.tool_root
        self.srv = agentd.ThreadingHTTPServer(("127.0.0.1", 0), agentd.Handler)
        self.srv.store = self.store
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); self.store.con.close()
        TX.ROOT = self._orig_root

    def req(self, method, path, body=None, token=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        hd = {"Content-Type": "application/json"}
        if token:
            hd["X-Agent-Token"] = token
        r = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                   data=data, headers=hd, method=method)
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_missing_token(self):
        self.assertEqual(self.req("GET", "/tasks")[0], 401)

    def test_capability_whitelist(self):
        self.assertEqual(self.req("POST", "/task", {"goal": "x", "capability": "write"},
                                  token="tok-d")[0], 403)
        self.assertEqual(self.req("POST", "/task", {"goal": "x", "capability": "read"},
                                  token="tok-d")[0], 200)

    def test_owner_comes_from_token(self):
        code, out = self.req("POST", "/task", {"goal": "x", "capability": "read", "owner": "fake"},
                             token="tok-d")
        self.assertEqual(code, 200)
        code, tasks = self.req("GET", "/tasks", token="tok-d")
        self.assertEqual(tasks["tasks"][0]["owner"], "deepseek")

    def test_locks_readonly(self):
        code, out = self.req("GET", "/locks", token="tok-d")
        self.assertEqual(code, 200)
        self.assertTrue(any("[锁:Qoder]" in x for x in out["locks"]))

    def test_task_requires_matching_blackboard_lock(self):
        code, out = self.req("POST", "/task", {"goal": "locked", "capability": "read",
                                               "lock_owner": "Qoder", "lock_key": "G1 游戏线"}, token="tok-q")
        self.assertEqual(code, 200)
        tid = out["id"]
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "deepseek"},
                                  token="tok-d")[0], 409)
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "qoder"},
                                  token="tok-q")[0], 200)

    def test_task_with_missing_lock_owner_refused(self):
        code, out = self.req("POST", "/task", {"goal": "locked", "capability": "read",
                                               "lock_owner": "Qoder", "lock_key": "Nope"},
                             token="tok-q")
        self.assertEqual(code, 200)
        tid = out["id"]
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "qoder"},
                                  token="tok-q")[0], 409)


    def test_approve_requires_capability(self):
        code, out = self.req("POST", "/task", {"goal": "w", "capability": "write"}, token="tok-q")
        self.assertEqual(code, 200)
        tid = out["id"]
        self.assertEqual(self.req("POST", "/approve", {"id": tid}, token="tok-d")[0], 403)
        self.assertEqual(self.req("POST", "/approve", {"id": tid}, token="tok-q")[0], 200)

    def test_default_is_fail_closed(self):
        d = tempfile.mkdtemp()
        st = agentd.Store(d)
        try:
            self.assertTrue(st.token_required())
            self.assertFalse(agentd.Store(d, dev=True).token_required())
        finally:
            st.con.close()

    def test_remote_source_is_not_local(self):
        self.assertTrue(agentd.is_local_addr("127.0.0.1"))
        self.assertFalse(agentd.is_local_addr("192.168.1.3"))

    def test_deny_sidecar_and_keys(self):
        for name in ("memory.db-wal", "memory.db-shm", "keys.json",
                     "Tokens.JSON", "a.db-journal", "sub.db", ".env"):
            p = self.tool_root / name
            p.write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(PermissionError, "禁止访问"):
                TX._resolve(str(p), False)


    def test_real_header_lock_is_recognized(self):
        self.board.write_text("      G1  AI 自己玩游戏   [锁:Qoder] [默认关]\n",
                              encoding="utf-8")
        code, out = self.req("POST", "/task", {"goal": "x", "capability": "read",
                                               "lock_owner": "Qoder",
                                               "lock_key": "G1  AI 自己玩游戏"}, token="tok-q")
        tid = out["id"]
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "qoder"},
                                  token="tok-q")[0], 200)

    def test_prose_lock_is_not_a_lock(self):
        self.board.write_text("                见 [锁:Qoder] 这段散文\n", encoding="utf-8")
        code, out = self.req("POST", "/task", {"goal": "x", "capability": "read",
                                               "lock_owner": "Qoder", "lock_key": "x"},
                             token="tok-q")
        tid = out["id"]
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "qoder"},
                                  token="tok-q")[0], 409)

    def test_closed_lock_is_not_active(self):
        self.board.write_text("[锁:Qoder] [已并入] G1\n", encoding="utf-8")
        code, out = self.req("POST", "/task", {"goal": "x", "capability": "read",
                                               "lock_owner": "Qoder", "lock_key": "G1"},
                             token="tok-q")
        tid = out["id"]
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "qoder"},
                                  token="tok-q")[0], 409)


    def test_lock_requires_key(self):
        code, out = self.req("POST", "/task", {"goal": "x", "capability": "read",
                                               "lock_owner": "Qoder"}, token="tok-q")
        self.assertEqual(code, 400)
        self.assertIn("lock_key", out["error"])

    def test_lock_key_is_exact_title_not_substring(self):
        self.board.write_text("      G1  AI 自己玩游戏   [锁:Qoder]\n", encoding="utf-8")
        code, out = self.req("POST", "/task", {"goal": "x", "capability": "read",
                                               "lock_owner": "Qoder", "lock_key": "G1"},
                             token="tok-q")
        tid = out["id"]
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "qoder"},
                                  token="tok-q")[0], 409)

    def test_lock_owner_whitelist(self):
        code, out = self.req("POST", "/task", {"goal": "x", "capability": "read",
                                               "lock_owner": "守门人", "lock_key": "G1"},
                             token="tok-q")
        self.assertEqual(code, 400)

    def test_adr_section_lock_is_not_backlog(self):
        self.board.write_text(
            "---- Backlog & 认领锁 ----\n"
            "      G1  AI 自己玩游戏   [锁:Qoder]\n"
            "---- ADR ----\n"
            "      ADR-999 历史引文 [锁:Qoder]\n", encoding="utf-8")
        code, out = self.req("POST", "/task", {"goal": "x", "capability": "read",
                                               "lock_owner": "Qoder",
                                               "lock_key": "ADR-999 历史引文"},
                             token="tok-q")
        tid = out["id"]
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "qoder"},
                                  token="tok-q")[0], 409)


    def test_empty_lock_key_never_matches(self):
        self.board.write_text("      G1  AI 自己玩游戏   [锁:Qoder]\n", encoding="utf-8")
        self.assertFalse(self.store.lock_ok("Qoder", ""))
        code, out = self.req("POST", "/task", {"goal": "x", "capability": "read",
                                               "lock_owner": "Qoder"}, token="tok-q")
        self.assertEqual(code, 400)
        self.assertIn("lock_key", out["error"])

    def test_lock_owner_whitelist_in_reader(self):
        self.board.write_text("      G1  AI 自己玩游戏   [锁:守门人]\n", encoding="utf-8")
        self.assertEqual(self.store.locks_detail(), [])
        self.assertFalse(self.store.lock_ok("守门人", "G1  AI 自己玩游戏"))


    def test_read_token_cannot_claim_write(self):
        code, out = self.req("POST", "/task", {"goal": "w", "capability": "write"}, token="tok-q")
        tid = out["id"]
        self.assertEqual(self.req("POST", "/approve", {"id": tid}, token="tok-q")[0], 200)
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "deepseek"},
                                  token="tok-d")[0], 403)
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "qoder"},
                                  token="tok-q")[0], 200)

    def test_read_token_cannot_run_write(self):
        code, out = self.req("POST", "/task", {"goal": "w", "capability": "write"}, token="tok-q")
        tid = out["id"]
        self.store.con.execute(
            "UPDATE tasks SET status='running', owner='deepseek', approved=1 WHERE id=?", (tid,))
        self.store.con.commit()
        self.assertEqual(self.req("POST", "/run", {"id": tid, "owner": "deepseek"},
                                  token="tok-d")[0], 403)


    def test_read_token_cannot_replay_reaped_write(self):
        code, out = self.req("POST", "/task", {"goal": "w", "capability": "write"}, token="tok-q")
        tid = out["id"]
        self.assertEqual(self.req("POST", "/approve", {"id": tid}, token="tok-q")[0], 200)
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "qoder"},
                                  token="tok-q")[0], 200)
        self.store.con.execute(
            "UPDATE tasks SET lease_until='2000-01-01T00:00:00' WHERE id=?", (tid,))
        self.store.con.commit()
        self.assertEqual(self.req("POST", "/reap", {}, token="tok-q")[1]["reaped"], 1)
        self.assertEqual(int(self.store.get(tid)["approved"]), 0)
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "deepseek"},
                                  token="tok-d")[0], 403)
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "qoder"},
                                  token="tok-q")[0], 409)
        self.assertEqual(self.req("POST", "/approve", {"id": tid}, token="tok-q")[0], 200)
        self.assertEqual(self.req("POST", "/claim", {"id": tid, "owner": "qoder"},
                                  token="tok-q")[0], 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
