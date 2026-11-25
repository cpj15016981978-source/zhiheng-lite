# -*- coding: utf-8 -*-
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tools import review_world_shadow as R  # noqa: E402


class ReviewWriteGuardTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.old = os.environ.get("PAI_WORLD_SHADOW_DIR")
        os.environ["PAI_WORLD_SHADOW_DIR"] = str(self.dir)
        (self.dir / "shadow-test.jsonl").write_text(json.dumps({
            "source_episode": 1,
            "candidates": [{"kind": "entity", "type": "person",
                            "name": "测试人", "evidence": "我叫/我是"}],
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        self.cid = R._rows()[0]["id"]

    def tearDown(self):
        if self.old is None:
            os.environ.pop("PAI_WORLD_SHADOW_DIR", None)
        else:
            os.environ["PAI_WORLD_SHADOW_DIR"] = self.old

    def test_reject_without_write_does_not_write(self):
        with contextlib.redirect_stdout(io.StringIO()):
            R.main(["--reject", self.cid])
        self.assertFalse((self.dir / "rejected.jsonl").exists())

    def test_reject_with_write_writes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            R.main(["--reject", self.cid, "--write"])
        self.assertTrue((self.dir / "rejected.jsonl").exists())

    def test_review_list_does_not_create_db(self):
        db = pathlib.Path(tempfile.mkdtemp()) / "missing.db"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = R.main(["--review-list", "--db", str(db)])
        self.assertNotEqual(rc, 0)
        self.assertFalse(db.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
