# -*- coding: utf-8 -*-
"""1.2.0 产品化基线：许可证、单版本源、数据目录。"""
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ProductizationTest(unittest.TestCase):
    def test_apache_license_present(self):
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", text)
        self.assertIn("Version 2.0", text)
        self.assertTrue((ROOT / "NOTICE").exists())
        self.assertTrue((ROOT / "THIRD_PARTY.md").exists())
        self.assertTrue((ROOT / "SECURITY.md").exists())

    def test_version_single_source(self):
        v = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
        self.assertEqual(v["version"], "1.1.0")
        self.assertIn("1.1.0", (ROOT / "README.txt").read_text(encoding="utf-8"))

    def test_data_dir_is_not_repo_data(self):
        from pai import paths
        self.assertNotEqual(paths.DATA_DIR.resolve(), (ROOT / "data").resolve())
        self.assertTrue(paths.DATA_DIR.exists())


if __name__ == "__main__":
    unittest.main()
