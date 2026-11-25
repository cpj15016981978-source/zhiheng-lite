# -*- coding: utf-8 -*-
"""P174（Boss 2026-11-22 ③ 点头的 (a)）：reports/ 与 *.bak* 摘出 R1 比对面，但不免检。

两条正向＋一条拔刀：拔刀把排除项从常量里拿掉，证明它们立刻回到 R1 面 ——
红来自这一刀，不是 os.walk 自己看不见它们。0 出网、0 模型调用、只写临时目录。
"""
import pathlib
import tempfile
import unittest
from unittest import mock

from tools import check_relay as CR


class ReportsExclusionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "reports").mkdir()
        (self.root / "reports" / "gates_x.json").write_text("{}", encoding="utf-8")
        (self.root / "pai").mkdir()
        (self.root / "pai" / "x.py").write_text("hi", encoding="utf-8")
        (self.root / "留底.bak").write_text("old", encoding="utf-8")
        (self.root / "a.txt.bak2").write_text("old", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_reports_and_bak_are_out_of_the_r1_face(self):
        face = CR._walk_files(self.root)
        self.assertIn("pai/x.py", face)
        self.assertNotIn("reports/gates_x.json", face)
        self.assertNotIn("留底.bak", face)
        self.assertNotIn("a.txt.bak2", face)

    def test_they_still_land_in_the_privacy_face(self):
        ex = CR._walk_excluded(self.root)
        self.assertIn("reports/gates_x.json", ex, "摘出比对面不等于免检，R5 仍须扫到它")
        self.assertIn("留底.bak", ex)
        self.assertIn("a.txt.bak2", ex)

    def test_reverse_proof_pulling_the_exclusion_puts_them_back(self):
        with mock.patch.object(CR, "EXCLUDE_DIRS", set(CR.EXCLUDE_DIRS) - {"reports"}):
            self.assertIn("reports/gates_x.json", CR._walk_files(self.root))
        with mock.patch.object(CR, "EXCLUDE_NAMES", set(CR.EXCLUDE_NAMES) - {"*.bak*"}):
            self.assertIn("留底.bak", CR._walk_files(self.root))
            self.assertIn("a.txt.bak2", CR._walk_files(self.root))


if __name__ == "__main__":
    unittest.main(verbosity=2)
