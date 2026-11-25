# -*- coding: utf-8 -*-
"""M3 Step 1 · 两模型互审（不综合、不裁判，只让分歧可见）

要钉住的三件事：
  1. 默认行为完全不变——不设 council 就还是单模型一次调用。
  2. 议会模式下两份答案都进 trace，且 response 里能看到"另一视角"。
  3. 单个模型失败不影响另一个；分歧用可解释的字符二元相似度标注，
     并且**明确它是提示不是判决**。

运行：python -m unittest tests.test_council_review -v
"""

import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.memory import Memory
from pai.orchestrator import Orchestrator, _divergence


class CouncilBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mem = Memory(os.path.join(self.tmpdir, "c.db"))
        self.orch = Orchestrator(self.mem)

    def tearDown(self):
        self.mem.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class DefaultBehaviourTest(CouncilBase):

    def test_no_council_keeps_single_model(self):
        """不启用议会时，行为必须和以前一模一样。"""
        res = self.orch.handle("你好")
        trace = res["trace"]
        self.assertEqual(len(trace["models"]), 1)
        self.assertEqual(len(trace["proposals"]), 1)
        self.assertIsNone(trace["divergence"], "单模型不该有分歧度")
        self.assertEqual(trace["council_failures"], [])

    def test_env_with_single_model_is_ignored(self):
        """只配一个模型 = 没议会，不该白跑一次。"""
        with mock.patch.dict(os.environ, {"PAI_COUNCIL_MODELS": "mock"}, clear=False):
            res = self.orch.handle("你好")
        self.assertEqual(len(res["trace"]["models"]), 1)
        self.assertIsNone(res["trace"]["divergence"])

    def test_env_with_two_models_enables_council(self):
        with mock.patch.dict(os.environ, {"PAI_COUNCIL_MODELS": "mock,mock"}, clear=False):
            res = self.orch.handle("我最近有点焦虑")
        self.assertEqual(len(res["trace"]["models"]), 2)
        self.assertIsNotNone(res["trace"]["divergence"])


class CouncilBehaviourTest(CouncilBase):

    def test_explicit_council_runs_both(self):
        res = self.orch.handle("该不该换工作", council=("mock", "mock"))
        trace = res["trace"]
        self.assertEqual(len(trace["proposals"]), 2)
        for p in trace["proposals"]:
            self.assertIn("output", p)
            self.assertIn("model", p)

    def test_response_shows_second_view(self):
        res = self.orch.handle("该不该换工作", council=("mock", "mock"))
        self.assertIn("另一视角", res["response"])
        self.assertIn("未综合", res["response"], "必须明说没有综合，判断权在用户")

    def test_identical_outputs_are_not_flagged(self):
        """两份一样 → 相似度 1.0，不应误报分歧。"""
        res = self.orch.handle("该不该换工作", council=("mock", "mock"))
        div = res["trace"]["divergence"]
        self.assertAlmostEqual(div["score"], 1.0, places=2)
        self.assertFalse(div["flagged"])

    def test_cost_is_sum_of_proposals(self):
        res = self.orch.handle("随便聊聊", council=("mock", "mock"))
        trace = res["trace"]
        self.assertAlmostEqual(trace["cost"], sum(p["cost"] for p in trace["proposals"]))

    def test_one_model_failing_keeps_the_other(self):
        """一个 provider 挂了，另一个的答案仍要留下——否则议会让系统更脆。

        而且降级必须可见：少了一个视角却不说，比报错更糟。
        """
        res = self.orch.handle("聊聊", council=("mock", "bogus"))
        trace = res["trace"]
        self.assertEqual(len(trace["proposals"]), 1)
        self.assertEqual(len(trace["council_failures"]), 1)
        self.assertEqual(trace["council_failures"][0]["model"], "bogus")
        self.assertIn("没有回应", res["response"], "降级要明说，不能静默少一个视角")

    def test_all_failing_degrades_gracefully(self):
        res = self.orch.handle("聊聊", council=("bogus", "nope"))
        self.assertIn("全部模型调用失败", res["trace"]["reason"])
        trace = res["trace"]
        self.assertEqual(len(trace["council_failures"]), 2)
        self.assertIsNone(trace["divergence"])


class DivergenceTest(unittest.TestCase):

    def test_different_texts_flagged(self):
        outs = [{"model": "a", "output": "今天天气不错，适合出门走走"},
                {"model": "b", "output": "生死之外无大事，先把心安顿好"}]
        div = _divergence(outs)
        self.assertLess(div["score"], 0.35)
        self.assertTrue(div["flagged"])
        self.assertEqual(div["method"], "bigram_jaccard")

    def test_single_output_returns_none(self):
        self.assertIsNone(_divergence([{"model": "a", "output": "只有一份"}]))

    def test_empty_outputs_return_none(self):
        self.assertIsNone(_divergence([{"model": "a", "output": ""},
                                       {"model": "b", "output": ""}]))


class SingleHomeTest(unittest.TestCase):
    """ADR-030：分歧度算法在整棵脊柱里只能有一处实现。

    背景：council.py（DeepSeek 旧血脉）与 orchestrator 内联版各写了一份同算法的
    divergence，取整、pair、空文本、阈值、去噪规则全都开始漂移。这是 ADR-011
    平行骨架的小复发。守卫不关心它住在哪个文件，只关心**只有一个住址**——
    Phase 4.1 真开工拆模块时，删掉内联版即可，测试不需要跟着改口径。
    """

    ROOT = Path(__file__).resolve().parents[1]
    # 隔离区/备份区里本来就该躺着平行实现（那是待评审的投递，不是脊柱），不参与守卫。
    SKIP_DIRS = {"__pycache__", ".pytest_cache", "data", "backups",
                 "_incoming_deepseek", "_superseded", "node_modules", ".git"}

    def _homes(self, pattern):
        found = []
        for py in sorted(self.ROOT.rglob("*.py")):
            if any(part in self.SKIP_DIRS or part.startswith(
                    ("_incoming", "_superseded", "_backup", "外部AI投递"))
                   for part in py.parts[:-1]):
                continue
            src = py.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(pattern, src, re.M):
                found.append(str(py.relative_to(self.ROOT)).replace("\\", "/"))
        return found

    def test_divergence_algorithm_lives_in_exactly_one_file(self):
        homes = self._homes(r"^def _?divergence\(")
        self.assertEqual(homes, ["pai/orchestrator.py"],
                         f"divergence 的住址不是唯一一处：{homes}。"
                         "算法只允许一个家（ADR-030），另一处要么删要么改成调用。")

    def test_bigram_copies_are_frozen_at_the_documented_three(self):
        # 字符二元切分目前有三份（文化匹配 / 原则查重 / 分歧度），去噪规则各不相同。
        # 这是已知债，写进 ADR-030；此处立的是棘轮：**不许长出第四份**，
        # 要合并或新增，必须先改这条断言并说明理由。
        homes = sorted(set(self._homes(r"^def _?bigrams\(")))
        self.assertEqual(homes, ["pai/cultural_kb.py", "pai/orchestrator.py",
                                 "pai/self_model.py"],
                         f"_bigrams 的份数与 ADR-030 记录的已知债不一致：{homes}。")


if __name__ == "__main__":
    unittest.main(verbosity=2)
