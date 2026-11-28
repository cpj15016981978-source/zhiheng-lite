# -*- coding: utf-8 -*-
"""品牌一致性：产品知衡，助手月白，旧名不再出现。"""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


class BrandingTest(unittest.TestCase):
    def test_no_old_assistant_name_in_web(self):
        bad = []
        for p in WEB.rglob("*"):
            if p.is_file() and p.suffix in (".html", ".js", ".css"):
                if "时中" in p.read_text(encoding="utf-8", errors="replace"):
                    bad.append(str(p.relative_to(ROOT)))
        self.assertEqual([], bad)


if __name__ == "__main__":
    unittest.main()
