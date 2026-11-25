# -*- coding: utf-8 -*-
"""M14 语音层（能说）的行为测试。

这里钉的是四条纪律，不是"能不能发声"（发声要人耳听，测试代不了）：
  1 关掉 = 零副作用：不起进程、不改文本（ADR-032 那条口径）
  2 原文绝不进 argv，只进 stdin；也不落临时文件（防注入 + 不开新的原文住址）
  3 后端坏了 / 不是 Windows / 起不动 → 静默降级，绝不打断对话
  4 trace 里不回显原文（ADR-017 同一条红线）
"""
import ast
import contextlib
import os
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pai import voice as M14


class _Env(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       (M14.ENV_BACKEND, M14.ENV_VOICE_NAME, M14.ENV_RATE)}
        os.environ.pop(M14.ENV_BACKEND, None)
        os.environ.pop(M14.ENV_VOICE_NAME, None)
        os.environ.pop(M14.ENV_RATE, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        M14._current = None             # 每条测试自己收干净，不留"正在念"的状态


class _FakeProc:
    def __init__(self, rc=None):
        self.payload = None
        self.stdin = self._Pipe()
        self.killed = 0
        self._rc = rc

    class _Pipe:
        def __init__(self):
            self.closed = False
            self.buf = b""

        def write(self, data):
            if self.closed:
                raise ValueError("stdin 已关")
            self.buf += data

        def close(self):
            self.closed = True

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return self._rc

    def kill(self):
        self.killed += 1
        self._rc = -15


@contextlib.contextmanager
def _as_windows():
    """平台判断就地顶掉，不靠「跑测试的这台正好是 Windows」——否则那些用例是静默空跑。"""
    real = M14.platform.system
    M14.platform.system = lambda: "Windows"
    try:
        yield
    finally:
        M14.platform.system = real


def _make_recorder():
    box = {"calls": []}

    def spawn(payload, env):
        box["calls"].append((payload, env))
        return _FakeProc()
    return spawn, box


class OffIsReallyOff(_Env):
    def test_default_backend_is_off(self):
        self.assertEqual(M14.backend(), M14.BACKEND_OFF)
        self.assertFalse(M14.speaking_enabled())

    def test_off_speaks_nothing_and_starts_no_process(self):
        spawn, box = _make_recorder()
        t = M14.speak("今天还是难受", spawn=spawn)
        self.assertFalse(t["spoken"])
        self.assertEqual(box["calls"], [])            # 关态零副作用
        self.assertIn("开关", t["reason"])

    def test_unknown_backend_value_is_treated_as_off(self):
        os.environ[M14.ENV_BACKEND] = "Windowz"        # 写错值不猜
        self.assertEqual(M14.backend(), M14.BACKEND_OFF)

    def test_enabled_flag_does_not_spawn(self):
        # speaking_enabled() 只读环境变量：探测本身不许起进程
        os.environ[M14.ENV_BACKEND] = M14.BACKEND_WINDOWS
        real = M14.subprocess.Popen
        try:
            M14.subprocess.Popen = lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("开关探测不该起进程"))
            M14.speaking_enabled()
        finally:
            M14.subprocess.Popen = real


class TheWordsTravelOnlyThroughStdin(_Env):
    def setUp(self):
        super().setUp()
        os.environ[M14.ENV_BACKEND] = M14.BACKEND_WINDOWS

    def test_payload_goes_to_stdin_not_into_argv(self):
        nasty = "半句话'); Remove-Item 一切; $env:SECRET # 引号 分号 美元符"
        captured = {}
        real = M14.subprocess.Popen

        class _P(_FakeProc):
            def __init__(self, argv, **kw):
                super().__init__()
                captured["argv"] = argv

        M14.subprocess.Popen = _P
        try:
            M14._spawn_windows(nasty.encode("utf-8"), dict(os.environ))
        finally:
            M14.subprocess.Popen = real
        joined = "\x00".join(captured["argv"])
        for frag in ("半句话", "Remove-Item", "$env:SECRET"):
            self.assertNotIn(frag, joined)             # 常量脚本里不许出现这些
        self.assertEqual(len(captured["argv"]), 6)     # exe + 3 旗 + -Command + 常量脚本

    def test_the_words_appear_in_no_env_value_or_argv(self):
        spawn, box = _make_recorder()
        M14.speak("我哥查出来是癌", spawn=spawn)
        payload, env = box["calls"][0]
        self.assertEqual(payload.decode("utf-8"), "我哥查出来是癌")   # 只在这条通道里
        for k, v in env.items():
            self.assertNotIn("我哥查出来是癌", str(v), "%s 里出现了原文" % k)

    def test_trace_never_echoes_the_words(self):
        spawn, box = _make_recorder()
        t = M14.speak("我用的真名是张三丰", spawn=spawn)
        self.assertNotIn("张三丰", repr(t))
        self.assertEqual(t["chars"], 9)

    def test_voice_name_and_rate_travel_as_env_not_source(self):
        spawn, box = _make_recorder()
        M14.speak("念一句", rate="7", voice="Microsoft Huihui Desktop", spawn=spawn)
        _, env = box["calls"][0]
        self.assertEqual(env[M14.ENV_VOICE_NAME], "Microsoft Huihui Desktop")
        self.assertEqual(env[M14.ENV_RATE], "7")


