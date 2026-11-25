# -*- coding: utf-8 -*-
import os
import pathlib
import tempfile
import unittest
import zipfile

from pai import backup as B


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.old = B.DATA
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        B.DATA = self.tmp / "data"
        (B.DATA / "notes").mkdir(parents=True)
        (B.DATA / "notes" / "a.md").write_text("note", encoding="utf-8")
        (B.DATA / "writing_style.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        B.DATA = self.old

    def test_create_zip(self):
        out = B.create(self.tmp / "backups")
        self.assertTrue(out["ok"])
        with zipfile.ZipFile(out["path"]) as z:
            names = set(z.namelist())
        self.assertIn("data/notes/a.md", names)
        self.assertIn("backup.json", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
