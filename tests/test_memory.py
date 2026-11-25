"""验证 Phase1 核心承诺：M2 记忆【跨进程持久化】——关掉再打开还记得。
运行： python -m unittest discover -s tests   （在项目根目录）
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.memory import Memory
from pai.orchestrator import Orchestrator


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "m.db")

    def test_memory_survives_reopen(self):
        """模拟“关掉软件/重启进程”：第一个连接写入并关闭，第二个连接应能召回。"""
        m1 = Memory(self.db)
        m1.write_episode("user", "我爷爷1959年在挨饿", "s1")
        m1.upsert_fact("用户", "爷爷的经历", "1959年挨饿")
        m1.close()

        m2 = Memory(self.db)  # 全新连接 = 全新进程
        rec = m2.recall("爷爷 1959")
        self.assertTrue(len(rec) > 0, "重开后应能召回之前的记忆")
        facts = m2.all_facts()
        self.assertTrue(any(f["predicate"] == "爷爷的经历" for f in facts))
        m2.close()

    def test_orchestrator_extracts_and_recalls(self):
        m = Memory(self.db)
        orch = Orchestrator(m)
        orch.handle("我叫阿明")
        out = orch.handle("你还记得我叫什么吗 阿明")
        self.assertTrue(any(f["predicate"] == "姓名" and f["object"] == "阿明"
                            for f in m.all_facts()))
        self.assertTrue(out["trace"]["is_reason"])
        m.close()

    def test_trace_separates_recall_from_reason(self):
        """铁律6：trace 必须能区分 recalled(查) 与 reason(推)。"""
        m = Memory(self.db)
        orch = Orchestrator(m)
        orch.handle("记住 我家在潮州")
        out = orch.handle("我家在哪 潮州")
        self.assertIn("recalled", out["trace"])
        self.assertIn("reason", out["trace"])
        m.close()


if __name__ == "__main__":
    unittest.main()