class ClippingAndRate(_Env):
    def setUp(self):
        super().setUp()
        os.environ[M14.ENV_BACKEND] = M14.BACKEND_WINDOWS

    def test_long_text_is_cut_at_a_sentence_boundary(self):
        spawn, box = _make_recorder()
        body = ("甲" * 300 + "。" + "乙" * 400)
        t = M14.speak(body, spawn=spawn)
        payload = box["calls"][0][0].decode("utf-8")
        self.assertTrue(t["truncated"])
        self.assertLessEqual(len(payload), M14.MAX_SPEAK_CHARS)
        self.assertTrue(payload.endswith("。"))         # 不许劈开半句
        self.assertNotIn("乙", payload)

    def test_rate_is_clamped_into_the_supported_window(self):
        self.assertEqual(M14._clamp_rate(99), 10)
        self.assertEqual(M14._clamp_rate(-99), -10)
        self.assertEqual(M14._clamp_rate("胡说"), 0)
        self.assertEqual(M14._clamp_rate(None), 0)


class FailuresNeverInterruptTheTalk(_Env):
    def setUp(self):
        super().setUp()
        os.environ[M14.ENV_BACKEND] = M14.BACKEND_WINDOWS

    def test_spawn_oserror_degrades_to_silence(self):
        def boom(payload, env):
            raise OSError("PowerShell 不在")
        t = M14.speak("照常说下去", spawn=boom)
        self.assertFalse(t["spoken"])
        self.assertIn("静默跳过", t["reason"])
        self.assertNotIn("PowerShell 不在", t["reason"])   # 只报异常类型，不带正文

    def test_unexpected_backend_error_is_also_swallowed(self):
        def boom(payload, env):
            raise RuntimeError("后端内部炸了")
        t = M14.speak("照常说下去", spawn=boom)
        self.assertFalse(t["spoken"])
        self.assertIn("RuntimeError", t["reason"])

    def test_non_windows_platform_declines_without_touching_the_backend(self):
        real = M14.platform.system
        M14.platform.system = lambda: "Linux"
        try:
            spawn, box = _make_recorder()
            t = M14.speak("换平台也要不响", spawn=spawn)
            self.assertFalse(t["spoken"])
            self.assertEqual(box["calls"], [])
            self.assertTrue(M14.backend() == M14.BACKEND_WINDOWS)  # 配置没错，是平台不支持
        finally:
            M14.platform.system = real

    def test_blank_text_is_not_spoken(self):
        spawn, box = _make_recorder()
        t = M14.speak("   \n  ", spawn=spawn)
        self.assertFalse(t["spoken"])
        self.assertEqual(box["calls"], [])


class InterruptIsSupported(_Env):
    def setUp(self):
        super().setUp()
        os.environ[M14.ENV_BACKEND] = M14.BACKEND_WINDOWS
        M14._current = None
        self.procs = []

        def spawn(payload, env):
            p = _FakeProc()
            p.payload = payload
            self.procs.append(p)
            M14._adopt(p, payload)
            return p
        self.spawn = spawn

    def test_second_speak_kills_the_previous_one(self):
        M14.speak("第一句很长", spawn=self.spawn)
        M14.speak("第二句", spawn=self.spawn)
        self.assertEqual(self.procs[0].killed, 1)
        self.assertEqual(self.procs[1].killed, 0)

    def test_stop_kills_current_and_is_harmless_when_idle(self):
        M14.speak("正在念的话", spawn=self.spawn)
        self.assertTrue(M14.stop())
        self.assertEqual(self.procs[0].killed, 1)
        self.assertFalse(M14.stop())                   # 没在念 → 什么都不做

    def test_text_reaches_the_child_stdin(self):
        M14.speak("要念出来的内容", spawn=self.spawn)
        proc = self.procs[0]
        for _ in range(50):
            if proc.stdin.buf:
                break
            time.sleep(0.01)
        self.assertEqual(proc.stdin.buf.decode("utf-8"), "要念出来的内容")
        self.assertTrue(proc.stdin.closed)             # 写完就关，子进程才知道结束
        M14.stop()

    def test_set_enabled_roundtrip_touches_only_this_process(self):
        M14.set_enabled(True)
        self.assertEqual(M14.backend(), M14.BACKEND_WINDOWS)
        M14.set_enabled(False)
        self.assertEqual(M14.backend(), M14.BACKEND_OFF)
        self.assertEqual(os.environ[M14.ENV_BACKEND], M14.BACKEND_OFF)


