# -*- coding: utf-8 -*-
import os
import pathlib
import tempfile
import unittest

from pai import doc_index as DI


class DocIndexTest(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("PAI_DOC_DB")
        self.db = pathlib.Path(tempfile.mkdtemp()) / "doc.db"
        os.environ["PAI_DOC_DB"] = str(self.db)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("PAI_DOC_DB", None)
        else:
            os.environ["PAI_DOC_DB"] = self.old

    def test_index_and_search(self):
        text = "报价单需要核对数量。" * 100 + "工厂对账流程要走审批。" * 100
        out = DI.index("work", "报价单.md", text)
        self.assertGreater(out["chunks"], 1)
        hits = DI.search("work", out["doc_id"], "对账 审批", 3)
        self.assertTrue(hits)
        self.assertIn("对账", hits[0]["text"])

    def test_profile_isolation(self):
        out = DI.index("work", "a", "工厂对账")
        self.assertEqual(DI.search("side", out["doc_id"], "对账"), [])

    def test_empty(self):
        with self.assertRaises(ValueError):
            DI.index("work", "a", "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
