"""M2 recall() 召回质量测试 —— 先钉住契约，再暴露 bug，最后才是改。

背景（2026-10-20 实测）：库里存着「我爷爷1959年在挨饿，他总讲那段事」，
  查询「爷爷」   → 命中 2 条
  查询「爷爷挨饿」→ 命中 0 条     ← 两个词都在库里，却什么都没找到
原因：recall 只按空格/逗号切词，中文整句被当成【一个 token】，必须原文出现
连续同样的字才命中。这不是“得等向量检索”的局限，是 plain bug——
前面 Phase5 补的是“存得住”，取的那一半还漏着。用户体感“它记不住”多半来自这里。

改的时候不能动的东西（TestContractMustNotBreak 钉住）：
  · 签名 recall(query, k=...) 不变，返回形状 [{item:{type:...}, relevance:float}] 不变
    （ADR-005：只换内部实现，将来上向量也只换内部）
  · 相关性倒序、不超 k、无关查询得 0 条
  · episode 与 fact 两类都要能召回

运行： python -m unittest discover -s tests
"""
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.memory import Memory
from pai import memory as M2


class _Mem(unittest.TestCase):
    def setUp(self):
        self.m = Memory(os.path.join(tempfile.mkdtemp(), "r.db"))

    def tearDown(self):
        self.m.close()

    def hit(self, query, k=5):
        return self.m.recall(query, k=k)


class TestTokenizer(_Mem):
    """切词层单独可测——出问题能一眼看出是切词还是打分。"""

    def test_cjk_runs_split_into_bigrams(self):
        self.assertEqual(M2._tokens("爷爷挨饿"), ["爷爷", "爷挨", "挨饿"])

    def test_single_cjk_char_is_kept(self):
        self.assertEqual(M2._tokens("饿"), ["饿"])

    def test_ascii_words_and_numbers_lowercased(self):
        self.assertEqual(M2._tokens("GPT 4o Mini 1959"), ["gpt", "4o", "mini", "1959"])

    def test_mixed_chinese_english_year(self):
        toks = M2._tokens("我1959年挨饿")
        self.assertIn("1959", toks)
        self.assertIn("挨饿", toks)
        self.assertIn("年挨", toks)

    def test_punctuation_is_not_a_token(self):
        self.assertEqual(M2._tokens("，。！？、：；“”"), [])
        self.assertEqual(M2._tokens(""), [])

    def test_query_and_stored_text_share_tokens(self):
        """这个测试才是 bug 的根：查询和原文必须能被切出可相交的 token 集合。"""
        stored = M2._tokens("我爷爷1959年在挨饿，他总讲那段事")
        query = M2._tokens("爷爷挨饿")
        self.assertTrue(set(query) & set(stored),
                        "中文查询与中文原文没有任何共同 token = 必然召回 0")


class TestRecallQuality(_Mem):

    def setUp(self):
        super().setUp()
        self.m.write_episode("user", "我爷爷1959年在挨饿，他总讲那段事")
        self.m.upsert_fact("用户", "家族记忆", "爷爷1959年挨饿")

    def test_two_words_apart_still_recall(self):
        """★ 暴露 bug 的那条：两个词都在库里，只是中间隔了字。"""
        self.assertTrue(self.hit("爷爷挨饿"),
                        "「爷爷」和「挨饿」都在记忆中，却召回 0 条")

    def test_reordered_words_recall(self):
        self.assertTrue(self.hit("挨饿 爷爷"))

    def test_words_from_different_clauses_recall(self):
        self.assertTrue(self.hit("爷爷 那段事"))

    def test_single_word_still_works(self):
        rows = self.hit("爷爷")
        self.assertGreaterEqual(len(rows), 2, "episode 与 fact 都应命中")

    def test_no_recall_for_absent_topic(self):
        """修召回不能顺手把无关查询也召回——那是另一个方向的失效。"""
        self.assertEqual(self.hit("床垫弹簧软硬"), [])
        self.assertEqual(self.hit("今天天气"), [])

    def test_partial_overlap_beats_zero_overlap(self):
        self.m.write_episode("user", "我喜欢喝铁观音")
        rows = self.hit("爷爷挨饿")
        self.assertTrue(rows)
        top = rows[0]["item"]["content"] if rows[0]["item"]["type"] == "episode" \
            else rows[0]["item"]["object"]
        self.assertIn("爷爷", top)

    def test_relevance_is_normalized_0_to_1(self):
        """旧实现按命中次数累加，长文本天然占便宜、且能超过 1。分数应可比。"""
        for q in ("爷爷", "爷爷挨饿", "1959 挨饿"):
            for row in self.hit(q):
                self.assertGreaterEqual(row["relevance"], 0.0, q)
                self.assertLessEqual(row["relevance"], 1.0,
                                     f"{q} 的相关性 {row['relevance']} 超过 1，分数不可比")


