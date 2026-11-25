# -*- coding: utf-8 -*-
import sys
import pathlib
import unittest
import warnings
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai import self_facts as SF  # noqa: E402


class SelfFactsDecodeGuardTest(unittest.TestCase):
    def test_none_stdout_does_not_escape(self):
        class R:
            stdout = None
        with mock.patch.object(SF.subprocess, "run", return_value=R()):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                self.assertEqual(SF._listening_ports(), set())
        self.assertTrue(any("stdout" in str(x.message) for x in w))

    def test_invalid_bytes_are_replaced(self):
        raw = (b"  TCP    0.0.0.0:8753    0.0.0.0:0    LISTENING    1\n"
               b"\xff\n")

        def fake_run(*args, **kwargs):
            if kwargs.get("text") and kwargs.get("errors") == "replace":
                return SimpleNamespace(stdout=raw.decode("utf-8", "replace"))
            # 模拟没带 errors= 的真实解码：抛 UnicodeDecodeError，函数兜底成空集
            return SimpleNamespace(stdout=raw.decode("utf-8"))

        with mock.patch.object(SF.subprocess, "run", side_effect=fake_run):
            self.assertIn(8753, SF._listening_ports())


if __name__ == "__main__":
    unittest.main(verbosity=2)
