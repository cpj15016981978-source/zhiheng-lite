# -*- coding: utf-8 -*-
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agent_layer"))
from registry import Registry  # noqa: E402


class RegistryTest(unittest.TestCase):
    def test_register_get(self):
        r = Registry()
        r.register("t", 1)
        self.assertEqual(r.get("t"), 1)

    def test_scope_shadow_and_fallback(self):
        r = Registry()
        r.register("t", "global")
        r.register("t", "agent", scope="a")
        self.assertEqual(r.get("t"), "global")
        self.assertEqual(r.get("t", "a"), "agent")
        r.dispose("t", "a")
        self.assertEqual(r.get("t", "a"), "global")

    def test_dispose_is_idempotent(self):
        r = Registry()
        h = r.register("t", 1)
        h.dispose()
        h.dispose()
        self.assertIsNone(r.get("t"))

    def test_dispose_restores_previous(self):
        r = Registry()
        r.register("t", "old")
        h = r.register("t", "new")
        self.assertEqual(r.get("t"), "new")
        h.dispose()
        self.assertEqual(r.get("t"), "old")


if __name__ == "__main__":
    unittest.main(verbosity=2)
