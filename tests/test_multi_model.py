# -*- coding: utf-8 -*-
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import multi_model as MM  # noqa: E402


class MultiModelPackageTest(unittest.TestCase):
    def test_estimate_uses_prompt_length(self):
        short, _ = MM.estimate("openai:MiniMax-M3", "x")
        long, _ = MM.estimate("openai:MiniMax-M3", "x" * 2000)
        self.assertGreater(long, short)

    def test_off_gate_refuses_colon_spec(self):
        from pai import model_gateway as M10
        with self.assertRaises(M10.ModelGatewayError):
            M10.complete("hi", models=("openai:MiniMax-M3",))

    def test_missing_runs_dir_is_not_created(self):
        old = MM.RUNS
        tmp = pathlib.Path(tempfile.mkdtemp()) / "missing"
        MM.RUNS = tmp
        try:
            self.assertEqual(MM.run("x", ["mock"], dry_run=False, spend_ok=True), 3)
            self.assertFalse(tmp.exists())
        finally:
            MM.RUNS = old

    def test_no_pai_refuses(self):
        d = pathlib.Path(tempfile.mkdtemp())
        tools = d / "tools"
        tools.mkdir()
        shutil.copy2(ROOT / "tools" / "multi_model.py", tools / "multi_model.py")
        r = subprocess.run([sys.executable, str(tools / "multi_model.py"),
                            "--dry-run", "--models", "mock", "x"],
                           cwd=str(d), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=10)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("找不到 pai", ((r.stdout or "") + (r.stderr or "")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
