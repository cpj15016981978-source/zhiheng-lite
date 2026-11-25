# -*- coding: utf-8 -*-
"""三档门禁唯一执行器：contract / quick / full。

用法：
  python tools/gates.py --tier contract
  python tools/gates.py --tier quick
  python tools/gates.py --tier full
  python tools/gates.py --tier contract --explain

逃生阀只放 contract 档，且必须写 reason；pre-receive 会重跑 quick。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, str(ROOT))
SPEC = json.loads((ROOT / "gates.json").read_text(encoding="utf-8"))
PY = os.environ.get("PAI_GATE_PYTHON") or sys.executable


def resolve(tier, seen=None):
    seen = seen or []
    if tier in seen:
        raise SystemExit("gates.json 档位循环 extends: %s" % " -> ".join(seen + [tier]))
    t = SPEC["tiers"][tier]
    tests = list(t.get("tests", []))
    checks = list(t.get("checks", []))
    budget = t.get("budget_s", 0)
    mode = t.get("mode")
    if t.get("extends"):
        p = resolve(t["extends"], seen + [tier])
        tests, checks = p[0] + tests, p[1] + checks
        if not budget:
            budget = p[2]
        mode = mode or p[3]
    return tests, checks, budget, mode


def run(label, argv, timeout):
    t0 = time.time()
    try:
        p = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        rc = p.returncode
    except subprocess.TimeoutExpired:
        rc, p = 124, None
    return {"name": label, "rc": rc, "ok": rc == 0, "secs": round(time.time() - t0, 1),
            "tail": ((p.stdout or "")[-1500:] + (p.stderr or "")[-1500:]) if p else "timeout"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", required=True, choices=sorted(SPEC["tiers"]))
    ap.add_argument("--explain", action="store_true")
    ns = ap.parse_args(argv)
    tests, checks, budget, mode = resolve(ns.tier)

    if ns.explain:
        print(json.dumps({"tier": ns.tier, "tests": tests,
                          "checks": [c[0] for c in checks], "mode": mode},
                         ensure_ascii=False, indent=2))
        return 0

    if not tests and mode != "discover":
        print("gates[%s] 名单为空 —— 拒绝把空名单当绿。" % ns.tier, file=sys.stderr)
        return 2

    if os.environ.get("PAI_GATE_SKIP") == "1" and ns.tier == "contract":
        reason = (os.environ.get("PAI_GATE_SKIP_REASON") or "").strip()
        if not reason:
            print("PAI_GATE_SKIP=1 但没写 PAI_GATE_SKIP_REASON —— 不放行。", file=sys.stderr)
            return 2
        from tools.gate_report import record_skip
        record_skip(ns.tier, reason)
        print("!! 契约档被跳过，已记入 reports/skips.jsonl；pre-receive 仍会拦。", file=sys.stderr)
        return 0

    steps = []
    if mode == "discover":
        steps.append(run("unittest-discover",
                         [PY, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
                         budget or 1200))
    else:
        for m in tests:
            steps.append(run("tests." + m, [PY, "-m", "unittest", "tests." + m], 90))
    for c in checks:
        steps.append(run(c[0], [PY] + c, 120))

    ok = all(s["ok"] for s in steps)
    spent = round(sum(s["secs"] for s in steps), 1)
    from tools.gate_report import write_report
    write_report(ns.tier, ok, spent, budget, steps)
    for s in steps:
        print(("PASS " if s["ok"] else "FAIL ") + "%-38s %5.1fs rc=%s" % (s["name"], s["secs"], s["rc"]))
        if not s["ok"]:
            print(s["tail"])
    over = budget and spent > budget
    print("\ngates[%s] %s  %.1fs%s" % (ns.tier, "GREEN" if ok else "RED", spent,
                                        "  (超预算 %ss)" % budget if over else ""))
    if over:
        print("!! 超预算：把慢用例挪到 full，或拆细断言；不要靠调大 budget。", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