class WaitSemantics(_Env):
    """tools/say.py 要"念完再返回"，对话里要"边念边聊"——两条都得钉住。"""

    def setUp(self):
        super().setUp()
        os.environ[M14.ENV_BACKEND] = M14.BACKEND_WINDOWS
        self.main_id = threading.get_ident()
        self.done = threading.Event()

        def _proc():
            p = _FakeProc()
            p.wait = lambda timeout=None: (
                self.done.set() if threading.get_ident() == self.main_id else None)
            return p
        self.proc = _proc()

        def spawn(payload, env):
            M14._adopt(self.proc, payload)
            return self.proc
        self.spawn = spawn

    def test_wait_true_returns_only_after_the_child_is_done(self):
        t = M14.speak("慢慢念给我听", spawn=self.spawn, wait=True)
        self.assertTrue(t["spoken"])
        self.assertTrue(self.done.is_set())            # 是调用方自己等的

    def test_default_does_not_make_the_caller_wait(self):
        M14.speak("一边念一边接着聊", spawn=self.spawn)
        self.assertFalse(self.done.is_set())           # 主线程没等，聊天不被念完卡住


class VoiceInventoryTest(_Env):
    """P32 那一族里「查已装嗓子」这条：原先 inline 在 tools/say.py，上收到 pai/voice.py。
    钉的是它【只读清单】—— 不出声、不开麦克风、不起第二份登记面。"""

    def test_non_windows_degrades_without_spawning(self):
        real = M14.platform.system
        M14.platform.system = lambda: "Linux"
        try:
            called = []
            got = M14.installed_voices(run=lambda: called.append(1) or "")
        finally:
            M14.platform.system = real
        self.assertEqual(got["rows"], [])
        self.assertIn("Windows", got["reason"])
        self.assertEqual(called, [], "非 Windows 却起了 PowerShell")

    def test_rows_are_parsed_and_blank_lines_dropped(self):
        with _as_windows():
            got = M14.installed_voices(
                run=lambda: "Microsoft Huihui Desktop | zh-CN\r\n\r\n"
                            "  Microsoft Zira | en-US  \r\n")
        self.assertEqual(got["rows"], ["Microsoft Huihui Desktop | zh-CN",
                                       "Microsoft Zira | en-US"])
        self.assertEqual(got["reason"], "")

    def test_empty_inventory_says_so_instead_of_returning_a_green_empty(self):
        """一个都没装要明说，不能给一张空表加空原因 —— 调用方会把「没嗓子」当「查成功」。"""
        with _as_windows():
            got = M14.installed_voices(run=lambda: "   \n\n")
        self.assertEqual(got["rows"], [])
        self.assertIn("都没装", got["reason"])

    def test_backend_errors_never_reach_the_caller(self):
        for boom, want in ((OSError("PowerShell 不在"), "PowerShell 起不动"),
                           (RuntimeError("炸了"), "静默跳过")):
            def run(b=boom):
                raise b
            with _as_windows():
                got = M14.installed_voices(run=run)
            self.assertEqual(got["rows"], [])
            self.assertIn(want, got["reason"])

    def test_listing_script_never_speaks_and_never_opens_the_mic(self):
        src = M14._PS_LIST
        for bad in (".Speak(", "SelectVoice", "Add-Type -AssemblyName System.Speech.Recognition"):
            self.assertNotIn(bad, src, "%s 会让『查清单』变成『出声/采集』" % bad)
        for bad in ("Out-File", "Set-Content", "Add-Content"):
            self.assertNotIn(bad, src, "%s 会新开一个原文住址" % bad)
        self.assertIn("GetInstalledVoices", src)

    def test_no_user_text_can_reach_the_listing_script(self):
        """清单这条走常量 argv：正文里没有占位符、也不许有 shell=True。
        用 AST 查，不用字符串查 —— 第一版拿 assertNotIn('shell=True') 扫全文，
        结果被一句解释「无 shell=True」的注释顶红，守卫自己成了噪音。"""
        src = (Path(__file__).resolve().parent.parent / "pai" / "voice.py").read_text(
            encoding="utf-8")
        self.assertNotIn("$env:", M14._PS_LIST, "清单脚本不读任何外部文本变量")
        self.assertNotIn("ReadToEnd", M14._PS_LIST, "清单脚本不收 stdin，也就没地方灌原文")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call):
                names = [k.arg for k in node.keywords]
                self.assertNotIn("shell", names, "有子进程开了 shell=True = 注入面")


if __name__ == "__main__":
    unittest.main()
