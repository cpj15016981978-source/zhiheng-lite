# -*- coding: utf-8 -*-
"""2026-11-15 三件点头项的守卫：默认路径不出项目、台账状态机、ollama 只许回环。

每条都带反向证明（把刀拔了要看见红），不然只是橡皮图章。
"""
import os
import pathlib
import tempfile
import unittest

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pai import model_gateway as MG          # noqa: E402
from pai import profile as PF                # noqa: E402
from pai import paths as PATHS
from pai import task_ledger as TL            # noqa: E402

PROJECT_DATA = str(PATHS.DATA_DIR).lower()


class DefaultPathsStayInsideProject(unittest.TestCase):
    def test_task_db_default_is_project_data(self):
        # 不实例化真库：只查那条默认表达式的落点。
        default = str(PF.data_root() / "tasks.db").lower()
        self.assertTrue(default.startswith(PROJECT_DATA), default)
        self.assertNotIn("d:\\dsh", default)

    def test_skills_dir_default_is_project_data(self):
        old = os.environ.pop("PAI_SKILLS_DIR", None)
        try:
            got = str(PF.skills_dir("work")).lower()
        finally:
            if old is not None:
                os.environ["PAI_SKILLS_DIR"] = old
        self.assertTrue(got.startswith(PROJECT_DATA), got)
        self.assertNotIn("d:\\dsh", got)

    def test_env_override_still_wins(self):
        # 反向证明：显式 PAI_SKILLS_DIR 时上面那条默认不算硬编码死了。
        tmp = tempfile.mkdtemp()
        os.environ["PAI_SKILLS_DIR"] = tmp
        try:
            self.assertEqual(str(PF.skills_dir("work")), tmp)
        finally:
            os.environ.pop("PAI_SKILLS_DIR", None)

    def test_task_db_env_override_wins(self):
        tmp = str(pathlib.Path(tempfile.mkdtemp()) / "tasks.db")
        os.environ["PAI_TASK_DB"] = tmp
        try:
            led = TL.TaskLedger()
            self.assertEqual(led.db_path, tmp)
            led.close()
        finally:
            os.environ.pop("PAI_TASK_DB", None)


class TaskLedgerStateMachine(unittest.TestCase):
    def setUp(self):
        self.db = str(pathlib.Path(tempfile.mkdtemp()) / "tasks.db")
        self.t = TL.TaskLedger(self.db)

    def tearDown(self):
        self.t.close()

    def _new(self):
        return self.t.create("work", "日报", "做日报")["id"]

    def test_cannot_claim_success_without_running(self):
        tid = self._new()
        with self.assertRaises(ValueError):
            self.t.transition(tid, "succeeded", result="做完了")
        self.assertEqual(self.t.get(tid)["state"], "queued")

    def test_retry_then_success_still_needs_a_run(self):
        tid = self._new()
        self.t.start(tid)
        self.t.transition(tid, "failed", error="缺价格")
        self.t.retry(tid)
        self.assertEqual(self.t.get(tid)["state"], "queued")
        with self.assertRaises(ValueError):
            self.t.transition(tid, "succeeded", result="补一句就做完了")

    def test_terminal_states_are_frozen(self):
        tid = self._new()
        self.t.start(tid)
        self.t.transition(tid, "succeeded", result="好了")
        with self.assertRaises(ValueError):
            self.t.transition(tid, "queued")
        with self.assertRaises(ValueError):
            self.t.transition(tid, "running")

    def test_waiting_approval_needs_an_approver(self):
        tid = self._new()
        self.t.start(tid)
        self.t.transition(tid, "waiting_approval")
        with self.assertRaises(ValueError):
            self.t.transition(tid, "running")
        with self.assertRaises(ValueError):
            self.t.transition(tid, "running", approver="chuixue")
        row = self.t.transition(tid, "running", approver="Boss")
        self.assertEqual(row["approved_by"], "boss")
        self.assertEqual(row["state"], "running")

    def test_start_bumps_attempts_through_the_machine(self):
        tid = self._new()
        row = self.t.start(tid)
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["state"], "running")
        with self.assertRaises(ValueError):
            self.t.start(tid)


class OllamaStaysOnLoopback(unittest.TestCase):
    def test_default_url_passes(self):
        self.assertEqual(MG._ollama_url(),
                         "http://127.0.0.1:11434/api/chat")

    def test_remote_url_is_refused(self):
        # 反向守卫：把回环这刀拔了（指到远端），调用必须当场红，不是悄悄出网。
        old = MG.OLLAMA_URL
        for bad in ("http://10.0.0.5:11434/api/chat",
                    "https://ollama.example.com/api/chat"):
            MG.OLLAMA_URL = bad
            try:
                with self.assertRaises(MG.ModelGatewayError):
                    MG._ollama_url()
            finally:
                MG.OLLAMA_URL = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
