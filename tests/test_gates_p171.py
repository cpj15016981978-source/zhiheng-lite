# -*- coding: utf-8 -*-
"""P171 四族新闸门的常驻守卫：归档族登记 / H6 截断再读 / R1 在飞口径 / initiative 三态。

每族都带「会红」那一臂：把刀拔掉必须炸，不然这条闸门只是装饰。
"""
import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PW = _load("check_paperwork")
RL = _load("check_relay")


def _txt(path, lines, crlf=True):
    sep = "\r\n" if crlf else "\n"
    path.write_bytes((sep.join(lines) + sep).encode("utf-8"))


class ArchiveDebtArms(unittest.TestCase):
    """归档族登记：处数涨=H5、处数少=H0、形状换=H5、整片不见=H4、对上=绿但必须念出来。"""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="arch_debt_"))
        (self.d / "留底_ARCH1").mkdir()
        _txt(self.d / "留底_ARCH1" / "a.txt", ["脏一行", "又一行"], crlf=False)
        _txt(self.d / "留底_ARCH1" / "b.txt", ["干净一行"])
        (self.d / "日志").mkdir()
        _txt(self.d / "日志" / "2099-01-01-Qoder.txt", ["日志一行"])
        self.pfx = "留底_ARCH1/"

    def _run(self, n0, shapes=("H1-lf",)):
        return PW.run(self.d, cutoff="2020-01-01", known_debt={},
                      archive_debt={self.pfx: (n0, tuple(shapes), "测试用", "P171 测试格")}, h6=False)

    def test_matched_count_is_green_and_read_aloud(self):
        v, debt, _notice, st = self._run(1)
        self.assertEqual(v, [], "处数与形状都对上，不该红：%s" % v)
        self.assertEqual(len(debt), 1, "对上也要念出来，不许静默绿")
        self.assertIn("归档欠账", debt[0])
        self.assertEqual(st["arch_hits"], 1)
        self.assertEqual(st["arch_files"], 2)

    def test_grown_count_goes_red(self):
        v, _debt, _n, _st = self._run(0)
        self.assertEqual(len(v), 1, "处数涨了必须红：%s" % v)
        self.assertIn("[H5]", v[0])

    def test_shrunk_count_goes_red(self):
        v, _debt, _n, _st = self._run(2)
        self.assertEqual(len(v), 1, "处数少了（被悄悄归一）必须红：%s" % v)
        self.assertIn("[H0]", v[0])

    def test_shape_change_goes_red(self):
        v, _debt, _n, _st = self._run(1, shapes=("H2-tab",))
        self.assertEqual(len(v), 1, "处数没变但形状换了必须红：%s" % v)
        self.assertIn("[H5]", v[0])

    def test_missing_archive_face_goes_red(self):
        debt = {self.pfx: (1, ("H1-lf",), "测试", "P171 测试格"),
                "留底_没这一族/": (1, ("H1-lf",), "测试", "P171 测试格")}
        v, _debt, _n, _st = PW.run(self.d, cutoff="2020-01-01", known_debt={},
                                   archive_debt=debt, h6=False)
        self.assertEqual(len(v), 1, "登记那一片整片不见了必须红（面塌不叫绿），其余不该红：%s" % v)
        self.assertIn("[H4]", v[0])


