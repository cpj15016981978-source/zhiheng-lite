# -*- coding: utf-8 -*-
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai.memory import Memory  # noqa: E402
from pai import manifest as MF  # noqa: E402
from pai import self_facts as SF  # noqa: E402


class SelfFactsTest(unittest.TestCase):
    def setUp(self):
        os.environ["PAI_SELF_FACTS"] = "on"
        os.environ["PAI_MANIFEST"] = "on"
        self.m = Memory(str(pathlib.Path(tempfile.mkdtemp()) / "sf.db"))

    def tearDown(self):
        os.environ.pop("PAI_SELF_FACTS", None)
        os.environ.pop("PAI_MANIFEST", None)

    def test_manifest_shape(self):
        d = MF.build(self.m)
        self.assertRegex(d["code_sha12"], r"^[0-9a-f]{12}$")
        self.assertEqual(d["model_provider"], "mock")
        self.assertIn("训练数据", " ".join(d["unknown"]))
        self.assertTrue(d["db_path"].endswith("sf.db"))

    def test_collect_has_four_dimensions(self):
        d = SF.collect(self.m, ports={8753})
        for k in ("enabled", "existence", "identity", "origin", "boundaries", "unknown"):
            self.assertIn(k, d)
        self.assertEqual(d["existence"]["pid"], os.getpid())
        self.assertEqual(d["boundaries"]["network"], False)
        self.assertEqual(d["boundaries"]["self_approval"], False)
        self.assertIn("训练数据", d["unknown"])

    def test_c1_02_is_live_port_not_constant(self):
        off = SF.collect(self.m, ports=set())
        self.assertIsNone(off["existence"]["web_port"])
        on = SF.collect(self.m, ports={8753})
        self.assertEqual(on["existence"]["web_port"], 8753)
        self.assertTrue(on["existence"]["web_port_listening"])

    def test_switch_off_is_visible(self):
        os.environ.pop("PAI_SELF_FACTS", None)
        d = SF.collect(self.m, ports={8753})
        self.assertFalse(d["enabled"])


    def test_manifest_switch_off_is_visible(self):
        os.environ.pop("PAI_MANIFEST", None)
        d = MF.build(self.m)
        self.assertFalse(d["enabled"])
        d2 = SF.collect(self.m, ports={8753})
        self.assertFalse(d2["origin"]["enabled"])

    def test_missing_identity_says_unregistered(self):
        d = SF.collect(self.m, ports=set())
        self.assertEqual(d["identity"]["status"], "未登记")

    def test_no_secret_in_facts(self):
        s = repr(SF.collect(self.m, ports={8753}))
        self.assertNotIn("sk-", s)
        self.assertNotIn("API_KEY", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
