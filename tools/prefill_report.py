# -*- coding: utf-8 -*-
"""真实 prefill 报表：读 trace 里的 Ollama metrics，不靠估算。"""
from __future__ import annotations

import argparse
import glob
import json
import pathlib
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
TRACE_DIRS = [ROOT / "data" / "traces", pathlib.Path(r"D:\ZhihengInstall\spine\data\traces")]


def _rows(days=7, last=30):
    out = []
    for d in TRACE_DIRS:
        if not d.is_dir():
            continue
        files = sorted(d.glob("trace-*.jsonl"))[-max(1, int(days)):]
        for f in files:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                c = ((r.get("trace") or {}).get("cascade") or {})
                m = c.get("metrics") or {}
                if not m:
                    continue
                out.append({"ts": r.get("ts"), "prompt_mode": c.get("prompt_mode"),
                            "model": c.get("chosen_model"), "route": c.get("route"),
                            "prompt_len": r.get("prompt_len"),
                            "prefill_ms": round(float(m.get("prompt_eval_duration") or 0) / 1e6, 1),
                            "load_ms": round(float(m.get("load_duration") or 0) / 1e6, 1),
                            "eval_ms": round(float(m.get("eval_duration") or 0) / 1e6, 1),
                            "prompt_tokens": m.get("prompt_eval_count"),
                            "out_tokens": m.get("eval_count")})
    return out[-max(1, int(last)):]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--last", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)
    rows = _rows(ns.days, ns.last)
    if ns.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("没有带 metrics 的 cascade trace。")
        return 1
    print("ts                  mode           model                 prefill_ms load_ms eval_ms prompt_tok out_tok")
    for r in rows:
        print("%-19s %-14s %-21s %10s %7s %7s %10s %7s" % (
            r["ts"], r["prompt_mode"], r["model"], r["prefill_ms"], r["load_ms"],
            r["eval_ms"], r["prompt_tokens"], r["out_tokens"]))
    avg = sum(r["prefill_ms"] for r in rows) / len(rows)
    print("\n样本 %d · 平均 prefill %.1f ms" % (len(rows), avg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
