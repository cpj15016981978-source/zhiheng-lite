# -*- coding: utf-8 -*-
"""local_cascade 回归：路由 fail-closed、三级分发、降级链、离线闸、回退开关。"""
from __future__ import annotations

import os
import pathlib
import sys
import unittest
from unittest import mock

INSTALL = pathlib.Path(r"D:\ZhihengInstall")
SRC = pathlib.Path(__file__).resolve().parents[1]

from pai import local_cascade as LC  # noqa: E402


class Route(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"PAI_CASCADE_ROUTE": "on"}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_simple_goes_small(self):
        d = LC.route("你好", call=lambda p, m, o: ("simple", 1, 1))
        self.assertEqual(d["level"], "simple")
        self.assertEqual(d["model"], LC.SMALL_MODEL())
        self.assertTrue(d["ok"])

    def test_complex_goes_main(self):
        d = LC.route("帮我分析一下这套架构的风险和取舍", call=lambda p, m, o: ("complex", 1, 1))
        self.assertEqual(d["level"], "complex")
        self.assertEqual(d["model"], LC.MAIN_MODEL())

    def test_unparsable_fails_closed_to_main(self):
        d = LC.route("……", call=lambda p, m, o: ("我不知道对不起", 1, 1))
        self.assertEqual(d["level"], "normal")
        self.assertEqual(d["model"], LC.MAIN_MODEL())
        self.assertFalse(d["ok"])
        self.assertIn("fail-closed", d["reason"])

    def test_router_crash_fails_closed(self):
        def boom(p, m, o):
            raise RuntimeError("router down")
        d = LC.route("测试", call=boom)
        self.assertEqual(d["model"], LC.MAIN_MODEL())
        self.assertFalse(d["ok"])

    def test_parse_word_in_sentence(self):
        self.assertEqual(LC._parse_route("结果: COMPLEX 因为..."), "complex")
        self.assertEqual(LC._parse_route(""), "")

    def test_simple_guard_for_explanation(self):
        """多轮复现：0.6B 会把解释 BOM 判 simple，护栏必须拦下。"""
        d = LC.route("解释一下什么是 BOM 表", call=lambda p, m, o: ("simple", 1, 1))
        self.assertEqual(d["level"], "normal")
        self.assertEqual(d["model"], LC.MAIN_MODEL())
        self.assertIn("口径护栏", d["reason"])
        self.assertTrue(d["ok"])

    def test_simple_guard_for_pricing(self):
        d = LC.route("床垫出厂价一般怎么算？", call=lambda p, m, o: ("simple", 1, 1))
        self.assertEqual(d["level"], "normal")
        self.assertEqual(d["model"], LC.MAIN_MODEL())

    def test_greeting_stays_simple(self):
        d = LC.route("你好呀", call=lambda p, m, o: ("simple", 1, 1))
        self.assertEqual(d["level"], "simple")
        self.assertEqual(d["model"], LC.SMALL_MODEL())

    def test_off_returns_off(self):
        with mock.patch.dict(os.environ, {"PAI_CASCADE_ROUTE": "off"}):
            self.assertFalse(LC.enabled())
            d = LC.route("x", call=lambda p, m, o: ("simple", 1, 1))
            self.assertEqual(d["level"], "off")


