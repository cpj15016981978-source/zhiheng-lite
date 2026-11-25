"""Phase5「记忆体补完」测试 —— M2 契约里列了但先前没做的五件事。

  1. 改口留痕：upsert_fact 覆盖旧值时，旧值必须进 fact_history（不然
     “你以前以为我做什么”答不上来，记忆就只剩当前快照）。
  2. 遗忘：forget_fact / purge 是正规接口，不再靠手改数据库。
  3. 原则可修订可作废：get_principles/set_principles（契约名），且
     revised_ts / revision_reason 两列【必须真被写】——空转的列等于没设计。
  4. 情绪状态底座：get_affect / set_affect（契约名，单行不重复）。
  5. 备份：backup() 拍出可独立打开的快照。

运行： python -m unittest discover -s tests
"""
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.memory import Memory
from pai import reflection as M4
from pai import memory as M2


class _TempDB(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "m.db")
        self.m = Memory(self.db)

    def tearDown(self):
        self.m.close()


class TestFactRevision(_TempDB):

    def test_changed_value_keeps_old_one(self):
        """改口：新值生效，旧值进历史——两头都在，才叫“记得自己曾经怎么想”。"""
        self.m.upsert_fact("用户", "职业", "床垫销售")
        self.m.upsert_fact("用户", "职业", "转行做运营")
        now = [f["object"] for f in self.m.all_facts() if f["predicate"] == "职业"]
        self.assertEqual(now, ["转行做运营"])
        hist = self.m.fact_history("用户", "职业")
        self.assertEqual([h["object"] for h in hist], ["床垫销售"])
        self.assertEqual(hist[0]["fact_id"], [f["id"] for f in self.m.all_facts()
                                              if f["predicate"] == "职业"][0])

    def test_same_value_written_twice_leaves_no_history(self):
        """重复说同一件事不是“改口”，不该制造假旧账。"""
        self.m.upsert_fact("用户", "姓名", "阿明")
        self.m.upsert_fact("用户", "姓名", "阿明")
        self.assertEqual(self.m.fact_history("用户", "姓名"), [])

    def test_history_survives_reopen(self):
        self.m.upsert_fact("用户", "职业", "A")
        self.m.upsert_fact("用户", "职业", "B")
        self.m.close()
        m2 = Memory(self.db)
        self.assertEqual([h["object"] for h in m2.fact_history("用户", "职业")], ["A"])
        m2.close()
        self.m = Memory(self.db)      # tearDown 还要用

    def test_history_filter_by_subject_only(self):
        self.m.upsert_fact("用户", "职业", "A")
        self.m.upsert_fact("用户", "职业", "B")
        self.m.upsert_fact("母亲", "行业", "X")
        self.m.upsert_fact("母亲", "行业", "Y")
        self.assertEqual(len(self.m.fact_history("母亲")), 1)
        self.assertEqual(self.m.fact_history("母亲")[0]["subject"], "母亲")


class TestForgetting(_TempDB):

    def test_forget_fact_removes_it_and_its_history(self):
        self.m.upsert_fact("用户", "职业", "A")
        fid = [f["id"] for f in self.m.all_facts()][0]
        self.m.upsert_fact("用户", "职业", "B")
        self.assertTrue(self.m.forget_fact(fid))
        self.assertEqual(self.m.all_facts(), [])
        self.assertEqual(self.m.fact_history(), [], "忘掉就该连旧账一起清")

    def test_forget_missing_fact_returns_false(self):
        self.assertFalse(self.m.forget_fact(9999))

    def test_purge_clears_episodes_and_facts(self):
        """正规清除接口：误贴进来的敏感内容不该只能靠手工 UPDATE 收拾。"""
        eid = self.m.write_episode("user", "我的密钥是 sk-abcdef123456")
        self.m.write_episode("user", "我爷爷1959年在挨饿")
        self.m.upsert_fact("用户", "备注", "sk-abcdef123456")
        n = self.m.purge(r"sk-[A-Za-z0-9]+")
        self.assertEqual((n["episodes"], n["facts"]), (1, 1))
        left = self.m.recent_episodes(50)
        self.assertEqual(len(left), 1)
        self.assertIn("爷爷", left[0]["content"])
        self.assertEqual(self.m.all_facts(), [])

    def test_purge_returns_zero_when_nothing_matches(self):
        self.m.write_episode("user", "今天天气好")
        n = self.m.purge(r"sk-\d+")
        self.assertEqual((n["episodes"], n["facts"], n["outbox"], n["triggers"],
                          n["affect_reset"], n["principles_deactivated"],
                          n["blanked_histories"], n["blanked_reasons"]),
                         (0, 0, 0, 0, 0, 0, 0, 0))

    def test_purge_bad_regex_raises_not_swallowed(self):
        self.m.write_episode("user", "abc")
        with self.assertRaises(re.error):
            self.m.purge("(未闭合")