class H6TruncateReadArms(unittest.TestCase):
    """H6：先截断打开、之后又读同一路径＝读到空件。读在写之前是合规写法，不许假红。"""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="h6_"))

    def _w(self, name, text):
        p = self.d / name
        p.write_text(text, encoding="utf-8")
        return p

    # 样本里的截断打开（wb 那档）一律用 %r 现场格式化：H6 是按原文配对的静态扫，分不出「夹具」与「真调用」，
    # 原样写进行内它就会把自己家咬成红（上线第一遍真咬到了）。渲染出来的样本字节一字不变，臂照样会红。
    def test_truncate_then_read_is_red(self):
        p = self._w("bad.py", 'p = "a.bin"\nopen(p, %r).write(b"x")\ny = open(p, %r).read()\n' % ("wb", "rb"))
        hits = PW.check_truncate_read(p)
        self.assertEqual(len(hits), 1, "脏样本必须红：%s" % hits)
        self.assertIn("[H6]", hits[0])

    def test_read_then_truncate_is_clean(self):
        p = self._w("ok.py", 'p = "a.bin"\nx = open(p, %r).read()\nopen(p, %r).write(x)\n' % ("rb", "wb"))
        self.assertEqual(PW.check_truncate_read(p), [], "读在写先是合规写法，判它红就是假红")

    def test_attr_read_after_truncate_is_red(self):
        p = self._w("attr.py", 'p = Path("a.bin")\nopen(p, %r).write(b"x")\nz = p.read_bytes()\n' % ("wb",))
        self.assertEqual(len(PW.check_truncate_read(p)), 1, "read_bytes 那一族也要挡住")

    def test_append_mode_is_clean(self):
        p = self._w("ab.py", 'p = "a.bin"\nopen(p, %r).write(b"x")\ny = open(p, %r).read()\n' % ("ab", "rb"))
        self.assertEqual(PW.check_truncate_read(p), [], "追加不是截断，判红就是假红")

    def test_collapsed_face_is_red(self):
        v, f = PW.run_h6(self.d)
        self.assertEqual(f, 0, "这个临时面里没有 .py，件数该是 0")
        self.assertEqual(len(v), 1, "一件也没扫到必须响一声：%s" % v)

    def test_foreign_tree_does_not_inherit_the_face_red(self):
        # 陌生合成树：没有那一面，run() 不该替真实板根背红，但必须念一行知会。
        (self.d / "日志").mkdir()
        _txt(self.d / "日志" / "2099-01-01-Qoder.txt", ["干净一行，纯 CRLF。"])
        v, _debt, notice, st = PW.run(self.d, cutoff="2020-01-01", known_debt={}, archive_debt={})
        self.assertEqual([x for x in v if x.startswith("[H6]")], [],
                         "这一棵树压根没有 H6 那一面，判红就是替真实板根背红：%s" % v)
        self.assertEqual(st["py_face"], 0)
        self.assertTrue(st.get("h6_skipped"), "不比也要有一声口径，不许静默：%s" % st)
        self.assertEqual(notice, [], "这一声走 stats，不占 notice——notice 的行数在 run() 里当少比一件用")

    def test_default_registry_reports_skipped_count(self):
        # 归档登记未套用这件事也得有口径：件数进 stats，不许只在人眼里。
        (self.d / "日志").mkdir()
        _txt(self.d / "日志" / "2099-01-01-Qoder.txt", ["干净一行，纯 CRLF。"])
        v, _debt, notice, st = PW.run(self.d, cutoff="2020-01-01", known_debt={}, archive_debt=None)
        self.assertEqual(v, [], "陌生合成树不该替真实板根背归档红：%s" % v)
        self.assertEqual(st["arch_skipped"], len(PW.ARCHIVE_DEBT), st)
        self.assertEqual(notice, [], notice)

    def test_tree_that_should_have_the_face_still_goes_red(self):
        # 脊柱那层在、可 H6 那一面零件也没有＝面塌，照样红——这条证明上面那刀不是给自己开的后门。
        (self.d / "personal-ai-system" / "tools").mkdir(parents=True)
        (self.d / "日志").mkdir()
        _txt(self.d / "日志" / "2099-01-01-Qoder.txt", ["干净一行，纯 CRLF。"])
        v, _debt, _notice, _st = PW.run(self.d, cutoff="2020-01-01", known_debt={}, archive_debt={})
        self.assertEqual(len([x for x in v if x.startswith("[H6]")]), 1, "面塌必须红：%s" % v)


