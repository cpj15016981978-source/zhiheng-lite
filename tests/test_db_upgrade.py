# -*- coding: utf-8 -*-
"""老库迁移回归测试（tests/test_db_upgrade.py）—— X2 · [锁:DeepSeek]

守的是 2026-10-20 真实运行踩过的那类坑：**老库 + 新 schema**。
单测用全新库永远测不到它——全新库是 CREATE TABLE 与 CREATE INDEX 一起跑的，
只有“库里已经有旧表、旧数据”时，顺序与兼容问题才会现形。

三类场景（守门人回执第四节）：
  ① 最老库（Phase1：只有 episodes/facts/principles/affective_state）能直接打开，
     缺的表自动补，旧数据一条不少；
  ② 较新的老库（Phase3：多 meta/triggers/outbox）升级同样无损；
  ③ 升级后“改口留痕”必须落在 M2 的唯一写入口（principle_history），
     且【绕过自我模型、走界面/CLI 那条路】改的原则，账上也看得见——
     这是“两份真相”的长期守卫。

边界：只新增本文件；不改 pai/*.py、不改 schema.sql、不碰 data/、不写 D:\\Q。
全部在 tempfile 目录里建库，零第三方依赖。
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.memory import Memory          # noqa: E402
from pai import self_model as SM       # noqa: E402


# ---- 历史 schema 的两个真实快照 ------------------------------------------------
# ① Phase1（v0.3 时代）：只有四张表，没有 meta / 触发器 / 发件箱 / 两张历史表
OLD_PHASE1 = """
CREATE TABLE episodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, role TEXT NOT NULL,
  content TEXT NOT NULL, session_id TEXT);
CREATE TABLE facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT NOT NULL, predicate TEXT NOT NULL,
  object TEXT NOT NULL, ts TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0.5,
  source_episode_id INTEGER);
