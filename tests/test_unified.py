# -*- coding: utf-8 -*-
"""统一入口：/task、/tasks、/search、代码块落盘。"""
import pathlib
import tempfile
import unittest
from unittest import mock

from pai import unified as U


class UnifiedTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.p = mock.patch.object(U, "DB", pathlib.Path(self.td.name) / "tasks.db")
        self.q = mock.patch.object(U, "SCRIPTS", pathlib.Path(self.td.name) / "scripts")
        self.p.start(); self.q.start()

    def tearDown(self):
        self.q.stop(); self.p.stop(); self.td.cleanup()

    def test_task_add_list(self):
        self.assertIn("已加提醒", U.handle("/task 21:30 提醒我喝水"))
        self.assertIn("提醒我喝水", U.handle("/tasks"))

    def test_search(self):
        mem = mock.Mock()
        mem.recall.return_value = [{"item": {"type": "episode", "ts": "2026-11-20", "content": "查到了"}}]
        self.assertIn("查到了", U.handle("/search 喝水", mem=mem))

    def test_code_saved_not_run(self):
        saved = U.save_code_blocks("```python\ndef add(a,b):\n    return a+b\n```")
        self.assertEqual(len(saved), 1)
        self.assertIn("return a+b", pathlib.Path(saved[0]).read_text(encoding="utf-8"))

    def test_due_and_mark(self):
        U.add_task("00:00", "x")
        rows = U.due(now=__import__("datetime").datetime(2026, 11, 20, 12, 0))
        self.assertTrue(rows)
        U.mark_done(rows[0][0], when=__import__("datetime").datetime(2026, 11, 20, 12, 0))
        self.assertFalse(U.due(now=__import__("datetime").datetime(2026, 11, 20, 12, 1)))


if __name__ == "__main__":
    unittest.main()
