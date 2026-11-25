# -*- coding: utf-8 -*-
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai import code_graph as CG  # noqa: E402


class CodeGraphTest(unittest.TestCase):
    def setUp(self):
        os.environ["PAI_CODE_GRAPH"] = "on"
        self.root = pathlib.Path(tempfile.mkdtemp())
        (self.root / "pkg").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "pkg" / "a.py").write_text(
            "class A:\n"
            "    def f(self):\n"
            "        return g()\n"
            "    def g(self):\n"
            "        return 1\n",
            encoding="utf-8")
        (self.root / "tests" / "test_a.py").write_text(
            "from pkg.a import A\n"
            "def test_f():\n"
            "    A().f()\n",
            encoding="utf-8")
        self.db = self.root / "graph.db"
        self.g = CG.CodeGraph(self.db)

    def tearDown(self):
        self.g.close()
        os.environ.pop("PAI_CODE_GRAPH", None)

    def test_build_and_find(self):
        st = self.g.build(self.root)
        self.assertEqual(st["files_total"], 3)
        self.assertGreaterEqual(st["symbols_total"], 4)
        got = self.g.find("A")
        self.assertTrue(got)
        self.assertEqual(got[0]["kind"], "class")

    def test_callers_and_tests(self):
        self.g.build(self.root)
        callers = self.g.callers("g")
        self.assertTrue(any("f" in c["src"] for c in callers))
        tests = self.g.tests_for("f")
        self.assertTrue(any("test_f" in t["src"] for t in tests))

    def test_incremental(self):
        self.g.build(self.root)
        st = self.g.build(self.root)
        self.assertEqual(st["changed"], 0)

    def test_off_refuses(self):
        os.environ["PAI_CODE_GRAPH"] = "off"
        with self.assertRaises(RuntimeError):
            self.g.build(self.root)

    def test_parse_error_recorded(self):
        (self.root / "pkg" / "bad.py").write_text("def x(:\n", encoding="utf-8")
        st = self.g.build(self.root)
        self.assertTrue(st["errors"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