class TestPrinciplesAreEditable(_TempDB):

    def _add(self, *texts):
        return [self.m.add_principle(t)[0] for t in texts]

    def test_contract_names_exist(self):
        """契约写的是 get_principles/set_principles，实现不能只留一个 active_principles。"""
        self.assertTrue(callable(self.m.get_principles))
        self.assertTrue(callable(self.m.set_principles))

    def test_get_principles_active_only(self):
        pid, _ = self.m.add_principle("谈历史要落到具体的人")
        self.m.set_principles([{"op": "retire", "id": pid}], "这条对我没用")
        self.assertEqual([p["text"] for p in self.m.get_principles()], [])
        self.assertEqual([p["text"] for p in self.m.get_principles(active_only=False)],
                         ["谈历史要落到具体的人"])

    def test_revise_writes_the_two_dead_columns(self):
        """revised_ts / revision_reason 自 Phase1 建表以来从没人写过——现在必须真写。"""
        pid = self._add("用户喜欢喝茶，可借此建立共鸣")[0]
        r = self.m.set_principles(
            [{"op": "revise", "id": pid, "text": "用户喜欢喝茶，但别每次都提"}],
            reason="太刻意")
        self.assertEqual(r["revised"], 1)
        p = self.m.get_principles()[0]
        self.assertEqual(p["text"], "用户喜欢喝茶，但别每次都提")
        self.assertTrue(p["revised_ts"], "修订时间必须落下")
        self.assertEqual(p["revision_reason"], "太刻意")

    def test_retire_records_reason(self):
        pid = self._add("要主动安慰用户")[0]
        self.m.set_principles([{"op": "retire", "id": pid}], "我不需要被安慰，要被当成人")
        p = self.m.get_principles(active_only=False)[0]
        self.assertEqual(p["active"], 0)
        self.assertEqual(p["revision_reason"], "我不需要被安慰，要被当成人")
        self.assertTrue(p["revised_ts"])

    def test_principle_history_is_the_single_ledger(self):
        """改口的账记在【写入口】里：任何调用方改原则都自动留痕。

        为什么要单独钉住：自我模型曾想在 meta 里另记一本，实测只要有人
        绕过它（CLI / 本机界面直接调 M2），旧话就丢了——两份真相。
        """
        pid = self._add("陪伴就是顺着对方说")[0]
        self.m.set_principles([{"op": "revise", "id": pid, "text": "陪伴不等于没有立场"}],
                              "顺着说不算陪伴")
        self.m.set_principles([{"op": "retire", "id": pid}], "这条已经不需要了")
        h = self.m.principle_history(pid)
        # 这张表是原则的完整流水（add/revise/retire），不只“改口”。
        # “新增不算改口”由自我模型按 kind 过滤，M2 不替它做决定。
        self.assertEqual([x["op"] for x in h], ["retire", "revise", "add"], "倒序：最近的在前")
        self.assertEqual(h[1]["old_text"], "陪伴就是顺着对方说")
        self.assertEqual(h[1]["new_text"], "陪伴不等于没有立场")
        self.assertEqual(h[0]["new_text"], None, "作废没有新文本")
        self.assertTrue(all(x["reason"] and x["ts"] for x in h))

    def test_principle_history_has_no_fake_entry(self):
        """与 upsert_fact 同一条纪律：文本没变就不记“改过”。"""
        pid = self._add("先分清事实再下判断")[0]
        self.m.set_principles([{"op": "revise", "id": pid, "text": "先分清事实再下判断"}],
                              "原样重写")
        self.assertEqual([x["op"] for x in self.m.principle_history()], ["add"],
                         "没改动就不许记 revise")

    def test_principle_history_survives_reopen(self):
        pid = self._add("要主动但不越界")[0]
        self.m.set_principles([{"op": "retire", "id": pid}], "主动过头了")
        self.m.close()
        self.m = Memory(self.db)
        self.assertEqual([x["op"] for x in self.m.principle_history(pid)],
                         ["retire", "add"])

    def test_reason_is_mandatory(self):
        """自我成长要留下“为什么改”；没有理由就不许动原则。"""
        pid = self._add("旧原则")[0]
        for bad in ("", "   "):
            with self.assertRaises(ValueError):
                self.m.set_principles([{"op": "retire", "id": pid}], bad)
        self.assertEqual(self.m.get_principles()[0]["active"], 1, "失败不得留下半个变更")

    def test_unknown_op_rejected(self):
        with self.assertRaises(ValueError):
            self.m.set_principles([{"op": "delete_forever", "id": 1}], "试试")

    def test_retired_principle_does_not_come_back_after_reflect(self):
        """最关键的一条回归：反思是确定性的，同批 fact 每次算出同一句话。
        若去重只看 active，用户作废的原则下轮就会原样复活——作废成空话。"""
        self.m.write_episode("user", "我喜欢喝茶")
        M4.reflect(self.m)
        before = {p["id"] for p in self.m.get_principles()}
        tea = [p for p in self.m.get_principles() if "喝茶" in p["text"]]
        self.assertTrue(tea, "反思应从‘我喜欢喝茶’提炼出共鸣原则")
        self.m.set_principles([{"op": "retire", "id": tea[0]["id"]}], "太刻意")
        again = M4.reflect(self.m)
        after = {p["id"] for p in self.m.get_principles()}
        self.assertEqual(again["new_principles"], 0, "重跑反思不该新增")
        self.assertNotIn(tea[0]["id"], after)
        self.assertFalse(any("喝茶" in p["text"] for p in self.m.get_principles()),
                         "作废的原则不得复活")
        self.assertTrue(before <= after | {tea[0]["id"]})


