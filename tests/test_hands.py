# -*- coding: utf-8 -*-
"""G1 手和眼（pai/hands.py）的行为测试。

钉的是 Boss 给的那四条限制，不是"识别得准不准"：
  1 ★关 = 一个字都不改：不读像素、不发键、【不建任何文件/目录】（ADR-032 + ADR-062 第①问）
  2 只碰自家窗口：标题前缀对不上就拒绝对它动手，注入的 post 一次也不许被调
  3 一次只玩一手：一个键 down+up，源码里没有循环、没有宏
  4 画面不上传不落盘：返回值里没有像素缓冲，源码不 open 文件、不碰网络
  5 按键走 PostMessage 到自家窗口的键盘目标，【不走 SendInput】（那会打进系统当前焦点）
  6 认不出来的格子如实标 other、蛇头不是正好一个就拒读 —— 不许把遮住的东西当背景猜

像素路径不是空跑：测试注入一份按 pai.game 的四色画出来的合成画面，
分类 → 重建状态 这一段是真跑的（在 C:\\tmp 之外不需要真屏幕）。
"""
import contextlib
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pai import game, hands

ROOT = Path(__file__).resolve().parent.parent
HANDS_SRC = (ROOT / "pai" / "hands.py").read_text(encoding="utf-8")
GAME_SRC = (ROOT / "pai" / "game.py").read_text(encoding="utf-8")
SHELL_SRC = (ROOT / "tools" / "pai_game.py").read_text(encoding="utf-8")

FAKE = 123456
OWN_TITLE = hands.WINDOW_TITLE_PREFIX + "贪吃蛇"


def own_scan():
    return [(FAKE, OWN_TITLE)]


def foreign_scan():
    return [(FAKE, OWN_TITLE), (999, "记事本 - 无标题.txt")]


@contextlib.contextmanager
def _not_windows():
    real = hands.platform.system
    hands.platform.system = lambda: "Linux"
    try:
        yield
    finally:
        hands.platform.system = real


@contextlib.contextmanager
def _targets(candidates):
    real = hands.key_target_candidates
    hands.key_target_candidates = lambda hwnd: list(candidates)
    try:
        yield
    finally:
        hands.key_target_candidates = real


