# -*- coding: utf-8 -*-
"""Security-critical helpers in the current app.py."""
import unittest
from pathlib import Path

import app


class WorkspaceNameTest(unittest.TestCase):
    def test_allows_plain_files(self):
        self.assertEqual(app.safe_workspace_name("report.md"), "report.md")

    def test_rejects_traversal(self):
        for name in ("../x.txt", "a/b.txt", "..\\x.txt", ".hidden", "x.exe", "x"):
            self.assertIsNone(app.safe_workspace_name(name), name)


class FileProposalTest(unittest.TestCase):
    def test_extracts_only_safe_blocks(self):
        text = (
            "[写文件: ok.txt]\nhello\n[/写文件]\n"
            "[写文件: ../bad.txt]\nno\n[/写文件]\n"
            "[写文件: bad.exe]\nno\n[/写文件]"
        )
        out = app.extract_file_proposals(text)
        self.assertEqual([x["filename"] for x in out], ["ok.txt"])
        self.assertEqual(out[0]["content"], "hello")


class GitIgnoreTest(unittest.TestCase):
    def test_local_secrets_and_data_ignored(self):
        text = Path(".gitignore").read_text(encoding="utf-8")
        for item in ("muse_config.json", "*.db", "workspace/", "scripts/"):
            self.assertIn(item, text)


if __name__ == "__main__":
    unittest.main()
