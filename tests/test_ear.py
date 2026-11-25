# -*- coding: utf-8 -*-
"""X6 语音输入·命令级（pai/ear.py）的行为测试。

钉的是纪律，不是"认得准不准"（准不准要人开口，测试代不了）：
  1 关掉 = 零副作用：不开麦克风、不起采集进程（ADR-032 那条口径）
  2 ★问"能不能听"这条读【永远不开设备】—— 这是它和 listen_once 的分界
  3 词表只进 stdin 的纯 ASCII JSON，绝不进 argv、不落文件、不进脚本正文
  4 后端坏了 / 没麦克风 / 超时 = 静默降级，绝不抛给上层
  5 听清了但不在词表里的话：不猜、不模糊匹配，也不许出现在理由串里
  6 词表只有一个住址，且每个词都要有落地动作（没动作的词=假能力）
  7 ★P32 选②之后：开麦/读按键/认 WAV 三样设备动作住在本模块，所以「开关关 = 一个设备也不碰、
    一个临时目录也不建」在这里是能证的（PushToTalkDeviceTest）；入口壳那边由
    tests/test_tool_entry_surface.py 守着「不许自己下场」

平台判断用 _as_windows() 就地顶掉，不靠"跑测试的这台正好是 Windows"：
否则在别的机器上这些用例是【静默空跑】，绿得没有意义。
"""
import contextlib
import json
import os
import subprocess
import sys
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pai import ear as X6

ROOT = Path(__file__).resolve().parent.parent
CHAT_SRC = (ROOT / "chat.py").read_text(encoding="utf-8")
EAR_SRC = (ROOT / "pai" / "ear.py").read_text(encoding="utf-8")


@contextlib.contextmanager
def _as_windows():
    real = X6.platform.system
    X6.platform.system = lambda: "Windows"
    try:
        yield
    finally:
        X6.platform.system = real


def _make_recorder(reply=""):
    box = {}

    def spawn(payload, env):
        box["payload"] = payload
        box["env"] = env
        return reply

    return spawn, box