class TestAffectiveState(_TempDB):

    def test_get_affect_none_before_first_write(self):
        self.assertIsNone(self.m.get_affect())

    def test_set_then_read_back(self):
        self.m.set_affect(mood="低落", valence=-0.4, arousal=0.2, note="聊到家里的事")
        a = self.m.get_affect()
        self.assertEqual(a["mood"], "低落")
        self.assertAlmostEqual(a["valence"], -0.4)
        self.assertEqual(a["note"], "聊到家里的事")
        self.assertTrue(a["updated_ts"])

    def test_stays_single_row(self):
        """affective_state 用 CHECK(id=1) 锁单行；写入必须 UPSERT 而不是堆行。"""
        self.m.set_affect(mood="平静")
        self.m.set_affect(mood="烦躁")
        n = self.m.conn.execute("SELECT COUNT(*) c FROM affective_state").fetchone()["c"]
        self.assertEqual(n, 1)
        self.assertEqual(self.m.get_affect()["mood"], "烦躁")

    def test_survives_reopen(self):
        self.m.set_affect(mood="想念", valence=-0.2)
        self.m.close()
        m2 = Memory(self.db)
        self.assertEqual(m2.get_affect()["mood"], "想念")
        m2.close()
        self.m = Memory(self.db)


class TestBackup(_TempDB):

    def test_backup_is_independent_openable_snapshot(self):
        self.m.write_episode("user", "我爷爷1959年在挨饿")
        self.m.upsert_fact("用户", "家族记忆", "1959年挨饿")
        dest = Path(self.db).parent / "bk" / "memory-20261020.db"
        got = self.m.backup(str(dest))
        self.assertTrue(Path(got).exists())
        conn = sqlite3.connect(got)
        try:
            rows = conn.execute("SELECT content FROM episodes").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(conn.execute("SELECT object FROM facts").fetchone()[0],
                             "1959年挨饿")
        finally:
            conn.close()

    def test_existing_backup_is_not_overwritten(self):
        """备份是用户的唯一资产；同名覆盖等于把上一条退路烧掉。"""
        dest = Path(self.db).parent / "again.db"
        self.m.backup(str(dest))
        with self.assertRaises(ValueError):
            self.m.backup(str(dest))

    def test_backup_creates_missing_parent_dir(self):
        dest = Path(self.db).parent / "deep" / "nested" / "b.db"
        self.m.backup(str(dest))
        self.assertTrue(dest.exists())


