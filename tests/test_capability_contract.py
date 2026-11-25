# -*- coding: utf-8 -*-
"""能力契约自检器（tools/check_capability.py）自己的测试 —— M15 试点，见 ADR-036。

规矩与 X3 三版那条一样：只给绿的不算数。所以要【当场证明它会红】——
在临时目录里造各种偷懒的模块，确认每一条契约都能抓出来。全程不碰真实 pai/
（除了那条"当前脊柱必须绿"的体检）。

运行： python -m unittest tests.test_capability_contract -v
"""
import ast
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import check_capability as CC

_OK_MODULE = '''"""一个合规的能力模块（测试用，不是产品代码）。"""
ENV_SWITCH = "PAI_FAKE_BACKEND"

CAPABILITY = {
    "name": "假能力",
    "switch_env": ENV_SWITCH,
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "坏了就静默不做，不打断主流程",
}


def go(x):
    return x
'''

_SUBSTRATE_MODULE = '''"""地基模块（测试用）：没有开关、本来就常开。"""
CAPABILITY = {
    "name": "假地基",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "坏了直接抛错，不假装还在干活",
    "approval": "ADR-036 的地基条款（测试用）",
}
'''


# 既成常开（不是地基，但确实一直没有开关）—— M15 第二批的登记形状，见 ADR-046。
_LEGACY_ON_MODULE = '''"""既成常开的模块（测试用）。"""
CAPABILITY = {
    "name": "假既成常开",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "坏了就静默不做，不打断主流程",
    "approval": "未获单独批准：既成常开，补开关需 Boss 另行定（ADR-046）",
}
'''

# 测试用的"黑板"：只有这两个号算真实存在过的住址。
_FIXTURE_ADRS = {"ADR-036", "ADR-046"}


def _substrate(**overrides):
    src = _SUBSTRATE_MODULE
    for key, val in overrides.items():
        src = re.sub(r'("%s": )[^,]*,' % key, r'\g<1>%s,' % val, src)
    return src


def _legacy_on(**overrides):
    src = _LEGACY_ON_MODULE
    for key, val in overrides.items():
        src = re.sub(r'("%s": )[^,]*,' % key, r'\g<1>%s,' % val, src)
    return src


def _off(**overrides):
    """默认关的登记（契约⑨ 的主战场：⑦⑧ 都不看这一类）；approval 缺失时补一行。"""
    src = _OK_MODULE
    if "approval" in overrides:
        src = src.replace('    "degrade": "坏了就静默不做，不打断主流程",',
                          '    "degrade": "坏了就静默不做，不打断主流程",\n    "approval": %s,'
                          % overrides.pop("approval"))
    for key, val in overrides.items():
        src = re.sub(r'("%s": )[^,]*,' % key, r'\g<1>%s,' % val, src)
    return src


