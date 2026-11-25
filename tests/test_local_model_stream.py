# -*- coding: utf-8 -*-
"""本地流式兜底：cloud 失败时仍能往 SSE 发 delta。"""
import json
import os
import unittest
from unittest import mock

from pai import local_model as LM
from pai import orchestrator as O


class _Resp:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self.lines)


class LocalStreamTest(unittest.TestCase):
    def test_local_stream_calls_delta_and_returns_text(self):
        lines = [
            json.dumps({"message": {"content": "你"}, "done": False}).encode(),
            json.dumps({"message": {"content": "好"}, "done": True}).encode(),
        ]
        seen = []
        with mock.patch.object(LM, "enabled", return_value=True), \
                mock.patch("urllib.request.urlopen", return_value=_Resp(lines)):
            out = LM.complete("hi", on_delta=seen.append)
        self.assertEqual(out, "你好")
        self.assertEqual(seen, ["你", "好"])

    def test_cloud_fail_stream_falls_back_to_local(self):
        seen = []
        with mock.patch.object(LM, "enabled", return_value=True), \
                mock.patch.object(O.M10, "complete", side_effect=RuntimeError("cloud down")), \
                mock.patch.object(LM, "complete", return_value="本地回答") as lc:
            outs = O._complete("hi", on_delta=seen.append)
        self.assertTrue(outs[0]["fallback"])
        self.assertEqual(outs[0]["output"], "本地回答")
        lc.assert_called_once()


if __name__ == "__main__":
    unittest.main()
