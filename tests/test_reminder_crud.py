# -*- coding: utf-8 -*-
"""提醒 CRUD 走统一命令入口。"""
import datetime
import pathlib
import tempfile
import unittest
from unittest import mock

from pai import unified as U
from pai.memory import Memory


class ReminderCrudTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()) / "tasks.db"
        self.patcher = mock.patch.object(U, "DB", self.tmp)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_due_fires_once_and_writes_outbox(self):
        now = datetime.datetime.now()
        t = (now - datetime.timedelta(minutes=1)).strftime("%H:%M")
        U.add_task(t, "喝水")
        mem = Memory(str(self.tmp.with_suffix(".mem.db")))
        self.assertEqual(len(U.fire_due(mem, now)), 1)
        self.assertEqual(len(U.fire_due(mem, now)), 0)
        self.assertEqual(len(mem.read_outbox(unread_only=True)), 1)
        mem.close()

    def test_add_list_update_delete(self):
        self.assertIn("已加提醒", U.handle("提醒：21:30 喝水"))
        self.assertIn("#1", U.handle("提醒"))
        self.assertIn("已更新", U.handle("提醒：改 1 22:00 喝温水"))
        self.assertIn("22:00", U.handle("提醒"))
        self.assertIn("已删除", U.handle("提醒：删 1"))
        self.assertNotIn("#1", U.handle("提醒"))


if __name__ == "__main__":
    unittest.main()
