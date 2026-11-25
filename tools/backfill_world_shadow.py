# -*- coding: utf-8 -*-
"""从真实 episode 回填影子候选；默认只读演练，--write 才写 shadow 文件。

不写 memory、不写 world、不调模型。
"""
import argparse
import os
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
while not (ROOT / "pai").exists() and ROOT.parent != ROOT:
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from pai import world_shadow as WS  # noqa: E402


def rows(db, limit):
    con = sqlite3.connect("file:%s?mode=ro" % pathlib.Path(db).as_posix(), uri=True)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT id, content FROM episodes WHERE role='user' ORDER BY id LIMIT ?",
            (limit,)).fetchall()
    finally:
        con.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "memory.db"))
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--write", action="store_true")
    ns = ap.parse_args(argv)

    total = 0
    written = 0
    for r in rows(ns.db, ns.limit):
        cands = WS.extract(r["content"])
        if not cands:
            continue
        total += len(cands)
        print("episode %s: %d 条候选" % (r["id"], len(cands)))
        if ns.write:
            os.environ["PAI_WORLD_SHADOW"] = "on"
            if WS.observe(r["content"], source_episode=r["id"], source="backfill"):
                written += 1
    print("候选 %d 条；写入 shadow 记录 %d 条" % (total, written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
