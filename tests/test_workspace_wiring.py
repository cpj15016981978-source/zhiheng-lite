# -*- coding: utf-8 -*-
"""B1 接 prompt 的接线测试：默认关、选中裁剪、空选回退。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.memory import Memory              # noqa: E402
from pai.orchestrator import Orchestrator, _workspace_candidates, _workspace_trim  # noqa: E402


class WiringTest(unittest.TestCase):
    def setUp(self):
        self.mem = Memory(os.path.join(tempfile.mkdtemp(), "ws.db"))
        self.ep = self.mem.write_episode("user", "我爷爷1959年在挨饿")
        self.pid, _ = self.mem.add_principle("秘密原则XYZ：先核实再回答")
        self.orch = Orchestrator(self.mem)

    def tearDown(self):
        self.mem.close()

    def _run(self, enabled, selected):
        with mock.patch.dict(os.environ, {"PAI_CASCADE_ROUTE": "0", "PAI_HYBRID": "0", "PAI_AGENT_ENGINE": "builtin"}, clear=False):
            with mock.patch("pai.orchestrator.WS.backend", return_value=("on" if enabled else "off")):
                with mock.patch("pai.orchestrator.WS.select",
                                return_value={"selected": selected, "dropped": [], "budget": 7}) as sel:
                    with mock.patch("pai.orchestrator.M10.complete",
                                    return_value=[{"model_id": "mock", "output": "好",
                                                   "cost": 0, "tokens": 0}]) as mc:
                        out = self.orch.handle("我爷爷呢")
        return out, sel, mc

    def test_off_does_not_call_or_add_trace(self):
        out, sel, mc = self._run(False, [])
        sel.assert_not_called()
        self.assertNotIn("workspace", out["trace"])

    def test_on_keeps_only_selected(self):
        selected = [{"kind": "episode", "id": self.ep, "score": 0.9}]
        out, sel, mc = self._run(True, selected)
        sel.assert_called_once()
        prompt = mc.call_args[0][0]
        self.assertIn("爷爷", prompt)
        self.assertNotIn("秘密原则XYZ", prompt, "没被选中的原则不该进 prompt")
        self.assertEqual(out["trace"]["workspace"]["selected"][0]["kind"], "episode")

    def test_empty_selection_falls_back_to_full(self):
        out, sel, mc = self._run(True, [])
        prompt = mc.call_args[0][0]
        self.assertIn("秘密原则XYZ", prompt, "空选必须回退全量，不能把上下文饿死")
        self.assertEqual(out["trace"]["workspace"]["reason"], "empty_selection_fallback")


class KeyConsistencyTest(unittest.TestCase):
    def test_episode_missing_type_survives_trim(self):
        recalled = [{"item": {"id": 999, "content": "无类型"}, "relevance": 0.9}]
        cands = _workspace_candidates(recalled, [], [], [])
        self.assertEqual(cands[0]["kind"], "episode")
        with mock.patch("pai.orchestrator.WS.select",
                        return_value={"selected": [{"kind": "episode", "id": 999}],
                                      "dropped": [], "budget": 7}):
            r, _f, _p, _c, tr = _workspace_trim(recalled, [], [], [], "x")
        self.assertEqual(len(r), 1, "候选键与过滤键必须一致")
        self.assertEqual(tr["selected"][0]["kind"], "episode")

    def test_culture_missing_ref_survives_trim(self):
        culture = [{"text": "文化条目"}]
        cands = _workspace_candidates([], [], [], culture)
        self.assertEqual(cands[0]["id"], "文化条目")
        with mock.patch("pai.orchestrator.WS.select",
                        return_value={"selected": [{"kind": "culture", "id": "文化条目"}],
                                      "dropped": [], "budget": 7}):
            _r, _f, _p, c, _tr = _workspace_trim([], [], [], culture, "x")
        self.assertEqual(len(c), 1)

    def test_same_fact_from_recall_and_facts_is_deduped(self):
        recalled = [{"item": {"type": "fact", "id": 1, "subject": "用户",
                              "predicate": "姓名", "object": "阿明"}, "relevance": 0.8}]
        facts = [{"id": 1, "subject": "用户", "predicate": "姓名", "object": "阿明"}]
        cands = _workspace_candidates(recalled, facts, [], [])
        self.assertEqual(sum(1 for x in cands if x["kind"] == "fact"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
