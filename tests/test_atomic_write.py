# -*- coding: utf-8 -*-
"""原子写测试：不截断、不留临时文件。"""
import pathlib
import tempfile
import unittest

from pai import atomic as A


class AtomicTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = pathlib.Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_write_text_and_no_tmp_left(self):
        p = self.root / "sub" / "a.txt"
        A.write_text(p, "一\n二\n")
        self.assertEqual(p.read_text(encoding="utf-8"), "一\n二\n")
        self.assertEqual([x.name for x in p.parent.iterdir()], ["a.txt"])

    def test_write_bytes(self):
        p = self.root / "b.bin"
        A.write_bytes(p, b"\x00\x01")
        self.assertEqual(p.read_bytes(), b"\x00\x01")

    def test_overwrite_keeps_content_on_success(self):
        p = self.root / "c.txt"
        A.write_text(p, "old")
        A.write_text(p, "new")
        self.assertEqual(p.read_text(encoding="utf-8"), "new")


if __name__ == "__main__":
    unittest.main()
