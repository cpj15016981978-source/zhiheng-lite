# -*- coding: utf-8 -*-
"""核心推理政策：十一种镜头按任务选，不要求每轮全开。"""
import unittest

from pai.orchestrator import REASONING_MODES, reasoning_block, reasoning_policy


class ReasoningPolicyTest(unittest.TestCase):
    def test_modes_are_not_all_dumped(self):
        p = reasoning_policy("比较两个方案的风险")
        self.assertTrue(2 <= len(p["modes"]) <= 4)
        self.assertIn("发散", p["modes"])
        self.assertIn("收敛", p["modes"])

    def test_causal_and_abductive(self):
        p = reasoning_policy("为什么故障，可能原因是什么")
        self.assertIn("因果", p["modes"])
        self.assertIn("溯因", p["modes"])

    def test_metacognition_for_uncertainty(self):
        p = reasoning_policy("我不确定，可能要猜一下")
        self.assertIn("元认知", p["modes"])
        self.assertEqual("低", p["confidence"])

    def test_core_modes_have_a_route(self):
        cases = {
            "推导证明": "演绎", "从错误总结规律": "归纳", "异常诊断": "溯因",
            "感觉怎么选": "直觉", "像这个案例": "类比", "头脑风暴": "发散",
            "评分选最好": "收敛", "检查漏洞": "批判", "依赖和影响": "系统",
            "不确定": "元认知", "为什么故障": "因果",
        }
        for text, mode in cases.items():
            self.assertIn(mode, reasoning_policy(text)["modes"], text)
        self.assertTrue(set(cases.values()).issubset(set(REASONING_MODES)))

    def test_prompt_injects_reasoning_modes(self):
        from pai import orchestrator as O
        text = O._build_prompt("比较两个方案的风险", [], [])
        self.assertIn("[本轮认知模式]", text)
        self.assertIn("发散", text)

    def test_block_has_discipline(self):
        b = reasoning_block("比较方案")
        self.assertIn("[本轮认知模式]", b)
        self.assertIn("不是台词", b)


if __name__ == "__main__":
    unittest.main()
