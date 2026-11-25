# -*- coding: utf-8 -*-
"""M8 只读工具 v0：注册表契约 + 三个只读工具 + 无写入证明。"""
import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pai import tools as M8          # noqa: E402
from pai.memory import Memory        # noqa: E402


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.mem = Memory(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.mem.write_episode("user", "我爷爷1959年在挨饿")
        self.mem.upsert_fact("用户", "姓名", "阿明")
        self.mem.add_trigger("manual", "该喝水了", "你上午说过")
        self.mem.add_principle("先问清楚再下结论")
        M8.set_enabled(True)

    def tearDown(self):
        self.mem.close()
        M8.set_enabled(False)

    def test_registry_is_declarative_with_read_and_write(self):
        tools = M8.list_tools()
        names = {t["name"] for t in tools}
        self.assertEqual(names, {"recall"})
        for t in tools:
            self.assertEqual(t["permission"], "read")
            self.assertTrue(t["desc"])
            self.assertIsInstance(t["params"], list)

    def test_off_refuses_without_side_effects(self):
        M8.set_enabled(False)
        res = M8.run("recall", {"query": "爷爷"}, self.mem)
        self.assertFalse(res["ok"])
        self.assertIn("关", res["reason"])
        self.assertIsNone(res["data"])

    def test_recall_returns_items(self):
        res = M8.run("recall", {"query": "爷爷挨饿", "k": 3}, self.mem)
        self.assertTrue(res["ok"])
        self.assertLessEqual(len(res["data"]), 3)
        for row in res["data"]:
            self.assertIn(row["item"]["type"], ("episode", "fact"))

    def test_unknown_tool_is_error_not_crash(self):
        res = M8.run("delete_everything", {}, self.mem)
        self.assertFalse(res["ok"])
        self.assertIn("没有这个工具", res["reason"])

    def test_missing_query_is_error(self):
        res = M8.run("recall", {}, self.mem)
        self.assertFalse(res["ok"])
        self.assertIn("query", res["reason"])

    def test_k_is_clamped(self):
        for i in range(30):
            self.mem.write_episode("user", "床垫记录%d" % i)
        res = M8.run("recall", {"query": "床垫", "k": 999}, self.mem)
        self.assertTrue(res["ok"])
        self.assertLessEqual(len(res["data"]), M8.MAX_K)

    def test_no_writes(self):
        def counts():
            c = self.mem.conn
            return (c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0],
                    c.execute("SELECT COUNT(*) FROM facts").fetchone()[0],
                    c.execute("SELECT COUNT(*) FROM triggers").fetchone()[0],
                    c.execute("SELECT COUNT(*) FROM principles").fetchone()[0])
        before = counts()
        for name, args in (("recall", {"query": "爷爷", "k": 5}),):
            res = M8.run(name, args, self.mem)
            # 先证明工具真的跑了：三次都 ok。少了这三行，一个"什么都不干"的实现
            # 也能让下面的"行数不变"照样绿 —— 空转的守卫等于没有守卫。
            self.assertTrue(res["ok"], "%s 没跑成，无写入证明是空转" % name)
        self.assertEqual(before, counts(), "只读工具不许写库")

    def test_no_sqlite_or_network_imports(self):
        tree = ast.parse(Path(M8.__file__).read_text(encoding="utf-8"))
        mods = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods.append(node.module or "")
        for bad in ("sqlite3", "urllib", "socket", "requests", "http"):
            self.assertFalse(any(m == bad or m.startswith(bad + ".") for m in mods),
                             "只读工具不许 import %s：%r" % (bad, mods))

    def test_permission_comes_from_the_table(self):
        # 返回值里的 permission 必须抄工具表，不许在 run() 里硬编码成 "read"：
        # 否则将来加一个写工具，它会把自己报成只读，审计字段直接说谎。
        saved = M8.TOOLS["recall"]["permission"]
        try:
            M8.TOOLS["recall"]["permission"] = "write"
            res = M8.run("recall", {"query": "爷爷"}, self.mem)
            self.assertEqual(res["permission"], "write")
        finally:
            M8.TOOLS["recall"]["permission"] = saved
        M8.set_enabled(False)
        try:
            # 开关关着时还没查到工具表，权限如实报"不知道"，不许先替工具作证
            self.assertIsNone(M8.run("recall", {"query": "爷爷"}, self.mem)["permission"])
        finally:
            M8.set_enabled(True)

    def test_cli_refuses_a_missing_db_instead_of_creating_it(self):
        # Memory() 一开就建库：CLI 若不做存在性检查，路径打错 = 静默换一颗空脑子。
        missing = os.path.join(tempfile.mkdtemp(), "nope.db")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run([sys.executable, os.path.join(str(ROOT), "tools", "do.py"),
                               "recall", "爷爷", "--db", missing],
                              capture_output=True, env=env)
        self.assertNotEqual(proc.returncode, 0, "库不存在时 CLI 必须非零退出")
        self.assertFalse(os.path.exists(missing), "只读 CLI 不许把不存在的库建成空库")

    def test_capability_declared(self):
        cap = M8.CAPABILITY
        self.assertEqual(cap["switch_env"], M8.ENV_BACKEND)
        self.assertIs(cap["default_on"], False)
        self.assertIs(cap["network"], False)
        self.assertIs(cap["stores_user_text"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
