# -*- coding: utf-8 -*-
"""M10 网关 · 价格表与国产 provider（M3 第 0 步）

两条要钉住的契约：
  1. cost 必须是真的钱，不是占位 0。未登记的模型要显式标 priced=False，
     免得"没算钱"被当成"没花钱"。
  2. deepseek 走 OpenAI 兼容端点，零新依赖；缺 Key 时报错不得泄漏 Key。

运行：python -m unittest tests.test_gateway_pricing -v
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai import model_gateway as M10


class PricingTest(unittest.TestCase):

    def test_known_model_is_priced(self):
        cost, priced = M10.price_of("deepseek", "deepseek-chat", 1_000_000, 1_000_000)
        self.assertTrue(priced)
        self.assertAlmostEqual(cost, 5.0, places=6)   # 1.0 输入 + 4.0 输出

    def test_unknown_model_is_not_free_but_unpriced(self):
        """未登记 ≠ 免费。必须能让调用方区分这两件事。"""
        cost, priced = M10.price_of("deepseek", "某个没登记的模型", 1_000_000, 1_000_000)
        self.assertEqual(cost, 0.0)
        self.assertFalse(priced)

    def test_all_default_providers_have_pricing(self):
        """新增 provider 却忘了登记价格，是最容易悄悄发生的事。"""
        for provider, model in M10.DEFAULT_NAMES.items():
            if provider == "aggregator":
                # 聚合平台价格由 PAI_AGGREGATOR_PRICES_JSON 显式登记；没有这条 env 就应当拒发
                continue
            cost, priced = M10.price_of(provider, model, 1000, 1000)
            self.assertTrue(priced, "provider %s 的默认模型 %s 不在价格表里" % (provider, model))

    def test_mock_complete_is_zero_and_priced(self):
        outs = M10.complete("随便问一句", models=("mock",))
        self.assertEqual(len(outs), 1)
        out = outs[0]
        self.assertEqual(out["cost"], 0.0)
        self.assertTrue(out["priced"], "mock 是真的零成本，应标记为已计价")
        self.assertEqual(out["tokens"], 0)
        self.assertIn("tokens_in", out)
        self.assertIn("tokens_out", out)


class ProviderTest(unittest.TestCase):

    def test_unknown_provider_message_lists_choices(self):
        with self.assertRaises(M10.ModelGatewayError) as ctx:
            M10.complete("hi", models=("bogus",))
        msg = str(ctx.exception)
        self.assertIn("bogus", msg)
        self.assertIn("deepseek", msg)

    @mock.patch.dict(os.environ, {}, clear=False)
    def test_deepseek_without_key_raises_and_never_leaks(self):
        """缺 Key 时必须报错，且错误文本里不能出现任何 Key 样式的内容。"""
        env = {k: v for k, v in os.environ.items()
               if k not in ("DEEPSEEK_API_KEY", "PAI_MODEL_API_KEY")}
        env["PAI_MODEL_PROVIDER"] = "deepseek"
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(M10.ModelGatewayError) as ctx:
                M10.complete("hi", models=("deepseek",))
        msg = str(ctx.exception)
        self.assertIn("DEEPSEEK_API_KEY", msg)
        self.assertNotIn("sk-", msg)

    def test_active_provider_reflects_env(self):
        with mock.patch.dict(os.environ, {"PAI_MODEL_PROVIDER": "deepseek"}, clear=False):
            self.assertEqual(M10.active_provider(), "deepseek:deepseek-chat")

    def test_deepseek_uses_openai_compatible_endpoint(self):
        """deepseek 必须与 openai 共用同一套 /chat/completions 调用路径。"""
        self.assertIn("deepseek", M10.OPENAI_LIKE)
        self.assertTrue(M10.OPENAI_LIKE["deepseek"].endswith("/v1"))


class PricingConsistencyTest(unittest.TestCase):

    def test_pricing_keys_are_tuples_of_provider_and_model(self):
        for key in M10.PRICING:
            self.assertIsInstance(key, tuple)
            self.assertEqual(len(key), 2)
            provider, model = key
            if provider in M10.LOCAL_PROVIDERS:
                continue
            self.assertIn(provider, M10.DEFAULT_NAMES,
                          "价格表里出现了默认表里没有的 provider：%s" % provider)

    def test_output_price_not_cheaper_than_input(self):
        """输出通常比输入贵；反了多半是抄错列。"""
        for (provider, model), (p_in, p_out) in M10.PRICING.items():
            self.assertGreaterEqual(p_out, p_in, "%s/%s 的输出价低于输入价？" % (provider, model))


class MinimaxPricingTest(unittest.TestCase):
    """X5：真机那条路（双击 bat / minimax_quickstart）用的坐标必须有价。

    键取自启动器自己的常量，不在这里抄第二份；启动器改了模型名而价格表没跟上，
    这条会红——因为那时 trace.cost 又变成 0 且 priced=False，账单就隐形了。
    """

    # Boss 2026-10-22 从 MiniMax 控制台抄来的实付价（元/百万 token）。这里【故意】重述一遍，
    # 用来抓 PRICING 的抄录错误：M3 记的是"永久五折"后的 2.1/8.4，不是标价 4.2/16.8。
    CONSOLE_PRICE = {
        "MiniMax-M3.1-Flash": (1.0, 4.0),
        "MiniMax-M3": (2.1, 8.4),
    }

    def test_launch_path_model_is_priced(self):
        from tools import minimax_quickstart as Q
        self.assertEqual(Q.PROVIDER, "openai", "协议名不是公司名（ADR-041），别改成 minimax")
        cost, priced = M10.price_of(Q.PROVIDER, Q.MODEL_NAME, 1_000_000, 1_000_000)
        self.assertTrue(priced, "%s 不在 PRICING 里 → 界面会显示 0 元，那是没算钱不是免费"
                        % Q.MODEL_NAME)
        self.assertEqual(M10.PRICING[(Q.PROVIDER, Q.MODEL_NAME)],
                         self.CONSOLE_PRICE[Q.MODEL_NAME],
                         "%s 的价格与 Boss 抄来的控制台实价对不上" % Q.MODEL_NAME)
        self.assertGreater(cost, 0.0)

    def test_both_minimax_rows_match_console(self):
        for model, price in self.CONSOLE_PRICE.items():
            self.assertEqual(M10.PRICING[("openai", model)], price, model)


if __name__ == "__main__":
    unittest.main(verbosity=2)
