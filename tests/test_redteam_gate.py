# -*- coding: utf-8 -*-
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai import reasoning as R  # noqa: E402


class RedteamGateTest(unittest.TestCase):
    def test_english_write_commands_are_write(self):
        for t in ("delete /tmp/x", "rm -rf important", "DROP TABLE facts",
                  "please write the config", "save this file"):
            self.assertEqual(R.classify(t), R.WRITE, t)

    def test_chat_stays_chat(self):
        self.assertEqual(R.classify("你好"), R.CHAT)

    def test_benign_english_questions_stay_chat(self):
        for t in (
            "What format should I use for the report?",
            "Please remove the typo in my sentence",
            "How do I save my notes in Notion?",
            "Can you execute a summary of this article?",
            "What is the best way to write a poem?",
        ):
            self.assertEqual(R.classify(t), R.CHAT, t)


if __name__ == "__main__":
    unittest.main(verbosity=2)
