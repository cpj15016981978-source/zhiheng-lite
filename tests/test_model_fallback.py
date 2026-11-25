# -*- coding: utf-8 -*-
"""模型网关默认调用 fallback：主失败切备用，成功即停。"""
import os
import unittest
from unittest import mock

from pai import model_gateway as MG


class ModelFallbackTest(unittest.TestCase):
    def test_primary_fails_then_fallback(self):
        old = MG._complete_many
        calls = []
        def fake(prompt, models=None, **kw):
            calls.append(tuple(models or ()))
            if len(calls) == 1:
                raise MG.ModelGatewayError("连不上 primary")
            return [{"model_id": models[0], "output": "ok"}]
        MG._complete_many = fake
        try:
            with mock.patch.dict(os.environ, {"PAI_MODEL_FALLBACKS": "ollama:test-local"}):
                out = MG.complete("hi")
            self.assertEqual(out[0]["output"], "ok")
            self.assertEqual(len(calls), 2)
        finally:
            MG._complete_many = old

    def test_explicit_models_not_swallowed(self):
        old = MG._complete_many
        MG._complete_many = lambda prompt, models=None, **kw: [{"model_id": m} for m in models]
        try:
            out = MG.complete("x", models=("a", "b"))
            self.assertEqual(len(out), 2)
        finally:
            MG._complete_many = old


if __name__ == "__main__":
    unittest.main()
