# -*- coding: utf-8 -*-
"""统一重试策略。"""
import unittest

from pai import exec_guard as EG


class RetryPolicyTest(unittest.TestCase):
    def test_retry_then_success(self):
        calls = {"n": 0}
        def fn():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("temporary")
            return "ok"
        self.assertEqual(EG.retry_call(fn, "test_retry", retries=2, base_delay=0), "ok")
        self.assertEqual(calls["n"], 3)

    def test_retry_exhausted_raises(self):
        def fn():
            raise RuntimeError("always")
        with self.assertRaises(RuntimeError):
            EG.retry_call(fn, "test_retry", retries=1, base_delay=0)


if __name__ == "__main__":
    unittest.main()