class RelayInflightArms(unittest.TestCase):
    """R1 在飞口径：工作树改了还没提交、投递区＝已提交那一版 → 只知会不判红；两头都不等 → 红。"""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="relay_inflight_"))
        self.ws = self.d / "ws"
        self.mi = self.d / "mirror"
        self.ws.mkdir()
        self.mi.mkdir()
        (self.ws / "a.py").write_bytes("工作树在飞的那一版\n".encode("utf-8"))
        (self.mi / "a.py").write_bytes("已提交那一版\n".encode("utf-8"))
        self.head = "已提交那一版\n".encode("utf-8")

    def _patched(self, dirty, head):
        old_dirty, old_head = RL._git_dirty_files, RL._git_head_bytes
        RL._git_dirty_files, RL._git_head_bytes = dirty, head
        try:
            inflight = []
            v = RL.check_mirror_equal(self.ws, self.mi, inflight)
        finally:
            RL._git_dirty_files, RL._git_head_bytes = old_dirty, old_head
        return v, inflight

    def test_inflight_head_equal_is_not_red(self):
        v, inflight = self._patched(lambda ws: {"a.py"}, lambda ws, rel: self.head)
        self.assertEqual(v, [], "投递区就是已发布那一版，不该判红：%s" % v)
        self.assertEqual(inflight, ["a.py"], "但必须登记成在飞，别念成镜像一致")

    def test_third_version_is_still_red(self):
        v, inflight = self._patched(lambda ws: {"a.py"}, lambda ws, rel: "第三版\n".encode("utf-8"))
        self.assertEqual(len(v), 1, "躲进在飞的第三版必须红：%s" % v)
        self.assertEqual(inflight, [])

    def test_without_git_falls_back_to_strict(self):
        old = RL._git_dirty_files
        RL._git_dirty_files = lambda ws: None
        try:
            v = RL.check_mirror_equal(self.ws, self.mi, [])
        finally:
            RL._git_dirty_files = old
        self.assertEqual(len(v), 1, "git 拿不到时退回严判，不许静默降级成绿：%s" % v)

    def test_without_inflight_arg_keeps_old_behaviour(self):
        self.assertEqual(len(RL.check_mirror_equal(self.ws, self.mi)), 1,
                         "不传 inflight 就还是旧的严判口径，老调用点的行为不许被我改味")


