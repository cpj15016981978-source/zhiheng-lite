# -*- coding: utf-8 -*-
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai.memory import Memory  # noqa: E402
from pai import router as R  # noqa: E402
from pai import memory_brain as MB  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


class RouterTest(unittest.TestCase):
    def setUp(self):
        for k in ("PAI_ROUTER", "PAI_MEMORY_BRAIN", "PAI_WIKI", "PAI_SKILLS", "PAI_CODE_GRAPH"):
            os.environ[k] = "on"
        self.mem = Memory(str(pathlib.Path(tempfile.mkdtemp()) / "m.db"))
        b = MB.MemoryBrain(self.mem)
        b.remember("当前主线是吹雪 Desktop", subject="助手", predicate="当前主线",
                   source="test", importance=1.0, confidence=1.0)
        self.r = R.Router(self.mem, code_db=str(ROOT / "data" / "code_graph.db"))

    def tearDown(self):
        self.mem.close()
        for k in ("PAI_ROUTER", "PAI_MEMORY_BRAIN", "PAI_WIKI", "PAI_SKILLS", "PAI_CODE_GRAPH"):
            os.environ.pop(k, None)

    def test_plan_chat(self):
        self.assertIn("chat", self.r.plan("当前主线是什么"))

    def test_plan_wiki(self):
        self.assertIn("wiki", self.r.plan("C1 协议是什么"))

    def test_plan_skill(self):
        self.assertIn("skill", self.r.plan("工厂日报怎么做"))

    def test_plan_code(self):
        self.assertIn("code", self.r.plan("谁调用 recall 这个函数"))

    def test_route_has_citations(self):
        out = self.r.route("当前主线是什么")
        self.assertTrue(out["sources"])
        self.assertIn("[chat]", out["context"])

    def test_off_returns_empty(self):
        os.environ["PAI_ROUTER"] = "off"
        out = self.r.route("当前主线")
        self.assertEqual(out["context"], "")
        self.assertEqual(out["sources"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
