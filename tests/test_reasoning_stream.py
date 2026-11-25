# -*- coding: utf-8 -*-
import json
import unittest
from unittest import mock

from pai import model_gateway as MG


class FakeResp:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for obj in self.lines:
            yield ("data: " + json.dumps(obj) + "\n").encode("utf-8")


class ReasoningStreamTest(unittest.TestCase):
    def test_reasoning_and_content_are_split(self):
        lines = [
            {"choices": [{"delta": {"reasoning_content": "先想"}}]},
            {"choices": [{"delta": {"reasoning_content": "再想"}}]},
            {"choices": [{"delta": {"content": "回答"}}]},
            {"choices": [{"delta": {"content": "结束"}}]},
            {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 2, "completion_tokens": 3}},
        ]
        got = {"r": "", "c": ""}
        with mock.patch.object(MG.NP, "is_host_allowed", return_value=True), \
             mock.patch.object(MG.urllib.request, "urlopen", return_value=FakeResp(lines)):
            text, tin, tout = MG._openai_stream(
                "http://example/v1", "k", {"model": "x"}, 5,
                lambda s: got.__setitem__("c", got["c"] + s),
                on_reasoning=lambda s: got.__setitem__("r", got["r"] + s))
        self.assertEqual(got["r"], "先想再想")
        self.assertEqual(got["c"], "回答结束")
        self.assertEqual(text, "回答结束")
        self.assertEqual((tin, tout), (2, 3))


if __name__ == "__main__":
    unittest.main(verbosity=2)
