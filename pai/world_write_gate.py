# -*- coding: utf-8 -*-
"""B 世界写入审批闸 v0（entity only）。

流程：prepare → approve(Boss) → execute。
默认不写真库；真库必须 --allow-real-db。
"""
import argparse
import datetime
import hashlib
import hmac
import json
import os
import pathlib

from pai import atomic as AT
import secrets
import sys


CAPABILITY = {
    "name": "世界写入审批闸",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "未批准=不写真库；审批文件不可写=拒绝执行",
    "approval": "ADR-046：审批后才写，不新增开关",
}

ROOT = pathlib.Path(__file__).resolve().parents[1]
while not (ROOT / "pai").exists() and ROOT.parent != ROOT:
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))


def _dir():
    p = pathlib.Path(os.environ.get("PAI_WORLD_APPROVAL_DIR") or (ROOT / "data" / "world_approvals"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _key():
    p = _dir() / ".approval.key"
    if not p.exists():
        AT.write_text(p, secrets.token_hex(32), encoding="utf-8")
    return p.read_text(encoding="utf-8").strip().encode("utf-8")


MAX_BATCH = 5


def _sha12(path):
    try:
        return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()[:12]
    except OSError:
        return "missing"


def _shadow_rows():
    sys.path.insert(0, str(ROOT / "tools"))
    from review_world_shadow import _rows
    return _rows()


def _find_entity(mem, name):
    row = mem.conn.execute(
        "SELECT id FROM world_entity WHERE status='active' AND lower(name)=lower(?)",
        (str(name or ""),)).fetchone()
    return row["id"] if row else None


def prepare(cid, db, allow_real=False):
    hit = next((r for r in _shadow_rows() if r["id"] == cid), None)
    if not hit:
        raise ValueError("找不到候选 %s" % cid)
    c = hit["candidate"]
    kind = c.get("kind")
    if kind not in ("entity", "belief", "relation"):
        raise ValueError("不支持的 kind=%s" % kind)
    dbp = pathlib.Path(db).resolve()
    if dbp == (ROOT / "data" / "memory.db").resolve() and not allow_real:
        raise ValueError("拒绝准备真库写入；确需请加 --allow-real-db")
    src = int(hit["rec"].get("source_episode") or 0)
    req = {
        "v": 1,
        "id": "wr_" + secrets.token_hex(6),
        "candidate_id": cid,
        "kind": kind,
        "source_episode": src,
        "db": str(dbp),
        "before_sha12": _sha12(dbp),
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    if kind == "entity":
        req.update({"type": c.get("type") or "person", "name": c.get("name"),
                    "aliases": c.get("aliases") or [],
                    "effects": ["insert or update entity"]})
    elif kind == "belief":
        req.update({"holder": c.get("holder") or "user",
                    "predicate": c.get("predicate"), "value": c.get("value"),
                    "effects": ["insert belief"]})
    else:
        from pai.memory import Memory
        m = Memory(str(dbp))
        try:
            sub = _find_entity(m, c.get("subject"))
            obj = _find_entity(m, c.get("object"))
        finally:
            m.conn.close()
        if not sub or not obj:
            raise ValueError("relation 两端实体必须已存在")
        req.update({"subject_id": sub, "object_id": obj,
                    "predicate": c.get("predicate"),
                    "effects": ["insert relation"]})
    p = _dir() / (req["id"] + ".request.json")
    AT.write_text(p, json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8")
    return p, req


def prepare_batch(cids, db, allow_real=False):
    """批量只到 MAX_BATCH；超限在写任何 request 之前就拒。"""
    ids = list(cids or [])
    if not ids:
        raise ValueError("批量为空")
    if len(ids) > MAX_BATCH:
        raise ValueError("批量超限：最多 %d 条，当前 %d" % (MAX_BATCH, len(ids)))
    return [prepare(cid, db, allow_real) for cid in ids]


def approve(reqfile, by):
    req = json.loads(pathlib.Path(reqfile).read_text(encoding="utf-8"))
    if by.strip().lower() in ("ai", "deepseek", "self", "ds", ""):
        raise ValueError("AI 不能批准自己")
    if by.strip() not in ("Boss", "老板"):
        raise ValueError("v0 只接受 Boss 批准")
    nonce = secrets.token_hex(8)
    msg = "|".join([req["id"], req["candidate_id"], req["db"], nonce, by]).encode("utf-8")
    sig = hmac.new(_key(), msg, hashlib.sha256).hexdigest()
    appr = {"v": 1, "request_id": req["id"], "by": by, "nonce": nonce,
            "sig": sig, "used": False,
            "at": datetime.datetime.now().isoformat(timespec="seconds")}
    p = pathlib.Path(reqfile).with_suffix(".approval.json")
    AT.write_text(p, json.dumps(appr, ensure_ascii=False, indent=2), encoding="utf-8")
    return p, appr


def execute(reqfile, apprfile, allow_real=False):
    req = json.loads(pathlib.Path(reqfile).read_text(encoding="utf-8"))
    appr = json.loads(pathlib.Path(apprfile).read_text(encoding="utf-8"))
    if appr.get("used"):
        raise ValueError("这次批准已用过")
    if appr.get("request_id") != req.get("id"):
        raise ValueError("批准与请求不匹配")
    msg = "|".join([req["id"], req["candidate_id"], req["db"],
                    appr.get("nonce", ""), appr.get("by", "")]).encode("utf-8")
    if not hmac.compare_digest(hmac.new(_key(), msg, hashlib.sha256).hexdigest(),
                               str(appr.get("sig") or "")):
        raise ValueError("批准签名无效")
    dbp = pathlib.Path(req["db"]).resolve()
    if dbp == (ROOT / "data" / "memory.db").resolve() and not allow_real:
        raise ValueError("拒绝写真库；确需请加 --allow-real-db")
    from pai.memory import Memory
    from pai.world import World
    m = Memory(str(dbp))
    try:
        w = World(m)
        if req.get("kind") == "entity":
            eid = w.upsert_entity(
                req["type"], req["name"], aliases=req.get("aliases") or [],
                source_episode=int(req.get("source_episode") or 0))
        elif req.get("kind") == "belief":
            eid = w.set_belief(
                req.get("holder") or "user", req.get("predicate"),
                req.get("value"), confidence=0.6,
                source="candidate:%s" % req.get("candidate_id"))
        elif req.get("kind") == "relation":
            eid = w.add_relation(
                req["subject_id"], req.get("predicate"),
                object_id=req.get("object_id"), confidence=0.6,
                source_episode=int(req.get("source_episode") or 0))
        else:
            raise ValueError("不支持的 kind")
    finally:
        m.conn.close()
    appr["used"] = True
    appr["used_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    AT.write_text(pathlib.Path(apprfile), json.dumps(appr, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    log = _dir() / "write_log.jsonl"
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": appr["used_at"], "request": req["id"],
                            "candidate_id": req["candidate_id"], "by": appr["by"],
                            "object_id": eid, "db": req["db"]},
                           ensure_ascii=False) + "\n")
    return eid, log


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("prepare")
    p1.add_argument("--candidate", required=True)
    p1.add_argument("--db", required=True)
    p1.add_argument("--allow-real-db", action="store_true")
    p2 = sub.add_parser("approve")
    p2.add_argument("--request", required=True)
    p2.add_argument("--by", required=True)
    p3 = sub.add_parser("execute")
    p3.add_argument("--request", required=True)
    p3.add_argument("--approval", required=True)
    p3.add_argument("--allow-real-db", action="store_true")
    ns = ap.parse_args(argv)
    try:
        if ns.cmd == "prepare":
            p, req = prepare(ns.candidate, ns.db, ns.allow_real_db)
            print(json.dumps({"request": str(p), "id": req["id"]}, ensure_ascii=False))
        elif ns.cmd == "approve":
            p, a = approve(ns.request, ns.by)
            print(json.dumps({"approval": str(p), "used": a["used"]}, ensure_ascii=False))
        else:
            eid, log = execute(ns.request, ns.approval, ns.allow_real_db)
            print(json.dumps({"object_id": eid, "log": str(log)}, ensure_ascii=False))
    except Exception as e:
        print("ERROR " + str(e))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
