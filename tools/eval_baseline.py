# -*- coding: utf-8 -*-
"""30 题模型基线：可重复、结果带时间/模型/耗时。"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pai import model_gateway as M10
from pai import persona as PS

CASES = ROOT / "evals" / "model_baseline.json"
OUT = ROOT / "reports" / "eval_baseline.json"


def score(case, text):
    must = case.get("must") or []
    bad = case.get("must_not") or []
    ok = (not must) or any(x in text for x in must)
    banned = any(x in text for x in bad)
    length_ok = not case.get("max_len") or len(text) <= case["max_len"]
    return bool(ok and not banned and length_ok)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    if args.limit:
        cases = cases[:args.limit]
    rows = []
    t0 = time.time()
    for c in cases:
        prompt = PS.prompt_block() + "\n[用户] " + c["q"]
        try:
            outs = M10.complete(prompt)
            text = str(outs[0].get("output") or "") if outs else ""
            err = ""
        except Exception as e:
            text, err = "", "%s: %s" % (type(e).__name__, e)
        rows.append({"id": c["id"], "cat": c["cat"], "pass": score(c, text),
                     "error": err, "output": text[:800]})
    elapsed = round(time.time() - t0, 1)
    report = {"at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
              "count": len(rows), "passed": sum(1 for x in rows if x["pass"]),
              "elapsed_sec": elapsed, "results": rows}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("baseline %d/%d %.1fs -> %s" % (report["passed"], report["count"], elapsed, OUT))
    return 0 if report["passed"] == report["count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
