# -*- coding: utf-8 -*-
"""入口壳（tools/*.py）的能力面守卫 —— P30 结案、P32 选②，依据黑板 ADR-079。

口径的唯一住址在 tools/check_capability.py 的 docstring，这里只测、不抄第二份解释。
测的是三件事：
  ① 入口壳 import 到的 pai 模块，每一个都必须在【已登记】或【欠账名单】里 —— 名单外 = 红；
  ② 入口壳自己不许写 CAPABILITY：写了就等于开出第二份登记住址，而闸门只扫 pai/；
  ③ 入口壳不许自己动手碰采集设备（开麦 / 读按键 / 起 PowerShell 认语音）。
     P32 之前这一面是【盲区】：设备动作 inline 在 shell 里，登记面和闸门都看不见它。
     Boss 2026-10-25 选【②把设备动作上收到 pai/】，这条就是把「上收」钉成规矩：
     搬上去之后，shell 里再出现一次 ctypes / winmm / System.Speech，当场红。

③只看 AST 里的【可执行节点】，不看正文：入口壳的 docstring 本来就要解释「我为什么不算设备面」，
拿字符串扫正文会把这句解释判成违规（第一版就这么假红过一次）。所以先摘掉所有文档字符串，
再查 import、属性调用、字符串常量和 subprocess 的 argv。
"""
import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import check_capability as CC  # noqa: E402

TOOLS_DIR = Path(CC.ROOT) / "tools"

# 设备面住址黑名单：这些只许住在 pai/，出现在入口壳的可执行代码里 = P32 又开了一道口子。
BANNED_IMPORTS = {"ctypes", "wave", "winmm"}
BANNED_ATTRS = {"waveInOpen", "waveInGetNumDevs", "waveInGetDevCapsW",
                "waveInPrepareHeader", "waveInAddBuffer", "waveInStart", "waveInStop",
                "waveInReset", "waveInUnprepareHeader", "waveInClose",
                "GetAsyncKeyState", "GetKeyState", "windll", "SpeechSynthesizer",
                "SpeechRecognitionEngine"}
BANNED_SCRIPT_MARKS = ("Add-Type -AssemblyName System.Speech",
                       "New-Object System.Speech",
                       "SetInputToDefaultAudioDevice", "SetInputToWaveFile")
BANNED_ARGV = {"powershell", "powershell.exe", "cmd", "cmd.exe"}


def _pai_modules_used(path):
    """AST 取出这个入口壳 import 的 pai 顶层模块名。绝不 import 它：一 import 就开麦、连库。"""
    names = set()
    for node in ast.walk(ast.parse(Path(path).read_bytes().decode("utf-8"))):
        if isinstance(node, ast.ImportFrom):
            parts = (node.module or "").split(".")
            if parts[0] != "pai":
                continue
            if len(parts) == 1:
                names.update("%s.py" % alias.name for alias in node.names)   # from pai import voice
            else:
                names.add("%s.py" % parts[1])                                # from pai.memory import Memory
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "pai" and len(parts) > 1:
                    names.add("%s.py" % parts[1])                            # import pai.voice as v
    return names


def claimed_registry():
    """pai/ 里【已登记】或【挂在欠账名单】的模块；先确认闸门对 pai/ 本身没违规。"""
    registered, pending, violations = CC.scan()
    if violations:
        raise AssertionError("闸门对 pai/ 就有违规，先修那条再来跑这条守卫：%s" % violations)
    return set(registered) | set(pending) | set(CC.STRUCTURAL)


def unclaimed_uses(tools_dir, ok):
    """{入口壳: [用到但没人认领的 pai 模块]}；空表 = 健康。"""
    out = {}
    for f in sorted(Path(tools_dir).glob("*.py")):
        missing = sorted(_pai_modules_used(f) - ok)
        if missing:
            out[f.name] = missing
    return out


def shells_that_self_register(tools_dir):
    """在 tools/ 里自己写了 CAPABILITY 字典的入口壳（应当一件都没有）。"""
    out = []
    for f in sorted(Path(tools_dir).glob("*.py")):
        fields, _ = CC._capability_node(f.read_bytes().decode("utf-8"))
        if fields is not None:
            out.append(f.name)
    return out


def _docstring_ids(tree):
    """把模块/类/函数的文档字符串节点挑出来 —— 它们是解释，不是动作，不参与设备面判定。"""
    nodes = [tree]
    for n in ast.walk(tree):
        if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            nodes.append(n)
    out = set()
    for scope in nodes:
        body = getattr(scope, "body", None) or []
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            out.add(id(body[0].value))
    return out


def _literal_text(node):
    """常量字符串，或字符串常量拼出来的赋值（_PS = r'''...''' 这种）；文档字符串一律跳过。"""
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def device_actions_in_shell(src):
    """这个入口壳自己动手的设备/子进程动作（P32 ②之后应当一件都没有）。"""
    tree = ast.parse(src)
    docs = _docstring_ids(tree)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in BANNED_IMPORTS:
                    hits.append("import %s" % alias.name)
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if top in BANNED_IMPORTS:
                hits.append("from %s import ..." % node.module)
            for alias in node.names:
                if alias.name in BANNED_ATTRS:
                    hits.append("from ... import %s" % alias.name)
        elif isinstance(node, ast.Attribute):
            if node.attr in BANNED_ATTRS:
                hits.append("%s()" % node.attr)
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in ("run", "Popen", "call", "check_output", "check_call"):
                for arg in node.args[:1] + [kw.value for kw in node.keywords
                                            if kw.arg in (None, "cmd", "args", "command")]:
                    for item in (arg.elts if isinstance(arg, (ast.List, ast.Tuple)) else [arg]):
                        text = _literal_text(item)
                        if text and text.strip().lower() in BANNED_ARGV:
                            hits.append("起子进程：%s" % text)
            for arg in node.args:
                text = _literal_text(arg)
                if text is not None and id(arg) not in docs:
                    for mark in BANNED_SCRIPT_MARKS:
                        if mark in text:
                            hits.append("脚本正文里写着 %s" % mark)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    text = _literal_text(node.value)
                    if text:
                        for mark in BANNED_SCRIPT_MARKS:
                            if mark in text:
                                hits.append("常量 %s 里写着 %s" % (target.id, mark))
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def shells_with_device_actions(tools_dir):
    """{入口壳: [自己动手的设备动作]}；空表 = P32② 收到位了。"""
    out = {}
    for f in sorted(Path(tools_dir).glob("*.py")):
        hits = device_actions_in_shell(f.read_bytes().decode("utf-8"))
        if hits:
            out[f.name] = hits
    return out


