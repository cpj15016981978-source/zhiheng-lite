# -*- coding: utf-8 -*-
"""开发版办公附件流水线。"""
import io
import unittest
from unittest import mock

import openpyxl

from pai import file_extract as FE
from pai import orchestrator as O


class OfficeFlowTest(unittest.TestCase):
    def test_xlsx_extract(self):
        wb = openpyxl.Workbook(); ws = wb.active
        ws.append(["负责人", "事项"]); ws.append(["张三", "交报表"])
        b = io.BytesIO(); wb.save(b)
        text = FE.extract("t.xlsx", b.getvalue())
        self.assertIn("张三", text)
        self.assertIn("交报表", text)

    def test_split_and_run(self):
        text = "会议内容。" * 2400
        self.assertGreater(len(O._office_split(text, 1000)), 1)
        o = object.__new__(O.Orchestrator)
        with mock.patch.object(O.Orchestrator, "_office_complete", return_value="## 摘要\n测试\n## 待办\n| 事项 | 负责人 | 时间 |"):
            out = o.office_run("minutes", text, "测试")
        self.assertGreater(out["chunks"], 1)
        self.assertIn("待办", out["content"])

    def test_exports(self):
        content = "## 摘要\n测试\n| 事项 | 负责人 |\n| 交报表 | 张三 |"
        self.assertTrue(object.__new__(O.Orchestrator).office_export("docx", content).startswith(b"PK"))
        self.assertTrue(object.__new__(O.Orchestrator).office_export("xlsx", content).startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()
