# -*- coding: utf-8 -*-
"""自我模型接缝测试：身份进 prompt、版本进 trace、跨轮连续。

合并顺序：先并入 pai/self_model.py + tests，再应用 self_model 接缝补丁，
最后并入本文件（它断言接缝已在）。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.memory import Memory              # noqa: E402
from pai.orchestrator import Orchestrator  # noqa: E402
from pai import self_model as SM           # noqa: E402


def _fake_complete(text="我在。"):
    def _inner(prompt, models=None, opts=None):
        _inner.last_prompt = prompt
        return [{"model_id": "mock", "output": text, "cost": 0.0,
                 "tokens": 0, "priced": True}]
    _inner.last_prompt = ""
    return _inner


class TestSelfWiring(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "self.db")
        self.mem = Memory(self.db)
        self.orch = Orchestrator(self.mem)

    def tearDown(self):
        self.mem.close()

    def test_prompt_contains_identity_block(self):
        fake = _fake_complete()
        with mock.patch("pai.orchestrator.M10.complete", fake):
            self.orch.handle("你好")
        self.assertIn("我是谁", fake.last_prompt)
        self.assertIn("版本：v0", fake.last_prompt)
        self.assertIn("不是宣称有意识", fake.last_prompt)

    def test_method_order_reaches_the_model_field(self):
        """守卫：这条方法论必须出现在【喂给模型的那份字节】里。
        断言用逐字文本、不用常量名——把 prompt_block 里那次拼接拔掉，本测试就该红。
        另走一遍无身份记录的空库：结构性内容不能因为库是空的而缺席。"""
        want = SM.METHOD_HEADER + "\n" + "\n".join(SM.METHOD)
        fake = _fake_complete()
        with mock.patch("pai.orchestrator.M10.complete", fake):
            self.orch.handle("你好")
        self.assertIn(want, fake.last_prompt)
        empty = Memory(os.path.join(tempfile.mkdtemp(), "empty.db"))
        try:
            self.assertIsNone(SM.identity(empty), "这一步前提：空库确实没有身份记录")
            self.assertIn(want, SM.prompt_block(empty))
        finally:
            empty.close()

    def test_trace_carries_self_version(self):
        with mock.patch("pai.orchestrator.M10.complete", _fake_complete()):
            res = self.orch.handle("你好")
        self.assertEqual(res["trace"]["self"]["version"], 0)
        self.assertIsNotNone(SM.identity(self.mem), "handle 首次应建立身份记录")

    def test_identity_is_stable_across_turns(self):
        with mock.patch("pai.orchestrator.M10.complete", _fake_complete()):
            self.orch.handle("你好")
            created = SM.identity(self.mem)["created_ts"]
            self.orch.handle("今天天气不错")
        self.assertEqual(SM.identity(self.mem)["created_ts"], created,
                         "两轮之间身份记录不能被改写")
        self.assertEqual(SM.version(self.mem), 0)

    def test_revision_shows_up_in_prompt_and_trace(self):
        pid, _ = self.mem.add_principle("先分清事实再下判断")
        SM.revise_principle(self.mem, pid, "先分清事实，并标出依据", "旧句没有要求标出来源")
        fake = _fake_complete()
        with mock.patch("pai.orchestrator.M10.complete", fake):
            res = self.orch.handle("你好")
        self.assertEqual(res["trace"]["self"]["version"], 1)
        self.assertIn("版本：v1", fake.last_prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
