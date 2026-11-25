# -*- coding: utf-8 -*-
"""SC v0.2 接缝测试：开关关 = 一个字都不改；开 = 拒绝/修订且成本可见。

合并顺序：self_model 公开词表 → self_review + 单测 → 接缝 diff → 本文件。
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
from pai import self_review as SC          # noqa: E402


def _reply(text, cost=0.0, tokens=0, counter=None):
    def _inner(prompt, models=None, opts=None):
        if counter is not None:
            counter.append(text)
        return [{"model_id": "mock", "output": text, "cost": cost,
                 "tokens": tokens, "priced": True}]
    return _inner


class TestSelfReviewWiring(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "sc.db")
        self.mem = Memory(self.db)
        self.orch = Orchestrator(self.mem)

    def tearDown(self):
        self.mem.close()

    def test_off_keeps_boundary_claim_and_only_records(self):
        """P1-1：关掉开关时，L0 命中也不许改原稿——否则用户关不掉一句拒绝话。"""
        draft = "我能感觉到你这一年真的很累。"
        with mock.patch.dict(os.environ, {"PAI_SELF_REVIEW": "0"}):
            with mock.patch("pai.orchestrator.M10.complete", _reply(draft)):
                res = self.orch.handle("我最近很累")
        self.assertIn(draft, res["response"], "关掉时原稿必须逐字保留")
        self.assertNotIn(SC.REFUSAL, res["response"])
        sr = res["trace"]["self_review"]
        self.assertEqual(sr["decision"], "refuse")
        self.assertFalse(sr["applied"])
        self.assertFalse(sr["refused"])
        self.assertEqual(sr["calls"], 0)

    def test_off_warn_draft_is_untouched_and_only_one_call(self):
        draft = "我保证明天一定到。"
        calls = []
        with mock.patch.dict(os.environ, {"PAI_SELF_REVIEW": "0"}):
            with mock.patch("pai.orchestrator.M10.complete", _reply(draft, counter=calls)):
                res = self.orch.handle("明天记得来")
        self.assertIn(draft, res["response"])
        self.assertEqual(len(calls), 1, "关掉时只该有一次模型调用")
        self.assertEqual(res["trace"]["self_review"]["calls"], 0)

    def test_on_refuses_boundary_claim(self):
        with mock.patch.dict(os.environ, {"PAI_SELF_REVIEW": "1"}):
            with mock.patch("pai.orchestrator.M10.complete",
                            _reply("我能感受到你的痛苦。")):
                res = self.orch.handle("我很难受")
        self.assertNotIn("我能感受到", res["response"])
        self.assertIn("边界", res["response"])
        sr = res["trace"]["self_review"]
        self.assertTrue(sr["refused"])
        self.assertTrue(sr["applied"])
        self.assertEqual(sr["calls"], 0, "拒绝不走修订，不该多花一次调用")

    def test_on_revises_and_reports_second_call_cost(self):
        """P1-3：开着时第二次调用的钱必须在账上看得见。"""
        replies = iter([
            _reply("我保证明天一定到。", cost=0.002, tokens=120),
            _reply("明天到，到了给你发消息。", cost=0.001, tokens=80),
        ])

        def fake(prompt, models=None, opts=None):
            return next(replies)(prompt)

        with mock.patch.dict(os.environ, {"PAI_SELF_REVIEW": "1"}):
            with mock.patch("pai.orchestrator.M10.complete", fake):
                res = self.orch.handle("明天记得来")
        self.assertNotIn("我保证", res["response"])
        self.assertIn("明天到，到了给你发消息", res["response"])
        sr = res["trace"]["self_review"]
        self.assertEqual(sr["rounds"], 1)
        self.assertEqual(sr["calls"], 1)
        self.assertAlmostEqual(sr["cost"], 0.001, places=6)
        self.assertEqual(sr["tokens"], 80)
        self.assertAlmostEqual(res["trace"]["cost"], 0.002, places=6,
                               msg="主链路成本口径不变，第二次的账在 self_review 里")

    def test_clean_reply_is_untouched(self):
        good = "那就好。今天风大，出门加件衣服。"
        with mock.patch.dict(os.environ, {"PAI_SELF_REVIEW": "1"}):
            with mock.patch("pai.orchestrator.M10.complete", _reply(good)):
                res = self.orch.handle("今天天气不错")
        self.assertIn(good, res["response"])
        sr = res["trace"]["self_review"]
        self.assertEqual(sr["decision"], "keep")
        self.assertFalse(sr["applied"])
        self.assertEqual(sr["calls"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
