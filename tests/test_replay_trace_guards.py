# -*- coding: utf-8 -*-
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tools import replay_trace  # noqa: E402


class ReplayGuardTest(unittest.TestCase):
    def run_main(self, path):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return replay_trace.main([str(path)])

    def test_replay_does_not_touch_gate_env(self):
        src = pathlib.Path(replay_trace.__file__).read_text(encoding="utf-8")
        self.assertNotIn("PAI_REASONING_GATE", src)

    def test_missing_path_is_red(self):
        p = pathlib.Path(tempfile.mkdtemp()) / "nope.jsonl"
        self.assertEqual(self.run_main(p), 2)

    def test_empty_file_is_red(self):
        p = pathlib.Path(tempfile.mkdtemp()) / "empty.jsonl"
        p.write_text("", encoding="utf-8")
        self.assertEqual(self.run_main(p), 2)

    def test_other_family_shape_is_red(self):
        p = pathlib.Path(tempfile.mkdtemp()) / "other.jsonl"
        p.write_text(json.dumps({"at": "2026-11-01T00:00:00", "kind": "prompt.changed"}) + "\n",
                     encoding="utf-8")
        self.assertEqual(self.run_main(p), 2)

    def test_malformed_json_is_red(self):
        p = pathlib.Path(tempfile.mkdtemp()) / "bad.jsonl"
        p.write_text("{not-json}\n", encoding="utf-8")
        self.assertEqual(self.run_main(p), 2)

    def test_valid_trace_store_shape_still_runs(self):
        p = pathlib.Path(tempfile.mkdtemp()) / "ok.jsonl"
        p.write_text(json.dumps({"prompt": "你好", "trace": {}}, ensure_ascii=False) + "\n",
                     encoding="utf-8")
        self.assertEqual(self.run_main(p), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
