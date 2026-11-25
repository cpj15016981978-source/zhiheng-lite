# -*- coding: utf-8 -*-
import os
import unittest

from pai import model_gateway as MG
from pai import vision as V
from pai import web_search as S


class OfflineGateTest(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("PAI_OFFLINE")
        os.environ["PAI_OFFLINE"] = "1"

    def tearDown(self):
        if self.old is None:
            os.environ.pop("PAI_OFFLINE", None)
        else:
            os.environ["PAI_OFFLINE"] = self.old

    def test_cloud_model_refused(self):
        os.environ["PAI_MODEL_PROVIDER"] = "openai"
        os.environ["PAI_MODEL_NAME"] = "gpt-4o-mini"
        os.environ["OPENAI_API_KEY"] = "sk-test"
        try:
            with self.assertRaises(MG.ModelGatewayError) as ctx:
                MG.complete("hi", models=("openai:gpt-4o-mini",))
            self.assertIn("离线模式", str(ctx.exception))
        finally:
            for k in ("PAI_MODEL_PROVIDER", "PAI_MODEL_NAME", "OPENAI_API_KEY"):
                os.environ.pop(k, None)

    def test_vision_closed(self):
        self.assertFalse(V.configured())
        with self.assertRaises(V.VisionError):
            V.describe(b"x")

    def test_search_closed(self):
        self.assertFalse(S.configured())
        with self.assertRaises(S.SearchError):
            S.search("q")


if __name__ == "__main__":
    unittest.main(verbosity=2)
