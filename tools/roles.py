# -*- coding: utf-8 -*-
"""ModelRouter CLI：查看角色、跑 embedding/rerank/asr。

    python tools/roles.py status
    python tools/roles.py embed "工厂对账"
    python tools/roles.py rerank "对账" "报价单" "工厂对账流程"
    python tools/roles.py asr path.wav
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pai.model_router import default  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    pe = sub.add_parser("embed"); pe.add_argument("text")
    pr = sub.add_parser("rerank"); pr.add_argument("query"); pr.add_argument("docs", nargs="+")
    pa = sub.add_parser("asr"); pa.add_argument("path")
    ns = ap.parse_args(argv)
    r = default()
    if ns.cmd == "status":
        print(json.dumps(r.status(), ensure_ascii=False, indent=2))
    elif ns.cmd == "embed":
        out = r.embed(ns.text)
        print(json.dumps({"backend": out["backend"], "count": len(out["vectors"]),
                          "dim": len(out["vectors"][0]) if out["vectors"] else 0},
                         ensure_ascii=False, indent=2))
    elif ns.cmd == "rerank":
        print(json.dumps(r.rerank(ns.query, ns.docs), ensure_ascii=False, indent=2))
    elif ns.cmd == "asr":
        print(r.asr(ns.path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
