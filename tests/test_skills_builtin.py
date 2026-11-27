# -*- coding: utf-8 -*-
"""内置技能包：适配 v2.2 的 SKILL.md 格式，保持只读。"""
import os
import unittest

from pai import skill_library as SL


class BuiltinSkillTest(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("PAI_SKILLS")
        os.environ["PAI_SKILLS"] = "on"

    def tearDown(self):
        if self.old is None:
            os.environ.pop("PAI_SKILLS", None)
        else:
            os.environ["PAI_SKILLS"] = self.old

    def test_six_builtin_skills_load(self):
        names = {s["name"] for s in SL.SkillLibrary().list()}
        self.assertTrue({"planning", "file-approval", "self-check", "chat",
                         "scheduled-tasks", "document-handling"} <= names)

    def test_find_and_readonly(self):
        lib = SL.SkillLibrary()
        self.assertIn("planning", [s["name"] for s in lib.find("帮我写一份周报")])
        with self.assertRaises(SL.SkillError):
            lib.approve("planning")

    def test_skill_switch_off_means_no_injection(self):
        import webapp
        old = os.environ.pop("PAI_SKILLS", None)
        try:
            self.assertEqual("", webapp._skill_rule("帮我写一份周报"))
        finally:
            if old is not None:
                os.environ["PAI_SKILLS"] = old

    def test_webapp_injects_matched_skill_index_only(self):
        import webapp
        rule = webapp._skill_rule("帮我写一份周报")
        self.assertIn("planning", rule)
        self.assertIn("路径：skills/planning/SKILL.md", rule)
        self.assertNotIn("# 日常对话", rule)
        self.assertEqual(webapp._skill_rule("今天天气"), "")


if __name__ == "__main__":
    unittest.main()
