# -*- coding: utf-8 -*-
"""B0 世界模型 eval：临时库、手动写入、只验结构/约束/矛盾/退休。

不调模型、不碰真库、不做自动抽取。
"""
import datetime
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pai.memory import Memory  # noqa: E402
from pai.world import World  # noqa: E402


def main():
    db = pathlib.Path(tempfile.mkdtemp()) / "world-eval.db"
    m = Memory(str(db))
    w = World(m)
    results = []

    def check(name, fn):
        try:
            fn()
            results.append({"name": name, "ok": True, "error": None})
        except Exception as e:
            results.append({"name": name, "ok": False, "error": "%s: %s" % (type(e).__name__, e)})

    src = m.write_episode("user", "我叫阿明，妻子是李四")
    now = datetime.datetime.now().isoformat(timespec="seconds")

    check("entity_requires_source", lambda: _expect(ValueError, lambda: w.upsert_entity("person", "无源")))
    check("secret_rejected", lambda: _expect(ValueError, lambda: w.upsert_entity("person", "秘密", source_episode=src, sensitivity="secret")))

    ids = {}

    def make_entities():
        ids["aming"] = w.upsert_entity("person", "阿明", aliases=["明哥"], source_episode=src)
        ids["lisi"] = w.upsert_entity("person", "李四", source_episode=src)
        ids["aming2"] = w.upsert_entity("person", "阿明", aliases=["阿明哥"], source_episode=src)
        assert ids["aming"] == ids["aming2"], "同名实体应合并"
        assert len(w.relations(ids["aming"])) == 0
    check("entities_and_dedupe", make_entities)

    def relation():
        rid = w.add_relation(ids["aming"], "妻子", object_id=ids["lisi"],
                             confidence=0.9, source_episode=src)
        ids["rid"] = rid
        rows = w.relations(ids["aming"])
        assert len(rows) == 1 and rows[0]["id"] == rid
        assert rows[0]["object_id"] == ids["lisi"]
    check("relation_readback", relation)

    def event():
        eid = w.add_event(now, "见面", "阿明和李四见面", participants=[ids["aming"], ids["lisi"]],
                          source_episode=src)
        rows = w.events_between("2000-01-01", "2099-01-01")
        assert any(r["id"] == eid for r in rows)
    check("event_window", event)

    def belief():
        bid = w.set_belief("user", "喜欢", "茶", confidence=0.8,
                            source="user:%s" % src, subject_id=ids["aming"])
        rows = w.beliefs("user")
        assert any(r["id"] == bid and r["value"] == "茶" for r in rows)
    check("belief", belief)

    def contradiction():
        w.add_relation(ids["aming"], "妻子", object_value="王五",
                       confidence=0.4, source_episode=src)
        found = w.check_contradictions()
        assert found, "应检测到妻子对象冲突"
        again = w.check_contradictions()
        assert not again, "同一矛盾不应重复登记"
    check("contradiction_open_once", contradiction)

    def retire():
        w.retire("relation", ids["rid"], "测试退休")
        assert not any(r["id"] == ids["rid"] for r in w.relations(ids["aming"])), "已退休关系不应再被读取"
    check("retire_entity", retire)

    m.conn.close()
    ok = all(r["ok"] for r in results)
    print(json.dumps({"ok": ok, "checks": results}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _expect(exc, fn):
    try:
        fn()
    except exc:
        return
    raise AssertionError("expected %s" % exc.__name__)


if __name__ == "__main__":
    sys.exit(main())
