# -*- coding: utf-8 -*-
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai import world_shadow as WS  # noqa: E402


class WorldShadowTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["PAI_WORLD_SHADOW"] = "off"
        os.environ["PAI_WORLD_SHADOW_DIR"] = self.dir

    def tearDown(self):
        os.environ.pop("PAI_WORLD_SHADOW", None)
        os.environ.pop("PAI_WORLD_SHADOW_DIR", None)

    def test_extract_names_and_relations(self):
        c = WS.extract("我叫阿明，我的妻子是李四，我在东宝床垫工作，我喜欢茶")
        kinds = {(x["kind"], x.get("name") or x.get("value")) for x in c}
        self.assertIn(("entity", "阿明"), kinds)
        self.assertIn(("entity", "李四"), kinds)
        self.assertIn(("entity", "东宝床垫"), kinds)
        self.assertIn(("belief", "茶"), kinds)
        self.assertTrue(any(x.get("predicate") == "妻子" for x in c))

    def test_question_not_extracted_as_name(self):
        self.assertEqual(WS.extract("你还记得我叫什么吗 阿明"), [])
        self.assertEqual(WS.extract("我叫什么名字"), [])

    def test_off_writes_nothing(self):
        self.assertIsNone(WS.observe("我叫阿明"))
        self.assertEqual(list(pathlib.Path(self.dir).glob("*")), [])

    def test_on_writes_candidates_not_raw_text(self):
        os.environ["PAI_WORLD_SHADOW"] = "on"
        p = WS.observe("我叫阿明", source_episode=3)
        self.assertTrue(p.exists())
        raw = p.read_text(encoding="utf-8")
        self.assertIn("阿明", raw)
        self.assertNotIn("我叫阿明，我的", raw)
        rec = json.loads(raw.splitlines()[0])
        self.assertEqual(rec["source_episode"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
