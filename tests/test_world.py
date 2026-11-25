# -*- coding: utf-8 -*-
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai.memory import Memory  # noqa: E402
from pai.world import World  # noqa: E402


class WorldTest(unittest.TestCase):
    def setUp(self):
        self.m = Memory(str(pathlib.Path(tempfile.mkdtemp()) / "world.db"))
        self.w = World(self.m)

    def test_entities_relations_events_and_time(self):
        a = self.w.upsert_entity("person", "阿明", aliases=["Vincent"], source_episode=1)
        d = self.w.upsert_entity("place", "东莞", source_episode=1)
        self.w.add_relation(a, "住在", object_id=d, valid_from="2020-01-01",
                            confidence=0.9, source_episode=1)
        self.w.add_event("2026-10-28T08:00:00", "trip", "去东莞出差",
                         participants=[a], place_id=d, source_episode=1)
        self.assertEqual(len(self.w.relations(a, "2026-10-28T00:00:00")), 1)
        self.assertEqual(len(self.w.relations(a, "2019-01-01T00:00:00")), 0)
        self.assertEqual(len(self.w.events_between("2026-10-28T00:00:00",
                                                   "2026-10-29T00:00:00")), 1)
        # 同名合并不新建
        a2 = self.w.upsert_entity("person", "阿明", source_episode=1)
        self.assertEqual(a, a2)

    def test_belief_is_not_fact(self):
        a = self.w.upsert_entity("person", "朋友", source_episode=1)
        self.w.set_belief("user", "可信", "是", confidence=0.6, source="episode:1",
                          subject_id=a)
        self.assertEqual(len(self.w.beliefs("user")), 1)
        self.assertEqual(self.w.relations(a), [])

    def test_contradiction_detected_not_deleted(self):
        a = self.w.upsert_entity("person", "阿明", source_episode=1)
        self.w.add_relation(a, "婚姻", object_value="已婚",
                            valid_from="2020-01-01", confidence=0.9, source_episode=1)
        self.w.add_relation(a, "婚姻", object_value="未婚",
                            valid_from="2020-01-01", confidence=0.9, source_episode=2)
        found = self.w.check_contradictions()
        self.assertEqual(len(found), 1)
        self.assertEqual(len(self.w.relations(a)), 2)

    def test_source_required(self):
        a = self.w.upsert_entity("person", "阿明", source_episode=1)
        with self.assertRaises(ValueError):
            self.w.add_relation(a, "备注", object_value="x")
        with self.assertRaises(ValueError):
            self.w.upsert_entity("person", "无来源")

    def test_secret_is_not_stored(self):
        with self.assertRaises(ValueError):
            self.w.upsert_entity("person", "秘密", sensitivity="secret",
                                 source_episode=1)

    def test_retire_removes_from_active_read(self):
        a = self.w.upsert_entity("person", "阿明", source_episode=1)
        rid = self.w.add_relation(a, "备注", object_value="x", source_episode=1)
        self.w.retire("relation", rid, "用户要求")
        self.assertEqual(self.w.relations(a), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
