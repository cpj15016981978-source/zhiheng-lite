# -*- coding: utf-8 -*-
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai import correction_rules as CR  # noqa: E402
from pai import orchestrator as O  # noqa: E402


class CorrectionRulesTest(unittest.TestCase):
    def setUp(self):
        self.old_env = os.environ.get(CR.ENV)
        self.old_store = CR.STORE
        CR.STORE = pathlib.Path(tempfile.mkdtemp(prefix="cr_")) / "corrections.jsonl"
        os.environ[CR.ENV] = "on"

    def tearDown(self):
        CR.STORE = self.old_store
        if self.old_env is None:
            os.environ.pop(CR.ENV, None)
        else:
            os.environ[CR.ENV] = self.old_env

    def test_detect_and_propose_pending(self):
        row = CR.propose("以后回答先给结论，再解释。")
        self.assertEqual(row["status"], "pending")
        self.assertIn("先给结论", row["candidate"])
        self.assertIsNone(CR.active_rule())

    def test_approve_then_prompt_block(self):
        row = CR.propose("以后回答先给结论，再解释。")
        CR.approve(row["correction_id"])
        self.assertTrue(CR.active_rule())
        self.assertIn("先给结论", CR.prompt_block())
        fields = CR.trace_fields()
        self.assertEqual(fields["correction_id"], row["correction_id"])

    def test_disable(self):
        row = CR.propose("以后回答先给结论，再解释。")
        CR.approve(row["correction_id"])
        CR.disable(row["correction_id"])
        self.assertIsNone(CR.active_rule())
        self.assertEqual(CR.prompt_block(), "")

    def test_off_switch_no_write(self):
        os.environ[CR.ENV] = "off"
        self.assertIsNone(CR.propose("以后回答先给结论，再解释。"))
        self.assertFalse(CR.STORE.exists())

    def test_stores_text_and_purges(self):
        self.assertTrue(CR.CAPABILITY["stores_user_text"])
        self.assertEqual(CR.CAPABILITY["text_hosts_ref"], "pai/memory.py:_TEXT_HOSTS")
        CR.propose("以后回答先给结论，再解释。")
        self.assertEqual(CR.purge_text("先给结论"), 1)
        self.assertEqual(CR.list_all(), [])

    def test_build_prompt_includes_rule(self):
        prompt = O._build_prompt("问题", [], [], extra_rule="先给结论，再解释。")
        self.assertIn("[本轮行为规则]", prompt)
        self.assertIn("先给结论", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
