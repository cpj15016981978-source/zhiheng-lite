# -*- coding: utf-8 -*-
import os
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def run(code, env_extra=None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                          env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


class ReplayGateEnvTest(unittest.TestCase):
    def test_explicit_off_survives_import(self):
        r = run("import os; from tools import replay_trace; "
                "print(os.environ.get('PAI_REASONING_GATE'))",
                {"PAI_REASONING_GATE": "off"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "off")

    def test_dead_trace_store_import_gone(self):
        r = run("from tools import replay_trace; "
                "print(hasattr(replay_trace, 'TS'))")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "False")


if __name__ == "__main__":
    unittest.main(verbosity=2)
