from __future__ import annotations

import unittest

from pai import meta


class CapabilityMetaTest(unittest.TestCase):
    def test_detects_capability_question(self):
        self.assertTrue(meta.is_question("这些你能做吗？逐项说能做/不能做"))
        self.assertTrue(meta.is_question("你会什么"))

    def test_does_not_claim_unconnected_services(self):
        text = meta.answer()
        for word in ("知衡", "能做", "不能"):
            self.assertIn(word, text)
        for word in ("订票", "支付", "Spotify", "自动付款"):
            self.assertIn(word, text)
        self.assertNotIn("爷爷", text)

    def test_normal_chat_is_not_meta(self):
        self.assertFalse(meta.is_question("今天天气怎么样"))


if __name__ == "__main__":
    unittest.main()
