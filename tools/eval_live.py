# -*- coding: utf-8 -*-
"""真实模型 eval：临时库、单轮、硬预算。

用法：
    python tools/eval_live.py
    python tools/eval_live.py --prompt "你好" --max-cost 0.01
    python tools/eval_live.py --record evals/live/last.json

要求环境：
    MINIMAX_API_KEY 或 OPENAI_API_KEY
    PAI_MODEL_PROVIDER=openai
    PAI_MODEL_NAME=MiniMax-M3.1-Flash
    PAI_OPENAI_BASE_URL=https://api.minimaxi.com/v1
"""
import argparse
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="用一句话说明你是谁")
    ap.add_argument("--max-cost", type=float, default=0.01)
    ap.add_argument("--record", default=None)
    ns = ap.parse_args(argv)

    if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("MINIMAX_API_KEY")):
        print("SKIP 没有 OPENAI_API_KEY / MINIMAX_API_KEY")
        return 2

    from pai.memory import Memory
    from pai.orchestrator import Orchestrator

    db = pathlib.Path(tempfile.mkdtemp()) / "live-eval.db"
    mem = Memory(str(db))
    try:
        out = Orchestrator(mem).handle(ns.prompt, channel="eval-live")
    finally:
        mem.conn.close()

    trace = out.get("trace") or {}
    cost = float(trace.get("cost") or 0.0)
    text = str(out.get("response") or "")
    ok = bool(text.strip()) and cost <= ns.max_cost
    rec = {
        "ok": ok,
        "prompt": ns.prompt,
        "response": text[:2000],
        "cost": cost,
        "tokens": trace.get("tokens", 0),
        "models": trace.get("models", []),
        "max_cost": ns.max_cost,
    }
    if ns.record:
        p = pathlib.Path(ns.record)
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rec, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
