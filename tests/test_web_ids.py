# -*- coding: utf-8 -*-
"""前端静态回归：防止删元素后旧 JS 绑定把初始化打断。"""
import pathlib
import re
import unittest

HTML = pathlib.Path(__file__).resolve().parents[1] / "web" / "chat.html"


class WebSmokeTest(unittest.TestCase):
    def setUp(self):
        self.text = HTML.read_text(encoding="utf-8")
        self.ids = set(re.findall(r'id="([^"]+)"', self.text))

    def test_no_missing_bound_ids(self):
        refs = set(re.findall(r"\$\('#([^']+)'\)\.(?:onclick|onchange|addEventListener)", self.text))
        self.assertEqual(sorted(refs - self.ids), [])

    def test_send_enter_scenario_present(self):
        for i in ("send", "input", "modelSelect", "networkBtn"):
            self.assertIn(i, self.ids)
        self.assertIn("inputEl.addEventListener('keydown'", self.text)
        self.assertIn("sendBtn.onclick", self.text)
        self.assertNotIn("隐私离线", self.text)


if __name__ == "__main__":
    unittest.main()