class InitiativeArms(unittest.TestCase):
    """initiative 读数面三态：无源／没有那一支＝测不了（退 2）；有＝出读数（退 0）。阈值不住这里。"""

    def setUp(self):
        self.E = _load("self_eval")
        self.d = Path(tempfile.mkdtemp(prefix="init_"))

    def _say(self, *args, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.E.run_initiative(*args, **kw)
        return rc, buf.getvalue()

    def _stream(self, lines):
        p = self.d / "a1.activity.jsonl"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    def test_missing_source_is_not_measurable(self):
        rc, out = self._say(str(self.d / "没这个文件.jsonl"))
        self.assertEqual(rc, 2)
        self.assertIn("测不了", out)

    def test_no_initiative_vocab_is_not_measurable(self):
        p = self._stream(['{"kind": "user_activity", "at": "2026-11-16T09:00:00"}',
                          '{"kind": "user_activity", "at": "2026-11-16T10:00:00"}'])
        rc, out = self._say(str(p))
        self.assertEqual(rc, 2, "地基没接上时不许报成功，也不许写成失败")
        self.assertIn("测不到", out)
        self.assertNotIn("通过", out)

    def test_events_give_readings_not_verdicts(self):
        p = self._stream(['{"kind": "user_activity", "at": "2026-11-16T09:00:00"}',
                          '{"kind": "user_activity", "at": "2026-11-16T10:00:00"}',
                          '{"kind": "initiative", "at": "2026-11-16T11:00:00", "reason": "我把两条原则并排看了一眼"}',
                          '{"kind": "initiative", "at": "2026-11-16T12:00:00", "reason": "我把两条原则并排看了一眼"}',
                          '{"kind": "initiative", "at": "2026-11-16T12:40:00", "reason": "先问 Boss 要不要留这一格"}'])
        rc, out = self._say(str(p))
        self.assertEqual(rc, 0)
        self.assertIn("计入 3 次", out, "三条都落在静默段：%s" % out)
        self.assertIn("句式去重 2 种", out, "两条同句式要能看出复读：%s" % out)
        self.assertIn("只出数", out, "这句要自己声明只出数，阈值不住这里")
        self.assertNotIn("通过", out, "读数面不许自己签发通过——那是协议那一节的活")

    def test_no_request_times_is_not_measurable(self):
        p = self._stream(['{"kind": "initiative", "at": "2026-11-16T11:00:00", "reason": "自主一条"}'])
        rc, out = self._say(str(p))
        self.assertEqual(rc, 2, "没有请求侧时刻就无从判静默，别猜窗口")
        self.assertIn("测不了", out)

    def test_existing_strict_guard_still_holds(self):
        """DS 那条守卫（initiative 不许假装有结果）在新口径下仍然为真：现读发布位流仍退 2。"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.E.main(["initiative"])
        self.assertEqual(rc, 2)
        self.assertIn("测不了", buf.getvalue())



class H7TokenArms(unittest.TestCase):
    """H7：正文里留着未替换的模板令牌＝那一行没有读数，必须红；小写 at 那种是名字，不许判红。"""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="h7_"))
        (self.d / "日志").mkdir()
        self.p = self.d / "日志" / "2099-01-01-Qoder.txt"

    def _hits(self, lines):
        _txt(self.p, lines)
        return PW.check_one("日志/2099-01-01-Qoder.txt", self.p)

    def test_uppercase_token_is_red(self):
        hits = self._hits(["取数时刻 11:40：板根 @BBS@ B"])
        self.assertEqual(len(hits), 1, "未替换令牌必须红：%s" % hits)
        self.assertIn("[H7]", hits[0][1])

    def test_double_at_shape_is_also_red(self):
        self.assertIn("[H7]", self._hits(["坐标 @@GMAX@@ 取号现算"])[0][1])

    def test_single_letter_token_is_red(self):
        self.assertIn("[H7]", self._hits(["取数时刻 @@T@@ 由刀填"])[0][1],
                    "单字母那一档也要抓到：落板刀自己就写过这种令牌，而旧形状 {1,24} 既不会替换它、也不会判红")

    def test_lowercase_at_sign_is_not_red(self):
        self.assertEqual(self._hits(["联系 someone@example 那个人就行"]), [],
                         "@小写 那种是正文里的名字，不是刀里的令牌，判红就是假红")

    def test_clean_lines_are_not_red(self):
        self.assertEqual(self._hits(["板根 2175670 B／d3d8b509319d（取数时刻 11:00:00）"]), [])

    def test_gate_level_red(self):
        _txt(self.p, ["第 1 款坐标 @GMAX@ 取号现算"])
        v, _debt, _n, _st = PW.run(self.d, cutoff="2020-01-01", known_debt={}, archive_debt={}, h6=False)
        self.assertEqual(len(v), 1, "闸门层也必须红：%s" % v)
        self.assertIn("[H7]", v[0])

class SpineBoardFaceAndDoubleCrlf(unittest.TestCase):
    """P172 两臂：发布位那支板根要在面内；每行多一个 CR 那种坏法要会红。

    这一族的来历是 2026-11-16 我自己那一刀：文本写盘传 newline="\\r\\n"，已有 CRLF 里那个 LF
    又被翻一层，板根 6581 行变成 6581 处双写换行。CRLF 计数与裸LF 两项都照样绿，
    而当时面里只有 D:\\Q 那两支黑板——我要提交的这份字节根本没人查。
    """

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="spine_face_"))

    def test_release_tree_board_is_in_the_face(self):
        rels = [r for r, _ in PW.scan_targets(PW.DEFAULT_ROOT)]
        self.assertIn(PW.SPINE_REL, rels, "发布位板根不在面内＝面又漏回原来那一支")
        hit = [p for r, p in PW.scan_targets(PW.DEFAULT_ROOT) if r == PW.SPINE_REL]
        self.assertEqual(len(hit), 1, "这一支只能以这一个 rel 进面一次：%s" % hit)

    def test_double_crlf_is_red(self):
        p = self.d / "zzq_double.txt"
        p.write_bytes("第一行\r\r\n第二行\r\r\n".encode("utf-8"))
        got = PW.check_one("zzq_double.txt", p)
        self.assertEqual(len(got), 1, "双写换行必须红：%s" % got)
        self.assertIn("H1-rrrn", got[0][0], "指纹要落进 H1-rrrn，不然归档登记认不到它：%s" % (got[0][0],))

    def test_pure_crlf_stays_clean(self):
        p = self.d / "zzq_pure.txt"
        p.write_bytes("第一行\r\n第二行\r\n".encode("utf-8"))
        self.assertEqual(PW.check_one("zzq_pure.txt", p), [], "纯 CRLF 判红就是假红")

    def test_bare_lf_still_red_with_its_own_fingerprint(self):
        p = self.d / "zzq_lf.txt"
        p.write_bytes("第一行\n第二行\n".encode("utf-8"))
        got = PW.check_one("zzq_lf.txt", p)
        self.assertIn("H1-lf", got[0][0], "旧那条判据不能被我加新臂时弄丢：%s" % (got[0][0],))


if __name__ == "__main__":
    unittest.main(verbosity=2)
