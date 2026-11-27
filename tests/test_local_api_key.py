# -*- coding: utf-8 -*-
"""本地 /v1 默认 key：首次生成、后续复用。"""
import pathlib
import tempfile
import unittest

import webapp


class LocalApiKeyTest(unittest.TestCase):
    def test_key_is_stable_and_not_empty(self):
        old = webapp.PATHS.DATA_DIR
        webapp.PATHS.DATA_DIR = pathlib.Path(tempfile.mkdtemp())
        try:
            a = webapp._local_api_key()
            b = webapp._local_api_key()
            self.assertTrue(a)
            self.assertEqual(a, b)
            self.assertTrue((webapp.PATHS.DATA_DIR / "local_api_key").is_file())
        finally:
            webapp.PATHS.DATA_DIR = old


if __name__ == "__main__":
    unittest.main()
