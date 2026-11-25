# -*- coding: utf-8 -*-
"""低配模式 / 四层记忆 / 安全文件工具。"""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from pai import file_tools as FT
from pai import low_spec as LS
from pai import memory_layers as ML


class LowSpecTest(unittest.TestCase):
    def test_enable_sets_15b(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(LS, "FLAG", pathlib.Path(d) / "low.json"), \
                mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch("pai.model_settings.save") as save:
            out = LS.enable()
            self.assertTrue(out["enabled"])
            self.assertEqual(os.environ["PAI_MODEL_NAME"], "qwen2.5:1.5b")
            self.assertEqual(os.environ["PAI_OLLAMA_NUM_CTX"], "2048")
            save.assert_called_once()
            self.assertFalse(LS.disable()["enabled"])


class MemoryLayersTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.p = mock.patch.multiple(ML, BASE=pathlib.Path(self.td.name),
                                     DB=pathlib.Path(self.td.name) / "memory.db")
        self.p.start()

    def tearDown(self):
        self.p.stop(); self.td.cleanup()

    def test_remember_and_prompt(self):
        ML.remember("profile", "称呼", "叫我大佬")
        ML.remember("lessons", "备份", "先备份再改")
        self.assertIn("叫我大佬", ML.prompt_block())
        self.assertTrue(ML.search("大佬"))


class FileToolsTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.p = mock.patch.object(FT, "ROOT", pathlib.Path(self.td.name))
        self.p.start()

    def tearDown(self):
        self.p.stop(); self.td.cleanup()

    def test_draft_no_overwrite(self):
        rel = FT.write_draft("a.md", "hello")
        self.assertEqual(FT.read_file(rel), "hello")
        with self.assertRaises(ValueError):
            FT.write_draft("a.md", "again")
        self.assertIn("drafts/a.md", [pathlib.Path(x).as_posix() for x in FT.list_files()])

    def test_traversal_rejected(self):
        with self.assertRaises(ValueError):
            FT.read_file("../secret.txt")


if __name__ == "__main__":
    unittest.main()