class Complete(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"PAI_CASCADE_ROUTE": "on"}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_simple_uses_small_and_shape(self):
        calls = []
        def call(p, m, o):
            calls.append(m)
            if "任务分类器" in p:
                return "simple", 1, 1
            return "短答", 3, 2
        out = LC.complete("你好", user_text="你好", call=call)
        self.assertEqual(out["output"], "短答")
        self.assertEqual(out["route"], "simple")
        self.assertFalse(out["degraded"])
        self.assertEqual(out["cost"], 0.0)
        self.assertTrue(out["priced"])
        self.assertIn(LC.SMALL_MODEL(), calls)
        self.assertEqual(out["prompt_mode"], "small_compact")

    def test_small_prompt_strips_persona_and_emotion(self):
        """1.5B 快答档不许再吃完整人格/情绪壳——那正是套话的来源。"""
        seen = []

        def call(p, m, o):
            seen.append((m, p))
            if "任务分类器" in p:
                return "simple", 1, 1
            return "短答", 3, 2

        full = ("[人格 · 时中 / 知衡]\n你急的时候，我先稳住\n"
                "[对方此刻的情绪基调（系统内部估计 · 功能态，不是诊断）]\n"
                "- 基调：平静\n[用户] 你好呀")
        out = LC.complete(full, user_text="你好呀", call=call)
        small_prompts = [p for m, p in seen if m == LC.SMALL_MODEL()]
        self.assertEqual(len(small_prompts), 1)
        p = small_prompts[0]
        self.assertNotIn("人格 · 时中", p)
        self.assertNotIn("情绪基调", p)
        self.assertNotIn("我先稳住", p)
        self.assertIn("快答档", p)
        self.assertIn("你好呀", p)
        self.assertEqual(out["prompt_mode"], "small_compact")

    def test_fallback_to_small_also_compacts(self):
        seen = []

        def call(p, m, o):
            seen.append((m, p))
            if "任务分类器" in p:
                return "complex", 1, 1
            if m == LC.MAIN_MODEL():
                raise RuntimeError("main down")
            return "兜底答", 2, 1

        out = LC.complete("难题", user_text="你好", call=call)
        small_prompts = [p for m, p in seen if m == LC.SMALL_MODEL()]
        self.assertEqual(len(small_prompts), 1)
        self.assertIn("快答档", small_prompts[0])
        self.assertEqual(out["prompt_mode"], "small_compact")

    def test_main_keeps_full_prompt(self):
        seen = []

        def call(p, m, o):
            seen.append((m, p))
            if "任务分类器" in p:
                return "complex", 1, 1
            return "长答", 3, 2

        out = LC.complete("分析风险", user_text="分析风险", call=call)
        main_prompts = [p for m, p in seen if m == LC.MAIN_MODEL()]
        self.assertEqual(len(main_prompts), 1)
        self.assertNotIn("快答档", main_prompts[0])
        self.assertEqual(out["prompt_mode"], "full")

    def test_main_failure_degrades_to_small(self):
        def call(p, m, o):
            if "任务分类器" in p:
                return "complex", 1, 1
            if m == LC.MAIN_MODEL():
                raise RuntimeError("main down")
            return "兜底答", 2, 1
        out = LC.complete("难题", user_text="难题", call=call)
        self.assertEqual(out["output"], "兜底答")
        self.assertTrue(out["degraded"])
        self.assertTrue(any("降级" in t for t in out["trace"]))

    def test_both_fail_raises(self):
        def call(p, m, o):
            if "任务分类器" in p:
                return "complex", 1, 1
            raise RuntimeError("all down")
        with self.assertRaises(RuntimeError):
            LC.complete("难题", user_text="难题", call=call)

    def test_small_is_final_no_false_hope(self):
        def call(p, m, o):
            if "任务分类器" in p:
                return "simple", 1, 1
            raise RuntimeError("small down")
        with self.assertRaises(RuntimeError):
            LC.complete("你好", user_text="你好", call=call)


class Config(unittest.TestCase):
    def test_status_keys(self):
        s = LC.status()
        for k in ("enabled", "router", "small", "main"):
            self.assertIn(k, s)

    def test_capability_registered(self):
        self.assertTrue(LC.CAPABILITY["name"])
        self.assertEqual(LC.CAPABILITY["network"], False)

    def test_offline_mode_offline_env(self):
        with mock.patch.dict(os.environ, {"PAI_OFFLINE": "1", "PAI_MODEL_PROVIDER": "deepseek"}):
            self.assertTrue(LC.offline_mode())

    def test_offline_mode_cloud_provider_takes_over(self):
        with mock.patch.dict(os.environ, {"PAI_OFFLINE": "0", "PAI_MODEL_PROVIDER": "deepseek"}):
            self.assertFalse(LC.offline_mode())

    def test_offline_mode_no_provider(self):
        with mock.patch.dict(os.environ, {"PAI_OFFLINE": "0", "PAI_MODEL_PROVIDER": ""}):
            self.assertTrue(LC.offline_mode())

    def test_orchestrator_wires_cascade(self):
        for base in (SRC, INSTALL, pathlib.Path(r"D:\ZhihengBuild")):
            f = base / "spine" / "pai" / "orchestrator.py"
            if not f.is_file():
                f = base / "pai" / "orchestrator.py"
            src = f.read_text(encoding="utf-8")
            self.assertIn("local_cascade as CASCADE", src)
            self.assertIn('model == "local_cascade"', src)
            self.assertIn("CASCADE.offline_mode()", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
