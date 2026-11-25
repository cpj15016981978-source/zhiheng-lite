# -*- coding: utf-8 -*-
import os
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pai import embedding, rerank  # noqa: E402
from pai.model_router import ModelRouter, ROLES, RouterError  # noqa: E402


class RouterTest(unittest.TestCase):
    def test_roles_all_registered(self):
        st = ModelRouter().status()
        self.assertTrue(set(ROLES).issubset(set(st)))
        self.assertIn("enabled", st)

    def test_disabled_role_rejected(self):
        from pai import roles_config
        old = roles_config.enabled("embed")
        roles_config.set_enabled("embed", False)
        try:
            with self.assertRaises(RuntimeError):
                ModelRouter().embed("x")
        finally:
            roles_config.set_enabled("embed", old)

    def test_embedding_fallback_deterministic(self):
        with mock.patch.dict(os.environ, {"PAI_EMBED_BASE_URL": ""}, clear=False):
            a = embedding.embed("工厂对账")["vectors"][0]
            b = embedding.embed("工厂对账")["vectors"][0]
        self.assertEqual(len(a), 256)
        self.assertEqual(a, b)
        self.assertAlmostEqual(sum(x * x for x in a), 1.0, places=5)

    def test_rerank_fallback_orders_relevant(self):
        with mock.patch.dict(os.environ, {"PAI_RERANK_BASE_URL": ""}, clear=False):
            out = rerank.rerank("对账", ["报价单", "工厂对账流程", "质检报告"])
        self.assertEqual(out["backend"], "lexical")
        self.assertEqual(out["results"][0]["index"], 1)

    def test_guard_flags_destructive(self):
        r = ModelRouter()
        self.assertEqual(r.guard("rm -rf /")["decision"], "review")
        self.assertEqual(r.guard("帮我写日报")["decision"], "allow")

    def test_vision_unconfigured_clear_error(self):
        r = ModelRouter()
        old = __import__("os").environ.pop("PAI_VISION_BASE_URL", None)
        try:
            with self.assertRaises(RouterError):
                r.vision("x.png")
        finally:
            if old is not None:
                __import__("os").environ["PAI_VISION_BASE_URL"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)

