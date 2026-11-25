# -*- coding: utf-8 -*-
"""递归自我模型 v0 的 CLI：打印“我会什么/不会什么/多确定”。"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pai import self_knowledge as SK  # noqa: E402
from pai.memory import Memory          # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "memory.db"))
    ap.add_argument("--record-brier", type=float)
    ap.add_argument("--record-n", type=int)
    ap.add_argument("--record-accuracy", type=float)
    args = ap.parse_args(argv)

    SK.set_enabled(True)
    mem = Memory(args.db)
    try:
        if args.record_brier is not None:
            if args.record_n is None or args.record_accuracy is None:
                print("要记校准，需要同时给 --record-n 与 --record-accuracy")
                return 2
            try:
                data = SK.record_calibration(mem, args.record_brier,
                                             args.record_n, args.record_accuracy)
            except ValueError as e:
                print("没记：%s" % e)
                return 2
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        print(json.dumps(SK.report(mem), ensure_ascii=False, indent=2))
        return 0
    finally:
        mem.close()


if __name__ == "__main__":
    sys.exit(main())
