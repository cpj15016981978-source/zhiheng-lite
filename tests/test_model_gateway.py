"""M10 模型网关测试：默认 mock、opt-in 真实、缺 Key 报错、Key 绝不外泄。
全程 monkeypatch urlopen，不联网。运行： python -m unittest discover -s tests
"""
import io
import json
import os
import sys
import unittest
import urllib.error
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai import model_gateway as M10


class _Resp:
    """伪造 urllib 响应对象（支持 with + read）。"""
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@contextmanager
def _env(**kw):
    old = {}
    unset = ["PAI_MODEL_PROVIDER", "PAI_MODEL_NAME", "OPENAI_API_KEY",
             "ANTHROPIC_API_KEY", "PAI_MODEL_API_KEY", "PAI_OPENAI_BASE_URL",
             "PAI_ANTHROPIC_BASE_URL"]
    for k in unset:
        old[k] = os.environ.get(k)
        os.environ.pop(k, None)
    for k, v in kw.items():
        old[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(v)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestModelGateway(unittest.TestCase):

    def test_default_is_mock_offline(self):
        """不设 PAI_MODEL_PROVIDER → 永远走 mock，不联网。"""
        with _env():
            out = M10.complete("你好")
        self.assertEqual(out[0]["model_id"], "mock")
        self.assertTrue(out[0]["output"].startswith("[mock]"))

    def test_key_present_but_not_opt_in_stays_mock(self):
        """即使环境里有 Key，只要没设 PAI_MODEL_PROVIDER 仍走 mock（防误触网）。"""
        with _env(OPENAI_API_KEY="sk-should-not-be-used"):
            out = M10.complete("你好")
        self.assertEqual(out[0]["model_id"], "mock")

    def test_openai_without_key_raises(self):
        with _env(PAI_MODEL_PROVIDER="openai"):
            with self.assertRaises(M10.ModelGatewayError):
                M10.complete("你好", models=("openai",))

    def test_openai_request_and_parse(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["auth"] = req.get_header("Authorization")
            captured["body"] = json.loads(req.data.decode())
            return _Resp({"choices": [{"message": {"content": "真回答"}}],
                          "usage": {"total_tokens": 42}})

        orig = M10.urllib.request.urlopen
        M10.urllib.request.urlopen = fake_urlopen
        try:
            with _env(PAI_MODEL_PROVIDER="openai", OPENAI_API_KEY="sk-test-123",
                      PAI_MODEL_NAME="gpt-4o-mini"):
                out = M10.complete("问题", models=("openai",))
        finally:
            M10.urllib.request.urlopen = orig

        self.assertEqual(out[0]["output"], "真回答")
        self.assertEqual(out[0]["tokens"], 42)
        self.assertTrue(captured["url"].endswith("/chat/completions"))
        self.assertEqual(captured["auth"], "Bearer sk-test-123")
        self.assertEqual(captured["body"]["model"], "gpt-4o-mini")

    def test_anthropic_parse(self):
        def fake_urlopen(req, timeout=None):
            return _Resp({"content": [{"type": "text", "text": "你好"},
                                      {"type": "text", "text": "，阿明"}],
                          "usage": {"input_tokens": 10, "output_tokens": 5}})

        orig = M10.urllib.request.urlopen
        M10.urllib.request.urlopen = fake_urlopen
        try:
            with _env(PAI_MODEL_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant-x"):
                out = M10.complete("hi", models=("anthropic",))
        finally:
            M10.urllib.request.urlopen = orig
        self.assertEqual(out[0]["output"], "你好，阿明")
        self.assertEqual(out[0]["tokens"], 15)

    def test_connection_error_message_does_not_leak_key(self):
        """连不上时异常文本绝不能带上密钥值（安全底线）。"""
        SECRET = "sk-super-secret-DO-NOT-LEAK"

        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        orig = M10.urllib.request.urlopen
        M10.urllib.request.urlopen = fake_urlopen
        try:
            with _env(PAI_MODEL_PROVIDER="openai", OPENAI_API_KEY=SECRET):
                with self.assertRaises(M10.ModelGatewayError) as ctx:
                    M10.complete("hi", models=("openai",))
        finally:
            M10.urllib.request.urlopen = orig
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_resolve_model_provider_and_name(self):
        with _env(PAI_MODEL_NAME="gpt-4o-mini"):
            self.assertEqual(M10.resolve_model("openai"), ("openai", "gpt-4o-mini"))
            self.assertEqual(M10.resolve_model("openai:MiniMax-M3"), ("openai", "MiniMax-M3"))
            self.assertEqual(M10.resolve_model("deepseek:deepseek-reasoner"),
                             ("deepseek", "deepseek-reasoner"))

    def test_colon_spec_without_provider_refuses(self):
        with _env():
            with self.assertRaises(M10.ModelGatewayError):
                M10.complete("hi", models=("openai:MiniMax-M3",))

    def test_multi_model_specs_are_separate(self):
        calls = []

        def fake(provider, prompt, opts, model=None):
            calls.append((provider, model))
            return ("ok", 1000, 500)

        orig = M10._openai_complete
        M10._openai_complete = fake
        try:
            with _env(PAI_MODEL_PROVIDER="openai", OPENAI_API_KEY="sk-test"):
                outs = M10.complete("hi", models=("openai:MiniMax-M3",
                                                  "openai:MiniMax-M3.1-Flash"))
        finally:
            M10._openai_complete = orig
        self.assertEqual([c[1] for c in calls], ["MiniMax-M3", "MiniMax-M3.1-Flash"])
        self.assertEqual([o["model"] for o in outs], ["MiniMax-M3", "MiniMax-M3.1-Flash"])
        self.assertTrue(all(o["cost"] > 0 and o["priced"] for o in outs))


if __name__ == "__main__":
    unittest.main()
