# -*- coding: utf-8 -*-
"""启动前 / 提交前检查。

启动：
    python tools/preflight.py --startup
提交：
    python tools/preflight.py --commit
"""
import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--startup", action="store_true", help="只跑 eval + redteam")
    ap.add_argument("--commit", action="store_true", help="跑全量单测 + eval + redteam")
    ns = ap.parse_args(argv)
    if ns.startup == ns.commit:
        print("用法：--startup 或 --commit")
        return 2
    tier = "full" if ns.commit else "contract"
    p = subprocess.run([PY, "tools/gates.py", "--tier", tier], cwd=str(ROOT))
    if p.returncode != 0:
        return p.returncode
    return subprocess.run([PY, "tools/check_harness.py", "--quick"], cwd=str(ROOT)).returncode


if __name__ == "__main__":
    sys.exit(main())