class _Env(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       (X6.ENV_BACKEND, X6.ENV_TIMEOUT, "PAI_EAR_CULTURE")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class OffStateTest(_Env):
    def test_off_is_default_and_zero_side_effects(self):
        self.assertEqual(X6.backend(), X6.BACKEND_OFF)
        self.assertFalse(X6.listening_enabled())
        spawn, box = _make_recorder("别念了")
        with _as_windows():
            t = X6.listen_once(spawn=spawn)
        self.assertIsNone(t["command"])
        self.assertIn("关", t["reason"])
        self.assertEqual(box, {}, "关态却起了进程 = 违背 ADR-032（关=一个字都不改）")

    def test_bad_value_is_treated_as_off_not_guessed(self):
        os.environ[X6.ENV_BACKEND] = "windos"
        self.assertEqual(X6.backend(), X6.BACKEND_OFF)
        self.assertFalse(X6.listening_enabled())

    def test_set_enabled_only_touches_process_env(self):
        X6.set_enabled(True)
        self.assertEqual(X6.backend(), X6.BACKEND_WINDOWS)
        X6.set_enabled(False)
        self.assertEqual(X6.backend(), X6.BACKEND_OFF)

    def test_non_windows_listens_nothing(self):
        os.environ[X6.ENV_BACKEND] = X6.BACKEND_WINDOWS
        real = X6.platform.system
        X6.platform.system = lambda: "Linux"
        try:
            spawn, box = _make_recorder("别念了")
            t = X6.listen_once(spawn=spawn)
        finally:
            X6.platform.system = real
        self.assertEqual(box, {}, "非 Windows 起了 PowerShell = 未落平台却装能落")
        self.assertIn("Windows", t["reason"])


class TimeoutTest(_Env):
    def test_clamp(self):
        self.assertEqual(X6.clamp_timeout("7"), 7)
        self.assertEqual(X6.clamp_timeout(0), X6.MIN_TIMEOUT_S)
        self.assertEqual(X6.clamp_timeout(-3), X6.MIN_TIMEOUT_S)
        self.assertEqual(X6.clamp_timeout(999), X6.MAX_TIMEOUT_S)
        for bad in (None, "", "八", "  "):
            self.assertEqual(X6.clamp_timeout(bad), X6.DEFAULT_TIMEOUT_S)

    def test_child_env_gets_clamped_timeout(self):
        os.environ[X6.ENV_BACKEND] = X6.BACKEND_WINDOWS
        spawn, box = _make_recorder("")
        with _as_windows():
            t = X6.listen_once(timeout_s=900, spawn=spawn)
        self.assertEqual(box["env"][X6.ENV_TIMEOUT], str(X6.MAX_TIMEOUT_S))
        self.assertEqual(t["timeout_s"], X6.MAX_TIMEOUT_S)


class MatchTest(unittest.TestCase):
    def test_exact_hit_and_trailing_punctuation(self):
        for word in X6.COMMANDS:
            self.assertEqual(X6.match_command(word), word)
            self.assertEqual(X6.match_command(word + "。"), word)
            self.assertEqual(X6.match_command("  " + word + "！ "), word)

    def test_no_fuzzy_matching(self):
        self.assertIsNone(X6.match_command("别念"))
        self.assertIsNone(X6.match_command("别念了什么"))
        self.assertIsNone(X6.match_command("念最后一条消息"))
        self.assertIsNone(X6.match_command(""))
        self.assertIsNone(X6.match_command(None))


class PayloadTest(_Env):
    def test_payload_is_pure_ascii_and_is_the_command_list(self):
        """★这条钉的是本机实测出来的那个坑：中文原文进 PowerShell 会被 cp936 嚼碎，
        连 JSON 的引号都能被吃掉一字节，词表在 PowerShell 里数出来是【1 条乱码】而不是
        4 条 —— 语法照样装得上、永远不命中。改成纯 \\uXXXX 之后才数对。
        谁把 ensure_ascii 关掉，这条必须红。"""
        X6.set_enabled(True)
        spawn, box = _make_recorder("别念了")
        with _as_windows():
            X6.listen_once(spawn=spawn)
        payload = box.get("payload")
        self.assertIsNotNone(payload, "开态却没把词表送出去 —— 这条守卫是空的")
        self.assertLessEqual(max(payload), 127, "词表里有非 ASCII 字节 = 会被 cp936 嚼碎")
        self.assertEqual(json.loads(payload.decode("ascii")), list(X6.COMMANDS))

    def test_culture_is_forced_not_inherited_from_environment(self):
        os.environ["PAI_EAR_CULTURE"] = "en-GB"
        env = X6._base_env(8)
        self.assertEqual(env["PAI_EAR_CULTURE"], X6.RECOGNIZER_CULTURE,
                         "环境里一个旧值就能把识别语言带走")


class ScriptDisciplineTest(unittest.TestCase):
    def test_script_contains_no_command_words_and_no_file_writing(self):
        for word in X6.COMMANDS:
            self.assertNotIn(word, X6._PS, "词表被拼进脚本正文 = 注入面")
        for bad in ("Out-File", "Set-Content", "Add-Content"):
            self.assertNotIn(bad, X6._PS, "%s 会新开一个 purge 管不到的原文住址" % bad)
        self.assertIn("Environment]::Exit(0)", X6._PS, "不强制收尾，进程不退出=调用方只能等死")
        self.assertIn("RecognizeAsync", X6._PS)
        self.assertNotIn(".Recognize([TimeSpan]", X6._PS, "同步那条在交付材料里实测挂住 >60 秒")

    def test_probe_script_never_opens_the_microphone(self):
        self.assertNotIn("SetInputToDefaultAudioDevice", X6._PS_PROBE,
                         "probe 是『问能不能听』，不许把你的话录进去")
        self.assertIn("SetInputToDefaultAudioDevice", X6._PS,
                      "listen_once 不开设备就什么都听不到")

    def test_payload_never_reaches_argv_or_shell(self):
        self.assertNotIn("shell=True", EAR_SRC)
        self.assertIn("input=payload", EAR_SRC, "词表要走 stdin，不是 argv")


class DegradeTest(_Env):
    def test_no_microphone_degrades_silently(self):
        X6.set_enabled(True)

        def boom(payload, env):
            raise OSError("no audio device")

        with _as_windows():
            t = X6.listen_once(spawn=boom)
        self.assertIsNone(t["command"])
        self.assertIn("没听成", t["reason"])

    def test_any_backend_error_is_swallowed(self):
        X6.set_enabled(True)

        def boom(payload, env):
            raise RuntimeError("引擎炸了")

        with _as_windows():
            t = X6.listen_once(spawn=boom)
        self.assertIn("静默跳过", t["reason"])

    def test_silence_reports_timeout_seconds(self):
        X6.set_enabled(True)
        with _as_windows():
            t = X6.listen_once(timeout_s=5, spawn=_make_recorder("")[0])
        self.assertIsNone(t["command"], "空识别结果却报了命令")
        self.assertIn("5 秒", t["reason"])

    def test_probe_backend_error_does_not_reach_the_caller(self):
        """问一句『这台机器能不能听中文』不该把对话弄断 —— PowerShell 起不来、
        没装 System.Speech 都得降级成 can_hear=False + 一句原因。"""
        def boom(script, payload, env, timeout_s):
            raise OSError("PowerShell 不在")

        with _as_windows():
            p = X6.probe(run=boom)
        self.assertFalse(p["can_hear"])
        self.assertIn("问不了", p["reason"])
        self.assertEqual(p["cultures"], [])

    def test_probe_reports_empty_list_without_inventing_one(self):
        p = X6.probe(run=lambda script, payload, env, timeout_s: "count=0\nengine=NONE\n")
        self.assertEqual(p["cultures"], [])
        self.assertFalse(p["can_hear"])
        self.assertIn("一个都没有", p["reason"])

    def test_heard_text_off_list_is_not_echoed_in_reason(self):
        X6.set_enabled(True)
        with _as_windows():
            t = X6.listen_once(spawn=_make_recorder("我用的真名是张三丰")[0])
        self.assertIsNone(t["command"])
        self.assertNotIn("张三丰", t["reason"],
                         "不在词表里的话不许进理由串（trace 可能被打印/落盘）")
        self.assertEqual(t["text"], "我用的真名是张三丰", "界面要说清听到了什么，这一份只活在返回值里")


class VocabularyTest(unittest.TestCase):
    def test_commands_are_one_address_and_derived_from_constants(self):
        self.assertEqual(list(X6.COMMANDS),
                         [X6.CMD_STOP_SPEAK, X6.CMD_REPEAT_LAST,
                          X6.CMD_PROACTIVE_OFF, X6.CMD_PROACTIVE_ON])

    def test_every_command_has_a_landing_action_in_chat(self):
        """没落地动作的词=假能力：词表加了、界面什么都不做，比不加更坏。"""
        for name in ("CMD_STOP_SPEAK", "CMD_REPEAT_LAST", "CMD_PROACTIVE_OFF",
                     "CMD_PROACTIVE_ON"):
            self.assertIn("X6." + name, CHAT_SRC, "%s 在词表里却没人接" % name)

    def test_chat_does_not_copy_the_strings(self):
        for word in X6.COMMANDS:
            self.assertNotIn('"%s"' % word, CHAT_SRC, "chat.py 里抄了第二份词表（ADR-030）")


class _FakeWinmm:
    """假的 winmm 句柄：只记调用顺序。给了它，这条路上一个真设备也不会碰。"""

    def __init__(self, rc=0):
        self.rc = rc
        self.calls = []
        # 只有函数对象能挂 argtypes（bound method 不行）—— record_until 会写这一句
        self.waveInOpen = lambda *a: self._open(*a)

    def _open(self, *args):
        self.calls.append("open")
        return self.rc

    def waveInGetNumDevs(self):
        self.calls.append("list")
        return 0

    def __getattr__(self, name):
        if name.startswith("waveIn"):
            def call(*args):
                self.calls.append(name[len("waveIn"):].lower())
            return call
        raise AttributeError(name)


def _ptt_dirs():
    import glob
    return glob.glob(os.path.join(X6.tempfile.gettempdir(), "%s*" % X6.TEMP_DIR_PREFIX))


def _no_spawn(payload, env):
    raise AssertionError("关态却起了识别进程")


class PushToTalkDeviceTest(_Env):
    """P32 选②之后：开麦 / 读按键 / 认 WAV 住在 pai/ear.py，所以「开关挡不挡住设备」
    不再是入口壳的口头承诺，而是这里能证的事。每条都拔过刀：把 _user32/_winmm 顶成
    会炸的桩，关态还能过 = 真的没碰设备；一旦有人把检查挪到设备调用后面，立刻红。"""

    def _swap_seams(self):
        def boom(*a, **kw):
            raise AssertionError("关态碰了 ctypes 的设备句柄")
        real = (X6._user32, X6._winmm)
        X6._user32 = X6._winmm = boom
        return real

    def test_off_refuses_before_touching_any_device(self):
        real = self._swap_seams()
        before = _ptt_dirs()
        try:
            with _as_windows():
                self.assertFalse(X6.key_down("F8"))
                self.assertEqual(X6.mic_devices(), [])
                rec = X6.record_until(lambda: True)
                got = X6.recognize_wav("whatever.wav", spawn=_no_spawn)
        finally:
            X6._user32, X6._winmm = real
        self.assertIsNone(rec["path"], "关态却录出了文件")
        self.assertIn("关", rec["reason"])
        self.assertIn("关", got["reason"])
        self.assertEqual(_ptt_dirs(), before,
                         "关态却建了录音临时目录 = 留了一份没人管的音频住址")

    def test_switch_is_asked_before_the_platform(self):
        """顺序不能反：开关关着时连「这台机器支不支持」都不必问（关=一个字都不改）。"""
        with _as_windows():
            os.environ[X6.ENV_BACKEND] = "windos"
            self.assertIn("关", X6._device_refusal())
        os.environ[X6.ENV_BACKEND] = X6.BACKEND_WINDOWS
        real = X6.platform.system
        X6.platform.system = lambda: "Linux"
        try:
            self.assertIn("Windows", X6._device_refusal())
        finally:
            X6.platform.system = real

    def test_unknown_hotkey_raises_even_when_off(self):
        """按键名写错是编程错误，不降级成 False —— 否则「按了没反应」永远查不出来。"""
        for bad in ("F7", None, ""):
            with self.assertRaises(ValueError):
                X6.key_down(bad)
        with _as_windows():
            self.assertFalse(X6.key_down("f8"), "大小写该归一，且关态一律 False")

    def test_on_state_opens_once_and_always_releases(self):
        X6.set_enabled(True)
        fake = _FakeWinmm(rc=0)
        with _as_windows():
            rec = X6.record_until(lambda: True, max_s=1, winmm=fake)
        try:
            self.assertIn("open", fake.calls, "开态却没开麦")
            self.assertLess(fake.calls.index("open"), fake.calls.index("start"))
            self.assertLess(fake.calls.index("start"), fake.calls.index("stop"))
            self.assertEqual(fake.calls.count("open"), 1, "一次录像开了两只麦")
            self.assertEqual(fake.calls[-1], "close",
                             "返回前必须 waveInClose（纪律④：返回即释放）")
            self.assertTrue(rec["path"] and os.path.isfile(rec["path"]), rec["reason"])
        finally:
            X6.discard_record(rec)
        self.assertIsNone(rec["path"], "discard 之后返回值还指着文件 = 第二个人会再去读它")

    def test_open_failure_degrades_without_a_file(self):
        X6.set_enabled(True)
        fake = _FakeWinmm(rc=19)
        before = _ptt_dirs()
        with _as_windows():
            rec = X6.record_until(lambda: True, max_s=1, winmm=fake)
        self.assertIsNone(rec["path"])
        self.assertIn("开不了麦克风", rec["reason"])
        self.assertNotIn("start", fake.calls, "waveInOpen 失败后还去启停 = 拿着空句柄乱按")
        self.assertEqual(_ptt_dirs(), before)

    def test_release_path_runs_even_when_interrupted(self):
        X6.set_enabled(True)
        fake = _FakeWinmm(rc=0)

        def angry():
            raise KeyboardInterrupt                     # 按 Ctrl-C 时 finally 照样收尾

        with _as_windows():
            with self.assertRaises(KeyboardInterrupt):
                X6.record_until(angry, max_s=1, winmm=fake)
        self.assertEqual(fake.calls[-1], "close", "中途被打断也要关麦，不然设备一直占着")

    def test_recognize_wav_sends_ascii_words_and_path_via_env(self):
        X6.set_enabled(True)
        # 用一个真实存在的 WAV（recognize_wav 先核文件在不在），名字里带唯一标记好查有没有被拼进脚本
        directory = X6.tempfile.mkdtemp(prefix="pai_test_")
        path = os.path.join(directory, "zzmarker_ptt.wav")
        with wave.open(path, "wb") as wav_file:
            wav_file.setnchannels(X6.CHANNELS)
            wav_file.setsampwidth(X6.BITS // 8)
            wav_file.setframerate(X6.RATE)
            wav_file.writeframes(b"")
        spawn, box = _make_recorder("别念了")
        try:
            with _as_windows():
                got = X6.recognize_wav(path, spawn=spawn)
        finally:
            X6._discard(directory)
        payload = box.get("payload")
        self.assertIsNotNone(payload, "开态却没把词表送出去 —— 这条守卫是空的")
        self.assertLessEqual(max(payload), 127, "词表里有非 ASCII 字节 = 会被 cp936 嚼碎")
        self.assertEqual(json.loads(payload.decode("ascii")), list(X6.COMMANDS))
        self.assertEqual(box["env"][X6.ENV_WAV], path, "WAV 住址走环境变量")
        self.assertNotIn("zzmarker", X6._PS_WAV, "路径不许拼进脚本正文 = 注入面")
        self.assertEqual(got["command"], X6.CMD_STOP_SPEAK)
        self.assertEqual(got["reason"], "ok")

    def test_recognize_wav_missing_file_and_backend_errors_degrade(self):
        X6.set_enabled(True)
        here = os.path.abspath(__file__)
        with _as_windows():
            gone = X6.recognize_wav(r"C:\x\not_here.wav", spawn=_make_recorder("")[0])
            self.assertIn("没有这段音频文件", gone["reason"])

            def boom(payload, env):
                raise OSError("PowerShell 不在")
            self.assertIn("识别器不可用", X6.recognize_wav(here, spawn=boom)["reason"])

            def bang(payload, env):
                raise RuntimeError("引擎炸了")
            self.assertIn("静默跳过", X6.recognize_wav(here, spawn=bang)["reason"])

    def test_heard_text_off_list_never_enters_the_reason(self):
        X6.set_enabled(True)
        with _as_windows():
            got = X6.recognize_wav(os.path.abspath(__file__),
                                   spawn=_make_recorder("我用的真名是张三丰")[0])
        self.assertIsNone(got["command"])
        self.assertNotIn("张三丰", got["reason"])
        self.assertEqual(got["text"], "我用的真名是张三丰")

    def test_refusal_is_marked_as_not_attempted(self):
        """attempted 分的是【有没有真跑过一次识别】：关态/文件不在 = False。
        入口壳靠它决定退出码 —— 关态却回 0，就是 P33 那一族「静默的绿」。"""
        with _as_windows():
            self.assertFalse(X6.recognize_wav("whatever.wav", spawn=_no_spawn)["attempted"])
        X6.set_enabled(True)
        with _as_windows():
            self.assertFalse(X6.recognize_wav(r"C:\x\not_here.wav",
                                              spawn=_make_recorder("")[0])["attempted"])
            self.assertTrue(X6.recognize_wav(os.path.abspath(__file__),
                                             spawn=_make_recorder("")[0])["attempted"])

    def test_wav_script_keeps_the_same_discipline(self):
        for word in X6.COMMANDS:
            self.assertNotIn(word, X6._PS_WAV, "词表被拼进脚本正文 = 注入面")
        for bad in ("Out-File", "Set-Content", "Add-Content"):
            self.assertNotIn(bad, X6._PS_WAV, "%s 会新开一个 purge 管不到的原文住址" % bad)
        self.assertIn("SetInputToWaveFile", X6._PS_WAV, "认文件却不指到 WAV，等于认了个空")
        self.assertNotIn("SetInputToDefaultAudioDevice", X6._PS_WAV,
                         "--wav 这条只读文件，不许顺手把麦克风也开了")
        self.assertIn("Environment]::Exit(0)", X6._PS_WAV, "音频线程会让进程不退出，调用方只能等死")

    def test_module_never_opens_the_mic_anywhere_else(self):
        """全模块只允许两处采集设备入口：listen_once（实时）与 record_until（按住）。
        出现第三处 waveInOpen / 第三段开麦脚本 = 采集面悄悄变大，须红。
        数的是脚本常量里的调用点，不是正文：注释里提一句设备名不该把这条搞红。"""
        self.assertEqual(EAR_SRC.count("waveInOpen("), 1)
        scripts = X6._PS + X6._PS_PROBE + X6._PS_WAV
        self.assertEqual(scripts.count("SetInputToDefaultAudioDevice"), 1,
                         "只允许 _PS（实时听那一句）指默认采集设备")
        self.assertEqual(scripts.count("SetInputToWaveFile"), 1,
                         "只允许 _PS_WAV（认一段文件）读 WAV")


class RealBoxTest(unittest.TestCase):
    """这台机器的真读数：不开麦克风，只问『装了哪个识别器、引擎建不建得起来』。"""

    @unittest.skipUnless(sys.platform == "win32", "本机后端只在 Windows 上有意义")
    def test_this_box_can_hear_chinese_without_opening_the_mic(self):
        p = X6.probe()
        if X6.RECOGNIZER_CULTURE not in p["cultures"]:
            self.skipTest("这台机器没装 zh-CN 的 System.Speech 识别器；"
                          "这是环境前置缺失，不是代码红")
        self.assertTrue(p["can_hear"], p["reason"])
        self.assertEqual(p["engine"], "OK")

    @unittest.skipUnless(sys.platform == "win32", "同上")
    def test_shipped_script_parses_without_running(self):
        """只解析不执行：ScriptBlock 建得起来 = 语法过，麦克风一次也没开。"""
        env = dict(os.environ)
        env["InputScript"] = X6._PS
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
             "$s = [ScriptBlock]::Create($env:InputScript); "
             "if ($s) { 'PARSE_OK' } else { 'PARSE_EMPTY' }"],
            env=env, capture_output=True, timeout=90)
        out = proc.stdout.decode("utf-8", "replace")
        self.assertIn("PARSE_OK", out,
                      out + " || " + proc.stderr.decode("utf-8", "replace")[-400:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
