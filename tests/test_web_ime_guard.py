# -*- coding: utf-8 -*-
"""输入法守卫：组字中的 Enter 不许发消息。

真实界面是 web/chat.html 的内联脚本；web/app.js 是 M1 旧壳，仍随包发布。
两处 keydown 都必须先放过 isComposing / keyCode 229 / Process，
再判断 Enter && !shiftKey。用源码守卫钉住，防止 1.2.0-dev 那次回归重来。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    (ROOT / "web" / "chat.html", "inputEl.addEventListener('keydown'"),
    (ROOT / "web" / "app.js", '$("input").addEventListener("keydown"'),
)


class WebImeGuardTest(unittest.TestCase):
    def _handler(self, path, needle):
        src = path.read_text(encoding="utf-8")
        start = src.find(needle)
        self.assertGreaterEqual(start, 0, "%s 找不到 keydown 守卫" % path)
        return src[start:start + 600]

    def _assert_guard(self, path, needle):
        block = self._handler(path, needle)
        self.assertIn("e.isComposing", block, "%s 缺 isComposing" % path)
        self.assertRegex(block, r"e\.keyCode\s*===\s*229", "%s 缺 keyCode 229" % path)
        self.assertRegex(block, r"e\.key\s*===\s*['\"]Process['\"]", "%s 缺 Process" % path)
        self.assertIn("!e.shiftKey", block, "%s 缺 Shift+Enter 不发送" % path)

        guard = min(block.find("e.isComposing"), block.find("229"), block.find("Process"))
        send = block.find("send()")
        self.assertGreaterEqual(send, 0, "%s 找不到 send()" % path)
        self.assertLess(guard, send, "%s 组字守卫必须早于 send()" % path)
        self.assertRegex(block, r"e\.key\s*===\s*['\"]Enter['\"]", "%s 缺 Enter 判断" % path)

    def test_chat_html_keydown_guard(self):
        self._assert_guard(*TARGETS[0])

    def test_m1_app_js_keydown_guard(self):
        self._assert_guard(*TARGETS[1])


if __name__ == "__main__":
    unittest.main()
