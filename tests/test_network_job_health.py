# -*- coding: utf-8 -*-
"""联网策略 + 作业健康。"""
import os
import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock

from pai import job_health as JH
from pai import job_runner as JR
from pai import network_policy as NP


class NetworkTest(unittest.TestCase):
    def test_toggle(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(NP, "FLAG", pathlib.Path(d) / "n.json"), \
                mock.patch.dict("os.environ", {}, clear=False):
            self.assertFalse(NP.set_online(False)["online"])
            self.assertEqual(__import__("os").environ["PAI_OFFLINE"], "1")
            self.assertTrue(NP.set_online(True)["online"])
            self.assertEqual(__import__("os").environ["PAI_OFFLINE"], "0")


class JobHealthTest(unittest.TestCase):
    def test_counts(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "jobs.db"
            c = sqlite3.connect(p)
            c.execute("CREATE TABLE jobs(id TEXT,title TEXT,status TEXT,error TEXT,updated_at TEXT)")
            c.executemany("INSERT INTO jobs VALUES(?,?,?,?,?)",
                          [("1", "过期", "expired", "ttl", "2026-11-20"),
                           ("2", "失败", "failed", "err", "2026-11-20")])
            c.commit(); c.close()
            with mock.patch.object(JR, "DB_PATH", p):
                s = JH.summary()
                self.assertEqual(s["expired"], 1)
                self.assertEqual(s["failed"], 1)
                self.assertIn("作业异常", JH.text())


class NetworkDefaultOfflineTest(unittest.TestCase):
    """P174（Boss 2026-11-22 ②）：缺省离线、联网须显式开。四条正向＋一条拔刀。

    拔刀那条把 DEFAULT_ONLINE patch 回 True：它证明上面几条断言真的咬在这个缺省值上，
    而不是恒绿（缺省值搬走/改回去，尺必须跟着红）。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.flag = pathlib.Path(self.tmp.name) / "n.json"
        self.pf = mock.patch.object(NP, "FLAG", self.flag)
        self.pf.start()
        self.pe = mock.patch.dict("os.environ", {}, clear=False)
        self.pe.start()

    def tearDown(self):
        self.pf.stop()
        self.pe.stop()
        self.tmp.cleanup()

    def test_no_flag_means_offline(self):
        self.assertFalse(self.flag.exists())
        self.assertIs(NP.status()["online"], False)
        self.assertIs(NP.apply()["online"], False)
        self.assertEqual(os.environ["PAI_OFFLINE"], "1")

    def test_corrupt_flag_means_offline(self):
        self.flag.write_text("{不是 json", encoding="utf-8")
        self.assertIs(NP.status()["online"], False)
        self.assertEqual(NP.apply()["online"], False)
        self.assertEqual(os.environ["PAI_OFFLINE"], "1")

    def test_only_explicit_on_opens_the_net(self):
        self.assertEqual(NP.set_online(True)["online"], True)
        self.assertEqual(os.environ["PAI_OFFLINE"], "0")
        self.assertEqual(NP.set_online(False)["online"], False)
        self.assertEqual(os.environ["PAI_OFFLINE"], "1")

    def test_registration_says_default_off(self):
        self.assertIs(NP.CAPABILITY["default_on"], False)

    def test_reverse_proof_pulling_the_default_back_on_turns_it_red(self):
        with mock.patch.object(NP, "DEFAULT_ONLINE", True):
            self.assertIs(NP.status()["online"], True)


if __name__ == "__main__":
    unittest.main()