class PurgeCoverageTest(_TempDB):
    """X3 v3：purge 覆盖全库文本列（ADR-034）。

    这组测试盯的不是“我改过的那几处”，而是“schema 里还漏了哪一列”：
    守卫测试用 PRAGMA 枚举所有 TEXT 列，没登记就红（失败信息只报表.列，不报内容）。
    """

    MARK = "sk-UNIT-7f3a"
    PAT = r"sk-UNIT-[0-9a-f]+"

    def _seed(self, table, col, marker):
        c = self.m.conn
        if table == "episodes":
            c.execute("INSERT INTO episodes(ts,role,content,session_id) VALUES(?,?,?,?)",
                      ("t", "user", marker if col == "content" else "干净内容", "s"))
        elif table == "facts":
            vals = {"subject": "用户", "predicate": "备注", "object": "干净值"}
            vals[col] = marker
            c.execute("INSERT INTO facts(subject,predicate,object,ts) VALUES(?,?,?,?)",
                      (vals["subject"], vals["predicate"], vals["object"], "t"))
        elif table == "outbox":
            c.execute("INSERT INTO outbox(ts,channel,message) VALUES(?,?,?)",
                      ("t", "cli", marker if col == "message" else "干净消息"))
        elif table == "triggers":
            vals = {"message_template": "干净模板", "justify_reason": "用户自己设的",
                    "spec": "daily"}
            vals[col] = marker
            c.execute("INSERT INTO triggers(type,spec,message_template,justify_reason,active)"
                      " VALUES(?,?,?,?,1)",
                      ("manual", vals["spec"], vals["message_template"], vals["justify_reason"]))
        elif table == "fact_history":
            vals = {"subject": "用户", "predicate": "备注", "object": "旧值"}
            vals[col] = marker
            c.execute("INSERT INTO fact_history(fact_id,subject,predicate,object,ts,replaced_ts)"
                      " VALUES(?,?,?,?,?,?)",
                      (1, vals["subject"], vals["predicate"], vals["object"], "t", "t2"))
        elif table == "principle_history":
            vals = {"old_text": "旧", "new_text": "新", "reason": "因为"}
            vals[col] = marker
            c.execute("INSERT INTO principle_history(principle_id,op,old_text,new_text,reason,ts)"
                      " VALUES(?,?,?,?,?,?)",
                      (1, "revise", vals["old_text"], vals["new_text"], vals["reason"], "t"))
        elif table == "principles":
            c.execute("INSERT INTO principles(text,created_ts,revision_reason,active)"
                      " VALUES(?,?,?,1)", ("干净原则", "t", marker))
        elif table == "world_entity":
            vals = {"name": "干净实体", "aliases": "[]"}
            vals[col] = marker
            c.execute("INSERT INTO world_entity(id,type,name,aliases,created_at,status)"
                      " VALUES(?,?,?,?,?,?)",
                      ("ent_%s" % col, "person", vals["name"], vals["aliases"], "t", "active"))
        elif table == "world_relation":
            c.execute("INSERT INTO world_relation(id,subject_id,predicate,object_value,observed_at,status)"
                      " VALUES(?,?,?,?,?,?)",
                      ("rel_%s" % col, "ent_1", "备注", marker, "t", "active"))
        elif table == "world_event":
            c.execute("INSERT INTO world_event(id,at,kind,summary,status)"
                      " VALUES(?,?,?,?,?)",
                      ("evt_%s" % col, "t", "note", marker, "active"))
        elif table == "world_belief":
            c.execute("INSERT INTO world_belief(id,holder,predicate,value,updated_at,status)"
                      " VALUES(?,?,?,?,?,?)",
                      ("bel_%s" % col, "user", "备注", marker, "t", "active"))
        elif table == "memory_item":
            vals = {"text": "干净", "summary": "", "tags": ""}
            vals[col] = marker
            c.execute("INSERT INTO memory_item(text,summary,tags,created_at) VALUES(?,?,?,?)",
                      (vals["text"], vals["summary"], vals["tags"], "t"))
        elif table == "memory_notebook":
            c.execute("INSERT INTO memory_notebook(session_id,updated_at,data) VALUES(?,?,?)",
                      ("s_%s" % col, "t", marker if col == "data" else "{}"))
        else:
            raise AssertionError("未支持的种子表：%s" % table)
        c.commit()

    def test_every_text_column_has_a_home(self):
        """守卫：全库每个 TEXT 列都必须在 _TEXT_HOSTS 或 _EXEMPT_TEXT 里。"""
        missing = []
        tables = [r[0] for r in self.m.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for t in tables:
            for row in self.m.conn.execute("PRAGMA table_info(%s)" % t):
                name, typ = row["name"], (row["type"] or "").upper()
                if typ != "TEXT":
                    continue
                in_hosts = any(t == tt and name in cols
                               for tt, cols, _action in M2._TEXT_HOSTS)
                if not in_hosts and (t, name) not in M2._EXEMPT_TEXT:
                    missing.append("%s.%s" % (t, name))
        self.assertEqual(missing, [], "未定性的文本列：%s" % ", ".join(missing))

    def test_each_delete_row_column_removes_the_whole_row(self):
        for table, cols, action in M2._TEXT_HOSTS:
            if action != "delete_row":
                continue
            for col in cols:
                with self.subTest(table=table, column=col):
                    self._seed(table, col, self.MARK)
                    self.m.purge(self.PAT)
                    n = self.m.conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
                    self.assertEqual(n, 0, "%s.%s 命中后整行应删除" % (table, col))

    def test_affect_note_resets_to_baseline(self):
        from pai.affect import baseline, read_affect
        self.m.set_affect("低落", -0.6, 0.4, '{"v": 1, "cues": ["%s"]}' % self.MARK)
        n = self.m.purge(self.PAT)
        self.assertEqual(n["affect_reset"], 1)
        st = read_affect(self.m)
        self.assertEqual(st["mood"], baseline()["mood"])
        self.assertAlmostEqual(st["arousal"], baseline()["arousal"], places=6)
        self.assertEqual(st["cues"], [])
        self.assertIsNone(self.m.get_affect()["note"])

    def test_each_blank_column_is_blanked_not_deleted(self):
        for table, cols, action in M2._TEXT_HOSTS:
            if action != "blank":
                continue
            for col in cols:
                with self.subTest(table=table, column=col):
                    self._seed(table, col, self.MARK)
                    before = self.m.conn.execute(
                        "SELECT COUNT(*) FROM %s" % table).fetchone()[0]
                    self.m.purge(self.PAT)
                    after = self.m.conn.execute(
                        "SELECT COUNT(*) FROM %s" % table).fetchone()[0]
                    self.assertEqual(before, after, "流水行不许删（删行=造假账）")
                    row = self.m.conn.execute(
                        "SELECT %s FROM %s ORDER BY id DESC LIMIT 1" % (col, table)).fetchone()
                    self.assertEqual(row[0], M2._BLANK,
                                     "%s.%s 应置占位文本" % (table, col))

    def test_principles_text_is_placeholder_and_inactive(self):
        pid, _ = self.m.add_principle("遇到 %s 先确认再复述" % self.MARK)
        n = self.m.purge(self.PAT)
        self.assertEqual(n["principles_deactivated"], 1)
        row = self.m.conn.execute(
            "SELECT text, active FROM principles WHERE id=?", (pid,)).fetchone()
        self.assertEqual(row["text"], M2._BLANK)
        self.assertEqual(row["active"], 0, "命中后必须停用，否则还会进 prompt")
        hist = self.m.principle_history(pid)
        self.assertGreaterEqual(len(hist), 2, "改口流水要留，不能删行")
        for h in hist:
            for k in ("old_text", "new_text", "reason"):
                self.assertNotIn(self.MARK, (h[k] or ""),
                                 "流水 %s.%s 仍含原文" % (h["op"], k))

    def test_return_dict_only_adds_keys(self):
        n = self.m.purge(self.PAT)
        self.assertEqual(set(n), {"episodes", "facts", "outbox", "triggers",
                                  "affect_reset", "principles_deactivated",
                                  "blanked_histories", "blanked_reasons",
                                  "world_entity", "world_relation",
                                  "world_event", "world_belief",
                                  "memory_item", "memory_notebook"})


if __name__ == "__main__":
    unittest.main()
