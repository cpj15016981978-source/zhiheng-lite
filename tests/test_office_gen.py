# -*- coding: utf-8 -*-
"""Office 生成：一句话产出 docx/pptx/xlsx。"""
import pathlib
import tempfile
import unittest
from unittest import mock

from pai import office_gen as O


class OfficeGenTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.p = mock.patch.object(O, "OUT", pathlib.Path(self.td.name))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.td.cleanup()

    def test_docx(self):
        r = O.generate("做一份Word：周报，内容：本周完成A\n下周计划B")
        self.assertEqual(r["kind"], "docx")
        self.assertTrue(pathlib.Path(r["path"]).is_file())

    def test_pptx(self):
        r = O.generate("做一份PPT：周会，要点：进度；风险；下一步")
        self.assertEqual(r["kind"], "pptx")
        self.assertTrue(pathlib.Path(r["path"]).is_file())

    def test_xlsx(self):
        r = O.generate("做一份Excel：清单，内容：A,完成；B,进行中")
        self.assertEqual(r["kind"], "xlsx")
        self.assertTrue(pathlib.Path(r["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
