# -*- coding: utf-8 -*-
"""B 影子抽取 fixture eval：只跑规则，不写库、不调模型。"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pai import world_shadow as WS  # noqa: E402

KEYS = ("kind", "type", "name", "predicate", "object", "value", "holder")


def canon(c):
    return tuple((k, str(c.get(k))) for k in KEYS if c.get(k) is not None)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="evals/fixtures/world_shadow_cases.json")
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)
    p = pathlib.Path(ns.cases)
    if not p.is_absolute():
        p = ROOT / p
    cases = json.loads(p.read_text(encoding="utf-8"))
    results = []
    for c in cases:
        got = WS.extract(c["text"])
        want = c.get("expect") or []
        g = sorted(canon(x) for x in got)
        w = sorted(canon(x) for x in want)
        results.append({"id": c["id"], "ok": g == w, "got": got, "want": want})
    ok = all(r["ok"] for r in results)
    if ns.json:
        print(json.dumps({"ok": ok, "results": results}, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(("PASS " if r["ok"] else "FAIL ") + r["id"])
            if not r["ok"]:
                print("  got  " + json.dumps(r["got"], ensure_ascii=False))
                print("  want " + json.dumps(r["want"], ensure_ascii=False))
        print("\n%d/%d passed" % (sum(1 for r in results if r["ok"]), len(results)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
