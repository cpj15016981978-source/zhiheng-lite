# -*- coding: utf-8 -*-
"""能力路由：说出来触发只读能力，不新增菜单。"""
import unittest
from unittest import mock

from pai import capability_router as C


class RouteTest(unittest.TestCase):
    def test_routes(self):
        self.assertEqual(C.route("查记忆上次说的")["name"], "memory")
        self.assertEqual(C.route("时间账本省了多少")["name"], "roi")
        self.assertEqual(C.route("今日简报")["name"], "brief")
        self.assertIsNone(C.route("今天天气不错"))

    def test_memory_dispatch(self):
        mem = mock.Mock()
        mem.recall.return_value = [{"item": {"type": "fact", "subject": "用户",
                                             "predicate": "姓名", "object": "大佬"}}]
        out = C.dispatch("查记忆 名字", mem=mem)
        self.assertIn("大佬", out["response"])
        self.assertEqual(out["trace"]["capability"], "memory")

    def test_unknown_returns_none(self):
        self.assertIsNone(C.dispatch("你好"))

    def test_ambiguous_asks(self):
        r = C.route("查记忆 再查时间账本")
        self.assertEqual(r["name"], "abstain")
        out = C.dispatch("查记忆 再查时间账本")
        self.assertIn("不确定", out["response"])

    def test_office_question_not_routed(self):
        self.assertIsNone(C.route("你能不能写PPT，做表格，写文档和操作其他软件"))

    def test_confidence_exposed(self):
        r = C.route("查记忆 名字")
        self.assertGreaterEqual(r["confidence"], C.MIN_CONFIDENCE)


if __name__ == "__main__":
    unittest.main()
