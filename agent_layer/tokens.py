# -*- coding: utf-8 -*-
"""agent 令牌管理：每个 agent 一把，绑定 capability 白名单。

    python tokens.py add --owner deepseek --caps read,write
    python tokens.py list
    python tokens.py revoke --owner deepseek

令牌只显示一次；文件里只存 sha256。
"""
import argparse
import hashlib
import json
import secrets
import sys
from pathlib import Path
from pai import atomic as AT

DEFAULT = Path(__file__).with_name("data") / "tokens.json"


def load(path=DEFAULT):
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save(data, path=DEFAULT):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    AT.write_text(p, json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def add(owner, caps, path=DEFAULT):
    data = load(path)
    token = secrets.token_urlsafe(24)
    data[owner] = {"hash": hashlib.sha256(token.encode()).hexdigest(),
                   "caps": list(caps)}
    save(data, path)
    return token


def revoke(owner, path=DEFAULT):
    data = load(path)
    if owner in data:
        del data[owner]
        save(data, path)
        return True
    return False


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(DEFAULT))
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--owner", required=True)
    a.add_argument("--caps", default="read")
    sub.add_parser("list")
    r = sub.add_parser("revoke")
    r.add_argument("--owner", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "add":
        caps = [c.strip() for c in args.caps.split(",") if c.strip()]
        token = add(args.owner, caps, args.file)
        print("owner=%s caps=%s" % (args.owner, ",".join(caps)))
        print("token=%s" % token)
        print("只显示这一次；文件里只存 sha256。")
    elif args.cmd == "list":
        for owner, rec in load(args.file).items():
            print("%s  caps=%s" % (owner, ",".join(rec.get("caps") or [])))
    else:
        print("已撤销" if revoke(args.owner, args.file) else "没有这个 owner")
    return 0


if __name__ == "__main__":
    sys.exit(main())
