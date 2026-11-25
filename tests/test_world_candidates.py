# -*- coding: utf-8 -*-
import json
import os
import pathlib
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import webapp  # noqa: E402


class WorldCandidatesTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.old = os.environ.get("PAI_WORLD_SHADOW_DIR")
        self.old_on = os.environ.get("PAI_WORLD_SHADOW")
        os.environ["PAI_WORLD_SHADOW_DIR"] = str(self.dir)
        os.environ["PAI_WORLD_SHADOW"] = "on"
        (self.dir / "shadow-20261030.jsonl").write_text(json.dumps({
            "ts": "2026-10-30T00:00:00",
            "source_episode": 1,
            "candidates": [
                {"kind": "entity", "type": "person", "name": "阿明",
                 "evidence": "我叫/我是"},
                {"kind": "belief", "holder": "user", "predicate": "喜欢",
                 "value": "茶", "evidence": "我喜欢…"},
            ],
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        (self.dir / "reviewed.jsonl").write_text(json.dumps({
            "candidate_id": "abc", "kind": "entity",
        }) + "\n", encoding="utf-8")
        db = pathlib.Path(tempfile.mkdtemp()) / "w.db"
        self.httpd, self.thread = webapp.start(port=0, db_path=str(db),
                                               quiet=True, ready_timeout=40)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        webapp.stop(self.httpd, self.thread)
        if self.old is None:
            os.environ.pop("PAI_WORLD_SHADOW_DIR", None)
        else:
            os.environ["PAI_WORLD_SHADOW_DIR"] = self.old
        if self.old_on is None:
            os.environ.pop("PAI_WORLD_SHADOW", None)
        else:
            os.environ["PAI_WORLD_SHADOW"] = self.old_on

    def test_list_candidates(self):
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/world/candidates" % self.port,
                timeout=8) as r:
            data = json.loads(r.read().decode())
        self.assertEqual(len(data["items"]), 2)
        kinds = {x["kind"] for x in data["items"]}
        self.assertEqual(kinds, {"entity", "belief"})

    def test_off_is_403(self):
        os.environ["PAI_WORLD_SHADOW"] = "off"
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(
                "http://127.0.0.1:%d/api/world/candidates" % self.port, timeout=8)
        self.assertEqual(cm.exception.code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
