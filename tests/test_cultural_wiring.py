"""验证 M9 接入编排层：文化条目以 recall 身份进入 trace，与 reason 分开（铁律6）。
运行： python -m unittest discover -s tests （项目根目录）
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.memory import Memory
from pai.orchestrator import Orchestrator
from pai import cultural_kb as M9

# 语料里真实存在的出处集合，用于验证“召回的东西确实来自 M9”
CORPUS_REFS = {entry["ref"] for entry in M9.get_corpus()}


class TestCulturalWiring(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "w.db")
        self.mem = None

    def tearDown(self):
        if self.mem is not None:
            self.mem.close()

    def _orch(self):
        self.mem = Memory(self.db)
        return Orchestrator(self.mem)

    def test_grief_query_surfaces_culture_refs_in_trace(self):
        """丧亲问法应在 trace.culture_refs 里带回检索到的文化条目。"""
        orch = self._orch()
        res = orch.handle("亲人去世了，我一直走不出来")
        refs = res["trace"]["culture_refs"]
        self.assertIsInstance(refs, list)
        self.assertGreater(len(refs), 0, "丧亲语境应检索到生死类条目")
        for c in refs:
            self.assertEqual(set(c.keys()), {"ref", "text", "weight"})

    def test_culture_is_recall_not_reason(self):
        """铁律6：文化条目属 recall，必须与 reason 分开，不能混进模型推断。

        2026-10-20 修订：原断言 assertNotEqual(culture_refs, reason) 恒真
        （list[dict] 与 str 永远不相等），等于没测。现改为两条可失败的断言：
          ① 来源可查：每条 ref 必须能在文化语料里找到 → 证明它来自 M9，非模型编造
          ② 身份分离：reason 里不得出现文化条目原文 → 证明没把检索伪装成推理
        """
        orch = self._orch()
        res = orch.handle("我最近总在怪别人，怎么反省自己")
        trace = res["trace"]

        culture = trace.get("culture_refs")
        self.assertIsInstance(culture, list)
        self.assertGreater(len(culture), 0, "自省类问法应召回文化条目，否则本测试空转")

        # ① 来源可查
        for item in culture:
            self.assertIn(item["ref"], CORPUS_REFS,
                          "文化条目的 ref 必须能在语料中查到：%r" % (item["ref"],))

        # ② 身份分离：recall 的原文不得出现在 reason 里
        reason = trace.get("reason")
        reason_text = reason if isinstance(reason, str) else json.dumps(reason, ensure_ascii=False)
        for item in culture:
            self.assertNotIn(item["text"], reason_text,
                             "文化条目原文不得出现在 reason 里——那是检索，不是推理")

    def test_irrelevant_query_adds_no_culture(self):
        """无关寒暄不应强行引典——culture_refs 为空是对的。"""
        orch = self._orch()
        res = orch.handle("今天天气不错")
        self.assertEqual(res["trace"]["culture_refs"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
