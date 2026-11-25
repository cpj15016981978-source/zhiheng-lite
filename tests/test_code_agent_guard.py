# -*- coding: utf-8 -*-
"""P0-1：禁止 AI 写可执行体，源码必须自己钉住。"""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from pai import code_agent as CA


class ExecutableGuardTest(unittest.TestCase):
    def test_ps1_rejected(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"PAI_CODE_ROOTS": d}):
            p = pathlib.Path(d) / "x.ps1"
            p.write_text("echo hi", encoding="utf-8")
            with self.assertRaises(CA.CodeError):
                CA.apply_file(d, "x.ps1", "echo bad", confirm=True)

    def test_md_allowed(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"PAI_CODE_ROOTS": d}):
            p = pathlib.Path(d) / "x.md"
            p.write_text("old", encoding="utf-8")
            out = CA.apply_file(d, "x.md", "new", confirm=True)
            self.assertTrue(out["changed"])


if __name__ == "__main__":
    unittest.main()
