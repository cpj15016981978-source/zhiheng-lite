# -*- coding: utf-8 -*-
"""M9 文化根基知识库 · 单元测试

跑法（在 D:\\DSH 下）：
    python -m unittest tests.test_cultural_kb -v
或：
    python tests\\test_cultural_kb.py

说明：用 importlib 按路径加载被测模块，不依赖 pai 包是否已装 __init__，
      这样在你把文件移植进脊柱之前，也能独立跑绿。
"""

import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD_PATH = os.path.join(ROOT, "pai", "cultural_kb.py")

_spec = importlib.util.spec_from_file_location("cultural_kb_under_test", MOD_PATH)
kb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kb)

# 生死主题的 ref 集合，用于断言“丧亲语境能召回生死类条目”
DEATH_REFS = {"庄子·至乐", "庄子·大宗师", "陶渊明·形影神", "苏轼·前赤壁赋"}


class TestRetrieveCulture(unittest.TestCase):

    def test_returns_list_of_dicts_with_exact_keys(self):
        """返回必须是 list[dict]，且只含 ref/text/weight 三个键。"""
        result = kb.retrieve_culture("自省")
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        for item in result:
            self.assertIsInstance(item, dict)
            self.assertEqual(set(item.keys()), {"ref", "text", "weight"})
            self.assertIsInstance(item["ref"], str)
            self.assertIsInstance(item["text"], str)
            self.assertIsInstance(item["weight"], float)
            self.assertGreaterEqual(item["weight"], 0.0)
            self.assertLessEqual(item["weight"], 1.0)

    def test_self_reflection_query_hits_confucian_entries(self):
        """问“反省/自省”，应召回儒家修身类条目。"""
        result = kb.retrieve_culture("我最近总在怪别人，怎么反省自己")
        refs = [r["ref"] for r in result]
        self.assertTrue(any("论语" in ref or "孟子" in ref for ref in refs),
                        "应召回论语或孟子，实际：%s" % refs)

    def test_grief_context_recalls_death_entries(self):
        """丧亲语境应把生死观条目顶上来。"""
        result = kb.retrieve_culture("亲人去世了，一直走不出来")
        refs = {r["ref"] for r in result}
        self.assertTrue(refs & DEATH_REFS,
                        "应至少召回一条生死类条目，实际：%s" % sorted(refs))

    def test_empty_query_returns_empty(self):
        """空查询返回空列表——不塞兜底内容。"""
        self.assertEqual(kb.retrieve_culture(""), [])
        self.assertEqual(kb.retrieve_culture("   ", context=None), [])
        self.assertEqual(kb.retrieve_culture(""), [])

    def test_k_limits_result_size(self):
        """k 必须生效。"""
        result = kb.retrieve_culture("生死 人情 处世 自省 得失", k=2)
        self.assertLessEqual(len(result), 2)

    def test_weights_are_sorted_desc(self):
        """结果按权重降序，调用方才能放心截断。"""
        result = kb.retrieve_culture("生死 无常 放下")
        weights = [r["weight"] for r in result]
        self.assertEqual(weights, sorted(weights, reverse=True))

    def test_deterministic(self):
        """同样输入必须给同样输出（同分按 ref 排序）。"""
        a = kb.retrieve_culture("人情世故 识人")
        b = kb.retrieve_culture("人情世故 识人")
        self.assertEqual(a, b)

    def test_get_corpus_returns_independent_copy(self):
        """外部改副本不能污染模块内语料。"""
        corpus = kb.get_corpus()
        self.assertGreater(len(corpus), 0)
        corpus[0]["ref"] = "被改坏了"
        self.assertNotEqual(kb.get_corpus()[0]["ref"], "被改坏了")

    def test_context_is_merged_into_query(self):
        """context 也应参与打分（dict 会被序列化）。"""
        bare = kb.retrieve_culture("这件事")
        with_ctx = kb.retrieve_culture("这件事", context={"emotion": "丧亲 悲伤"})
        self.assertGreaterEqual(len(with_ctx), len(bare))


    def test_colloquial_friend_query_hits_perspicacity(self):
        """口语“该不该跟这个朋友断绝来往”应召回识人类，而不是被高频词带偏。"""
        result = kb.retrieve_culture("该不该跟这个朋友断绝来往")
        refs = [r["ref"] for r in result]
        self.assertIn("增广贤文", refs[:2],
                      "识人问题应把增广贤文排进前二，实际：%s" % refs)

    def test_colloquial_frustration_query_is_not_empty(self):
        """“努力没结果”这类无字面重合的问法，靠主题扩展也要能召回。"""
        result = kb.retrieve_culture("努力了很久还是没结果，怎么面对")
        self.assertGreater(len(result), 0, "口语句式不应召回为空")

    def test_irrelevant_query_returns_no_noise(self):
        """完全无关的话必须返回空——宁可空，不可塞。"""
        self.assertEqual(kb.retrieve_culture("今天天气不错"), [])


class TestCorpusIntegrity(unittest.TestCase):

    def test_every_entry_has_required_fields(self):
        """语料条目必须字段齐全，否则检索会静默失效。"""
        for entry in kb.get_corpus():
            self.assertTrue(entry.get("ref"), entry)
            self.assertTrue(entry.get("text"), entry)
            self.assertIsInstance(entry.get("tags"), list)
            self.assertIsInstance(entry.get("themes"), list)

    def test_corpus_size_is_curated_not_bloated(self):
        """刻意小规模：10—40 条。语料一旦膨胀就变成第二份互联网。"""
        self.assertGreaterEqual(len(kb.get_corpus()), 10)
        self.assertLessEqual(len(kb.get_corpus()), 40)


if __name__ == "__main__":
    unittest.main(verbosity=2)
