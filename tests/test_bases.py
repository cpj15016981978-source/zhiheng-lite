# -*- coding: utf-8 -*-
import json
import os
import pathlib
import tempfile
import unittest

from pai import profile, task_ledger
from pai import skill_library as S


class ProfileTest(unittest.TestCase):
    def test_work_uses_legacy_db(self):
        self.assertTrue(str(profile.db_path("work")).endswith("data\\memory.db"))

    def test_side_is_isolated(self):
        p = str(profile.db_path("side"))
        self.assertIn("profiles", p)
        self.assertIn("side", p)
        self.assertNotEqual(p, str(profile.db_path("work")))

    def test_unknown_rejected(self):
        with self.assertRaises(profile.ProfileError):
            profile.db_path("../escape")


class TaskLedgerTest(unittest.TestCase):
    def setUp(self):
        self.db = pathlib.Path(tempfile.mkdtemp()) / "tasks.db"
        self.t = task_ledger.TaskLedger(self.db)

    def tearDown(self):
        self.t.close()

    def test_lifecycle(self):
        row = self.t.create("work", "报价单", "做一份报价单", owner="boss")
        self.assertEqual(row["state"], "queued")
        row = self.t.start(row["id"])
        self.assertEqual(row["state"], "running")
        self.assertEqual(row["attempts"], 1)
        row = self.t.fail(row["id"], "缺价格")
        self.assertEqual(row["state"], "failed")
        row = self.t.retry(row["id"])
        self.assertEqual(row["state"], "queued")
        self.t.start(row["id"])
        row = self.t.fail(row["id"], "又缺价格")
        self.assertEqual(row["state"], "failed")
        row = self.t.retry(row["id"])
        self.t.start(row["id"])
        row = self.t.fail(row["id"], "第三次")
        self.assertEqual(row["state"], "needs_human")
        self.assertEqual(row["attempts"], 3)

    def test_idempotency(self):
        a = self.t.create("work", "日报", "做日报", idempotency_key="k1")
        b = self.t.create("work", "日报", "做日报", idempotency_key="k1")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(self.t.list("work")), 1)

    def test_counts(self):
        self.t.create("work", "a", "a")
        self.t.create("side", "b", "b")
        self.assertEqual(self.t.counts("work").get("queued"), 1)
        self.assertEqual(self.t.counts("side").get("queued"), 1)

    def test_cancel_is_registered(self):
        row = self.t.create("work", "退单", "客户退单")
        row = self.t.cancel(row["id"], "客户取消", owner="boss")
        self.assertEqual(row["state"], "cancelled")
        self.assertEqual(row["error"], "客户取消")
        self.assertEqual(row["owner"], "boss")

    def test_profiles_are_isolated(self):
        self.t.create("work", "同键", "work", idempotency_key="same")
        self.t.create("side", "同键", "side", idempotency_key="same")
        self.assertEqual(len(self.t.list("work")), 1)
        self.assertEqual(len(self.t.list("side")), 1)


class SkillVersionTest(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp()) / "skills"
        os.environ["PAI_SKILLS"] = "on"
        self.lib = S.SkillLibrary(self.root)

    def tearDown(self):
        os.environ.pop("PAI_SKILLS", None)

    def _make(self, name, status="draft"):
        d = self.root / name
        d.mkdir(parents=True)
        meta = {
            "name": name, "version": "1", "description": "demo",
            "when": "test", "inputs": ["x"], "outputs": ["y"],
            "tools": [], "permissions": [], "tests": ["echo"], "entry": "echo",
            "status": status,
        }
        (d / "SKILL.json").write_text(json.dumps(meta), encoding="utf-8")
        (d / "SKILL.md").write_text("# demo", encoding="utf-8")

    def test_draft_hidden_until_approved(self):
        self._make("demo", "draft")
        self.assertEqual(self.lib.list(), [])
        with self.assertRaises(S.SkillError):
            self.lib.load("demo")
        self.lib.approve("demo", by="boss")
        self.assertEqual(len(self.lib.list()), 1)
        self.assertEqual(self.lib.load("demo")["approved_by"], "boss")

    def test_disable_and_rollback(self):
        self._make("demo", "approved")
        self.lib.approve("demo", by="boss")
        meta_path = self.root / "demo" / "SKILL.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["description"] = "bad"
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        self.assertEqual(self.lib.load("demo")["description"], "bad")
        self.lib.rollback("demo")
        self.assertEqual(self.lib.load("demo")["description"], "demo")
        self.lib.disable("demo")
        self.assertEqual(self.lib.list(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
