# -*- coding: utf-8 -*-
"""从聊天文本/JSONL 蒸馏七层人设。"""
from __future__ import annotations

import argparse
import json
import pathlib

from pai import persona_distill as PD


def _load(path):
    p = pathlib.Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            if isinstance(d, dict):
                rows.append(d)
                continue
        except Exception:
            pass
        rows.append({"content": line})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--name", default="未命名")
    ap.add_argument("--slug", default="")
    arg = ap.parse_args(argv)
    data = PD.distill(_load(arg.input), name=arg.name)
    path = PD.save(data, slug=arg.slug or None)
    print(PD.prompt_block(data))
    print("\nSAVED " + path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
