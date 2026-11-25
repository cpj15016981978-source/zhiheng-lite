# -*- coding: utf-8 -*-
"""M10 报账闸门（ADR-080）——「调用前先报账，估不出就拒发」常驻化。

这块以前只住在外部实验室的脚本里，靠我每轮人工复跑核对；现在它是网关里的一刀，
所以守卫要证明三件事，缺一件都算橡皮图章：
  1. 会红：没登记的模型 => 抛错，且【一次 POST 都没发出去】（拒在出门之前，不是事后记账）。
  2. 会挡：大小写写法失配不再等于「估不出」——这正是 P19 那一版永远估不出还照样放行的成因。
  3. 常开：没有开关可绕。这条用「拔掉那一刀 => 本文件立刻红」的反向证明来立，不靠声明。

全程 monkeypatch urlopen，不联网、零花费。运行：python -m unittest tests.test_spend_guard -v
"""
import io
import json
import os
import sys
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai import model_gateway as M10

UNREGISTERED = "某个没登记的模型"
SECRET = "sk-super-secret-DO-NOT-LEAK"
# 这个测试要【全部删干净】再重钉的变量：漏一个就会沾到本机已有的 env（真 Key 就在那里）。
MANAGED = ("PAI_MODEL_PROVIDER", "PAI_MODEL_NAME", "OPENAI_API_KEY", "PAI_MODEL_API_KEY",
           "PAI_OPENAI_BASE_URL", "ANTHROPIC_API_KEY")


@contextmanager
def _set_env(**kw):
    saved = {k: os.environ.get(k) for k in MANAGED}
    for k in MANAGED:
        os.environ.pop(k, None)
    for k, v in kw.items():
        if v is not None:
            os.environ[k] = str(v)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _resp(payload):
    body = json.dumps(payload).encode("utf-8")

    class _R:
        def read(self_inner):
            return body

        def __enter__(self_inner):
            return self_inner

        def __exit__(self_inner, *a):
            return False
    return _R()


class RefusesWhenUnpriced(unittest.TestCase):

    def test_unregistered_model_never_leaves_the_machine(self):
        calls = []
        with _set_env(PAI_MODEL_PROVIDER="openai", PAI_MODEL_NAME=UNREGISTERED,
                      OPENAI_API_KEY=SECRET):
            with mock.patch.object(M10.urllib.request, "urlopen",
                                   lambda req, timeout=None: calls.append(1) or _resp({})):
                with self.assertRaises(M10.ModelGatewayError) as ctx:
                    M10.complete("问一句", opts={"max_tokens": 8})
        self.assertEqual(calls, [], "闸门只在事后记账 = 钱已经花了；必须是出门前就拒")
        self.assertIn("估不出来", str(ctx.exception))
        self.assertIn("PRICING", str(ctx.exception), "要给出路：登记实价，而不是另开一份表")

    def test_refusal_message_never_carries_the_key(self):
        with _set_env(PAI_MODEL_PROVIDER="openai", PAI_MODEL_NAME=UNREGISTERED,
                      OPENAI_API_KEY=SECRET):
            with mock.patch.object(M10.urllib.request, "urlopen",
                                   lambda req, timeout=None: _resp({})):
                with self.assertRaises(M10.ModelGatewayError) as ctx:
                    M10.complete("问一句")
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_unpriced_cost_cap_is_none_not_zero(self):
        """估不出 = None。写成 0 会被上层读成「这次没花钱」，那是两件事。"""
        est = M10.estimate("上下文", "openai", UNREGISTERED)
        self.assertFalse(est["priced"])
        self.assertIsNone(est["cost_cap"])
        self.assertIn("估不出", est["line"])


