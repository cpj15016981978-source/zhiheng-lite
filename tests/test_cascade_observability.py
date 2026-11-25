# -*- coding: utf-8 -*-
"""级联路由可观测性回归：决策进 trace、trace 落盘开关、统计口径。"""
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

BUILD = pathlib.Path(r"D:\ZhihengBuild")
INSTALL = pathlib.Path(r"D:\ZhihengInstall")
SRC = pathlib.Path(r"D:\DSH\chuixue_desktop")

# 按文件路径加载独立脚本，绕开 sys.modules 里被别的测试缓存的 pai 包
_spec = importlib.util.spec_from_file_location(
    "cascade_stats_under_test", INSTALL / "spine" / "tools" / "cascade_stats.py")
CS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CS)

from pai import local_cascade as LC  # noqa: E402
from pai import model_gateway as MG  # noqa: E402


def _rec(route, ok, chosen, degraded=False, prompt="测试输入", chosen_model=None,
         prompt_mode=None):
    cascade = {
        "route": route, "route_ok": ok, "chosen": chosen,
        "chosen_model": chosen_model or chosen,
        "degraded": degraded, "trace_steps": ["路由判定"],
    }
    if prompt_mode is not None:
        cascade["prompt_mode"] = prompt_mode
    return {
        "ts": "2026-11-18T14:00:00",
        "prompt": prompt,
        "trace": {"cascade": cascade},
    }


class TraceWiring(unittest.TestCase):
    def test_cascade_decision_enters_trace(self):
        for p in (INSTALL / "spine" / "pai" / "orchestrator.py",
                  BUILD / "spine" / "pai" / "orchestrator.py"):
            txt = p.read_text(encoding="utf-8", errors="replace")
            self.assertIn("cascade_observe = None", txt, str(p))
            self.assertIn('trace["cascade"] = cascade_observe', txt, str(p))
            self.assertIn('"route_ok": outs[0].get("route_ok")', txt, str(p))
            self.assertIn('"chosen_model": outs[0].get("chosen_model")', txt, str(p))
            self.assertIn('"prompt_mode": outs[0].get("prompt_mode")', txt, str(p))

    def test_route_disables_thinking_and_strips_tag(self):
        """qwen3 默认 thinking 会吞掉 8-token 预算——真源必须关掉并兜底剥离。"""
        txt = (INSTALL / "spine" / "pai" / "local_cascade.py").read_text(encoding="utf-8")
        self.assertIn('"think": False', txt)
        self.assertEqual(LC._parse_route("<think>想想</think> simple"), "simple")

    def test_gateway_sends_think_false(self):
        """行为契约：网关真的把 think=false 送进 Ollama body，不锁源码变量名。"""
        captured = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def __iter__(self):
                return iter([b'{"message":{"content":"x"},"done":true}\n'])

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _Resp()

        with mock.patch.object(MG.urllib.request, "urlopen", fake_urlopen):
            MG._ollama_complete("x", "qwen3:0.6b",
                                {"think": False, "max_tokens": 16})
        self.assertIs(captured["body"]["think"], False)

    def test_trace_store_enabled_in_all_launch_copies(self):
        for p in (INSTALL / "launch_qingniao.ps1",
                  SRC / "launch_qingniao.ps1",
                  BUILD / "launch_qingniao.ps1"):
            self.assertIn('$env:PAI_TRACE_STORE = "on"',
                          p.read_text(encoding="utf-8-sig", errors="replace"), str(p))

    def test_cascade_switch_still_on(self):
        txt = (INSTALL / "launch_qingniao.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('$env:PAI_CASCADE_ROUTE = "1"', txt)


class Stats(unittest.TestCase):
    def test_analyze_counts(self):
        records = [
            _rec("simple", True, "知衡离线分级（simple · qwen2.5:1.5b）",
                 chosen_model="qwen2.5:1.5b", prompt_mode="small_compact"),
            _rec("complex", True, "知衡离线分级（complex · qwen3-4b-instruct:latest）",
                 degraded=True, chosen_model="qwen3-4b-instruct:latest"),
            _rec("normal", False, "知衡离线分级（normal · qwen3-4b-instruct:latest）",
                 chosen_model="qwen3-4b-instruct:latest"),  # fail-closed
            {"ts": "2026-11-18T14:01:00", "prompt": "普通对话",
             "trace": {"models": ["deepseek"]}},  # 非 cascade
        ]
        s = CS.analyze(records)
        self.assertEqual(s["records_total"], 4)
        self.assertEqual(s["cascade_hits"], 3)
        self.assertEqual(s["coverage_pct"], 75.0)
        self.assertEqual(s["parse_ok"], 2)
        self.assertEqual(s["parse_fail"], 1)
        self.assertEqual(s["parse_rate_pct"], 66.7)
        self.assertEqual(s["levels"], {"simple": 1, "complex": 1})
        self.assertEqual(s["degraded"], 1)
        self.assertEqual(s["degrade_rate_pct"], 33.3)
        self.assertEqual(s["models"], {"qwen3-4b-instruct:latest": 2,
                                       "qwen2.5:1.5b": 1})
        self.assertEqual(s["prompt_modes"], {"full": 2, "small_compact": 1})
        self.assertEqual(s["samples"][0]["prompt_mode"], "small_compact")
        self.assertEqual(len(s["samples"]), 3)

    def test_analyze_empty(self):
        s = CS.analyze([])
        self.assertEqual(s["records_total"], 0)
        self.assertEqual(s["parse_rate_pct"], 0.0)
        self.assertEqual(s["samples"], [])

    def test_load_records_skips_bad_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "trace-20261118.jsonl"
            good = json.dumps(_rec("simple", True, "qwen2.5:1.5b"), ensure_ascii=False)
            p.write_text(good + "\n{bad json\n\n", encoding="utf-8")
            got = CS.load_records(d, days=7)
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0]["trace"]["cascade"]["route"], "simple")

    def test_render_marks_failclosed(self):
        s = CS.analyze([_rec("normal", False, "qwen3-4b-instruct:latest")])
        out = CS.render(s, last=5)
        self.assertIn("fail-closed 落主力 1 次", out)
        self.assertIn("FB", out)


if __name__ == "__main__":
    unittest.main()
