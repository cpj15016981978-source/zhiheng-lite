# -*- coding: utf-8 -*-
import datetime
import json
import os
import pathlib
import tempfile
import unittest

from pai import usage


class UsageTest(unittest.TestCase):
    def test_aggregates_today(self):
        d = pathlib.Path(tempfile.mkdtemp())
        p = d / ("trace-%s.jsonl" % datetime.date.today().isoformat())
        p.write_text("\n".join([
            json.dumps({"kind": "model.request"}),
            json.dumps({"kind": "model.response", "model_id": "a",
                        "cost": 0.5, "tokens": 10, "priced": True}),
            json.dumps({"kind": "model.response", "model_id": "a",
                        "cost": 0.25, "tokens": 5, "priced": True}),
            json.dumps({"kind": "model.response", "model_id": "b",
                        "cost": 0, "tokens": 3, "priced": False}),
        ]), encoding="utf-8")
        old = os.environ.get("PAI_TRACE_DIR")
        os.environ["PAI_TRACE_DIR"] = str(d)
        try:
            out = usage.today()
        finally:
            if old is None:
                os.environ.pop("PAI_TRACE_DIR", None)
            else:
                os.environ["PAI_TRACE_DIR"] = old
        self.assertEqual(out["requests"], 1)
        self.assertEqual(out["responses"], 3)
        self.assertAlmostEqual(out["cost"], 0.75)
        self.assertEqual(out["tokens"], 18)
        self.assertEqual(out["unpriced"], 1)
        self.assertEqual(out["by_model"][0]["model_id"], "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)
