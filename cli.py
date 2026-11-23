#!/usr/bin/env python3
"""Zhiheng Lite command-line entry point."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from core_model import CoreModel, CoreModelError
from office_pipeline import run as run_office, TASKS as OFFICE_TASKS

ROOT = pathlib.Path(__file__).resolve().parent


def cfg():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def core():
    return CoreModel(cfg())


def doctor():
    c = cfg()
    m = core()
    s = m.health()
    ok = bool(s.get("ok"))
    print("python:", sys.version.split()[0])
    print("provider:", s.get("provider"))
    print("model:", s.get("model"))
    print("runtime:", s.get("runtime"))
    print("model_path:", s.get("model_path"))
    print("data: none (stateless)")
    if not ok:
        print("status: MISSING (%s)" % s.get("error"))
        print("hint: put llama-server in runtime/ and core.gguf in models/ (release build),")
        print("      or set dev_ollama=true for local development.")
    else:
        print("status: ok")
    return 0 if ok else 1


def serve():
    import app
    from http.server import ThreadingHTTPServer
    c = cfg()
    host = str(c.get("host") or "127.0.0.1")
    port = int(c.get("port") or 8760)
    print("Zhiheng Lite http://%s:%d model=%s" % (host, port, c.get("model")))
    ThreadingHTTPServer((host, port), app.Handler).serve_forever()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="zhiheng", description="Zhiheng Lite CLI")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="start the browser UI")
    sub.add_parser("doctor", help="check core runtime and model")
    p_office = sub.add_parser("office", help="run the office pipeline")
    p_office.add_argument("file", nargs="?", default="")
    p_office.add_argument("--task", default="minutes", choices=sorted(OFFICE_TASKS))
    p_office.add_argument("--title", default="")
    p_model = sub.add_parser("model", help="core model status")
    p_model.add_argument("action", choices=["status", "list"], nargs="?", default="status")
    sub.add_parser("version", help="print configured core model")
    return ap.parse_args(argv)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    a = parse_args(argv)
    if a.cmd == "serve":
        serve()
        return 0
    if a.cmd == "doctor":
        return doctor()
    if a.cmd == "office":
        try:
            text = (pathlib.Path(a.file).read_text(encoding="utf-8", errors="replace")
                    if a.file else sys.stdin.buffer.read().decode("utf-8", "replace"))
            out = run_office(core(), a.task, text, a.title)
            print(out["content"])
            return 0
        except Exception as e:
            print("office failed: %s" % e, file=sys.stderr)
            return 1
    if a.cmd == "version":
        c = cfg()
        print("zhiheng-lite model=%s provider=%s host=%s port=%s" %
              (c.get("model"), c.get("provider"), c.get("host"), c.get("port")))
        return 0
    if a.cmd == "model":
        s = core().health()
        for k in ("provider", "model", "runtime", "model_path", "ok", "error"):
            if k in s:
                print("%s: %s" % (k, s[k]))
        return 0 if s.get("ok") else 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
