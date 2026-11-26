# -*- coding: utf-8 -*-
import unittest

from pai import orchestrator as O


class IdentityTest(unittest.TestCase):
    def test_identity_guard(self):
        o = object.__new__(O.Orchestrator)
        out = o.handle("你叫什么名字？这个 app 叫什么？")
        text = out["response"]
        self.assertIn("月白", text)
        self.assertIn("知衡", text)
        self.assertNotIn("Qwen", text)


if __name__ == "__main__":
    unittest.main()
