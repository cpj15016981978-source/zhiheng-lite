# -*- coding: utf-8 -*-
"""M1 编排层的策略测试（不是功能测试，是“该怎么做”的约束）。

覆盖评审报告里的两条 P2：
  1. 文化检索只吃用户原话，不吃 principles——防止“越用越偏”的自我确认回路。
  2. register_trigger 的失败必须抛可区分的原因（不是静默返回 None）。

运行：
    python -m unittest tests.test_orchestrator_policy -v
"""

import datetime
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
WIKI_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "wiki"

from pai.memory import Memory
from pai.orchestrator import Orchestrator
from pai import scheduler as M6
from pai import cultural_kb as M9
from pai import model_gateway as M10
from pai import memory_brain as MB


class PolicyBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, "policy.db")
        self.mem = Memory(self.db)
        self.orch = Orchestrator(self.mem)

    def tearDown(self):
        self.mem.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class MemoryBrainTurnPolicy(PolicyBase):
    def test_memory_context_reaches_prompt(self):
        os.environ["PAI_MEMORY_BRAIN"] = "on"
        self.addCleanup(os.environ.pop, "PAI_MEMORY_BRAIN", None)
        b = MB.MemoryBrain(self.mem)
        b.remember("当前主线是吹雪 Desktop", subject="助手", predicate="当前主线",
                   source="test", importance=1.0, confidence=1.0)
        b.update_notebook(self.orch.session_id, goal="C1 收口")
        captured = {}
        original = M10.complete

        def spy(prompt, *args, **kwargs):
            captured["prompt"] = prompt
            return original(prompt, *args, **kwargs)

        M10.complete = spy
        self.addCleanup(setattr, M10, "complete", original)
        out = self.orch.handle("当前主线是什么")
        self.assertIn("[记忆档案（带来源）]", captured["prompt"])
        self.assertIn("当前主线是吹雪 Desktop", captured["prompt"])
        self.assertIn("[记事本]", captured["prompt"])
        self.assertIn("memory", out["trace"])


class EphemeralTurnPolicy(PolicyBase):
    def test_ephemeral_does_not_write_episodes(self):
        before = self.mem.recent_episodes(200)
        out = self.orch.handle("游戏代玩：选 a", ephemeral=True)
        after = self.mem.recent_episodes(200)
        self.assertEqual(len(before), len(after))
        self.assertTrue(out.get("response"))
        self.assertFalse(any(e.get("content") == "游戏代玩：选 a" for e in after))

    def test_normal_turn_still_writes_episodes(self):
        before = len(self.mem.recent_episodes(200))
        self.orch.handle("普通对话：记住这句")
        after = len(self.mem.recent_episodes(200))
        self.assertGreaterEqual(after, before + 2)


class WikiTurnPolicy(PolicyBase):
    def test_wiki_context_reaches_prompt(self):
        os.environ["PAI_WIKI"] = "on"
        os.environ["PAI_WIKI_DIR"] = str(WIKI_FIXTURE)
        self.addCleanup(os.environ.pop, "PAI_WIKI", None)
        self.addCleanup(os.environ.pop, "PAI_WIKI_DIR", None)
        captured = {}
        original = M10.complete

        def spy(prompt, *args, **kwargs):
            captured["prompt"] = prompt
            return original(prompt, *args, **kwargs)

        M10.complete = spy
        self.addCleanup(setattr, M10, "complete", original)
        out = self.orch.handle("C1 判分尺现在是什么状态")
        self.assertIn("[Wiki 知识（带来源）]", captured["prompt"])
        self.assertIn("wiki:c1", captured["prompt"])
        self.assertIn("wiki", out["trace"])


