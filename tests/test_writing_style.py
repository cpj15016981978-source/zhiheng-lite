# -*- coding: utf-8 -*-
import os
import pathlib
import tempfile
import unittest

from pai import writing_style as S


class WritingStyleTest(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("PAI_WRITING_STYLE")
        os.environ["PAI_WRITING_STYLE"] = str(
            pathlib.Path(tempfile.mkdtemp()) / "style.json")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("PAI_WRITING_STYLE", None)
        else:
            os.environ["PAI_WRITING_STYLE"] = self.old

    def test_save_sample_block(self):
        S.save("work", "克制专业", "克制", "少形容词")
        S.add_sample("work", "这是一段样例。")
        b = S.block("work")
        self.assertIn("克制专业", b)
        self.assertIn("这是一段样例", b)
        self.assertIn("不抄事实", b)

    def test_clear(self):
        S.save("work", "x")
        S.clear("work")
        self.assertEqual(S.load("work")["name"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
