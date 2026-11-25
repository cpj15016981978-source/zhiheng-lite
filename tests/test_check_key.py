# -*- coding: utf-8 -*-
"""密钥自检器（tools/check_key.py）的测试。

要钉住的三件事：
  1 它自己不能成为泄露源——任何分支的输出里都不许出现密钥值；
  2 判据要准：没配 → 退出码 1（别让人以为能跑），配齐 → 0；
  3 它对当前脊柱报"红线无命中"，同时能真的抓出人为植入的坏行（不是永远绿）。

运行：python -m unittest tests.test_check_key -v
（全程离线：绝不触发 --probe，密钥用假值。）
"""
import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("check_key", ROOT / "tools" / "check_key.py")
ck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ck)

FAKE = "sk-FAKEabcdef1234567890"
_HKCU, _HKLM = 1, 2


class _FakeKey:
    def __init__(self, values):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeWinreg:
    """够用的假注册表：只有键与值，没有子键 —— 正是用来验"按子键数取值"那个 bug 的形状。"""

    HKEY_CURRENT_USER = _HKCU
    HKEY_LOCAL_MACHINE = _HKLM

    def __init__(self, table):
        self.table = table

    def OpenKey(self, hive, sub):
        if (hive, sub) not in self.table:
            raise OSError(2, "找不到键")
        return _FakeKey(self.table[(hive, sub)])

    def EnumValue(self, key, index):
        if index >= len(key.values):
            raise OSError(87, "越界")
        name, value = key.values[index]
        return name, value, 1


def run_main(argv, persisted=({}, "")):
    """持久层默认喂"空且读得到"，让每个用例的断言都跟本机真实环境无关。"""
    buf = io.StringIO()
    with mock.patch.object(ck, "_read_persisted_env", lambda: persisted):
        with redirect_stdout(buf):
            code = ck.main(argv)
    return code, buf.getvalue()


class NeverLeaksTheValue(unittest.TestCase):
    """★承重测试：自检器的输出里不许出现密钥本身。"""

    def test_output_contains_only_name_and_length(self):
        env = {"PAI_MODEL_PROVIDER": "openai", "OPENAI_API_KEY": FAKE}
        with mock.patch.dict(os.environ, env, clear=False):
            code, out = run_main([])
        self.assertEqual(code, 0)
        self.assertNotIn(FAKE, out, "输出里出现了密钥值——这就是泄露源")
        self.assertIn("OPENAI_API_KEY", out)                 # 变量名要说清
        self.assertIn(f"长度 {len(FAKE)}", out)               # 只许说长度

    def test_probe_flag_not_used_by_default(self):
        """默认路径绝不能联网：mock 网关，未配置探针就不该被调用。"""
        env = {"PAI_MODEL_PROVIDER": "openai", "OPENAI_API_KEY": FAKE}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(ck, "probe", side_effect=AssertionError("不该联网")) as p:
                code, out = run_main([])
        self.assertEqual(code, 0)
        p.assert_not_called()


