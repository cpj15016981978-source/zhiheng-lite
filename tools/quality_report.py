# -*- coding: utf-8 -*-
"""工程质量报告：只读统计，不修改代码；用于防止债务继续无声增长。"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP = ("data", ".git", "__pycache__", "_incoming", ".venv")


def files():
    for p in ROOT.rglob("*.py"):
        if any(x in p.parts or any(part.startswith(x) for part in p.parts) for x in SKIP):
            continue
        yield p


def main():
    bare = []
    for p in files():
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            if re.search(r"except Exception\s*:", line):
                bare.append({"file": str(p.relative_to(ROOT)), "line": i,
                             "text": line.strip()[:160]})
    sizes = [{"file": str(p.relative_to(ROOT)), "lines": len(p.read_text(encoding="utf-8", errors="ignore").splitlines())}
             for p in files()]
    sizes.sort(key=lambda x: -x["lines"])
    hard = []
    for p in files():
        try:
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if "D:\\DSH" in line or "D:/DSH" in line:
                    hard.append({"file": str(p.relative_to(ROOT)), "line": i})
        except OSError:
            pass
    out = {"bare_except": len(bare), "files": len(list(files())),
           "largest": sizes[:10], "hardcoded_dsh": len(hard),
           "bare_sample": bare[:20]}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
