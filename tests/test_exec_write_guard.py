# -*- coding: utf-8 -*-
"""P1：可执行体写保护必须钉在源码里，而且要钉在【两条】写盘路径上。

背景：护栏原先只存在于构建产物；DS 已把 code_agent.apply_file 那条搬回源码，
但 agent 还有第二条写盘路径 agent_layer/tools_exec.py:63 的 p.write_text()，
它的 _resolve() 只拦"沙箱越界 + DENY_NAMES + .env/.key/.pem/.db"，不拦可执行体。
沙箱里的 .ps1 照样能用绝对路径执行。

这个文件同时钉住两件事：
  A. tools_exec 的写路径必须拒绝可执行体，且不能把既有闸（越界/黑名单）改坏；
  B. code_agent 那条既有护栏不能被"看着有、其实没钉住"——
     test_core_exec_suffixes_present 是反缩水表针：
     只靠"遍历 EXEC_SUFFIXES 逐个断言"的测试，在有人把表清空时反而会全绿。
"""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "agent_layer")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pai import exec_guard as EG          # noqa: E402
from pai import code_agent as CA          # noqa: E402
import tools_exec as TX                   # noqa: E402

# 反缩水表针：这 20 个后缀一个都不能从 EXEC_SUFFIXES 里掉出去。
# 抄的是 exec_guard.py 里 EXEC_SUFFIXES 的当前全量，不是"挑几个常见的"。
CORE_EXEC_SUFFIXES = {
    ".bat", ".cmd", ".com", ".cpl", ".exe", ".hta", ".jar", ".js", ".jse", ".lnk",
    ".msi", ".msp", ".pif", ".ps1", ".psm1", ".scr", ".vbe", ".vbs", ".wsf", ".wsh",
}


class ToolsExecWriteGuardTest(unittest.TestCase):
    """第二条写盘路径：agent_layer/tools_exec.py"""

    def setUp(self):
        # 必须 resolve()：Windows 上 mkdtemp 可能给出 8.3 短名，而 _resolve() 两边都
        # 跑 resolve()，短名 vs 长名一比就会误判成"路径越界"。
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self._old_sb, self._old_root = TX.SANDBOX, TX.ROOT
        TX.SANDBOX = self.tmp
        TX.ROOT = self.tmp

    def tearDown(self):
        TX.SANDBOX, TX.ROOT = self._old_sb, self._old_root

    def test_write_rejects_every_executable_suffix(self):
        for sfx in sorted(EG.EXEC_SUFFIXES):
            with self.subTest(suffix=sfx):
                with self.assertRaises(PermissionError):
                    TX.execute("write_text", {"path": "evil" + sfx, "text": "x = 1"})

    def test_write_reject_leaves_no_file(self):
        """拒绝必须发生在落盘之前：不能留下半个文件。"""
        TX.execute("write_text", {"path": "ok.md", "text": "fine"})
        self.assertTrue((TX.SANDBOX / "ok.md").exists())
        with self.assertRaises(PermissionError):
            TX.execute("write_text", {"path": "evil.ps1", "text": "Write-Host pwn"})
        self.assertFalse((TX.SANDBOX / "evil.ps1").exists())

    def test_write_allows_ordinary_text(self):
        for name in ("note.md", "data.txt", "config.json", "script.py"):
            out = TX.execute("write_text", {"path": name, "text": "hello"})
            self.assertEqual(out["bytes"], 5, name)

    def test_read_path_still_allows_executables(self):
        """闸门只管写。读 .ps1 来审查是正当动作，把它一起挡了就是改坏行为。"""
        p = TX.SANDBOX / "reviewme.ps1"
        p.write_text("Get-Process", encoding="utf-8")
        out = TX.execute("read_text", {"path": "reviewme.ps1"})
        self.assertIn("Get-Process", out["text"])

    def test_existing_deny_list_still_enforced(self):
        """既有闸不能被我的补丁顶掉：.db / .env 仍然一律拒绝。"""
        for name in ("memory.db", "prod.env", "server.key"):
            (TX.SANDBOX / name).write_text("x", encoding="utf-8")
        for name in ("memory.db", "prod.env", "server.key"):
            with self.subTest(name=name):
                with self.assertRaises(PermissionError):
                    TX.execute("write_text", {"path": name, "text": "y"})

    def test_sandbox_confinement_still_enforced(self):
        outside = tempfile.mkdtemp()
        with self.assertRaises(PermissionError):
            TX.execute("write_text", {"path": os.path.join(outside, "x.md"), "text": "y"})


