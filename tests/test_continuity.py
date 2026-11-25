# -*- coding: utf-8 -*-
"""Muse 连续性 v1 的登记语义。

钉住两条曾经跑偏的行为：
  1. save(thread_id=...) 必须复用同一条线，不能每次新建。
     auto_from_chat 传 "auto-<profile>"、semantic.py 传 "semantic-<profile>"，
     两者的意图都是"每个 profile 一条线，原地更新"。
  2. 已 close 的线，写入新的 next_step 必须重新打开。
     all_threads() 默认排除 status=="done"，不重开就让简报的【未完成】和
     due() 结构性为空——线明明还有下一步，却再也看不见。
"""
from __future__ import annotations

import pathlib
import tempfile
import unittest

from pai import continuity as CT


class ContinuityTest(unittest.TestCase):
    def setUp(self):
        self._old = CT.PATH
        self.tmp = pathlib.Path(tempfile.mkdtemp()) / "continuity.json"
        CT.PATH = self.tmp

    def tearDown(self):
        CT.PATH = self._old

    # ---------- 1. thread_id 必须被当作主键 ----------

    def test_same_thread_id_updates_one_record(self):
        CT.save(profile="work", title="A", next_step="n1", thread_id="fixed-1")
        CT.save(profile="work", title="B", next_step="n2", thread_id="fixed-1")
        items = CT.all_threads(profile="work", include_done=True)
        self.assertEqual(len(items), 1, "同一 thread_id 不该长出第二条线")
        self.assertEqual(items[0]["title"], "B")
        self.assertEqual(items[0]["next_step"], "n2")

    def test_auto_from_chat_does_not_accumulate(self):
        for i in range(3):
            CT.auto_from_chat("下一步做什么 %d" % i, "回答：下一步 %d" % i, profile="work")
        items = CT.all_threads(profile="work", include_done=True)
        self.assertEqual(len(items), 1, "auto_from_chat 反复触发只该有一条线")
        self.assertEqual(items[0]["id"], "auto-work")

    def test_semantic_stable_id_is_single_record(self):
        for i in range(2):
            CT.save(profile="work", title="语义 %d" % i, next_step="n%d" % i,
                    thread_id="semantic-work")
        items = CT.all_threads(profile="work", include_done=True)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "semantic-work")

    def test_without_thread_id_each_call_is_new(self):
        CT.save(profile="work", title="A", next_step="n1")
        CT.save(profile="work", title="B", next_step="n2")
        self.assertEqual(len(CT.all_threads(profile="work", include_done=True)), 2,
                         "不传 thread_id 时仍该各开一条，别把 uuid 路径改坏")

    # ---------- 2. 有新动作就要重开 ----------

    def test_reopen_on_new_next_step(self):
        rec = CT.save(profile="work", title="A", next_step="n1", thread_id="t1")
        CT.close(profile="work", thread_id="t1")
        self.assertEqual(CT.all_threads(profile="work"), [])
        CT.save(profile="work", title="A2", next_step="n2", thread_id="t1")
        live = CT.all_threads(profile="work")
        self.assertEqual(len(live), 1, "写了新的下一步，这条线必须重新可见")
        self.assertEqual(live[0]["status"], "open")

    def test_close_still_hides_the_thread(self):
        """反向护栏：重开逻辑不能把 close 变成空操作。"""
        CT.save(profile="work", title="A", next_step="n1", thread_id="t2", due="2020-01-01T00:00:00")
        self.assertEqual(len(CT.due(profile="work")), 1)
        CT.close(profile="work", thread_id="t2")
        self.assertEqual(CT.due(profile="work"), [])
        self.assertEqual(CT.all_threads(profile="work"), [])

    # ---------- 3. 原有契约不动 ----------

    def test_requires_next_step(self):
        with self.assertRaises(ValueError):
            CT.save(profile="work", title="A", next_step="")

    def test_requires_title(self):
        with self.assertRaises(ValueError):
            CT.save(profile="work", title="", next_step="n1")


if __name__ == "__main__":
    unittest.main()