class RouterTurnPolicy(PolicyBase):
    def test_router_context_reaches_prompt(self):
        for k in ("PAI_ROUTER", "PAI_MEMORY_BRAIN", "PAI_WIKI", "PAI_SKILLS", "PAI_CODE_GRAPH"):
            os.environ[k] = "on"
            self.addCleanup(os.environ.pop, k, None)
        os.environ["PAI_WIKI_DIR"] = str(WIKI_FIXTURE)
        self.addCleanup(os.environ.pop, "PAI_WIKI_DIR", None)
        captured = {}
        original = M10.complete

        def spy(prompt, *args, **kwargs):
            captured["prompt"] = prompt
            return original(prompt, *args, **kwargs)

        M10.complete = spy
        self.addCleanup(setattr, M10, "complete", original)
        out = self.orch.handle("C1 协议是什么")
        self.assertIn("[Router 检索（带来源）]", captured["prompt"])
        self.assertIn("router", out["trace"])


class CultureRetrievalPolicy(PolicyBase):
    """文化检索的输入边界。"""

    def _spy_culture(self):
        captured = {}
        original = M9.retrieve_culture

        def spy(query, context=None, **kwargs):
            captured["query"] = query
            captured["context"] = context
            return original(query, context, **kwargs)

        M9.retrieve_culture = spy
        self.addCleanup(setattr, M9, "retrieve_culture", original)
        return captured

    def test_culture_retrieval_receives_no_principles(self):
        """核心断言：原则绝不能进入文化检索的上下文。

        原则是系统自己写下的立场。若让它参与“找哪条古籍”，检索会越来越偏向
        自己已有的看法——不是学错，是自我确认，这比学错更难发现。
        """
        self.mem.add_principle("凡事反求诸己")
        captured = self._spy_culture()

        self.orch.handle("最近和朋友闹了矛盾")

        self.assertIsNone(captured.get("context"),
                          "文化检索不应收到 principles 或任何系统自写的上下文")
        self.assertEqual(captured.get("query"), "最近和朋友闹了矛盾",
                         "应只传用户原话")

    def test_principles_still_reach_the_prompt(self):
        """去掉的只是检索通道；原则仍必须进 prompt（两条通道分开）。"""
        self.mem.add_principle("不把猜测当事实")
        captured = {}

        original = M10.complete

        def spy(prompt, *args, **kwargs):
            captured["prompt"] = prompt
            return original(prompt, *args, **kwargs)

        M10.complete = spy
        self.addCleanup(setattr, M10, "complete", original)

        self.orch.handle("帮我分析一下这件事")

        prompt = captured.get("prompt", "")
        self.assertIn("我恪守的原则", prompt)
        self.assertIn("不把猜测当事实", prompt)

    def test_grief_query_still_surfaces_culture(self):
        """去掉 principles 之后，丧亲语境仍应召回文化条目——不能修过头。"""
        res = self.orch.handle("亲人去世了，我一直走不出来")
        refs = res["trace"]["culture_refs"]
        self.assertGreater(len(refs), 0, "丧亲语境应召回生死类条目")
        for item in refs:
            self.assertEqual(set(item.keys()), {"ref", "text", "weight"})

    def test_irrelevant_query_still_gets_nothing(self):
        """无关寒暄不引典——修复不能把“宁缺”改成“硬塞”。"""
        res = self.orch.handle("今天天气不错")
        self.assertEqual(res["trace"]["culture_refs"], [])


class TriggerFailurePolicy(PolicyBase):
    """register_trigger 的失败必须可区分。"""

    def test_empty_reason_raises_with_message(self):
        with self.assertRaises(ValueError) as ctx:
            M6.register_trigger(
                self.mem, "date", "提醒", "   ",
                {"kind": "weekly", "weekday": 1, "time": "09:00"},
            )
        self.assertIn("justify_reason", str(ctx.exception))

    def test_unknown_kind_raises_with_allowed_list(self):
        with self.assertRaises(ValueError) as ctx:
            M6.register_trigger(
                self.mem, "date", "提醒", "正当理由", {"kind": "nope"},
            )
        msg = str(ctx.exception)
        self.assertIn("nope", msg)
        self.assertIn("annual", msg, "错误信息应列出可用的 kind，便于调用方自纠")

    def test_unknown_type_raises(self):
        with self.assertRaises(ValueError) as ctx:
            M6.register_trigger(self.mem, "bogus", "提醒", "正当理由", None)
        self.assertIn("bogus", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
