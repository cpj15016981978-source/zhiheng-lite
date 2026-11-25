# -*- coding: utf-8 -*-
"""harness evals v0：临时库起真 webapp，假 Orchestrator，零模型、零真库。

用例在 evals/cases.json。
支持：
  --record PATH   记录本次响应
  --replay PATH   与记录逐条比对
  --max-fake-calls / --max-seconds  预算
"""
import argparse
import json
import os
import pathlib
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import tempfile
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 必须在 import webapp 之前清掉，避免拿本机真 token 去调 agentd。
os.environ.pop("PAI_AGENTD_TOKEN_QODER", None)
os.environ.pop("PAI_REASONING_GATE", None)

import webapp  # noqa: E402
from pai.orchestrator import _looks_secret  # noqa: E402


class FakeOrch:
    calls = 0

    def __init__(self, mem):
        FakeOrch.calls = 0

    def handle(self, text, channel="web", **kw):
        if _looks_secret(text):
            return {"response": "SECRET_BLOCKED", "trace": {"blocked_secret": True}}
        FakeOrch.calls += 1
        return {"response": "EVAL_OK", "trace": {"eval": True, "text": text}}


def http_req(port, method, path, body=None, raw=None):
    data = None
    headers = {}
    if raw is not None:
        data = raw.encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), data=data,
        method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def stable(obj):
    """去掉每次启动会变的字段，供 replay 比对。"""
    if isinstance(obj, dict):
        return {k: stable(v) for k, v in obj.items()
                if k not in ("db", "agentd", "port", "pid")}
    if isinstance(obj, list):
        return [stable(x) for x in obj]
    return obj


def check_json(actual, expected):
    if not isinstance(expected, dict):
        return actual == expected
    if not isinstance(actual, dict):
        return False
    for k, v in expected.items():
        if k not in actual:
            return False
        if not check_json(actual[k], v):
            return False
    return True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--cases", default="evals/cases.json", help="用例文件")
    ap.add_argument("--record", default=None, help="把本次响应写到这个文件")
    ap.add_argument("--replay", default=None, help="与这个记录逐条比对")
    ap.add_argument("--max-fake-calls", type=int, default=1000, help="假模型调用总预算")
    ap.add_argument("--max-seconds", type=float, default=120.0, help="总耗时预算")
    ns = ap.parse_args(argv)

    case_path = pathlib.Path(ns.cases)
    if not case_path.is_absolute():
        case_path = ROOT / case_path
    cases = json.loads(case_path.read_text(encoding="utf-8"))

    recorded = {}
    if ns.replay:
        rp = pathlib.Path(ns.replay)
        if not rp.is_absolute():
            rp = ROOT / rp
        old = json.loads(rp.read_text(encoding="utf-8"))
        for x in old.get("results", []):
            if x.get("id"):
                recorded[x["id"]] = x

    db = pathlib.Path(tempfile.mkdtemp()) / "eval.db"
    old_orch = webapp.Orchestrator
    webapp.Orchestrator = FakeOrch
    httpd, thread = webapp.start(port=0, db_path=str(db), quiet=True, ready_timeout=40)
    port = httpd.server_address[1]
    results = []
    started = time.time()
    try:
        for c in cases:
            elapsed = time.time() - started
            if elapsed > ns.max_seconds:
                results.append({
                    "id": c["id"], "ok": False, "status": None,
                    "category": "budget_time",
                    "errors": [{"kind": "budget_time",
                                "detail": "%.1fs > %.1fs" % (elapsed, ns.max_seconds)}],
                    "text": "",
                })
                break
            before = FakeOrch.calls
            status, text = http_req(port, c.get("method", "GET"),
                                    c["path"], c.get("body"), c.get("body_raw"))
            exp = c.get("expect", {})
            errors = []
            if "status" in exp and status != exp["status"]:
                errors.append({"kind": "status",
                               "detail": "status %s != %s" % (status, exp["status"])})
            body = None
            try:
                body = json.loads(text)
            except Exception:
                pass
            if "json" in exp and not check_json(body, exp["json"]):
                errors.append({"kind": "json", "detail": "json mismatch"})
            if "contains" in exp and exp["contains"] not in text:
                errors.append({"kind": "contains", "detail": "missing %r" % exp["contains"]})
            if "fake_delta" in exp:
                delta = FakeOrch.calls - before
                if delta != exp["fake_delta"]:
                    errors.append({"kind": "fake_delta",
                                   "detail": "fake_delta %s != %s" % (delta, exp["fake_delta"])})
            if FakeOrch.calls > ns.max_fake_calls:
                errors.append({"kind": "budget_calls",
                               "detail": "%d > %d" % (FakeOrch.calls, ns.max_fake_calls)})
            if c["id"] in recorded:
                rec = recorded[c["id"]]
                now = stable(body) if body is not None else text
                old = rec.get("body") if rec.get("body") is not None else rec.get("text")
                if rec.get("status") != status or old != now:
                    errors.append({"kind": "replay",
                                   "detail": "response differs from recorded"})
            results.append({
                "id": c["id"],
                "ok": not errors,
                "status": status,
                "category": errors[0]["kind"] if errors else "pass",
                "errors": errors,
                "body": stable(body) if body is not None else None,
                "text": text if body is None else None,
            })
    finally:
        webapp.Orchestrator = old_orch
        webapp.stop(httpd, thread)

    ok = all(r["ok"] for r in results)
    summary = {}
    for r in results:
        summary[r["category"]] = summary.get(r["category"], 0) + 1
    out = {"ok": ok, "summary": summary,
           "fake_calls": FakeOrch.calls, "results": results}
    if ns.record:
        rp = pathlib.Path(ns.record)
        if not rp.is_absolute():
            rp = ROOT / rp
        rp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    if ns.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(("PASS " if r["ok"] else "FAIL ") + r["id"]
                  + "  status=" + str(r["status"]) + "  [" + r["category"] + "]")
            for e in r["errors"]:
                print("      " + e["kind"] + ": " + e["detail"])
        print("\n%d/%d passed  fake_calls=%d" % (
            sum(1 for r in results if r["ok"]), len(results), FakeOrch.calls))
        print("summary: " + json.dumps(summary, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