class CodeAgentGuardHardeningTest(unittest.TestCase):
    """第一条写盘路径：pai/code_agent.py 的 apply_file（DS 已搬回源码，这里补强）"""

    def _root(self, files):
        d = tempfile.mkdtemp()
        for name, body in files.items():
            (pathlib.Path(d) / name).write_text(body, encoding="utf-8")
        return d

    def test_core_exec_suffixes_present(self):
        """反缩水表针。没有这条，把 EXEC_SUFFIXES 清空也能全绿。"""
        missing = CORE_EXEC_SUFFIXES - set(EG.EXEC_SUFFIXES)
        self.assertFalse(missing, "EXEC_SUFFIXES 掉了这些后缀：%s" % sorted(missing))

    def test_apply_file_blocks_every_core_suffix(self):
        for sfx in sorted(CORE_EXEC_SUFFIXES):
            d = self._root({"evil" + sfx: "original"})
            with self.subTest(suffix=sfx), mock.patch.dict(os.environ, {"PAI_CODE_ROOTS": d}):
                with self.assertRaises(CA.CodeError):
                    CA.apply_file(d, "evil" + sfx, "injected", confirm=True)

    def test_apply_file_is_case_insensitive(self):
        for name in ("X.PS1", "y.Exe", "z.CmD", "w.BaT"):
            d = self._root({name: "original"})
            with self.subTest(name=name), mock.patch.dict(os.environ, {"PAI_CODE_ROOTS": d}):
                with self.assertRaises(CA.CodeError):
                    CA.apply_file(d, name, "injected", confirm=True)

    def test_reject_leaves_original_bytes_and_no_backup(self):
        """护栏必须在写之前，否则 .bak 和原文件都已经被改过了。"""
        d = self._root({"evil.ps1": "keepme"})
        with mock.patch.dict(os.environ, {"PAI_CODE_ROOTS": d}):
            with self.assertRaises(CA.CodeError):
                CA.apply_file(d, "evil.ps1", "injected", confirm=True)
        self.assertEqual((pathlib.Path(d) / "evil.ps1").read_text(encoding="utf-8"), "keepme")
        self.assertFalse((pathlib.Path(d) / "evil.ps1.bak").exists())

    def test_double_extension_uses_the_last_one(self):
        """钉住既有语义（不是新增行为）：判定只看最后一个后缀。
        payload.ps1.txt 不是可执行体，放行；notes.txt.ps1 是，拦住。"""
        d = self._root({"payload.ps1.txt": "text", "notes.txt.ps1": "ps"})
        with mock.patch.dict(os.environ, {"PAI_CODE_ROOTS": d}):
            out = CA.apply_file(d, "payload.ps1.txt", "newtext", confirm=True)
            self.assertTrue(out["changed"])
            with self.assertRaises(CA.CodeError):
                CA.apply_file(d, "notes.txt.ps1", "injected", confirm=True)

    def test_ordinary_files_still_writable(self):
        for name in ("a.md", "b.txt", "c.py", "d.json"):
            d = self._root({name: "old"})
            with self.subTest(name=name), mock.patch.dict(os.environ, {"PAI_CODE_ROOTS": d}):
                out = CA.apply_file(d, name, "new", confirm=True)
                self.assertTrue(out["changed"])

    def test_confirm_false_still_rejected_first(self):
        """confirm 检查必须仍在最前：两条闸的先后不能被补丁打乱。"""
        d = self._root({"a.md": "old"})
        with mock.patch.dict(os.environ, {"PAI_CODE_ROOTS": d}):
            with self.assertRaises(CA.CodeError):
                CA.apply_file(d, "a.md", "new", confirm=False)


if __name__ == "__main__":
    unittest.main()
