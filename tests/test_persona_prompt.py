# -*- coding: utf-8 -*-
"""人格单一真源 + prompt 注入回归测试。"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import unittest

from pai import persona as P
from pai import orchestrator as O

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_desktop_persona():
    p = pathlib.Path(os.environ.get("PAI_DESKTOP") or r"D:\DSH\chuixue_desktop") / "persona.py"
    if not p.is_file():
        raise unittest.SkipTest("桌面 persona.py 不存在")
    os.environ.setdefault("PAI_SPINE", str(ROOT))
    spec = importlib.util.spec_from_file_location("_desktop_persona_test", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PersonaPromptTest(unittest.TestCase):
    def test_prompt_block_has_persona(self):
        block = P.prompt_block()
        # 品牌断言绑定真源常量，改名时不改测试（避免"不要自称月白"这类句子造成的假通过）
        for word in (P.NAME, P.FORM, P.STYLE[0], P.STYLE[3]):
            self.assertIn(word, block)

    def test_build_prompt_injects_persona(self):
        text = O._build_prompt("你好", [], [])
        # 标题行格式由真源常量拼出，不再硬编码品牌字面量
        self.assertIn("[人格 · %s / %s]" % (P.NAME, P.FORM), text)
        self.assertIn(P.STYLE[3], text)
        self.assertIn("[用户] 你好", text)

    def test_build_prompt_injects_reminders(self):
        text = O._build_prompt("你好", [], [], reminders=[{"message_template": "该喝水了"}])
        self.assertIn("[提醒]", text)
        self.assertIn("该喝水了", text)
        self.assertNotIn("[提醒]", O._build_prompt("你好", [], []))

    def test_desktop_uses_same_persona(self):
        d = _load_desktop_persona()
        self.assertEqual(d.NAME, P.NAME)
        self.assertEqual(d.FORM, P.FORM)
        self.assertEqual(d.STYLE, P.STYLE)
        self.assertEqual(d.BOUNDARIES, P.BOUNDARIES)
        self.assertTrue(str(d.PERSONA_SOURCE).endswith("persona.py"))


if __name__ == "__main__":
    unittest.main()
