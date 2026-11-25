# -*- coding: utf-8 -*-
import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai import skill_library as S  # noqa: E402


class SkillLibraryTest(unittest.TestCase):
    def setUp(self):
        os.environ["PAI_SKILLS"] = "on"
        self.lib = S.SkillLibrary(pathlib.Path(r"D:\DSH\superagent\skills"))

    def tearDown(self):
        os.environ.pop("PAI_SKILLS", None)

    def test_find_factory(self):
        got = self.lib.find("要一份工厂交期日报")
        self.assertTrue(got)
        self.assertEqual(got[0]["name"], "factory_daily_report")

    def test_load_memory(self):
        s = self.lib.load("memory_recall")
        self.assertEqual(s["name"], "memory_recall")
        self.assertIn("记忆", s["_body"])

    def test_validate(self):
        out = self.lib.validate("factory_daily_report")
        self.assertTrue(out["ok"], out)

    def test_unknown(self):
        with self.assertRaises(S.SkillError):
            self.lib.load("no_such_skill")

    def test_off_returns_empty(self):
        os.environ["PAI_SKILLS"] = "off"
        self.assertEqual(self.lib.list(), [])
        self.assertEqual(self.lib.find("日报"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
