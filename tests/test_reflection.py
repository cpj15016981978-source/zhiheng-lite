"""验证 Phase2 反思闭环：M4 把对话蒸馏成 fact，再合成系统【自写】的 principle，
且增量+幂等、跨进程持久。运行： python -m unittest discover -s tests （项目根目录）
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.memory import Memory
from pai.orchestrator import Orchestrator
from pai import reflection as M4


class TestReflection(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "r.db")

    def test_distills_facts_from_episodes(self):
        """反思应从用户话里蒸馏出结构化事实（姓名/家族记忆/行业）。"""
        m = Memory(self.db)
        m.write_episode("user", "我叫阿明", "s1")
        m.write_episode("user", "我爷爷1959年在挨饿", "s1")
        m.write_episode("user", "我在家具厂工作", "s1")
        r = M4.reflect(m)
        facts = {(f["subject"], f["predicate"]): f["object"] for f in m.all_facts()}
        self.assertEqual(facts.get(("用户", "姓名")), "阿明")
        self.assertIn("家族记忆", [f["predicate"] for f in m.all_facts()])
        self.assertEqual(facts.get(("用户", "行业")), "家具厂")
        self.assertGreaterEqual(r["new_facts"], 3)
        m.close()

    def test_synthesizes_principles_grounded_in_facts(self):
        """原则应由事实触发（有家族记忆→谈历史落到具体人；有姓名→称呼带名字）。"""
        m = Memory(self.db)
        m.write_episode("user", "我叫阿明", "s1")
        m.write_episode("user", "我爷爷1959年在挨饿", "s1")
        M4.reflect(m)
        texts = "\n".join(p["text"] for p in m.get_principles())
        self.assertIn("阿明", texts)
        self.assertIn("家族", texts)
        # 铁律6 内化的自省原则应始终在场
        self.assertIn("不把猜测当事实", texts)
        m.close()

    def test_reflect_is_incremental_and_idempotent(self):
        """无新对话时重复反思不应产生新 fact / 新 principle（幂等）。"""
        m = Memory(self.db)
        m.write_episode("user", "我叫阿明", "s1")
        first = M4.reflect(m)
        second = M4.reflect(m)  # 水位线已推进，应扫到 0 条新对话
        self.assertEqual(second["scanned"], 0)
        self.assertEqual(second["new_facts"], 0)
        self.assertEqual(second["new_principles"], 0)
        self.assertEqual(len(m.get_principles()), first["principles_total"])
        m.close()

    def test_principles_survive_reopen(self):
        """跨进程：反思产物落盘，重开新连接仍在——“越用越懂你”不会因重启丢失。"""
        m1 = Memory(self.db)
        orch = Orchestrator(m1)
        orch.handle("我叫阿明")
        orch.handle("我爷爷1959年在挨饿")
        n_principles = len(m1.get_principles())
        m1.close()

        m2 = Memory(self.db)  # 全新连接 = 全新进程
        self.assertEqual(len(m2.get_principles()), n_principles)
        self.assertTrue(any(f["predicate"] == "姓名" and f["object"] == "阿明"
                            for f in m2.all_facts()))
        m2.close()

    def test_orchestrator_injects_principles_into_trace(self):
        """编排层闭环：handle 后 trace 应带上本轮恪守的原则与反思增量。"""
        m = Memory(self.db)
        orch = Orchestrator(m)
        out = orch.handle("我叫阿明")
        self.assertIn("principles_used", out["trace"])
        self.assertIn("reflection", out["trace"])
        self.assertTrue(len(out["trace"]["principles_used"]) > 0)
        m.close()


if __name__ == "__main__":
    unittest.main()
