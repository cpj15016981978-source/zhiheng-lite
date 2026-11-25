# -*- coding: utf-8 -*-
import os
import pathlib
import tempfile
import unittest

from pai import code_agent as C


class CodeAgentTest(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        (self.root / "tests").mkdir()
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "tests" / "test_a.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n"
            "    def test_x(self): self.assertEqual(1, 1)\n", encoding="utf-8")
        self.old = os.environ.get("PAI_CODE_ROOTS")
        os.environ["PAI_CODE_ROOTS"] = str(self.root)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("PAI_CODE_ROOTS", None)
        else:
            os.environ["PAI_CODE_ROOTS"] = self.old

    def test_read_and_apply_guard(self):
        f = C.read_file(str(self.root), "a.py")
        self.assertEqual(f["content"], "x = 1\n")
        with self.assertRaises(C.CodeError):
            C.apply_file(str(self.root), "a.py", "x = 2\n", f["sha256"], False)
        out = C.apply_file(str(self.root), "a.py", "x = 2\n", f["sha256"], True)
        self.assertTrue(out["changed"])
        with self.assertRaises(C.CodeError):
            C.apply_file(str(self.root), "a.py", "x = 3\n", f["sha256"], True)

    def test_rollback_and_batch(self):
        f = C.read_file(str(self.root), "a.py")
        C.apply_file(str(self.root), "a.py", "x = 2\n", f["sha256"], True)
        C.rollback_file(str(self.root), "a.py")
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "x = 1\n")
        out = C.apply_batch(str(self.root), [
            {"path": "a.py", "content": "x = 3\n"},
            {"path": "tests/test_a.py", "content": "x = 4\n"},
        ], True)
        self.assertEqual(out["count"], 2)

    def test_run_tests(self):
        out = C.run_tests(str(self.root))
        self.assertTrue(out["passed"], out["output"])

    def test_snippet_default_off(self):
        with self.assertRaises(C.CodeError):
            C.run_snippet("print(1)")

    def test_snippet_isolated_when_enabled(self):
        import os
        old = os.environ.get("PAI_CODE_EXEC")
        os.environ["PAI_CODE_EXEC"] = "on"
        try:
            out = C.run_snippet("print('ok')")
            self.assertTrue(out["isolated"])
            self.assertIn("ok", out["output"])
        finally:
            if old is None:
                os.environ.pop("PAI_CODE_EXEC", None)
            else:
                os.environ["PAI_CODE_EXEC"] = old

    def test_run_tests_rejects_pytest_and_named_file(self):
        with self.assertRaises(C.CodeError):
            C.run_tests(str(self.root), "python -m pytest tests/test_a.py")
        with self.assertRaises(C.CodeError):
            C.run_tests(str(self.root), "python -m unittest tests.test_a")


if __name__ == "__main__":
    unittest.main(verbosity=2)
