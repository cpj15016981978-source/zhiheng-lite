# -*- coding: utf-8 -*-
"""人工审核影子候选：默认只看/演练，写库必须显式 --write + --db。

用法：
    python tools/review_world_shadow.py --list
    python tools/review_world_shadow.py --approve <id> --write --db <临时库>
    python tools/review_world_shadow.py --approve <id> --write --db data/memory.db --allow-real-db
"""
import argparse
import hashlib
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _shadow_dir():
    return pathlib.Path(os.environ.get("PAI_WORLD_SHADOW_DIR") or (ROOT / "data" / "world_shadow"))


def _rows():
    d = _shadow_dir()
    out = []
    if not d.exists():
        return out
    for p in sorted(d.glob("shadow-*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            for c in rec.get("candidates") or []:
                cid = hashlib.sha256(
                    json.dumps(c, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()[:12]
                out.append({"id": cid, "file": str(p), "rec": rec, "candidate": c})
    return out


def _resolve_entity(mem, typ, name, source_episode=0):
    from pai.world import World
    return World(mem).upsert_entity(typ, name, source_episode=source_episode)


def _find_entity(mem, name):
    row = mem.conn.execute(
        "SELECT id FROM world_entity WHERE status='active' AND lower(name)=lower(?)",
        (str(name or ""),)).fetchone()
    return row["id"] if row else None


def _high_confidence(c):
    return (c.get("kind") == "entity" and c.get("type") == "person"
            and c.get("evidence") in ("我叫/我是",))


def _reviewed_ids():
    p = _shadow_dir() / "reviewed.jsonl"
    if not p.exists():
        return set()
    out = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.add(json.loads(line).get("candidate_id"))
        except Exception:
            pass
    return out


def _rejected_ids():
    p = _shadow_dir() / "rejected.jsonl"
    if not p.exists():
        return set()
    out = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.add(json.loads(line).get("candidate_id"))
        except Exception:
            pass
    return out


def _review_row(r, reviewed, mem=None, rejected=None):
    c = r["candidate"]
    kind = c.get("kind")
    if kind == "relation":
        text = "%s -%s-> %s" % (c.get("subject"), c.get("predicate"), c.get("object"))
        blockers = []
        if mem is not None:
            for role in ("subject", "object"):
                if not _find_entity(mem, c.get(role)):
                    blockers.append("%s 缺实体" % role)
        else:
            blockers.append("未给 --db，无法校验两端实体")
    else:
        text = "%s -%s-> %s" % (c.get("holder"), c.get("predicate"), c.get("value"))
        blockers = []
    rejected = rejected or set()
    if r["id"] in rejected:
        status = "已拒"
    elif r["id"] in reviewed:
        status = "已审"
    else:
        status = "待审"
    return {
        "id": r["id"],
        "kind": kind,
        "text": text,
        "evidence": c.get("evidence") or "",
        "source_episode": r["rec"].get("source_episode"),
        "status": status,
        "blockers": blockers,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--review-list", action="store_true", help="列 relation/belief 审核清单")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--high-confidence", action="store_true", help="只看/只审高置信实体")
    ap.add_argument("--approve", default=None)
    ap.add_argument("--reject", default=None)
    ap.add_argument("--reason", default="")
    ap.add_argument("--approve-high", action="store_true", help="批量审高置信实体")
    ap.add_argument("--write", action="store_true", help="真的写世界表；缺省只演练")
    ap.add_argument("--db", default=None)
    ap.add_argument("--allow-real-db", action="store_true")
    ns = ap.parse_args(argv)

    rows = _rows()
    if ns.review_list:
        rows = [r for r in rows if r["candidate"].get("kind") in ("relation", "belief")]
        reviewed = _reviewed_ids()
        rejected = _rejected_ids()
        mem = None
        if ns.db:
            dbp = pathlib.Path(ns.db)
            if not dbp.exists() or not dbp.is_file():
                print("拒绝：--db 路径不存在；review-list 不建库")
                return 2
            from pai.memory import Memory
            mem = Memory(str(dbp))
        try:
            items = [_review_row(r, reviewed, mem, rejected) for r in rows]
        finally:
            if mem is not None:
                mem.conn.close()
        if ns.json:
            print(json.dumps(items, ensure_ascii=False, indent=2))
        else:
            if not items:
                print("没有 relation/belief 候选")
            for it in items:
                print("[%s] %s  %s" % (it["kind"], it["id"], it["status"]))
                print("      " + it["text"])
                if it["evidence"]:
                    print("      evidence=" + it["evidence"])
                for b in it["blockers"]:
                    print("      ! " + b)
        return 0

    if ns.reject:
        hit = next((r for r in rows if r["id"] == ns.reject), None)
        if not hit:
            print("找不到候选 %s" % ns.reject)
            return 2
        if not ns.write:
            print("演练：将拒 %s（未写）；确需写加 --write" % hit["id"])
            return 0
        rec = {
            "ts": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "candidate_id": hit["id"],
            "reason": ns.reason or "人工拒绝",
            "candidate": hit["candidate"],
        }
        rp = _shadow_dir() / "rejected.jsonl"
        with rp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print("已拒 %s；记录 %s" % (hit["id"], rp))
        return 0

    if ns.high_confidence:
        rows = [r for r in rows if _high_confidence(r["candidate"])]
    if ns.list or (not ns.approve and not ns.approve_high):
        if not rows:
            print("没有影子候选")
            return 0
        rejected = _rejected_ids()
        for r in rows:
            c = r["candidate"]
            mark = "  已拒" if r["id"] in rejected else ""
            print("%s  %-8s %-8s %s%s" % (
                r["id"], c.get("kind"), c.get("type", ""),
                c.get("name") or c.get("value") or c.get("object") or "", mark))
        return 0

    if ns.approve_high:
        if not ns.write:
            print("演练：将写 %d 个高置信实体（未写）" % len(rows))
            for r in rows:
                print("  " + r["id"] + "  " + str(r["candidate"].get("name")))
            return 0
        if not ns.db:
            print("--write 必须给 --db")
            return 2
        p = pathlib.Path(ns.db).resolve()
        true_db = (ROOT / "data" / "memory.db").resolve()
        if p == true_db and not ns.allow_real_db:
            print("拒绝写真库；确需请加 --allow-real-db")
            return 2
        from pai.memory import Memory
        m = Memory(str(p))
        written = []
        try:
            for r in rows:
                c = r["candidate"]
                src = int(r["rec"].get("source_episode") or 0)
                eid = _resolve_entity(m, "person", c.get("name"), src)
                written.append((r["id"], eid, c.get("name")))
        finally:
            m.conn.close()
        rp = _shadow_dir() / "reviewed.jsonl"
        with rp.open("a", encoding="utf-8") as f:
            for cid, eid, name in written:
                f.write(json.dumps({
                    "ts": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                    "candidate_id": cid, "object_id": eid, "kind": "entity",
                    "name": name, "batch": "high-confidence",
                }, ensure_ascii=False) + "\n")
        print("已写 %d 个高置信实体；审核记录 %s" % (len(written), rp))
        return 0

    hit = next((r for r in rows if r["id"] == ns.approve), None)
    if not hit:
        print("找不到候选 %s" % ns.approve)
        return 2
    c = hit["candidate"]
    kind = c.get("kind")
    if kind not in ("entity", "belief", "relation"):
        print("不支持的 kind=%s" % kind)
        return 2
    if not ns.write:
        print("演练：将写 %s %s（未写）" % (kind, c.get("name") or c.get("value") or ""))
        return 0
    if not ns.db:
        print("--write 必须给 --db")
        return 2
    p = pathlib.Path(ns.db).resolve()
    true_db = (ROOT / "data" / "memory.db").resolve()
    if p == true_db and not ns.allow_real_db:
        print("拒绝写真库；确需请加 --allow-real-db")
        return 2

    from pai.memory import Memory
    m = Memory(str(p))
    try:
        src = int(hit["rec"].get("source_episode") or 0)
        if kind == "entity":
            oid = _resolve_entity(m, c.get("type") or "person", c.get("name"), src)
        elif kind == "belief":
            from pai.world import World
            oid = World(m).set_belief(
                c.get("holder") or "user", c.get("predicate") or "喜欢",
                c.get("value"), confidence=0.6, source="shadow:%s" % hit["id"])
        else:
            from pai.world import World
            sub = _find_entity(m, c.get("subject"))
            obj = _find_entity(m, c.get("object"))
            if not sub or not obj:
                print("relation 需要先审好 subject/object 两个实体")
                return 2
            oid = World(m).add_relation(
                sub, c.get("predicate") or "相关", object_id=obj,
                confidence=0.6, source_episode=src)
    finally:
        m.conn.close()
    review = {
        "ts": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "candidate_id": hit["id"],
        "object_id": oid,
        "kind": kind,
        "candidate": c,
    }
    rp = _shadow_dir() / "reviewed.jsonl"
    with rp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(review, ensure_ascii=False) + "\n")
    print("已写 %s %s；审核记录 %s" % (kind, oid, rp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
