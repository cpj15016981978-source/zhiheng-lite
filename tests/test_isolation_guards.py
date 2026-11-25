# -*- coding: utf-8 -*-
import os
import unittest
from unittest import mock

from pai import local_duo as D


class EnvIsolationTest(unittest.TestCase):
    def test_duo_does_not_mutate_provider_env(self):
        keys = ("PAI_MODEL_PROVIDER", "PAI_PROFILE", "PAI_AGGREGATOR_PRICES_JSON")
        old = {k: os.environ.get(k) for k in keys}
        os.environ["PAI_MODEL_PROVIDER"] = "mock"
        os.environ["PAI_PROFILE"] = "side"
        try:
            with mock.patch.object(D, "_call", side_effect=[
                ("草稿", 1, 1), ("审稿", 1, 1), ("最终", 1, 1)]):
                out = D.complete("测试")
            self.assertEqual(out["output"], "最终")
            self.assertEqual(os.environ["PAI_MODEL_PROVIDER"], "mock")
            self.assertEqual(os.environ["PAI_PROFILE"], "side")
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main(verbosity=2)
