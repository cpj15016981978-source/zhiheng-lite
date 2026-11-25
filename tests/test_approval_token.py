# -*- coding: utf-8 -*-
"""审批 token：一次性、短时、绑定动作类型。"""
import time
import unittest
from unittest import mock

from pai import approval_token as T


class ApprovalTokenTest(unittest.TestCase):
    def tearDown(self):
        T.clear()

    def test_issue_and_consume_once(self):
        t = T.issue("browser", "x")["token"]
        self.assertTrue(T.consume(t, "browser"))
        self.assertFalse(T.consume(t, "browser"))

    def test_wrong_kind_rejected(self):
        t = T.issue("code")["token"]
        self.assertFalse(T.consume(t, "browser"))
        self.assertTrue(T.consume(t, "code"))

    def test_expired_rejected(self):
        t = T.issue("mcp")["token"]
        with mock.patch.object(T, "_now", return_value=time.time() + 999):
            self.assertFalse(T.consume(t, "mcp"))


if __name__ == "__main__":
    unittest.main()
