# -*- coding: utf-8 -*-
"""Standard-library tests for the Lite CLI and core-model structure."""
import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import cli
from core_model import CoreModel, CoreModelError


class CliTest(unittest.TestCase):
    def test_version(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["version"])
        self.assertEqual(rc, 0)
        self.assertIn("zhiheng-lite", buf.getvalue())
        self.assertIn("provider=", buf.getvalue())

    def test_doctor_ok(self):
        fake = mock.Mock()
        fake.health.return_value = {"ok": True, "provider": "core", "model": "qwen3-4b-instruct-q4",
                                    "runtime": "runtime/llama-server.exe", "model_path": "models/core.gguf"}
        with mock.patch.object(cli, "core", return_value=fake):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["doctor"])
        self.assertEqual(rc, 0)
        self.assertIn("status: ok", buf.getvalue())

    def test_doctor_missing_core(self):
        fake = mock.Mock()
        fake.health.return_value = {"ok": False, "provider": "core", "model": "qwen3-4b-instruct-q4",
                                    "runtime": "runtime/llama-server.exe", "model_path": "models/core.gguf",
                                    "error": "缺少核心模型"}
        with mock.patch.object(cli, "core", return_value=fake):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["doctor"])
        self.assertEqual(rc, 1)
        self.assertIn("MISSING", buf.getvalue())


class OfficePipelineTest(unittest.TestCase):
    def test_minutes(self):
        class Fake:
            def chat(self, messages):
                self.messages = messages
                return "## 摘要\n测试\n## 待办\n| 事项 | 负责人 | 时间 |"
        out = __import__("office_pipeline").run(Fake(), "minutes", "开会：明天交报表", "测试会")
        self.assertEqual(out["task"], "minutes")
        self.assertIn("待办", out["content"])
        self.assertEqual(out["title"], "测试会")

    def test_rejects_empty(self):
        class Fake:
            def chat(self, messages): return "x"
        with self.assertRaises(ValueError):
            __import__("office_pipeline").run(Fake(), "summary", "")


class CoreModelTest(unittest.TestCase):
    def test_missing_runtime_is_clear(self):
        m = CoreModel({"runtime": "runtime/does-not-exist.exe",
                       "model_path": "models/does-not-exist.gguf"})
        self.assertFalse(m.health()["ok"])
        with self.assertRaises(CoreModelError):
            m.chat([{"role": "user", "content": "hi"}])


if __name__ == "__main__":
    unittest.main()
