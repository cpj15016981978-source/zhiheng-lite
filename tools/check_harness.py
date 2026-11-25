# -*- coding: utf-8 -*-
"""harness 总闸：跑全量单元测试 + normal eval + redteam eval。

零模型、零真库。任一步失败，退出 1。
"""
import argparse
import json
import pathlib
import subprocess
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable


def run(name, cmd):
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {
        "name": name,
        "rc": p.returncode,
        "ok": p.returncode == 0,
        "tail": (p.stdout or "")[-4000:],
        "err": (p.stderr or "")[-2000:],
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="只跑 eval，不跑全量单测")
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)

    steps = []
    if not ns.quick:
        steps.append(run("unittest", [PY, "-m", "unittest", "discover", "-s", "tests"]))
    steps.append(run("eval", [PY, "tools/run_eval.py", "--json"]))
    steps.append(run("redteam", [PY, "tools/run_eval.py", "--cases", "evals/redteam.json", "--json"]))
    steps.append(run("world", [PY, "tools/eval_world.py"]))
    steps.append(run("shadow", [PY, "tools/eval_world_shadow.py"]))
    ok = all(s["ok"] for s in steps)
    out = {"ok": ok, "steps": steps}
    if ns.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for s in steps:
            print(("PASS " if s["ok"] else "FAIL ") + s["name"] + "  rc=" + str(s["rc"]))
            if not s["ok"]:
                print(s["tail"])
                print(s["err"])
        print("\nharness " + ("GREEN" if ok else "RED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
