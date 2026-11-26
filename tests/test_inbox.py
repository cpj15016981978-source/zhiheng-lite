# -*- coding: utf-8 -*-
"""Inbox 导入：只转换、不落盘；路径/格式拒绝。"""
import pathlib
import tempfile
import unittest

from pai import file_extract as FE


class InboxTest(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        (self.root / "note.txt").write_text("第一行\n第二行", encoding="utf-8")

    def test_list_and_ingest(self):
        rows = FE.list_inbox(self.root)
        self.assertEqual([x["name"] for x in rows], ["note.txt"])
        name, content, chars = FE.ingest_inbox("note.txt", self.root)
        self.assertEqual(name, "note.md")
        self.assertIn("第一行", content)
        self.assertGreater(chars, 0)

    def test_rejects_path_traversal_and_unknown(self):
        for name in ("../note.txt", "a/b.txt", ".hidden", "x.exe"):
            with self.assertRaises(FE.ExtractError):
                FE.ingest_inbox(name, self.root)


if __name__ == "__main__":
    unittest.main()
