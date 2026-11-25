# -*- coding: utf-8 -*-
"""主动推送正文：可选让真模型写；默认关、失败回模板、出声仍是短模板。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.memory import Memory              # noqa: E402
from pai.orchestrator import Orchestrator  # noqa: E402

TRACE = {"spoken": True, "backend": "windows", "chars": 8,
         "truncated": False, "rate": 0, "reason": ""}


class ProactiveModelTest(unittest.TestCase):
    def setUp(self):
        self.mem = Memory(os.path.join(tempfile.mkdtemp(), "pm.db"))
        self.orch = Orchestrator(self.mem)
        self.mem.upsert_fact("用户", "姓名", "阿明")
        tid = self.mem.add_trigger("manual", "该喝水了", "你上午说过要提醒你")
        self.trig = self.mem.get_trigger(tid)

    def tearDown(self):
        self.mem.close()

    def _run(self, env=None, complete=None):
        env = env or {}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch("pai.orchestrator.M14.speak", return_value=dict(TRACE)) as sp:
                if complete is None:
                    with mock.patch("pai.orchestrator.M10.complete") as mc:
                        res = self.orch.proactive(self.trig)
                        return res, sp, mc
                with mock.patch("pai.orchestrator.M10.complete", side_effect=complete) as mc:
                    res = self.orch.proactive(self.trig)
                    return res, sp, mc

    def test_default_off_uses_template(self):
        res, sp, mc = self._run()
        mc.assert_not_called()
        self.assertFalse(res["generated"])
        self.assertIn("该喝水了", res["message"])
        self.assertEqual(sp.call_args[0][0], "阿明，该喝水了")

    def test_mock_provider_never_calls_model(self):
        res, sp, mc = self._run({"PAI_PROACTIVE_MODEL": "1"})
        mc.assert_not_called()
        self.assertFalse(res["generated"])

    def test_generated_body_used_when_enabled(self):
        def complete(prompt, **kw):
            self.assertIn("主动消息任务", prompt)
            return [{"model_id": "x", "output": "阿明，该喝水了，你上午说过的。",
                     "cost": 0.02, "tokens": 30, "priced": True}]
        with mock.patch("pai.orchestrator.M10.active_provider", return_value="openai"):
            res, sp, mc = self._run({"PAI_PROACTIVE_MODEL": "1"}, complete)
        self.assertTrue(res["generated"])
        self.assertIn("阿明，该喝水了，你上午说过的。", res["message"])
        self.assertAlmostEqual(res["cost"], 0.02)
        self.assertEqual(res["tokens"], 30)
        self.assertIs(res["priced"], True)
        # 出声仍是短模板：不让模型生成的内容直接进嗓子
        self.assertEqual(sp.call_args[0][0], "阿明，该喝水了")

    def test_gateway_error_falls_back(self):
        from pai.model_gateway import ModelGatewayError

        def complete(prompt, **kw):
            raise ModelGatewayError("boom")
        with mock.patch("pai.orchestrator.M10.active_provider", return_value="openai"):
            res, sp, mc = self._run({"PAI_PROACTIVE_MODEL": "1"}, complete)
        self.assertFalse(res["generated"])
        self.assertIn("该喝水了", res["message"])

    def test_failure_marker_falls_back(self):
        def complete(prompt, **kw):
            return [{"model_id": "x", "output": "[模型调用失败] 401",
                     "cost": 0, "tokens": 0}]
        with mock.patch("pai.orchestrator.M10.active_provider", return_value="openai"):
            res, sp, mc = self._run({"PAI_PROACTIVE_MODEL": "1"}, complete)
        self.assertFalse(res["generated"])
        self.assertIn("该喝水了", res["message"])

    def test_empty_output_falls_back(self):
        def complete(prompt, **kw):
            return [{"model_id": "x", "output": "   ", "cost": 0, "tokens": 0}]
        with mock.patch("pai.orchestrator.M10.active_provider", return_value="openai"):
            res, sp, mc = self._run({"PAI_PROACTIVE_MODEL": "1"}, complete)
        self.assertFalse(res["generated"])

    def test_reason_line_still_present(self):
        def complete(prompt, **kw):
            return [{"model_id": "x", "output": "阿明，记得喝水。", "cost": 0, "tokens": 0}]
        with mock.patch("pai.orchestrator.M10.active_provider", return_value="openai"):
            res, sp, mc = self._run({"PAI_PROACTIVE_MODEL": "1"}, complete)
        self.assertIn("我主动找你的理由", res["message"])


    def test_empty_list_falls_back(self):
        def complete(prompt, **kw):
            return []
        with mock.patch("pai.orchestrator.M10.active_provider", return_value="openai"):
            res, sp, mc = self._run({"PAI_PROACTIVE_MODEL": "1"}, complete)
        self.assertFalse(res["generated"])
        self.assertIn("该喝水了", res["message"])
        self.assertIs(res["priced"], False)

    def test_oserror_falls_back(self):
        def complete(prompt, **kw):
            raise OSError("socket blew up")
        with mock.patch("pai.orchestrator.M10.active_provider", return_value="openai"):
            res, sp, mc = self._run({"PAI_PROACTIVE_MODEL": "1"}, complete)
        self.assertFalse(res["generated"])
        self.assertIn("该喝水了", res["message"])
        self.assertIs(res["priced"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