class Verdicts(unittest.TestCase):

    def test_unset_provider_is_exit_1_not_ok(self):
        env = {"PAI_MODEL_PROVIDER": "", "OPENAI_API_KEY": "", "PAI_MODEL_API_KEY": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            code, out = run_main([])
        self.assertEqual(code, 1, "没配却报 0，等于骗人说能跑真模型")
        self.assertIn("还没配好", out)

    def test_unknown_provider_does_not_pass_as_configured(self):
        env = {"PAI_MODEL_PROVIDER": "minimax", "PAI_MODEL_API_KEY": FAKE}
        with mock.patch.dict(os.environ, env, clear=False):
            code, out = run_main([])
        self.assertEqual(code, 1)
        self.assertIn("不在已知列表", out)

    def test_leak_evidence_forces_exit_2_before_probe(self):
        """库里发现形似密钥的串 → 直接 2，且不去连通（先清干净再谈接模型）。"""
        with mock.patch.dict(os.environ, {"PAI_MODEL_PROVIDER": "openai",
                                          "OPENAI_API_KEY": FAKE}, clear=False):
            with mock.patch.object(ck, "scan_runtime_store_for_leaks",
                                   return_value=["memory.db: 命中 1 处"]):
                code, out = run_main([])
        self.assertEqual(code, 2)
        self.assertIn("先别 --probe", out)


class ScannersActuallyScan(unittest.TestCase):
    """两个扫描器不能是永远绿的摆设——喂一条坏行必须抓到。"""

    def test_source_scan_finds_a_planted_leak(self):
        bad = 'raise ModelGatewayError(f"认证失败：{secret}")'
        self.assertTrue(ck.line_leaks_value(bad))

    def test_source_scan_passes_the_real_gateway_lines(self):
        # 网关里那句提示只引用【变量名】，不是值——必须判为安全。
        ok = 'raise ModelGatewayError("要用 {} 但未设置密钥：请在终端 export MY_API_KEY")'
        self.assertFalse(ck.line_leaks_value(ok))

    def test_current_spine_source_is_clean(self):
        self.assertEqual(ck.scan_source_for_key_leaks(), [],
                         "当前脊柱源码里有把密钥值拼进异常/打印的行")


class ProbeReadsTheRealShape(unittest.TestCase):
    """M10.complete 返回的是【列表】（每模型一份），不是 dict。
    2026-10-21 首次真连通就撞上这个：按 dict 解读会把整个结构体数一遍字数、成本恒 None。
    全程 mock，绝不真联网。"""

    def test_list_result_reports_output_length_not_repr(self):
        res = [{"model": "MiniMax-M3", "output": "在的", "tokens": 232,
                "cost": 0.0001, "priced": True}]
        with mock.patch("pai.model_gateway.complete", return_value=res):
            ok, msg = ck.probe()
        self.assertEqual(ok, 1)
        self.assertIn("MiniMax-M3", msg)
        self.assertIn("2 字", msg)
        self.assertIn("232 tokens", msg)
        self.assertNotIn("在的", msg.replace("MiniMax-M3", ""))   # 只报字数，不回显正文

    def test_unpriced_model_is_called_out(self):
        """价目表缺这个模型 → 成本恒 0，必须明说，别让 0 被当成免费。"""
        res = [{"model": "some-new-model", "output": "嘿", "tokens": 9,
                "cost": 0.0, "priced": False}]
        with mock.patch("pai.model_gateway.complete", return_value=res):
            ok, msg = ck.probe()
        self.assertIn("价目表", msg)

    def test_failure_summary_keeps_http_code_but_drops_body(self):
        from pai.model_gateway import ModelGatewayError
        detail = "HTTP 401：Invalid API key sk-cs-AABqqxx11yhh3dd9ff00aa11bb22cc33"
        with mock.patch("pai.model_gateway.complete",
                        side_effect=ModelGatewayError(detail)):
            ok, msg = ck.probe()
        self.assertEqual(ok, 0)
        self.assertIn("401", msg)
        self.assertNotIn("sk-cs-AABqqxx11yhh3dd9ff00aa11bb22cc33", msg)
        self.assertNotIn("Invalid API key", msg, "服务商回显正文一个字都不许带出来")

    def test_network_failure_says_proxy_or_network(self):
        from pai.model_gateway import ModelGatewayError
        with mock.patch("pai.model_gateway.complete",
                        side_effect=ModelGatewayError("连不上 https://api.openai.com：timed out（境外端点常需代理）")):
            ok, msg = ck.probe()
        self.assertIn("连不上", msg)


class PersistedEnvResidue(unittest.TestCase):
    """ADR-038：持久层（setx/注册表）里的一份密钥会让"缺省 mock"在下一个终端失效。
    这一节要钉的是：看得见它、说得出它、但不许把它念出来。"""

    LONG_FAKE = "sk-api-FAKEPERSISTEDabcdefghijklmnopqrstuvwxyz0123456789"

    def test_persisted_key_shows_name_and_length_only(self):
        code, out = run_main([], persisted=({"OPENAI_API_KEY": self.LONG_FAKE}, ""))
        self.assertIn("OPENAI_API_KEY", out)
        self.assertIn("长度 %d" % len(self.LONG_FAKE), out)
        self.assertNotIn(self.LONG_FAKE, out, "持久层那一项也不能把值念出来")
        self.assertIn(code, (0, 1, 2))

    def test_provider_plus_persisted_key_is_called_live(self):
        """★这一条是整件事的核心：进程里没配，不代表这台机器上没配。"""
        vals = {"PAI_MODEL_PROVIDER": "openai", "OPENAI_API_KEY": self.LONG_FAKE}
        with mock.patch.dict(os.environ, {"PAI_MODEL_PROVIDER": "",
                                          "OPENAI_API_KEY": "", "PAI_MODEL_API_KEY": ""},
                             clear=False):
            code, out = run_main([], persisted=(vals, ""))
        self.assertIn("会出门", out)
        self.assertIn("REG delete", out)
        self.assertEqual(code, 1, "本终端确实还没配，别报 0 骗人")
        self.assertIn("持久层", out, "退出码 1 的同时要提醒还有一份持久的")

    def test_provider_without_key_stays_mock(self):
        ov = ck.persisted_overview({"PAI_MODEL_PROVIDER": "openai"})
        self.assertFalse(ov["goes_live"], "只有 provider 没有密钥就报会出门 = 冤枉用户")

    def test_unreadable_is_never_reported_as_clean(self):
        code, out = run_main([], persisted=({}, "这台不是 Windows，没有注册表这一层持久变量"))
        self.assertIn("读不到不等于没有", out)
        self.assertNotIn("√ 无（持久层", out)

    def test_unknown_provider_value_is_not_echoed(self):
        """provider 本该是枚举。有人把凭据塞进去也不能顺着打印出来。"""
        sneaky = "Bearer sk-cs-abcdef1234567890"
        ov = ck.persisted_overview({"PAI_MODEL_PROVIDER": sneaky})
        self.assertNotIn(sneaky, str(ov))
        self.assertIn("值不回显", ov["plain"]["PAI_MODEL_PROVIDER"])

    def test_secret_shaped_names_are_flagged_others_not(self):
        ov = ck.persisted_overview({"MY_TOKEN": "x" * 30, "PAI_MODEL_API_KEY": "y" * 30,
                                    "PAI_VOICE_BACKEND": "windows", "PATH": "c:\\windows"})
        named = {n for n, _ in ov["secrets"]}
        self.assertEqual(named, {"MY_TOKEN", "PAI_MODEL_API_KEY"})

    def test_endpoint_shaped_config_is_named_not_echoed(self):
        """第三类：既不像密钥名、也不在回显名单里的 PAI_/BASE_URL 变量。
        端点决定流量去哪台机器——它要是在报告里【静默消失】，"√ 无"就成了谎。
        我写完 ADR-038 第一版就是这样：这台机器上真有一条 PAI_OPENAI_BASE_URL 没被列出来。"""
        url = "https://sneaky-proxy.example.com/v1"
        ov = ck.persisted_overview({"PAI_OPENAI_BASE_URL": url, "NUMBER_OF_PROCESSORS": "8"})
        self.assertIn("PAI_OPENAI_BASE_URL", {n for n, _ in ov["others"]})
        self.assertNotIn(url, str(ov), "端点只报名与长度")
        self.assertNotIn("NUMBER_OF_PROCESSORS", {n for n, _ in ov["others"]},
                         "跟这套系统无关的环境变量别拉进来凑数")
        code, out = run_main([], persisted=({"PAI_OPENAI_BASE_URL": url}, ""))
        self.assertIn("PAI_OPENAI_BASE_URL", out)
        self.assertNotIn(url, out)
        self.assertNotIn("√ 无（持久层", out, "有一条配置时不许报成空")

    def test_enumeration_reads_values_not_subkey_count(self):
        """钉死我刚才真踩过的那个坑：QueryInfoKey 的 [0] 是【子键数】，
        拿它当值的个数 → 一个都读不到 → 却报「持久层√ 无」。假 winreg 里根本没有子键，
        值必须照样读出来。"""
        fake = _FakeWinreg({(_HKCU, "Environment"): [("PATH", "c:\\win"),
                                                    ("OPENAI_API_KEY", "sk-FAKEPERSISTEDx" * 2)]})
        with mock.patch.dict(sys.modules, {"winreg": fake}):
            with mock.patch.object(ck.platform, "system", lambda: "Windows"):
                vals, note = ck._read_persisted_env()
        self.assertEqual(note, "", "读到了就不该说读不到")
        self.assertIn("OPENAI_API_KEY", vals)
        self.assertIn("PATH", vals)

    def test_unopenable_hive_is_never_reported_as_empty(self):
        """打不开 ≠ 没有。报成"干净"比报成"读不到"危险得多。"""
        fake = _FakeWinreg({})
        with mock.patch.dict(sys.modules, {"winreg": fake}):
            with mock.patch.object(ck.platform, "system", lambda: "Windows"):
                vals, note = ck._read_persisted_env()
        self.assertEqual(vals, {})
        self.assertIn("读不到", note)

    def test_the_real_machine_run_never_echoes_a_value(self):
        """在当前这台机器上真跑一次：它读到什么我们不知道，但有一条硬保证——
        任何被它认作密钥的变量，它的【值】不许出现在输出里。"""
        vals, note = ck._read_persisted_env()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ck.print_persisted(vals, note)
        out = buf.getvalue()
        for name in vals:
            if not ck.SECRET_NAME_RE.search(name):
                continue
            value = (vals[name] or "").strip()
            if not value:
                continue                       # 空值不参与"有没有念出来"的判断
            self.assertNotIn(value, out, "%s 的值被念出来了" % name)
            self.assertIn(name, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