class _Fixture(unittest.TestCase):
    """每个用例一个独立临时目录，绝不在真实 pai/ 里造文件。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="pai_cap_"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def give(self, fname, source):
        (self.dir / fname).write_text(source, encoding="utf-8")

    def violations(self, pending=None, adrs=None):
        return CC.scan(str(self.dir), pending=pending or {}, adrs=adrs)[2]


class TheCurrentSpinePasses(_Fixture):
    def test_spine_has_no_gap(self):
        registered, pending, violations = CC.scan()
        self.assertEqual(violations, [], "当前脊柱的契约不该有缺口：%r" % violations)
        self.assertIn("voice.py", registered)

    def test_voice_is_the_pilot(self):
        fields = CC.scan()[0]["voice.py"]
        self.assertEqual(fields["name"], "M14 语音·说")
        self.assertIs(fields["default_on"], False)
        self.assertIs(fields["stores_user_text"], False)
        self.assertIs(fields["network"], False)
        self.assertTrue(fields["degrade"].strip())

    def test_memory_is_registered_as_the_substrate(self):
        """X3 落地后 M2 从欠账转正式登记：地基条款的形状要如实（ADR-036 增补二）。"""
        fields = CC.scan()[0]["memory.py"]
        self.assertEqual(fields["switch_env"], CC.SWITCH_NONE)
        self.assertIs(fields["default_on"], True, "地基本来就开着，写 False 是假话")
        self.assertIs(fields["stores_user_text"], True, "全库原文就存在这里，不许写成不存")
        self.assertEqual(fields["text_hosts_ref"], CC.TEXT_HOSTS_ADDR)
        self.assertTrue(fields["approval"].strip(), "常开是谁点头的，要写在登记里")

    def test_switch_env_name_has_exactly_one_address(self):
        """契约⑥：环境变量名不许抄第二份。模块里只能在常量定义处出现一次。"""
        src = (ROOT / "pai" / "voice.py").read_text(encoding="utf-8")
        fields = CC.scan()[0]["voice.py"]
        self.assertEqual(fields["switch_env"], "PAI_VOICE_BACKEND")
        self.assertEqual(src.count('"PAI_VOICE_BACKEND"'), 1,
                         "登记里把环境变量名又写了一遍 = 两份住址，迟早打架")

    def test_pending_list_covers_every_other_module(self):
        files = {f for f in os.listdir(str(CC.PAI_DIR)) if f.endswith(".py")}
        declared = set(CC.PENDING) | set(CC.scan()[0]) | set(CC.STRUCTURAL)
        self.assertEqual(files - declared, set(), "有模块既没登记也没进欠账名单")

    def test_the_five_late_registrations_state_the_truth(self):
        """M15 第二批（ADR-046）：这五个登记的是【今天真实的样子】，不是希望的样子。"""
        reg = CC.scan()[0]
        for fname in ("reflection.py", "scheduler.py", "affect.py", "cultural_kb.py"):
            fields = reg[fname]
            self.assertEqual(fields["switch_env"], CC.SWITCH_NONE,
                             "%s：脊柱里从来没有这个开关，登记不许编一个环境变量" % fname)
            self.assertIs(fields["default_on"], True,
                          "%s：它一直在跑，写缺省关是假话" % fname)
            self.assertIn("未获单独批准", fields["approval"],
                          "%s：这份常开没人批准过，不许写成 Boss 定调（ADR-044）" % fname)
        for fname in ("reflection.py", "scheduler.py", "affect.py"):
            self.assertIs(reg[fname]["stores_user_text"], True,
                          "%s：它写的列在 _TEXT_HOSTS 里，登记成不存是漏口" % fname)
        self.assertIs(reg["cultural_kb.py"]["stores_user_text"], False,
                      "M9 纯读纯函数，一张表都不写")
        gw = reg["model_gateway.py"]
        self.assertIs(gw["network"], True, "M10 是脊柱里唯一真会出门的一层")
        self.assertIs(gw["default_on"], False, "不设环境变量=永远 mock，缺省真的不出门")
        self.assertEqual(gw["switch_env"], "PAI_MODEL_PROVIDER")

    def test_pending_list_is_down_to_the_one_seamed_module(self):
        self.assertEqual(set(CC.PENDING), {"orchestrator.py"},
                         "欠账名单只能减：第二批之后只剩带接缝的那一个")
        self.assertIn("串行", CC.PENDING["orchestrator.py"], "为什么还没登记，要写明归谁等")

    def test_gateway_switch_name_has_exactly_one_address(self):
        """契约⑥ 同样管新登记的这一份：环境变量名在模块里只能有定义处那一个住址。"""
        src = (ROOT / "pai" / "model_gateway.py").read_text(encoding="utf-8")
        self.assertEqual(src.count('"PAI_MODEL_PROVIDER"'), 1,
                         "登记里又抄一遍环境变量名 = 两份住址，迟早打架")

    def test_the_real_blackboard_is_what_the_address_check_reads(self):
        """住址核验读的是真黑板，不是测试自己造的集合（否则这条守卫是空的）。"""
        adrs = CC._adr_index()
        if adrs is None:
            self.skipTest("黑板不在 %s 的上一层（这份拷贝只带了代码目录）"
                          % os.path.basename(CC.ROOT))
        self.assertIn("ADR-036", adrs)
        cited = set()
        for fields in CC.scan()[0].values():
            cited |= set(CC._ADR_RE.findall(str(fields.get("approval") or "")))
        self.assertTrue(cited, "该有登记引了 ADR 住址")
        self.assertEqual(cited - adrs, set(), "登记里引了黑板搜不到的 ADR 号")

    def test_claiming_stored_text_checks_the_real_table(self):
        """契约④：谁声称存过原文，登记里就必须指向那张真表 —— 两种结果都要试出来。
        不拿"今天表在不在"当断言（那是快照，不是规矩；X3 一落地就会把断言打翻）。"""
        self.give("liar.py", _OK_MODULE.replace(
            '"stores_user_text": False,',
            '"stores_user_text": True, "text_hosts_ref": "%s",' % CC.TEXT_HOSTS_ADDR))
        with mock.patch.object(CC, "_registry_ready", lambda: False):
            bad = self.violations()
            self.assertTrue(any("还没有那张表" in v for v in bad),
                            "表不在却声称存原文，必须红：%r" % bad)
        with mock.patch.object(CC, "_registry_ready", lambda: True):
            self.assertEqual(self.violations(), [], "表在且指对了，就该绿")

    def test_wrong_registry_address_is_always_red(self):
        self.give("wrong.py", _OK_MODULE.replace(
            '"stores_user_text": False,',
            '"stores_user_text": True, "text_hosts_ref": "别处",' ))
        bad = self.violations()
        self.assertTrue(any("要把 text_hosts_ref 指向" in v for v in bad), bad)

    def test_registry_probe_agrees_with_the_source(self):
        """登记器说"表在不在"，必须和 memory.py 的真实源码一致（不许自己猜）。"""
        src = (Path(CC.PAI_DIR) / "memory.py").read_text(encoding="utf-8")
        names = {t.id for n in ast.parse(src).body if isinstance(n, ast.Assign)
                 for t in n.targets if isinstance(t, ast.Name)}
        self.assertEqual(CC._registry_ready(), "_TEXT_HOSTS" in names)


class ItGoesRed(_Fixture):
    def test_unregistered_and_unlisted_module(self):
        self.give("sneaky.py", "def go(x):\n    return x\n")
        self.assertTrue(any("既没登记" in v for v in self.violations()))

    def test_registered_module_is_not_also_in_pending(self):
        """欠账名单不许当保险箱：登记过的模块同时挂在名单里 = 名单烂掉的开始。"""
        self.give("voice_like.py", _OK_MODULE)
        bad = self.violations({"voice_like.py": "混进来的"})
        self.assertTrue(any("不许再挂在欠账名单里" in v for v in bad), bad)

    def test_default_on_is_rejected(self):
        self.give("always_on.py", _OK_MODULE.replace('"default_on": False', '"default_on": True'))
        self.assertTrue(any("缺省必须关" in v for v in self.violations()))

    def test_empty_switch_env(self):
        self.give("noswitch.py", _OK_MODULE.replace('"switch_env": ENV_SWITCH', '"switch_env": ""'))
        self.assertTrue(any("switch_env" in v for v in self.violations()))

    def test_lowercase_env_name(self):
        self.give("bad_env.py", _OK_MODULE.replace('ENV_SWITCH = "PAI_FAKE_BACKEND"',
                                                  'ENV_SWITCH = "pai_fake_backend"'))
        self.assertTrue(any("大写环境变量名" in v for v in self.violations()))

    def test_vague_degrade(self):
        self.give("no_plan.py", _OK_MODULE.replace(
            '"degrade": "坏了就静默不做，不打断主流程",', '"degrade": "",'))
        self.assertTrue(any("degrade" in v for v in self.violations()))

    def test_network_without_approval(self):
        self.give("chatty.py", _OK_MODULE.replace('"network": False,', '"network": True,'))
        self.assertTrue(any("approval" in v for v in self.violations()))

    def test_non_literal_value_is_not_a_registration(self):
        """开关位置写成 os.environ.get(...) 这种运行期才知道的东西 = 等于没登记。"""
        self.give("runtime.py", _OK_MODULE.replace('"switch_env": ENV_SWITCH',
                                                  '"switch_env": os.environ.get("X")'))
        self.assertTrue(any("字面量" in v for v in self.violations()))

    def test_missing_required_field(self):
        self.give("half.py", _OK_MODULE.replace('"name": "假能力",\n', ""))
        self.assertTrue(any("缺必填字段 name" in v for v in self.violations()))

    def test_private_extra_field(self):
        self.give("extra.py", _OK_MODULE.replace('"name": "假能力",',
                                                 '"name": "假能力", "magic": 1,'))
        self.assertTrue(any("不在契约里" in v for v in self.violations()))

    def test_stale_pending_entry(self):
        self.give("ok.py", _OK_MODULE)
        bad = self.violations({"ghost.py": "文件早没了"})
        self.assertTrue(any("ghost.py" in v for v in bad), bad)

    def test_pending_entry_without_reason(self):
        self.give("lazy.py", "def go(x):\n    return x\n")
        bad = self.violations({"lazy.py": "   "})
        self.assertTrue(any("必须有理由" in v for v in bad), bad)

    def test_only_substrates_may_claim_no_switch(self):
        """地基豁免是有限口子：不在 SUBSTRATE 名单里的模块想用 NONE 躲开关，必须红。"""
        self.give("sneaky.py", _OK_MODULE.replace(
            '"switch_env": ENV_SWITCH,', '"switch_env": "NONE",'))
        bad = self.violations()
        self.assertTrue(any("想躲开关" in v for v in bad),
                        "普通能力声明没有开关，契约① 就不该放过它：%r" % bad)

    def test_substrate_must_adapt_to_be_always_on(self):
        self.give("memory.py", _substrate(default_on="False"))
        bad = self.violations()
        self.assertTrue(any("本来" in v and "True" in v for v in bad),
                        "地基写着缺省关 = 假话，必须红：%r" % bad)

    def test_substrate_must_say_who_accepted_always_on(self):
        self.give("memory.py", _substrate(approval='""'))
        bad = self.violations()
        self.assertTrue(any("approval 要写明" in v for v in bad), bad)

    def test_substrate_must_not_pretend_to_have_a_switch(self):
        self.give("memory.py", _substrate(switch_env='"PAI_FAKE_SWITCH"'))
        bad = self.violations()
        self.assertTrue(any("地基没有开关" in v for v in bad), bad)

    def test_clean_fixture_is_green(self):
        """反证：以上每条都改对了就该 0 违规 —— 否则说明上面是在测断言不是测契约。"""
        self.give("good.py", _OK_MODULE)
        self.assertEqual(self.violations(), [])


class AlwaysOnNeedsARealAddress(_Fixture):
    """契约⑧（ADR-046）：常开与出网两类登记要给一个【查得到】的 ADR 住址。

    ★两类失败都要试出来：没引号（红）、引了黑板里没有的号（红）、引对了（绿）。
    只测"没引就红"是不够的 —— 那样 DeepSeek 那类 "ADR-999" 的假住址照样放行。
    """

    def test_always_on_without_any_address(self):
        self.give("quiet_on.py", _legacy_on(approval='""'))
        bad = self.violations(adrs=_FIXTURE_ADRS)
        self.assertTrue(any("契约⑧" in v and "ADR" in v for v in bad), bad)

    def test_fabricated_address_is_red(self):
        """编一个不存在的 ADR 号 = 橡皮图章，必须被住址核验抓出来。"""
        self.give("fake_addr.py", _legacy_on(
            approval='"既成常开，由 ADR-999 定调（黑板里没这条）"'))
        bad = self.violations(adrs=_FIXTURE_ADRS)
        self.assertTrue(any("搜不到" in v for v in bad), bad)

    def test_verified_address_is_green(self):
        self.give("on_ok.py", _legacy_on())
        self.assertEqual(self.violations(adrs=_FIXTURE_ADRS), [])

    def test_network_module_also_needs_the_address(self):
        """出网这一侧：approval 有内容但没住址 → 契约⑦ 过了、契约⑧ 必须还红。
        ★少这一条，"只要写满 approval 就放行" 的写法会悄悄替代真正的守卫。"""
        self.give("out.py", _OK_MODULE.replace(
            '"network": False,', '"network": True, "approval": "Boss 2026-10-20 定调 opt-in",'))
        bad = self.violations(adrs=_FIXTURE_ADRS)
        self.assertTrue(any("契约⑧" in v and "出网" in v for v in bad), bad)
        self.give("out_ok.py", _OK_MODULE.replace(
            '"network": False,',
            '"network": True, "approval": "opt-in 由 ADR-036 定（测试用）",'))
        bad2 = self.violations(adrs=_FIXTURE_ADRS)
        self.assertEqual([v for v in bad2 if v.startswith("out_ok.py")], [], bad2)

    def test_claiming_no_switch_but_written_as_off_is_red(self):
        """没有开关（NONE）= 它其实一直在跑，default_on 写 False 是假话。"""
        self.give("off_but_none.py", _legacy_on(default_on="False"))
        bad = self.violations(adrs=_FIXTURE_ADRS)
        self.assertTrue(any("一直在跑" in v for v in bad), bad)

    def test_substrate_is_not_exempt_from_the_address(self):
        self.give("memory.py", _substrate(approval='"常开是产品定义，但没有住址"'))
        bad = self.violations(adrs=_FIXTURE_ADRS)
        self.assertTrue(any("契约⑧" in v for v in bad), bad)

    def test_missing_blackboard_only_checks_format(self):
        """adrs=None（代码目录搬走、黑板不在上一层）：格式仍要查，但不假装核验过。"""
        self.give("any_addr.py", _legacy_on(approval='"既成常开（ADR-999）"'))
        self.assertEqual(self.violations(adrs=None), [])
        self.give("no_addr.py", _legacy_on(approval='"既成常开，没写住址"'))
        bad = self.violations(adrs=None)
        self.assertTrue(any("契约⑧" in v for v in bad), bad)


class BossNodNeedsARealAddress(_Fixture):
    """契约⑨（X12，Boss 2026-10-22 点头「装」）：approval 声称"Boss 批过了"就得引真实 ADR 号。

    契约⑦⑧ 只查【常开】和【出网】，而历次造假全住在默认关的登记里（M8「点头 J2『能查』」、
    X10「Boss 原话『做 X10』」），所以这一条盯措辞、不看开关状态。
    """

    def test_default_off_fake_nod_is_red(self):
        """盲区本体：默认关 + 假点头 + 零住址。⑦⑧ 都放行，⑨ 必须拦。"""
        self.give("nod.py", _off(approval='"Boss 2026-10-21 点头 J2『能查』"'))
        bad = self.violations(adrs=_FIXTURE_ADRS)
        self.assertTrue(any("契约⑨" in v for v in bad), bad)

    def test_fake_nod_with_fabricated_address_is_red(self):
        """引了号不算数：ADR-999 这种没立过条的号一样红。"""
        self.give("nod.py", _off(approval='"Boss 点头（ADR-999）"'))
        bad = self.violations(adrs=_FIXTURE_ADRS)
        self.assertTrue(any("契约⑨" in v for v in bad), bad)

    def test_honest_not_approved_wording_is_green(self):
        """照 ADR-044 如实写"未获单独批准"的必须绿，否则等于逼投递方把话说空来躲检查。"""
        self.give("nod.py", _off(
            approval='"未获单独批准：由 DeepSeek 自选并默认关，常开需 Boss 另行点头"'))
        self.assertEqual(self.violations(adrs=_FIXTURE_ADRS), [])

    def test_real_nod_with_verified_address_is_green(self):
        self.give("nod.py", _off(approval='"Boss 2026-10-21 定调默认关（ADR-046）"'))
        self.assertEqual(self.violations(adrs=_FIXTURE_ADRS), [])

    def test_the_four_historical_fakes_all_light_up(self):
        """措辞判定要对得上真发生过的四次造假（句子抄自留底，不是我编的例子）。"""
        fakes = ("Boss 2026-10-21 点头 J2『能查』",
                 "Boss 原话『做 X10』：常驻守护",
                 "Boss 2026-10-21 点头 M8 第二批",
                 "Boss 指定全收")
        for txt in fakes:
            self.assertTrue(CC._claims_boss_nod(txt), txt)
        honest = ("未获单独批准：既成常开，补开关需 Boss 另行定（ADR-046）",
                  "云端 TTS 后端要把回答发给第三方，需 Boss 单独点头",
                  "未获单独批准：Boss 没对『模型选工具』点过头，ADR-043 明写需要 Boss 另批",
                  "身份层常开由 ADR-031 立项；不设开关")
        for txt in honest:
            self.assertFalse(CC._claims_boss_nod(txt), txt)

    def test_address_index_ignores_bare_mentions(self):
        """黑板正文里提到过的号不算住址：黑板自己写过「引 ADR-999→红」当反面例子，
        旧算法（全文 findall）会把 999 收进真实住址，这条钉死它只认标题行。"""
        board = self.dir / "board.txt"
        board.write_text("  ADR-046（2026-10-21）能力契约增补\n"
                         "  会红：删住址→红；引 ADR-999→红；\n"
                         "  ADR-031 [已定 2026-10-20] 安全\n", encoding="utf-8")
        adrs = CC._adr_index(str(board))
        self.assertEqual(adrs, {"ADR-046", "ADR-031"})

    def test_missing_blackboard_still_checks_the_nod_wording(self):
        """adrs=None（代码目录搬走、黑板不在上一层）：措辞照查，住址那层只查格式。

        两个形状写进【同一个文件】：留着上一条的 nod.py 再新建 nod2.py，
        violations() 会把 nod.py 那条红一起带进来，第二半就成了假的断言。
        """
        self.give("nod.py", _off(approval='"Boss 2026-10-21 点头 J2『能查』"'))
        self.assertTrue(any("契约⑨" in v for v in self.violations(adrs=None)))
        self.give("nod.py", _off(approval='"Boss 点头（ADR-999）"'))
        self.assertEqual(self.violations(adrs=None), [])


# 缺省开但【有真开关】的能力——ADR-086 那条例外通道的形状（测试用例，不是产品代码）。
# degrade 里不重复环境变量名：契约⑥ 那一格（一个名字只有一个住址）要拿当正面例子，别自己先破。
_ALWAYS_ON_MODULE = '''"""缺省开、有真开关的能力（测试用）。"""
ENV_SWITCH = "PAI_FAKE_ALWAYS_ON"

CAPABILITY = {
    "name": "假常开能力",
    "switch_env": ENV_SWITCH,
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "显式设成 off 才不干活；坏了静默不做，不打断主流程",
    "approval": "缺省开由 ADR-036 定调（测试用住址）",
}
'''


def _always_on(**overrides):
    src = _ALWAYS_ON_MODULE
    for key, val in overrides.items():
        src = re.sub(r'("%s": )[^,]*,' % key, r'\g<1>%s,' % val, src)
    return src


class AlwaysOnNeedsARealAddressForASwitch(_Fixture):
    """契约① 的常开例外通道（ADR-086，Boss 2026-10-30 定调「补 ADR」）。

    ★五条缺一条就不算守卫：引【查得到】的号→绿；【没引】→红；引【查不到的号】→红；
    含糊值→红；同一件其它判据照红（证明我没把整块 elif 跳过）。
    只测第一半等于把契约① 换成橡皮图章——ADR-046 那轮栽的就是这个形状（黑板里写过「引 ADR-999→红」的反面例子）。
    """

    def test_approved_always_on_with_real_address_is_green(self):
        self.give("on_ok.py", _always_on())
        self.assertEqual(self.violations(adrs=_FIXTURE_ADRS), [])

    def test_always_on_without_any_address_is_red(self):
        self.give("on_noaddr.py", _always_on(approval='""'))
        bad = self.violations(adrs=_FIXTURE_ADRS)
        self.assertTrue(any("契约①" in v and "缺省必须关" in v for v in bad), bad)

    def test_always_on_with_fabricated_address_is_red(self):
        self.give("on_fake.py", _always_on(approval='"缺省开由 ADR-999 定调（黑板里没立过这条）"'))
        bad = self.violations(adrs=_FIXTURE_ADRS)
        self.assertTrue(any("契约①" in v for v in bad), bad)
        self.assertTrue(any("搜不到" in v for v in bad), bad)

    def test_vague_default_on_value_is_red(self):
        """default_on 写成 "yes" 既不是 True 也不是 False：不许借例外通道溜过去。"""
        self.give("on_vague.py", _always_on(default_on='"yes"'))
        bad = self.violations(adrs=_FIXTURE_ADRS)
        self.assertTrue(any("只许 True/False" in v for v in bad), bad)

    def test_the_exception_does_not_loosen_the_other_contracts(self):
        """例外只放行【常开】这一格：同一件里 degrade 写空仍要红＝判据没被整块跳过。"""
        self.give("on_no_degrade.py", _always_on(degrade='""'))
        bad = self.violations(adrs=_FIXTURE_ADRS)
        self.assertTrue(any("契约②" in v for v in bad), bad)


class ReportShape(unittest.TestCase):
    def test_main_reports_and_returns_code(self):
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            code = CC.main([])
        text = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("契约无违规", text)
        self.assertIn("[登记]", text)
        self.assertIn("[欠账]", text)


if __name__ == "__main__":
    unittest.main()
