# -*- coding: utf-8 -*-
"""回放记录里的入口决策：只重跑 R0 闸，不调模型、不写库。

用法：
    python tools/replay_trace.py data/traces/trace-20261030.jsonl
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pai import reasoning_gate as RG  # noqa: E402


def _fail(msg):
    print(json.dumps({"error": msg}, ensure_ascii=False))
    return 2


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ns = ap.parse_args(argv)
    src = pathlib.Path(ns.path)
    if not src.is_file():
        return _fail("trace 文件不存在或不是文件：%s" % ns.path)
    rows = []
    try:
        for lineno, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                return _fail("第 %d 行不是 JSON：%s" % (lineno, e.__class__.__name__))
    except OSError as e:
        return _fail("trace 文件读不了：%s" % e.__class__.__name__)
    if not rows:
        return _fail("trace 文件没有记录：%s" % ns.path)
    for i, r in enumerate(rows, 1):
        if not isinstance(r, dict) or "prompt" not in r or "trace" not in r:
            return _fail("第 %d 条不是 trace_store 形状（需要 prompt/trace）：%s" % (i, ns.path))
    bad = 0
    total = 0
    for r in rows:
        if not r.get("prompt"):
            continue
        total += 1
        old = r.get("trace") or {}
        new = RG.gate(r["prompt"])
        old_blocked = bool(old.get("blocked"))
        new_trace = (new or {}).get("trace") or (new or {})
        new_blocked = bool(new_trace.get("blocked"))
        old_reason = old.get("reason") or ""
        new_reason = new_trace.get("reason") or ""
        if old_blocked != new_blocked or (old_reason and old_reason != new_reason):
            bad += 1
            print("DRIFT", r.get("ts"), "old=", old_blocked, old_reason,
                  "new=", new_blocked, new_reason)
    print(json.dumps({"total": total, "drift": bad}, ensure_ascii=False))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
