# -*- coding: utf-8 -*-
"""每日简报：只读 CSV，能识别逾期/临期/缺料/未完成工序。"""
import pathlib
import tempfile
import unittest
from unittest import mock

from pai import daily_brief as B


class DailyBriefTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = pathlib.Path(self.td.name)
        (self.root / "data").mkdir()
        (self.root / "out").mkdir()
        (self.root / "data" / "orders.csv").write_text(
            "order_id,customer,product,qty,due,status,process,material_ready,note\n"
            "A,客户,床垫,10,2026-11-10,生产中,裁剪,否,面料未到\n"
            "B,客户,床垫,10,2026-11-20,待包装,包装,是,\n", encoding="utf-8")
        (self.root / "data" / "dispatch.csv").write_text(
            "order_id,process,planned_end,status,owner\n"
            "B,包装,2026-11-18,doing,阿琴\n", encoding="utf-8")
        (self.root / "data" / "exceptions.csv").write_text(
            "date,order_id,type,description,status,owner\n"
            "2026-11-18,A,缺料,面料未到,open,采购\n", encoding="utf-8")
        self.p1 = mock.patch.object(B, "DATA", self.root / "data")
        self.p2 = mock.patch.object(B, "OUT", self.root / "out")
        self.p1.start(); self.p2.start()

    def tearDown(self):
        self.p1.stop(); self.p2.stop(); self.td.cleanup()

    def test_build_and_run(self):
        import datetime
        r = B.build(datetime.date(2026, 11, 18))
        self.assertGreaterEqual(r["counts"]["risks"], 2)
        self.assertTrue(any("逾期" in x["text"] for x in r["risks"]))
        self.assertTrue(any("缺料" in x["text"] for x in r["risks"]))
        out = B.run(datetime.date(2026, 11, 18))
        self.assertTrue(pathlib.Path(out["path"]).is_file())
        self.assertIn("三条要事", out["text"])

    def test_missing_data_is_explicit(self):
        import datetime
        (self.root / "data" / "orders.csv").unlink()
        r = B.build(datetime.date(2026, 11, 18))
        self.assertEqual(r["counts"]["orders"], 0)


if __name__ == "__main__":
    unittest.main()