class EntryShellSurfaceTest(unittest.TestCase):

    def test_entry_shells_use_only_claimed_pai_modules(self):
        self.assertEqual(unclaimed_uses(TOOLS_DIR, claimed_registry()), {},
                         "入口壳用到了没登记也没认领的 pai 模块")

    def test_entry_shells_do_not_self_register(self):
        self.assertEqual(shells_that_self_register(TOOLS_DIR), [],
                         "tools/ 里不许写 CAPABILITY：登记面只在 pai/（ADR-079）")

    def test_entry_shells_do_not_operate_devices_themselves(self):
        """P32②：开麦 / 读按键 / 认语音全住 pai/，入口壳只许调用、不许自己下场。"""
        self.assertEqual(shells_with_device_actions(TOOLS_DIR), {},
                         "入口壳又自己碰设备了：登记面看不见它，开关也挡不住它")

    def test_push_to_talk_delegates_to_the_registered_voice_ear_memory(self):
        used = _pai_modules_used(TOOLS_DIR / "push_to_talk.py")
        self.assertEqual(used, {"ear.py", "voice.py", "memory.py"},
                         "P30 的现场：X6 入口壳动用这三样，三样都得住在一个登记面里")
        from pai import ear as X6
        for name in ("record_until", "recognize_wav", "discard_record", "key_down"):
            self.assertTrue(callable(getattr(X6, name, None)),
                            "上收不能只把刀放下：%s 得真住在 pai/ear.py 里" % name)

    def test_off_state_exit_code_is_not_green(self):
        """开关没拧开时，入口壳的【退出码】也不许是 0 —— 双击的人只看得到 0/非 0。
        这条不碰设备：--wav 指一只不存在的文件，recognize_wav 在开任何设备之前就回绝。"""
        env = dict(os.environ)
        env.pop("PAI_EAR_BACKEND", None)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run([sys.executable, "-X", "utf8",
                               str(TOOLS_DIR / "push_to_talk.py"),
                               "--keep-switch", "--wav", "no_such_zz.wav"],
                              cwd=str(Path(CC.ROOT)), env=env,
                              capture_output=True, timeout=120)
        out = proc.stdout.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 2, out + proc.stderr.decode("utf-8", "replace")[-200:])
        self.assertIn("关", out)

    def test_guard_actually_goes_red(self):
        """反向证明：造一个 import 未登记模块的入口壳，这条守卫必须点名（不是橡皮图章）。"""
        d = Path(tempfile.mkdtemp())
        ghost = "ghost_capability"                      # pai/ 里没有这个名字，必然落在名单外
        self.assertNotIn("%s.py" % ghost, claimed_registry())
        (d / "shell_new.py").write_text("from pai import %s\n" % ghost, encoding="utf-8")
        (d / "shell_fake.py").write_text(
            "import pai.voice\nCAPABILITY = {'name': 'x'}\n", encoding="utf-8")
        ok = claimed_registry()
        self.assertEqual(unclaimed_uses(d, ok), {"shell_new.py": ["%s.py" % ghost]})
        self.assertEqual(shells_that_self_register(d), ["shell_fake.py"])

    def test_device_guard_actually_goes_red(self):
        """③的红证明：三件假壳各踩一种（import ctypes / 直接调 WinAPI / inline PowerShell 脚本），
        都得点名；再补一件【只有 docstring 提到设备】的壳，它必须不被点名 ——
        否则这条守卫是在查解释而不是查动作。"""
        d = Path(tempfile.mkdtemp())
        (d / "shell_ctypes.py").write_text(
            "import ctypes\nctypes.windll.user32.GetAsyncKeyState(0x77)\n", encoding="utf-8")
        (d / "shell_winmm.py").write_text(
            "from ctypes import windll\nwindll.winmm.waveInOpen(0,0,0,0,0,0)\n",
            encoding="utf-8")
        (d / "shell_ps.py").write_text(
            "import subprocess\n_PS = r'Add-Type -AssemblyName System.Speech'\n"
            "subprocess.run(['powershell', '-Command', _PS])\n", encoding="utf-8")
        (d / "shell_prose.py").write_text(
            '"""这件壳【不开麦克风】：waveInOpen、GetAsyncKeyState 都住在 pai/ear.py。\n'
            '正文里提到 System.Speech 只是解释，不是动作。"""\n', encoding="utf-8")
        got = shells_with_device_actions(d)
        self.assertEqual(sorted(got), ["shell_ctypes.py", "shell_ps.py", "shell_winmm.py"],
                         got)
        self.assertIn("GetAsyncKeyState()", str(got["shell_ctypes.py"]))
        self.assertIn("waveInOpen()", str(got["shell_winmm.py"]))
        self.assertIn("起子进程：powershell", got["shell_ps.py"])


if __name__ == "__main__":
    unittest.main()
