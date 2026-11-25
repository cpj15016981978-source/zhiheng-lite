# -*- coding: utf-8 -*-
import io
import unittest

import docx
from pptx import Presentation

from pai import file_extract as FE


class FileExtractTest(unittest.TestCase):
    def test_text(self):
        self.assertEqual(FE.extract("a.txt", "你好".encode("utf-8")), "你好")

    def test_docx(self):
        d = docx.Document()
        d.add_paragraph("订单 D-9 延迟")
        buf = io.BytesIO()
        d.save(buf)
        self.assertIn("D-9", FE.extract("a.docx", buf.getvalue()))

    def test_pptx(self):
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        box = slide.shapes.add_textbox(0, 0, 5000000, 1000000)
        box.text_frame.text = "月度报价复盘"
        buf = io.BytesIO()
        prs.save(buf)
        self.assertIn("月度报价复盘", FE.extract("a.pptx", buf.getvalue()))

    def test_unsupported(self):
        with self.assertRaises(FE.ExtractError):
            FE.extract("a.exe", b"\x00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
