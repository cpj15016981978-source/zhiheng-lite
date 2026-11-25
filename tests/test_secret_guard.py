# -*- coding: utf-8 -*-
"""密钥护栏测试 —— ADR-017 红线的内核级落实。

用户可能把 API Key 当成聊天内容贴进 `你 >`。handle() 必须在最前面拦截：
疑似密钥/私钥 → 不落库(episode) → 不反思 → 不召回 → 不联网调模型 → 不回显原句。
这条断言打在"接缝"上：既验证拦截本身，也验证它真的没把秘密写进记忆库。

运行：
    python -m unittest tests.test_secret_guard -v
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.memory import Memory
from pai.orchestrator import Orchestrator

FAKE_KEY = "sk-api-DUMMYNOTAREALKEY-0123456789abcdef"


class SecretGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, "guard.db")
        self.mem = Memory(self.db)
        self.orch = Orchestrator(self.mem)

    def tearDown(self):
        self.mem.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _all_text_in_db(self):
        return "\n".join(e["content"] for e in self.mem.recent_episodes(100))

    def test_pasted_key_is_blocked(self):
        res = self.orch.handle("我的密钥是 " + FAKE_KEY)
        self.assertTrue(res["trace"].get("blocked_secret"), "疑似密钥应被拦截")
        self.assertNotIn(FAKE_KEY, res["response"], "拦截回应里绝不复述密钥")

    def test_blocked_key_never_hits_memory(self):
        self.orch.handle(FAKE_KEY)
        self.assertNotIn(FAKE_KEY, self._all_text_in_db(),
                         "被拦截的密钥绝不能落进记忆库(episode)")

    def test_blocked_key_not_recalled_later(self):
        self.orch.handle(FAKE_KEY)
        # 之后再召回这句，也不应把它当记忆翻出来
        res = self.orch.handle("我刚才那串东西是什么")
        self.assertNotIn(FAKE_KEY, res["response"])

    def test_bearer_and_privatekey_also_blocked(self):
        self.assertTrue(self.orch.handle("Bearer abcdef1234567890")
                        ["trace"].get("blocked_secret"))
        self.assertTrue(self.orch.handle("-----BEGIN PRIVATE KEY-----\nMIIB")
                        ["trace"].get("blocked_secret"))

    def test_ordinary_text_not_blocked(self):
        """防误伤：正常中文/普通英文不该被当成密钥拦掉。"""
        for line in ("最近有点累", "我叫阿明", "help me think this through"):
            self.assertFalse(self.orch.handle(line)["trace"].get("blocked_secret"),
                             f"正常语句不应被拦：{line}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