class EstimatesBeforeTheFact(unittest.TestCase):

    def test_report_line_printed_before_the_request(self):
        seen = {}
        buf = io.StringIO()

        def urlopen(req, timeout=None):
            seen["stdout_at_call"] = buf.getvalue()
            return _resp({"choices": [{"message": {"content": "在的"}}],
                          "usage": {"prompt_tokens": 12, "completion_tokens": 3}})

        with _set_env(PAI_MODEL_PROVIDER="openai", PAI_MODEL_NAME="gpt-4o-mini",
                      OPENAI_API_KEY="sk-test"):
            with mock.patch.object(M10.urllib.request, "urlopen", urlopen):
                with redirect_stdout(buf):
                    outs = M10.complete("问题" * 10, opts={"max_tokens": 8})
        self.assertIn("[报账]", seen.get("stdout_at_call", ""),
                      "报账必须在派发之前，不是在响应之后补一句")
        self.assertIn("[报账]", outs[0]["spend_report"])
        self.assertLessEqual(outs[0]["cost"], outs[0]["cost_estimate"],
                             "实付高于估出的上限 = 上限是假的")

    def test_output_cap_uses_max_tokens_when_given(self):
        est = M10.estimate("abc", "openai", "gpt-4o-mini", {"max_tokens": 8})
        self.assertEqual(est["tokens_out_cap"], 8)
        self.assertFalse(est["assumed_out"])

    def test_openai_path_has_no_max_tokens_so_the_cap_is_marked_assumed(self):
        """openai 兼容那条调用不带 max_tokens => 上限不可知，读数必须自己承认是假设。"""
        est = M10.estimate("abc", "openai", "gpt-4o-mini")
        self.assertEqual(est["tokens_out_cap"], M10.ASSUMED_MAX_OUT)
        self.assertTrue(est["assumed_out"])
        self.assertIn("假设", est["line"])

    def test_input_cap_is_the_whole_prompt_and_errs_high(self):
        est = M10.estimate("字" * 500, "openai", "gpt-4o-mini", {"max_tokens": 10})
        self.assertEqual(est["tokens_in_cap"], 500)

    def test_price_table_is_the_only_source_of_the_number(self):
        """改 PRICING 一处，报账读数就跟着变 => 估账没有第二份住址（ADR-030）。"""
        key = ("openai", "临时占位模型-x")
        M10.PRICING[key] = (10.0, 10.0)
        try:
            est = M10.estimate("a" * 1000, "openai", "临时占位模型-x", {"max_tokens": 1000})
            self.assertAlmostEqual(est["cost_cap"], 0.02, places=6)
        finally:
            del M10.PRICING[key]


class CaseMismatchIsNotUnpriced(unittest.TestCase):
    """P19 的成因：PRICING 的键大小写混用，而模型名来自环境变量。

    写法不一致 => 旧 price_of 查不到 => 上限永远估不出、还照样放行。
    归一只统一【同一个名字的写法】，不替未知模型编价。
    """

    def test_uppercase_spelling_is_still_priced(self):
        cost, priced = M10.price_of("OpenAI", "GPT-4o-Mini", 1_000_000, 1_000_000)
        self.assertTrue(priced, "大小写失配不该被当成没登记")
        self.assertAlmostEqual(cost, 5.5, places=6)

    def test_estimate_survives_the_launchers_spelling(self):
        from tools import minimax_quickstart as Q
        est = M10.estimate("上下文", Q.PROVIDER, "  " + Q.MODEL_NAME.lower() + " ",
                           {"max_tokens": 64})
        self.assertTrue(est["priced"], "%s 的写法一变就估不出 = 闸门形同虚设" % Q.MODEL_NAME)
        self.assertIsNotNone(est["cost_cap"])

    def test_a_model_that_is_really_unknown_is_still_unknown(self):
        self.assertIsNone(M10._price_row("openai", "gpt-9-ultra-nope"))


class MockPathIsUntouched(unittest.TestCase):
    """默认关态一个字不改：mock 不花钱，也就用不着报账授权。"""

    def test_mock_does_not_call_the_guard(self):
        with mock.patch.object(M10, "guard_spend") as g:
            with _set_env():
                outs = M10.complete("你好")
        self.assertEqual(g.call_count, 0, "关态一旦被闸门拦住 = 「什么都不改」这句话就不成立了")
        self.assertEqual(outs[0]["model_id"], "mock")

    def test_mock_row_reports_zero_and_prints_no_spend_line(self):
        buf = io.StringIO()
        with _set_env():
            with redirect_stdout(buf):
                outs = M10.complete("你好")
        self.assertEqual(outs[0]["cost"], 0.0)
        self.assertTrue(outs[0]["priced"])
        self.assertEqual(outs[0]["cost_estimate"], 0.0)
        self.assertNotIn("provider=openai", buf.getvalue())


class GuardHasNoSwitch(unittest.TestCase):
    """常开的登记形状：闸门自己没有开关，也不许偷偷开一个。"""

    def test_capability_still_declares_the_provider_switch_only(self):
        self.assertEqual(M10.CAPABILITY["switch_env"], "PAI_MODEL_PROVIDER")
        self.assertIs(M10.CAPABILITY["default_on"], False)
        self.assertIs(M10.CAPABILITY["network"], True)

    def test_no_spend_switch_env_var_exists(self):
        src = Path(M10.__file__).read_bytes().decode("utf-8")
        self.assertNotIn("PAI_SPEND", src,
                         "给报账闸门配一个「关掉」的环境变量 = 这条常开就没了")


if __name__ == "__main__":
    unittest.main(verbosity=2)
