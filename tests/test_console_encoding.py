# -*- coding: utf-8 -*-
"""双击那条路的窗口是 GBK（cp936），不是 UTF-8。

为什么单独立一份测试：Boss 双击 接MiniMax-粘Key就行.bat 时，cmd 开的是 936 代码页，
Python 往控制台打一个 GBK 编不出来的字符（比如对勾 emoji）就直接
UnicodeEncodeError 崩掉 —— 整份自检报告一个字都看不到，而他看到的只是窗口一闪。
这种崩不在逻辑里，在【字符】里，普通测试跑在 UTF-8 环境下永远发现不了。

两条腿：
  1) 源码里不许有 GBK 编不出来的字符（连注释和 docstring 也算，因为 traceback 会把源码行打出来）；
  2) 行为探针：真的用 PYTHONIOENCODING=gbk 起一个子进程跑 check_key.py，
     要求它跑到最后一行「结论」，且 stdout 能用 GBK 严格解码。
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ["chat.py", "webapp.py"]
for d in ("pai", "tools", "tests"):
    SHIPPED += [f"{d}/{f}" for f in sorted(os.listdir(ROOT / d)) if f.endswith(".py")]

EXCLUDED_DIRS = ("_incoming_deepseek", "data", "__pycache__", "_backup", "_premerge")
BOM = b"\xef\xbb\xbf"


def _in_excluded_dir(rel):
    """只比目录成分：文件名里带 '_incoming_deepseek' 不算隔离区。"""
    return any(part in EXCLUDED_DIRS for part in Path(rel).parts[:-1])


def ungbk_chars(path):
    bad = set()
    # utf-8-sig：BOM 不是正文，别让 helper 把 BOM 当成不可编码字符。
    for ch in set(path.read_text(encoding="utf-8-sig")):
        try:
            ch.encode("gbk")
        except UnicodeEncodeError:
            bad.add(ch)
    return sorted(bad)


class TestShippedSourceIsConsoleSafe(unittest.TestCase):
    def test_no_char_outside_gbk(self):
        """脊柱里每个 .py（含本文件）都只许用 GBK 编得出的字符。

        隔离区 _incoming_deepseek/ 不算：那是外部 AI 递来的材料，采纳前不归我管字符。
        """
        offenders = {}
        for rel in SHIPPED:
            p = ROOT / rel
            self.assertFalse(_in_excluded_dir(rel),
                             f"名单里混进了不该扫的目录：{rel}")
            if (ungbk := ungbk_chars(p)):
                offenders[rel] = ungbk
        self.assertEqual(
            offenders, {},
            "GBK 控制台编不出这些字符，双击路径会当场崩："
            + "; ".join(f"{k} -> {''.join(v)}" for k, v in sorted(offenders.items())))

    def test_this_guard_actually_scans_something(self):
        """防我自己把名单写空 —— 空名单会让上一条永远绿。"""
        self.assertGreaterEqual(len(SHIPPED), 40)
        self.assertTrue((ROOT / "tools" / "check_key.py").exists())

    def test_no_shipped_python_starts_with_utf8_bom(self):
        """前 3 字节是 EF BB BF = BOM；源码带 BOM 会让 Windows 工具链/守卫读歪。"""
        bad = []
        for rel in SHIPPED:
            p = ROOT / rel
            if p.read_bytes()[:3] == BOM:
                bad.append(rel)
        self.assertEqual(bad, [], "这些 .py 带 UTF-8 BOM：" + "、".join(bad))

    def test_excluded_dir_guard_uses_exact_directory_parts(self):
        """反向守卫：只有整段目录名命中才算隔离区，文件名里撞字不算。"""
        self.assertTrue(_in_excluded_dir("_incoming_deepseek/a.py"))
        self.assertTrue(_in_excluded_dir("pai/data/a.py"))
        self.assertFalse(_in_excluded_dir("pai/_incoming_deepseek_notes.py"))
        self.assertFalse(_in_excluded_dir("pai/data_notes.py"))


class TestBatFilesAreAsciiOnly(unittest.TestCase):
    """★.bat 里一个中文都不许有 —— 这条是我自己踩出来的。

    cmd 按【字节】切行。GBK 汉字是两字节，第二字节常落在 0x40-0x7E，
    正好是 | 和 \\ 这些控制字符的位置，于是整行被拆断，
    报 "'xxx' is not recognized as an internal or external command"。
    我把中文提示写进 bat 试过两版，两版都断在这儿（v1 还多了 chcp 65001，断得更狠）。
    正确形状：bat 只有 ASCII 四行，中文全部由 Python 说。
    """

    def test_every_line_is_ascii_and_crlf(self):
        bats = sorted(p.name for p in ROOT.glob("*.bat"))
        self.assertTrue(bats, "根目录应该有 .bat，一条都没有说明名单写坏了")
        for name in bats:
            blob = (ROOT / name).read_bytes()
            non_ascii = sorted({b for b in blob if b > 0x7E})
            self.assertFalse(non_ascii,
                             f"{name} 里有非 ASCII 字节 {non_ascii}：cmd 会把这一行拆断")
            self.assertEqual(blob.count(b"\r"), blob.count(b"\r\n"),
                             f"{name} 里有孤立的 CR")
            self.assertEqual(blob.count(b"\n"), blob.count(b"\r\n"),
                             f"{name} 里有孤立 LF（bat 必须 CRLF）")


class TestCheckKeySurvivesAGbkConsole(unittest.TestCase):
    """★这条是行为探针，不是字符串比对：真起子进程、真用 936 编码写 stdout。"""

    def _run(self):
        env = dict(os.environ)
        # 假 Key 只为把报告推到「已配置」那一支；任何分支都不显示值，也不联网。
        env.update({"PYTHONIOENCODING": "gbk",
                    "PAI_MODEL_PROVIDER": "openai",
                    "PAI_MODEL_NAME": "MiniMax-M3",
                    "PAI_OPENAI_BASE_URL": "https://api.minimaxi.com/v1",
                    "OPENAI_API_KEY": "sk-api-FAKEONLY-for-gbk-console-test-000000"})
        return subprocess.run([sys.executable, str(ROOT / "tools" / "check_key.py")],
                              capture_output=True, cwd=str(ROOT), env=env, timeout=120)

    def test_prints_through_to_the_conclusion(self):
        p = self._run()
        self.assertNotIn("UnicodeEncodeError", p.stderr.decode("utf-8", "replace"),
                         "报告在半路崩了 —— 双击的人只会看到窗口一闪")
        out = p.stdout.decode("gbk")          # 严格解码：有一个非 GBK 字节就在这里炸
        self.assertIn("红线检查", out)
        self.assertIn("结论", out.splitlines()[-1] + out, "没跑到最后一行")
        self.assertIn("MiniMax-M3", out)
        self.assertIn("provider      : openai", out)


if __name__ == "__main__":
    unittest.main()