class TestRecency(_Mem):
    """同样的命中，新近的记忆更该在前面；但“旧”不等于“无关”，不得被抹零。"""

    def setUp(self):
        super().setUp()
        now = "2026-10-20T12:00:00"
        old = "2019-01-01T12:00:00"
        self.e_new = self.m.write_episode("user", "爷爷讲过挨饿那年")
        cur = self.m.conn.execute(
            "INSERT INTO episodes(ts, role, content, session_id) VALUES(?,?,?,?)",
            (old, "user", "爷爷讲过挨饿那年", "s"))
        self.e_old = cur.lastrowid
        self.m.conn.commit()
        self.now = now

    def test_newer_wins_when_content_identical(self):
        rows = self.m.recall("爷爷挨饿", k=5, now=self.now)
        ids = [r["item"]["id"] for r in rows if r["item"]["type"] == "episode"]
        self.assertEqual(ids[0], self.e_new, "同等相关时应新近的在前")
        self.assertEqual(len(ids), 2, "旧的不得被衰减到消失")

    def test_decay_is_gentle_and_never_zero(self):
        rows = self.m.recall("爷爷挨饿", k=5, now=self.now)
        scores = {r["item"]["id"]: r["relevance"] for r in rows}
        self.assertGreater(scores[self.e_old], 0.0, "七年前不等于不存在")
        self.assertLess(scores[self.e_old], scores[self.e_new])

    def test_no_now_means_same_clock(self):
        """不传 now 也要能跑（编排层没改），且结果确定。"""
        self.assertTrue(self.m.recall("爷爷挨饿"))


class TestContractMustNotBreak(_Mem):

    def setUp(self):
        super().setUp()
        self.m.write_episode("user", "我叫阿明，做了十几年床垫")
        self.m.upsert_fact("用户", "姓名", "阿明")

    def test_return_shape_unchanged(self):
        rows = self.hit("阿明")
        self.assertTrue(rows)
        for r in rows:
            self.assertIn("item", r)
            self.assertIn("relevance", r)
            self.assertIn(r["item"]["type"], ("episode", "fact"))
        self.assertIsInstance(r["relevance"], float)

    def test_sorted_desc_and_respects_k(self):
        for i in range(8):
            self.m.write_episode("user", f"床垫话题第{i}次讨论 阿明")
        rows = self.hit("阿明 床垫", k=3)
        self.assertLessEqual(len(rows), 3)
        scores = [r["relevance"] for r in rows]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_empty_query_returns_nothing(self):
        """空查询不该把整库倒出来——旧实现是 0 分不命中，行为要保持。"""
        self.assertEqual(self.hit("   "), [])

    def test_old_memories_are_recalled_not_windowed(self):
        """Stage 0 起不再切 500 条窗口：最老的那条也必须能被召回。
        （原测试钉的是“窗口外召不回”这个已知取舍；取舍已在 stage 0 消除。）"""
        cur = self.m.conn.execute(
            "INSERT INTO episodes(ts, role, content, session_id) VALUES(?,?,?,?)",
            ("2015-01-01T00:00:00", "user", "爷爷1959年挨饿", "ancient"))
        ancient = cur.lastrowid
        for i in range(505):
            self.m.conn.execute(
                "INSERT INTO episodes(ts, role, content, session_id) VALUES(?,?,?,?)",
                ("2026-10-20T12:00:00", "user", f"无关内容{i}", "s"))
        self.m.conn.commit()
        eps = [r for r in self.hit("爷爷挨饿") if r["item"]["type"] == "episode"]
        self.assertIn(ancient, [r["item"]["id"] for r in eps],
                      "全量候选后，窗口外的最老记忆也必须召回")


class TestThreeCharAliases(_Mem):
    """P13：三字词别名（挨饿/吃不饱/饿肚子、睡不着/失眠/睡不好）。"""

    def test_eat_group_all_directions(self):
        self.m.upsert_fact("用户", "记忆", "他当年吃不饱")
        for q in ("挨饿", "吃不饱", "饿肚子"):
            self.assertTrue(self.hit(q), q)

    def test_sleep_group_all_directions(self):
        self.m.upsert_fact("用户", "记忆", "他最近失眠")
        for q in ("睡不着", "失眠", "睡不好"):
            self.assertTrue(self.hit(q), q)

    def test_three_char_alias_does_not_match_unrelated(self):
        self.m.upsert_fact("用户", "记忆", "他当年吃不饱")
        self.assertFalse(self.hit("爷爷"))


if __name__ == "__main__":
    unittest.main()
