# -*- coding: utf-8 -*-
import pathlib
import tempfile
import unittest

from pai import conversation_store as CS


class ConversationStoreTest(unittest.TestCase):
    def setUp(self):
        self.db = pathlib.Path(tempfile.mkdtemp()) / "conversations.db"
        self.s = CS.ConversationStore(self.db)

    def tearDown(self):
        self.s.close()

    def test_roundtrip(self):
        data = {"active": "a", "updatedAt": 123,
                "sessions": [{"id": "a", "title": "你好", "messages": []}]}
        self.s.save("work", data)
        got = self.s.load("work")
        self.assertEqual(got["active"], "a")
        self.assertEqual(got["sessions"][0]["title"], "你好")

    def test_profiles_isolated(self):
        self.s.save("work", {"sessions": [{"id": "w"}]})
        self.s.save("side", {"sessions": [{"id": "s"}]})
        self.assertEqual(self.s.load("work")["sessions"][0]["id"], "w")
        self.assertEqual(self.s.load("side")["sessions"][0]["id"], "s")

    def test_rejects_bad_sessions(self):
        with self.assertRaises(ValueError):
            self.s.save("work", {"sessions": "not-list"})

    def test_append_message_updates_log_and_snapshot(self):
        self.s.append_message("work", "c1", "新对话",
                              {"role": "user", "content": "你好"})
        self.s.append_message("work", "c1", "新对话",
                              {"role": "ai", "content": "在的", "reasoning": "想"})
        got = self.s.load("work")
        self.assertEqual([m["content"] for m in got["sessions"][0]["messages"]],
                         ["你好", "在的"])
        n = self.s.conn.execute(
            "select count(*) from conversation_messages where profile='work'").fetchone()[0]
        self.assertEqual(n, 2)

    def test_append_rejects_bad_role(self):
        with self.assertRaises(ValueError):
            self.s.append_message("work", "c1", "x", {"role": "system", "content": "x"})

    def test_reads_from_messages_table(self):
        self.s.append_message("work", "c1", "标题",
                              {"role": "user", "content": "问"})
        self.s.append_message("work", "c1", "标题",
                              {"role": "ai", "content": "答"})
        got = self.s.load_from_messages("work")
        self.assertEqual([m["content"] for m in got["sessions"][0]["messages"]],
                         ["问", "答"])

    def test_replace_and_delete(self):
        self.s.append_message("work", "c1", "标题",
                              {"role": "user", "content": "旧"})
        self.s.replace_messages("work", "c1", "标题",
                                [{"role": "user", "content": "新"}])
        got = self.s.load_from_messages("work")
        self.assertEqual(got["sessions"][0]["messages"][0]["content"], "新")
        self.s.delete_conversation("work", "c1")
        self.assertEqual(self.s.load_from_messages("work")["sessions"], [])

    def test_migrates_existing_snapshot(self):
        self.s.save("work", {"active": "c1", "updatedAt": 1,
                             "sessions": [{"id": "c1", "title": "旧快照",
                                           "messages": [{"role": "user", "content": "嗨"}]}]})
        got = self.s.load_from_messages("work")
        self.assertEqual(got["sessions"][0]["messages"][0]["content"], "嗨")

    def test_meta_pin_persists_and_sorts_first(self):
        self.s.append_message("work", "a", "甲", {"role": "user", "content": "a"})
        self.s.append_message("work", "b", "乙", {"role": "user", "content": "b"})
        self.s.update_meta("work", "b", pinned=True)
        got = self.s.load_from_messages("work")
        self.assertEqual(got["sessions"][0]["id"], "b")
        self.assertTrue(got["sessions"][0]["pinned"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
