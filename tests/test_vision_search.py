# -*- coding: utf-8 -*-
import io
import json
import os
import unittest
from unittest import mock

from pai import vision as V
from pai import web_search as S


class FakeResp:
    def __init__(self, obj):
        self.obj = obj

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.obj).encode("utf-8")


class VisionTest(unittest.TestCase):
    def test_missing_key(self):
        old = os.environ.pop("PAI_VISION_API_KEY", None)
        old2 = os.environ.pop("SILICONFLOW_API_KEY", None)
        try:
            with self.assertRaises(V.VisionError):
                V.describe(b"x")
        finally:
            if old:
                os.environ["PAI_VISION_API_KEY"] = old
            if old2:
                os.environ["SILICONFLOW_API_KEY"] = old2

    def test_openai_compatible_response(self):
        os.environ["PAI_VISION_API_KEY"] = "k"
        payload = {"choices": [{"message": {"content": "一张图"}}]}
        try:
            with mock.patch.object(V.urllib.request, "urlopen", return_value=FakeResp(payload)):
                self.assertEqual(V.describe(b"x"), "一张图")
        finally:
            os.environ.pop("PAI_VISION_API_KEY", None)


class SearchTest(unittest.TestCase):
    def test_missing_key(self):
        old = os.environ.pop("TAVILY_API_KEY", None)
        old2 = os.environ.pop("PAI_SEARCH_API_KEY", None)
        try:
            with self.assertRaises(S.SearchError):
                S.search("hello")
        finally:
            if old:
                os.environ["TAVILY_API_KEY"] = old
            if old2:
                os.environ["PAI_SEARCH_API_KEY"] = old2

    def test_tavily_response(self):
        os.environ["TAVILY_API_KEY"] = "k"
        payload = {"answer": "摘要", "results": [
            {"title": "T", "url": "u", "content": "c"}]}
        try:
            with mock.patch.object(S.urllib.request, "urlopen", return_value=FakeResp(payload)):
                out = S.search("q")
            self.assertEqual(out["answer"], "摘要")
            self.assertEqual(out["results"][0]["url"], "u")
        finally:
            os.environ.pop("TAVILY_API_KEY", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
