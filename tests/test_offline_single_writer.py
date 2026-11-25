# -*- coding: utf-8 -*-
"""P174：PAI_OFFLINE 只有一个权威写者；界面与真值同源。

反向证明：
  - model_settings.apply 不得再写 PAI_OFFLINE；
  - 持久层写入 offline=False、权威仍判离线时，显示必须是离线；
  - 旧实现读 env/读自己 json 会在这两条上红。
"""
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from pai import model_settings as MS
from pai import network_policy as NP


class OfflineSingleWriterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.flag = pathlib.Path(self.tmp.name) / "network_policy.json"
        self.ms = pathlib.Path(self.tmp.name) / "model_settings.json"
        self.p1 = mock.patch.object(NP, "FLAG", self.flag)
        self.p2 = mock.patch.object(MS, "PATH", self.ms)
        self.p3 = mock.patch.dict(os.environ, {}, clear=False)
        self.p1.start()
        self.p2.start()
        self.p3.start()
        os.environ.pop("PAI_OFFLINE", None)
        os.environ.pop("PAI_MODEL_PROVIDER", None)
        os.environ.pop("PAI_MODEL_NAME", None)

    def tearDown(self):
        self.p3.stop()
        self.p2.stop()
        self.p1.stop()
        self.tmp.cleanup()

    def _authority_offline(self):
        self.flag.write_text(json.dumps({"online": False}), encoding="utf-8")

    def test_model_settings_never_writes_offline_env(self):
        self._authority_offline()
        os.environ["PAI_OFFLINE"] = "0"  # 故意留一个旧值
        MS.save("deepseek", "deepseek-chat", offline=False)
        MS.apply()
        self.assertEqual(os.environ.get("PAI_OFFLINE"), "0",
                         "model_settings.apply 不得再写 PAI_OFFLINE")

    def test_persisted_offline_false_cannot_override_authority(self):
        self._authority_offline()
        os.environ["PAI_OFFLINE"] = "0"  # 旧实现会读这个
        MS.save("deepseek", "deepseek-chat", offline=False)
        MS.apply()
        self.assertTrue(NP.is_offline())
        self.assertTrue(MS.current()["offline"],
                        "显示必须来自 network_policy，不是持久层 offline 字段")

    def test_save_strips_conflicting_offline_field(self):
        MS.save("ollama", "qwen3-4b-instruct:latest", offline=False)
        d = json.loads(self.ms.read_text(encoding="utf-8"))
        self.assertNotIn("offline", d)

    def test_display_and_authority_are_same_source(self):
        self._authority_offline()
        MS.save("deepseek", "deepseek-chat")
        MS.apply()
        self.assertEqual(MS.current()["offline"], NP.is_offline())

    def test_no_direct_pai_offline_writer_outside_network_policy(self):
        files = [pathlib.Path(MS.__file__).resolve(),
                 pathlib.Path(MS.__file__).resolve().parents[1] / "webapp.py"]
        for p in files:
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn('os.environ["PAI_OFFLINE"] =', text,
                             "%s 仍在直写 PAI_OFFLINE" % p)


if __name__ == "__main__":
    unittest.main()
