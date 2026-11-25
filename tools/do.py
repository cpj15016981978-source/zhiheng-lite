# -*- coding: utf-8 -*-
"""M8 只读工具 v0 的 CLI：list / recall / reminders / principles。

    python tools\\do.py list
    python tools\\do.py recall 爷爷 --k 3
    python tools\\do.py reminders
    python tools\\do.py principles

只读：不改库、不联网、不落文件。默认库 data/memory.db，可用 --db 指定。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pai import tools as M8  # noqa: E402
from pai.memory import Memory  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "memory.db"))
    ap.add_argument("cmd", nargs="?", default="list",
                    choices=["list", "recall", "reminders", "principles"])
    ap.add_argument("query", nargs="?")
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args(argv)

    if args.cmd == "list":
        print(json.dumps(M8.list_tools(), ensure_ascii=False, indent=2))
        return 0

    M8.set_enabled(True)
    db = Path(args.db)
    if not db.exists():
        # Memory() 一打开就会建库：路径打错一个字母 = 磁盘上多出一个空记忆库，
        # 使用者看到的是"它什么都不记得了"，其实是换了一颗空脑子。只读 CLI 不许建库。
        print(json.dumps({"ok": False, "tool": args.cmd, "permission": None, "data": None,
                          "reason": "库不存在：%s（这个 CLI 只读，不替你建空库）" % db},
                         ensure_ascii=False, indent=2))
        return 2
    mem = Memory(str(db))
    try:
        payload = {"query": args.query, "k": args.k} if args.cmd == "recall" else {}
        res = M8.run(args.cmd, payload, mem)
    finally:
        mem.close()
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
