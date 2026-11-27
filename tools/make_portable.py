# -*- coding: utf-8 -*-
"""打便携包：只带程序，不带用户数据/模型/备份。"""
from __future__ import annotations

import argparse
import datetime
import pathlib
import zipfile

EXCLUDE = {"data", "models", "backups", "logs", "__pycache__", ".git", "traces",
           "edge_profile", "browser_profile", "dist"}


def build(root, out=None, with_python=False):
    root = pathlib.Path(root).resolve()
    out = pathlib.Path(out or (root / "dist" / (
        "Zhiheng-portable-%s.zip" % datetime.datetime.now().strftime("%Y%m%d"))))
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in root.rglob("*"):
            rel = p.relative_to(root)
            if any(x in EXCLUDE for x in rel.parts):
                continue
            if rel.parts and rel.parts[0] == "ollama":
                continue
            if not with_python and rel.parts and rel.parts[0] == "python":
                continue
            if p.is_file():
                z.write(p, rel)
                n += 1
    return {"zip": str(out), "files": n, "bytes": out.stat().st_size}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("PAI_INSTALL") or str(pathlib.Path.home() / "ZhihengInstall"))
    ap.add_argument("--out", default="")
    ap.add_argument("--with-python", action="store_true")
    a = ap.parse_args()
    print(build(a.root, a.out or None, a.with_python))


if __name__ == "__main__":
    main()
