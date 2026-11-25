# -*- coding: utf-8 -*-
"""低配实测：1.5B、2048 上下文、单并发、加载/回收验证。

用法（Windows）：
  set PAI_LOW_SPEC=1
  set PAI_LOW_SPEC_BUDGET_MB=8192
  set PAI_MODEL_PROVIDER=ollama
  set PAI_MODEL_NAME=qwen2.5:1.5b
  python tools/low_spec_probe.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def main():
    os.environ.setdefault("PAI_LOW_SPEC", "1")
    os.environ.setdefault("PAI_LOW_SPEC_BUDGET_MB", "8192")
    os.environ.setdefault("PAI_MODEL_PROVIDER", "ollama")
    os.environ.setdefault("PAI_MODEL_NAME", "qwen2.5:1.5b")
    os.environ.setdefault("PAI_OLLAMA_NUM_CTX", "2048")
    from pai import low_spec as LS
    from pai import model_gateway as M10

    before = LS.memory_status()
    t0 = time.time()
    error = ""
    output = ""
    try:
        out = M10.complete("用一句话说明你在本地运行。", opts={"max_tokens": 60, "temperature": 0.2})
        output = str((out[0] or {}).get("output") or "")
    except Exception as e:
        error = "%s: %s" % (type(e).__name__, e)
    elapsed = round(time.time() - t0, 1)
    loaded = LS.loaded_models()
    reclaim = LS.unload_models()
    report = {
        "host_total_mb": before.get("total_mb"),
        "host_free_mb_before": before.get("free_mb"),
        "is_8gb_host": bool(before.get("total_mb") and before["total_mb"] < 9000),
        "budget_mb": before.get("budget_mb"),
        "concurrency": before.get("concurrency"),
        "model": os.environ.get("PAI_MODEL_NAME"),
        "num_ctx": os.environ.get("PAI_OLLAMA_NUM_CTX"),
        "elapsed_s": elapsed,
        "output": output[:300],
        "error": error,
        "loaded_after": loaded,
        "reclaimed": reclaim.get("reclaimed"),
        "reclaim": reclaim,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if output and reclaim.get("reclaimed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
