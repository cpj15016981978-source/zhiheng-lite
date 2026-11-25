# -*- coding: utf-8 -*-
"""SC-1 关态【逐字相同】回归 —— 守门人自己写的第三条腿（ADR-032 第一问）。

为什么单独有这个文件：退回信第七节 (c) 点名的那条测试，二版交付里没有——
他的 wiring 只断"原稿被包含在回应里"，那挡不住"前后各多了一句"这类改动。
基准不是谁的记忆，是【未打补丁的 343 脊柱】跑出来的三份留底（golden 逐字抄在下面）。
基准留底方式：三条原文已【逐字抄进下面的 GOLDEN_343】，不依赖任何外部副本——
        当初是在一份 343 脊柱的临时拷贝上跑的（守门人一次性脚本，已删）。

运行：python -m unittest tests.test_self_review_off_golden -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.memory import Memory                 # noqa: E402
from pai.orchestrator import Orchestrator     # noqa: E402

# (用户输入, mock 草稿, 343 脊柱当时的逐字回应)
GOLDEN_343 = [
    ("我妈妈走了，半年了，我还是没法正常工作。", "我能感觉到你这一年真的很累。",
     "(mock)\n我记得这些相关的（1 条）：\n  · 你之前说过：“我妈妈走了，半年了，我还是没法正常工作。”"
     "\n我的回应：我能感觉到你这一年真的很累。"),
    ("我该怎么做？", "你一定会好起来的。",
     "(mock)\n我记得这些相关的（1 条）：\n  · 你之前说过：“我该怎么做？”"
     "\n我的回应：你一定会好起来的。"),
    ("今天天气不错。", "是的，适合出去走走。",
     "(mock)\n我记得这些相关的（1 条）：\n  · 你之前说过：“今天天气不错。”"
     "\n我的回应：是的，适合出去走走。"),
]


class OffStateIsByteIdentical(unittest.TestCase):
    """开关关掉 = 一个字都不改（Boss 2026-10-20 23:28 定调，ADR-032）。"""

    def _run(self, user, draft):
        def fake(prompt, models=None, opts=None):
            return [{"model_id": "mock", "output": draft, "cost": 0.0,
                     "tokens": 5, "priced": True}]
        mem = Memory(os.path.join(tempfile.mkdtemp(), "g.db"))
        try:
            with mock.patch.dict(os.environ, {"PAI_SELF_REVIEW": "0"}, clear=False), \
                    mock.patch("pai.model_gateway.complete", side_effect=fake):
                return Orchestrator(mem).handle(user)
        finally:
            mem.close()

    def test_off_state_matches_the_343_spine_verbatim(self):
        for user, draft, golden in GOLDEN_343:
            with self.subTest(user=user[:12]):
                res = self._run(user, draft)
                self.assertEqual(res["response"], golden,
                                 "开关关着却和 343 脊柱的输出不一样——ADR-032 的红线")

    def test_boundary_hit_when_off_is_recorded_not_applied(self):
        """命中越界词时：关掉不许换句子，但要把发现如实放进 trace 给人看。"""
        res = self._run(*GOLDEN_343[0][:2])
        sr = res["trace"]["self_review"]
        self.assertTrue(sr["issues"], "这句草稿命中了词表，trace 里该看得见")
        self.assertEqual([i["code"] for i in sr["issues"]], ["boundary.consciousness"])
        self.assertFalse(sr["applied"], "关掉时 applied 必须是 False")
        self.assertFalse(sr["refused"], "关掉时不许换成固定拒绝话")
        self.assertEqual(sr["calls"], 0, "关掉时一次调用都不许多花")

    def test_switch_off_is_the_default_when_env_absent(self):
        """环境变量【没设】时必须等于关掉。默认开=违反契约①，也违反 Boss 的保守默认。"""
        user, draft, golden = GOLDEN_343[0]

        def fake(prompt, models=None, opts=None):
            return [{"model_id": "mock", "output": draft, "cost": 0.0,
                     "tokens": 5, "priced": True}]
        mem = Memory(os.path.join(tempfile.mkdtemp(), "g.db"))
        try:
            env = {k: v for k, v in os.environ.items() if k != "PAI_SELF_REVIEW"}
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch("pai.model_gateway.complete", side_effect=fake):
                res = Orchestrator(mem).handle(user)
            self.assertEqual(res["response"], golden, "不设变量就默认开口=契约①失效")
        finally:
            mem.close()

    def test_proactive_path_when_off_stays_silent(self):
        """M14 接主动推送：voice 关 = 不出声、落库照旧。
        ★这条必须真打 orch.proactive() 那条缝——我第一版写成直接 push_outbox，
        等于绕开被测代码自己承诺自己，那种测试永远绿、没有价值。"""
        from pai import voice as M14
        trig = {"id": 1, "type": "reminder", "message_template": "该喝水了",
                "justify_reason": "你上次说忘了喝水"}
        mem = Memory(os.path.join(tempfile.mkdtemp(), "v.db"))
        mem.upsert_fact("用户", "姓名", "阿明")
        try:
            orch = Orchestrator(mem)
            real = M14.speak
            with mock.patch.dict(os.environ, {}, clear=False), \
                    mock.patch.object(M14, "speak", side_effect=real) as sp:
                os.environ.pop("PAI_VOICE_BACKEND", None)
                res = orch.proactive(trig)
            sp.assert_called_once()
            self.assertFalse(res["voice"]["spoken"], "默认关却念出声=契约①失效")
            self.assertEqual(len(mem.read_outbox()), 1, "关掉声音不影响落库")
            self.assertIn("阿明，该喝水了", res["message"])
        finally:
            mem.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
