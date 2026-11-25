# -*- coding: utf-8 -*-
"""审批边界：空 id 不能命中、pending 不落 token 明文。"""
import pathlib
import tempfile
import unittest

from pai import approval_token as T


class ApprovalBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()) / "pending.json"
        self.old = T.PENDING
        T.PENDING = self.tmp
        T.clear()

    def tearDown(self):
        T.clear()
        T.PENDING = self.old

    def test_empty_id_never_takes_first(self):
        a = T.issue("desktop", "a", action={"type": "desktop"})
        T.issue("desktop", "b")
        self.assertIsNone(T.take(""))
        self.assertIsNone(T.take(" "))
        self.assertIsNotNone(T.take(a["id"]))
        self.assertFalse(T.deny_id(""))

    def test_pending_file_has_no_plaintext_token(self):
        T.issue("desktop", "secret", action={"type": "desktop"})
        text = self.tmp.read_text(encoding="utf-8")
        self.assertNotIn("token", text.lower())
        self.assertNotIn("secret-token", text)


if __name__ == "__main__":
    unittest.main()
