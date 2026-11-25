# -*- coding: utf-8 -*-
"""Agent Registry：身份、负责人、scope、停用。"""
import pathlib
import tempfile
import unittest
from unittest import mock

from pai import agent_registry as AR


class AgentRegistryTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.p = mock.patch.object(AR, "PATH", pathlib.Path(self.td.name) / "agents.json")
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.td.cleanup()

    def test_builtins(self):
        self.assertTrue(AR.allowed("daily", "job:create"))
        self.assertFalse(AR.allowed("daily", "job:claim"))
        self.assertTrue(AR.allowed("system", "approve"))

    def test_register_and_revoke(self):
        a = AR.register("writer", "Boss", ["job:create", "read"])
        self.assertTrue(AR.allowed(a["id"], "job:create"))
        AR.revoke(a["id"])
        self.assertFalse(AR.active(a["id"]))
        self.assertFalse(AR.allowed(a["id"], "job:create"))

    def test_builtin_cannot_revoke(self):
        with self.assertRaises(ValueError):
            AR.revoke("system")

    def test_enable(self):
        a = AR.register("x", scopes=["read"])
        AR.revoke(a["id"])
        AR.enable(a["id"])
        self.assertTrue(AR.active(a["id"]))


if __name__ == "__main__":
    unittest.main()
