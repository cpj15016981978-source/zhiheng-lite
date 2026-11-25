# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pai.memory import Memory
from pai.orchestrator import Orchestrator


class GamePromptTest(unittest.TestCase):
    def _mem(self):
        return Memory(str(Path(tempfile.mkdtemp()) / "g.db"))

    def test_game_prompt_is_raw_and_not_stored(self):
        mem = self._mem()
        try:
            before = len(mem.recent_episodes(100))
            fake = [{"model_id": "mock", "output": '{"choice":"a","override":false,"why":"x"}',
                     "cost": 0.0, "tokens": 5, "priced": True}]
            with mock.patch("pai.orchestrator.M10.complete", return_value=fake):
                out = Orchestrator(mem).handle(
                    '[系统] 你在替玩家玩《大唐西行录·西行模式》。只输出 JSON：'
                    '{"choice":"a","override":false,"why":"x"}')
            self.assertTrue(out["trace"]["game_mode"])
            self.assertEqual(out["response"], '{"choice":"a","override":false,"why":"x"}')
            self.assertEqual(len(mem.recent_episodes(100)), before)
        finally:
            mem.close()

    def test_hide_recall_env_returns_model_text(self):
        mem = self._mem()
        try:
            fake = [{"model_id": "mock", "output": "直接回答",
                     "cost": 0.0, "tokens": 5, "priced": True}]
            with mock.patch.dict(os.environ, {"PAI_HIDE_RECALL": "on"}, clear=False), \
                    mock.patch("pai.orchestrator.M10.complete", return_value=fake):
                out = Orchestrator(mem).handle("你好")
            self.assertEqual(out["response"], "直接回答")
            self.assertNotIn("我记得这些相关", out["response"])
        finally:
            mem.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
