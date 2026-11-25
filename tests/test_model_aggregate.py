# -*- coding: utf-8 -*-
import os
import unittest

from pai import model_gateway as MG


class AggregateLocalTest(unittest.TestCase):
    def test_ollama_spec_resolves(self):
        provider, model = MG.resolve_model("ollama:qwen3-4b-instruct:latest")
        self.assertEqual(provider, "ollama")
        self.assertEqual(model, "qwen3-4b-instruct:latest")

    def test_ollama_price_row_is_zero_not_missing(self):
        self.assertEqual(MG.price_of("ollama", "qwen3-4b-instruct:latest", 100, 100),
                         (0.0, True))

    def test_vision_price_rows(self):
        self.assertEqual(
            MG.price_of("siliconflow", "Qwen/Qwen3-VL-32B-Instruct", 1_000_000, 1_000_000),
            (5.0, True))
        self.assertEqual(
            MG.price_of("siliconflow", "Qwen/Qwen3-VL-8B-Instruct", 1_000_000, 1_000_000),
            (2.5, True))

    def test_aggregator_price_override(self):
        old = os.environ.get("PAI_AGGREGATOR_PRICES_JSON")
        os.environ["PAI_AGGREGATOR_PRICES_JSON"] = '{"gpt-4o-mini":[1.1,4.4]}'
        try:
            row = MG._price_row("aggregator", "gpt-4o-mini")
            self.assertEqual(row, (1.1, 4.4))
        finally:
            if old is None:
                os.environ.pop("PAI_AGGREGATOR_PRICES_JSON", None)
            else:
                os.environ["PAI_AGGREGATOR_PRICES_JSON"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