class _Env(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get(hands.ENV_SWITCH)
        os.environ.pop(hands.ENV_SWITCH, None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop(hands.ENV_SWITCH, None)
        else:
            os.environ[hands.ENV_SWITCH] = self._saved


def pixels_of(kinds, cols=None, rows=None):
    """按 game 的四色把"格子标签"涂成一份 BGRA 画面 —— 走的是真分类，不是桩。"""
    cols = cols or game.COLS
    rows = rows or game.ROWS
    cell = game.CELL_PX
    color = {game.KIND_BG: game.COLOR_BG, game.KIND_SNAKE: game.COLOR_SNAKE,
             game.KIND_HEAD: game.COLOR_HEAD, game.KIND_FOOD: game.COLOR_FOOD,
             game.KIND_OTHER: (0, 255, 0)}
    buf = bytearray(cols * cell * rows * cell * 4)
    for r in range(rows):
        for c in range(cols):
            kind = kinds[r * cols + c]
            red, green, blue = color[kind]
            for y in range(r * cell, (r + 1) * cell):
                for x in range(c * cell, (c + 1) * cell):
                    i = (y * cols * cell + x) * 4
                    buf[i], buf[i + 1], buf[i + 2], buf[i + 3] = blue, green, red, 0
    return bytes(buf), cols * cell, rows * cell


class OffStateTest(_Env):
    def test_off_reads_nothing_and_presses_nothing(self):
        self.assertFalse(hands.enabled())
        seen = []

        def post(target, msg, wparam, lparam):
            seen.append((target, msg))

        t = hands.press_key(FAKE, "right", post=post, scan=own_scan)
        self.assertFalse(t["ok"])
        self.assertIn("关", t["reason"])
        c = hands.capture_grid(FAKE, read_pixels=lambda h: pixels_of(
            [game.KIND_BG] * (game.ROWS * game.COLS)), scan=own_scan)
        self.assertFalse(c["ok"])
        self.assertIn("关", c["reason"])
        self.assertEqual(seen, [], "关态却按了键 = 违背 ADR-032（关=一个字都不改）")

    def test_off_leaves_no_file_and_no_directory(self):
        """★ADR-062 第①问的机器化：关着跑一遍，工作目录里一个新字节都不许多。"""
        with tempfile.TemporaryDirectory() as tmp:
            real_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                hands.press_key(FAKE, "right", post=lambda *a: None, scan=own_scan)
                hands.capture_grid(FAKE, read_pixels=lambda h: (b"", 0, 0), scan=own_scan)
                hands.probe(scan=own_scan)
            finally:
                os.chdir(real_cwd)
            self.assertEqual(os.listdir(tmp), [], "关掉的状态却留下了文件/目录")

    def test_bad_switch_value_is_off_not_guessed(self):
        os.environ[hands.ENV_SWITCH] = "Onn"
        self.assertFalse(hands.enabled())
        os.environ[hands.ENV_SWITCH] = "开"
        self.assertFalse(hands.enabled())

    def test_non_windows_says_platform_not_switch(self):
        """开关明明开着，就不能回「开关是关的」—— 那把人往错的方向支走（同 ear.py）。"""
        os.environ[hands.ENV_SWITCH] = hands.SWITCH_ON
        with _not_windows():
            t = hands.press_key(FAKE, "right", post=lambda *a: None, scan=own_scan)
            self.assertIn("Windows", t["reason"])
            self.assertNotIn("关", t["reason"])


class OwnWindowOnlyTest(_Env):
    def setUp(self):
        _Env.setUp(self)
        os.environ[hands.ENV_SWITCH] = hands.SWITCH_ON

    def test_foreign_hwnd_is_refused_and_nothing_is_posted(self):
        seen = []

        def post(target, msg, wparam, lparam):
            seen.append(target)

        t = hands.press_key(999, "right", post=post, scan=foreign_scan)
        self.assertFalse(t["ok"])
        self.assertIn("拒绝对它动手", t["reason"])
        self.assertEqual(seen, [], "对别人的窗口发键 = Boss 那四条限制里的第三条破了")

    def test_capture_refuses_foreign_hwnd(self):
        c = hands.capture_grid(999, read_pixels=lambda h: pixels_of(
            [game.KIND_BG] * (game.ROWS * game.COLS)), scan=foreign_scan)
        self.assertFalse(c["ok"])
        self.assertIn("自家窗口", c["reason"])

    def test_unknown_key_is_refused(self):
        t = hands.press_key(FAKE, "f13", post=lambda *a: None, scan=own_scan)
        self.assertFalse(t["ok"])
        self.assertIn("只认这几个键", t["reason"])

    def test_prefix_is_a_single_address(self):
        self.assertIn('"%s"' % hands.WINDOW_TITLE_PREFIX, HANDS_SRC,
                      "前缀不在 hands.py 里定义 = 这份住址丢了")
        self.assertNotIn('"%s"' % hands.WINDOW_TITLE_PREFIX, SHELL_SRC,
                         "游戏窗口抄了第二份标题前缀：改了常量画面照样开，守卫却再也认不出自家窗口")


class KeyPathTest(_Env):
    def setUp(self):
        _Env.setUp(self)
        os.environ[hands.ENV_SWITCH] = hands.SWITCH_ON

    @unittest.skipUnless(sys.platform == "win32", "lParam 由本机 MapVirtualKeyW 算出")
    def test_one_press_posts_down_and_up_to_the_first_candidate(self):
        seen = []

        def post(target, msg, wparam, lparam):
            seen.append((target, msg, wparam, lparam))

        with _targets([777, FAKE]):
            t = hands.press_key(FAKE, "down", post=post, scan=own_scan)
        self.assertTrue(t["ok"], t["reason"])
        self.assertEqual(t["posted"], 2)
        self.assertEqual([s[1] for s in seen], [hands.WM_KEYDOWN, hands.WM_KEYUP])
        self.assertEqual([s[0] for s in seen], [777, 777], "两半消息发去了不同的句柄")
        self.assertEqual([s[2] for s in seen], [hands.KEYS["down"]] * 2)

    @unittest.skipUnless(sys.platform == "win32", "同上")
    def test_up_lparam_carries_transition_bits(self):
        seen = []
        with _targets([FAKE]):
            hands.press_key(FAKE, "left", post=lambda *a: seen.append(a), scan=own_scan)
        self.assertNotEqual(seen[0][3], seen[1][3], "down 和 up 的 lParam 一样 = Tk 只会收到一个键")

    def test_post_failure_degrades_to_a_reason(self):
        def boom(*args):
            raise OSError("PostMessage 失败")

        with _targets([FAKE]):
            t = hands.press_key(FAKE, "up", post=boom, scan=own_scan)
        self.assertFalse(t["ok"])
        self.assertIn("没按成", t["reason"])

    def test_no_loops_no_sendinput_no_files_no_network(self):
        # 文档里要解释为什么不用它，所以查的是【调用形状】而不是这个词
        self.assertNotIn("SendInput(", HANDS_SRC, "SendInput 打进系统当前焦点，可能是 Boss 的窗口")
        self.assertNotIn("user32.SendInput", HANDS_SRC)
        self.assertNotIn("while ", HANDS_SRC, "本模块一次只发一手，循环不在这里")
        for bad in ("open(", "socket", "urllib", "http.client", "requests"):
            self.assertNotIn(bad, HANDS_SRC, "%s 会新开一个 purge 管不到的住址或出网面" % bad)
        self.assertNotIn("while ", GAME_SRC, "棋盘本体也不许自己跑循环")


class CaptureTest(_Env):
    def setUp(self):
        _Env.setUp(self)
        os.environ[hands.ENV_SWITCH] = hands.SWITCH_ON

    def _shot(self, kinds):
        return hands.capture_grid(FAKE, read_pixels=lambda h: pixels_of(kinds), scan=own_scan)

    def test_synthetic_board_roundtrip(self):
        state = game.new_state(rng=random.Random(9))
        kinds = [game.KIND_BG] * (game.ROWS * game.COLS)
        for i, (r, c) in enumerate(state["snake"]):
            kinds[r * game.COLS + c] = game.KIND_HEAD if i == 0 else game.KIND_SNAKE
        kinds[state["food"][0] * game.COLS + state["food"][1]] = game.KIND_FOOD
        shot = self._shot(kinds)
        self.assertTrue(shot["ok"], shot["reason"])
        self.assertEqual(shot["grid"], kinds, "涂进去的格子和读回来的不一样 = 分类或取样错位")
        rebuilt, other = game.state_from_cells(shot["grid"])
        self.assertEqual(other, 0)
        self.assertEqual(rebuilt["snake"][0], state["snake"][0])
        self.assertEqual(rebuilt["food"], state["food"])

    def test_returned_trace_carries_no_pixels(self):
        shot = self._shot([game.KIND_BG] * (game.ROWS * game.COLS - 1) + [game.KIND_HEAD])
        self.assertTrue(shot["ok"], shot["reason"])
        stack = [shot]
        while stack:
            item = stack.pop()
            self.assertNotIsInstance(item, (bytes, bytearray),
                                     "返回值里带了像素缓冲 = 有了可上传/可落盘的东西")
            if isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, (list, tuple)):
                stack.extend(item)

    def test_two_heads_is_refused_not_resolved(self):
        kinds = [game.KIND_BG] * (game.ROWS * game.COLS)
        kinds[0] = kinds[1] = game.KIND_HEAD
        shot = self._shot(kinds)
        self.assertFalse(shot["ok"])
        self.assertIn("蛇头认出 2 个", shot["reason"])

    def test_no_head_is_refused(self):
        shot = self._shot([game.KIND_SNAKE] * (game.ROWS * game.COLS))
        self.assertFalse(shot["ok"])
        self.assertIn("蛇头认出 0 个", shot["reason"])

    def test_unreadable_cells_are_counted_and_reported(self):
        kinds = [game.KIND_BG] * (game.ROWS * game.COLS)
        kinds[0] = game.KIND_HEAD
        kinds[5] = game.KIND_OTHER
        shot = self._shot(kinds)
        self.assertTrue(shot["ok"], shot["reason"])
        self.assertEqual(shot["counts"][game.KIND_OTHER], 1,
                         "认不出的格子要如实报数，调用方才不会把遮住半边的画面当完整现场")

    def test_client_size_mismatch_refuses_to_guess(self):
        """★DPI 缩放/窗口被拖小都是这一条：尺寸对不上就拒读，不许按比例缩着猜。"""
        wide = pixels_of([game.KIND_BG] * (game.ROWS * (game.COLS + 1)), cols=game.COLS + 1)
        shot = hands.capture_grid(FAKE, read_pixels=lambda h: wide, scan=own_scan)
        self.assertFalse(shot["ok"])
        self.assertIn("对不上", shot["reason"])


class ProbeTest(_Env):
    def test_probe_does_not_need_the_switch_and_reads_no_pixels(self):
        """问"这台机器能不能看自家窗口"这条读永远不动手（同 ear.probe 的分界）。"""
        self.assertFalse(hands.enabled())
        calls = []
        p = hands.probe(scan=lambda: calls.append(1) or [(FAKE, OWN_TITLE)])
        self.assertEqual(calls, [1])
        self.assertEqual(p["own_count"], 1)
        self.assertEqual(p["titles"], [OWN_TITLE])
        self.assertFalse(p["on"])

    def test_probe_reports_no_windows_instead_of_inventing_one(self):
        p = hands.probe(scan=lambda: [])
        self.assertEqual(p["own_count"], 0)
        self.assertEqual(p["titles"], [])

    def test_scan_failure_degrades_to_a_reason(self):
        def boom():
            raise RuntimeError("EnumWindows 炸了")

        p = hands.probe(scan=boom)
        self.assertEqual(p["own_count"], 0)
        self.assertIn("问不了窗口列表", p["reason"])


class RealBoxTest(unittest.TestCase):
    """本机真读数：只枚举窗口，不读像素、不发键。"""

    @unittest.skipUnless(sys.platform == "win32", "本机后端只在 Windows 上有意义")
    def test_this_box_can_enumerate_windows(self):
        found = hands.own_windows()
        self.assertIsInstance(found, list)
        self.assertTrue(all(w["title"].startswith(hands.WINDOW_TITLE_PREFIX) for w in found))

    @unittest.skipUnless(sys.platform == "win32", "同上")
    def test_gdi_is_loadable_here(self):
        p = hands.probe()
        self.assertTrue(p["gdi_ready"], p["reason"])
        self.assertEqual(p["windows"], "Windows")

    @unittest.skipUnless(sys.platform == "win32", "GDI 只在这台机器上有意义")
    def test_a_real_screen_read_returns_bytes(self):
        """★真跑一遍 DC→Bitmap→BitBlt→GetDIBits 这条链，句柄是这台机器给的真的。

        为什么单独立这条：CreateCompatibleDC 一开始只写了 restype 没写 argtypes，
        64 位的 DC 句柄被 ctypes 当 c_int 塞进去 => OverflowError，而异常被吞成
        一句「ArgumentError」—— 单元测试（注入像素）永远抓不到它。
        """
        data = hands._grab_screen_bits(0, 0, 8, 8)
        self.assertEqual(len(data), 8 * 8 * 4, "读回来的字节数不对 = GDI 那条链没真跑通")


if __name__ == "__main__":
    unittest.main(verbosity=2)