CREATE TABLE principles (
  id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, created_ts TEXT NOT NULL,
  revised_ts TEXT, revision_reason TEXT, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE affective_state (
  id INTEGER PRIMARY KEY CHECK (id = 1), mood TEXT, valence REAL, arousal REAL,
  updated_ts TEXT, note TEXT);
"""

# ② Phase3（v0.13 时代的 68 行 schema）：多 meta / triggers / outbox
OLD_PHASE3 = OLD_PHASE1 + """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE triggers (
  id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, spec TEXT,
  message_template TEXT NOT NULL, last_fired_ts TEXT, justify_reason TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, trigger_id INTEGER,
  channel TEXT NOT NULL DEFAULT "cli", message TEXT NOT NULL,
  is_read INTEGER NOT NULL DEFAULT 0);
"""

NEW_TABLES = {"fact_history", "principle_history", "meta", "triggers", "outbox"}


class UpgradeCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "old.db")

    def _make_old(self, schema, rows=True):
        conn = sqlite3.connect(self.db)
        conn.executescript(schema)
        if rows:
            conn.execute("INSERT INTO episodes(ts,role,content,session_id)"
                         " VALUES('2026-10-01T09:00:00','user','我爷爷1959年在挨饿','s0')")
            conn.execute("INSERT INTO episodes(ts,role,content,session_id)"
                         " VALUES('2026-10-01T09:00:05','ai','我记下了','s0')")
            conn.execute("INSERT INTO facts(subject,predicate,object,ts,confidence)"
                         " VALUES('用户','职业','床垫行业','2026-10-01T09:00:00',0.9)")
            conn.execute("INSERT INTO principles(text,created_ts,active)"
                         " VALUES('先分清事实再下判断','2026-10-01T09:00:00',1)")
            conn.commit()
        conn.close()

    def _tables(self, conn):
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}


class Phase1UpgradeTest(UpgradeCase):
    def test_old_db_opens_and_gains_missing_tables(self):
        self._make_old(OLD_PHASE1)
        mem = Memory(self.db)
        try:
            tables = self._tables(mem.conn)
            self.assertTrue(NEW_TABLES <= tables,
                            "升级后应补齐：%s" % (NEW_TABLES - tables))
        finally:
            mem.close()

    def test_old_data_is_intact_after_upgrade(self):
        self._make_old(OLD_PHASE1)
        mem = Memory(self.db)
        try:
            eps = mem.recent_episodes(10)
            self.assertEqual([e["content"] for e in eps],
                             ["我爷爷1959年在挨饿", "我记下了"])
            self.assertEqual([e["id"] for e in eps], [1, 2], "旧 id 不能被重排")
            facts = mem.all_facts()
            self.assertEqual(len(facts), 1)
            self.assertEqual(facts[0]["object"], "床垫行业")
            self.assertEqual(len(mem.get_principles()), 1)
        finally:
            mem.close()

    def test_autoincrement_continues_after_upgrade(self):
        self._make_old(OLD_PHASE1)
        mem = Memory(self.db)
        try:
            new_id = mem.write_episode("user", "升级后的新一句话")
            self.assertGreater(new_id, 2, "升级后写入不能与旧 id 撞号")
            self.assertEqual(len(mem.recent_episodes(10)), 3)
        finally:
            mem.close()

    def test_reopen_is_idempotent(self):
        self._make_old(OLD_PHASE1)
        Memory(self.db).close()
        mem = Memory(self.db)
        try:
            self.assertEqual(len(mem.recent_episodes(10)), 2)
            self.assertEqual(len(mem.all_facts()), 1)
            self.assertEqual(len(mem.get_principles()), 1)
        finally:
            mem.close()

    def test_phase3_db_upgrades_without_error(self):
        self._make_old(OLD_PHASE3)
        mem = Memory(self.db)
        try:
            self.assertTrue({"fact_history", "principle_history"} <= self._tables(mem.conn))
            self.assertEqual(len(mem.recent_episodes(10)), 2)
            mem.add_trigger("date", "提醒一句", "用户自己设的", "2026-11-01 09:00")
            self.assertEqual(len(mem.list_triggers()), 1)
        finally:
            mem.close()


class LedgerAfterUpgradeTest(UpgradeCase):
    """升级后的“改口留痕”：账必须在 M2 的唯一写入口，谁写都算。"""

    def _upgraded(self):
        self._make_old(OLD_PHASE1)
        return Memory(self.db)

    def test_old_principle_can_be_revised_after_upgrade(self):
        mem = self._upgraded()
        try:
            pid = mem.get_principles()[0]["id"]
            out = mem.set_principles(
                [{"op": "revise", "id": pid, "text": "先分清事实，再标出依据"}],
                "旧句只说分清，没说标出依据")
            self.assertEqual(out["revised"], 1)
            row = mem.get_principles()[0]
            self.assertIn("标出依据", row["text"])
            hist = mem.principle_history(pid)
            self.assertEqual(len(hist), 1)
            self.assertEqual(hist[0]["op"], "revise")
            self.assertEqual(hist[0]["old_text"], "先分清事实再下判断")
        finally:
            mem.close()

    def test_any_write_path_leaves_a_trace(self):
        """界面/CLI 绕过自我模型直接调 M2，账也要看得见（两份真相的长期守卫）。"""
        mem = self._upgraded()
        try:
            pid = mem.get_principles()[0]["id"]
            # ① 模拟“本机界面”那条路：直接调 M2，不经过 self_model
            mem.set_principles([{"op": "revise", "id": pid, "text": "界面改的第一版"}],
                               "界面里当场填的理由")
            log = SM.revision_log(mem)
            self.assertEqual(len(log), 1, "绕过自我模型的改口也必须进账")
            self.assertEqual(log[0]["old_text"], "先分清事实再下判断")
            # ② 再走自我模型那条路
            SM.revise_principle(mem, pid, "自我模型改的第二版", "两条路都要进同一本账")
            self.assertEqual(len(SM.revision_log(mem)), 2)
        finally:
            mem.close()

    def test_add_is_not_a_change(self):
        mem = self._upgraded()
        try:
            mem.add_principle("新增一条原则，不是改口")
            self.assertEqual(SM.revision_log(mem), [],
                             "新增不是改口，不该进 revision_log")
            hist = mem.principle_history()
            self.assertEqual(hist[0]["op"], "add")
        finally:
            mem.close()

    def test_unchanged_text_is_not_logged(self):
        mem = self._upgraded()
        try:
            pid = mem.get_principles()[0]["id"]
            mem.set_principles([{"op": "revise", "id": pid, "text": "先分清事实再下判断"}],
                               "文本没变，不该造一笔假账")
            self.assertEqual(mem.principle_history(pid), [], "文本未变不得记账")
            self.assertEqual(SM.revision_log(mem), [])
        finally:
            mem.close()

    def test_retire_is_logged(self):
        mem = self._upgraded()
        try:
            pid = mem.get_principles()[0]["id"]
            mem.set_principles([{"op": "retire", "id": pid}], "这条原则不再适用")
            log = SM.revision_log(mem)
            self.assertEqual([x["kind"] for x in log], ["retire"])
            self.assertEqual(log[0]["reason"], "这条原则不再适用")
        finally:
            mem.close()

    def test_reason_required_no_partial_write(self):
        mem = self._upgraded()
        try:
            pid = mem.get_principles()[0]["id"]
            with self.assertRaises(ValueError):
                mem.set_principles([{"op": "revise", "id": pid, "text": "偷偷改"}], "")
            self.assertEqual(mem.get_principles()[0]["text"], "先分清事实再下判断")
            self.assertEqual(mem.principle_history(), [])
        finally:
            mem.close()

    def test_history_order_and_limit(self):
        mem = self._upgraded()
        try:
            pid = mem.get_principles()[0]["id"]
            for i in range(3):
                mem.set_principles([{"op": "revise", "id": pid,
                                     "text": "第 %d 版" % i}], "第 %d 次改口" % i)
            desc = mem.principle_history(pid, limit=2)
            self.assertEqual(len(desc), 2)
            self.assertEqual(desc[0]["new_text"], "第 2 版", "接口按时刻倒序")
            asc = SM.revision_log(mem)
            self.assertEqual([x["new_text"] for x in asc],
                             ["第 0 版", "第 1 版", "第 2 版"], "自我模型视图按升序")
        finally:
            mem.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
