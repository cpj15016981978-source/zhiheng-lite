# -*- coding: utf-8 -*-
"""M5 接缝测试：情感层接入 M1 之后，在【真实编排路径】上验证行为。

注意合并顺序：
    ① 先把 pai/affect.py 与 tests/test_affect.py 并入脊柱；
    ② 再把 orchestrator.py 的 M5 接缝补上（见 D:\\DSH\\affect_接缝补丁.diff）；
    ③ 最后才并入本文件——它断言的是“接缝已经在”，不是 M5 单独能跑。

为什么值得单写一份接缝测试（而不是只留 test_affect.py）：
    M5 单测全绿只能证明模块内部对；真正会坏的是“写库时机 / prompt 拼装顺序 /
    回应过护栏的顺序”。这三处只有走 Orchestrator.handle 才照得出来。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.memory import Memory            # noqa: E402
from pai.orchestrator import Orchestrator  # noqa: E402


def _fake_complete(text):
    def _inner(prompt, models=None, opts=None):
        _inner.last_prompt = prompt
        return [{"model_id": "mock", "output": text, "cost": 0.0,
                 "tokens": 0, "priced": True}]
    _inner.last_prompt = ""
    return _inner


class TestAffectWiring(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "w.db")
        self.mem = Memory(self.db)
        self.orch = Orchestrator(self.mem)

    def tearDown(self):
        self.mem.close()

    def _row(self):
        return self.mem.conn.execute(
            "SELECT * FROM affective_state WHERE id=1").fetchone()

    def test_grief_message_persists_state_and_trace(self):
        with mock.patch("pai.orchestrator.M10.complete", _fake_complete("我在。")):
            res = self.orch.handle("我爷爷去世了，我一直走不出来")
        row = self._row()
        self.assertIsNotNone(row, "handle 之后 affective_state 应有一行")
        self.assertEqual(row["mood"], "丧痛")
        self.assertEqual(res["trace"]["affect"]["mood"], "丧痛")
        self.assertTrue(res["trace"]["affect"]["heavy"])

    def test_prompt_contains_affect_block(self):
        fake = _fake_complete("我在。")
        with mock.patch("pai.orchestrator.M10.complete", fake):
            self.orch.handle("亲人去世了")
        prompt = fake.last_prompt
        self.assertIn("情绪基调", prompt)
        self.assertIn("不是诊断", prompt)
        self.assertIn("以对方说的为准", prompt)
        # 铁律6：情感基调块与“可引用的文化背景”是两段，不能混
        self.assertIn("可引用的文化背景", prompt)

    def test_heavy_response_drops_false_comfort(self):
        fake = _fake_complete("想开点，节哀顺变。明天我陪你去殡仪馆。")
        with mock.patch("pai.orchestrator.M10.complete", fake):
            res = self.orch.handle("爷爷去世了")
        resp = res["response"]
        self.assertNotIn("想开点", resp)
        self.assertNotIn("节哀顺变", resp)
        self.assertIn("明天我陪你去殡仪馆", resp)

    def test_calm_message_keeps_response_untouched(self):
        fake = _fake_complete("那就好，今天风大，出门加件衣服。")
        with mock.patch("pai.orchestrator.M10.complete", fake):
            res = self.orch.handle("今天天气不错")
        self.assertEqual(res["trace"]["affect"]["mood"], "平静")
        self.assertFalse(res["trace"]["affect"]["heavy"])
        self.assertIn("今天风大，出门加件衣服", res["response"])

    def test_secret_input_writes_no_affect(self):
        res = self.orch.handle("sk-abcdefghijklmnopqrstuvwxyz012345")
        self.assertTrue(res["trace"].get("blocked_secret"))
        self.assertIsNone(self._row(), "被拦截的输入不应写心境（连估计都不做）")

    def test_second_message_does_not_downgrade_grief(self):
        with mock.patch("pai.orchestrator.M10.complete", _fake_complete("我在。")):
            self.orch.handle("爷爷去世了")
            res = self.orch.handle("明天要去殡仪馆，心里堵得慌")
        self.assertEqual(res["trace"]["affect"]["mood"], "丧痛")

    def test_affect_written_only_once_per_session(self):
        with mock.patch("pai.orchestrator.M10.complete", _fake_complete("嗯。")):
            self.orch.handle("我很难受")
            self.orch.handle("今天天气不错")
        rows = self.mem.conn.execute("SELECT COUNT(*) FROM affective_state").fetchone()[0]
        self.assertEqual(rows, 1, "心境是单行状态（id=1），不能每轮插一行")


if __name__ == "__main__":
    unittest.main(verbosity=2)
