r"""留底/日志卫生自检器自己的测试（tools/check_paperwork.py）。

自检器一旦失真就比没有更糟——它会让我以为留底面是干净的。所以每条判据都要能【真的失败】：
先造故意歪掉的临时板根确认它报红，再确认干净树 0 违规，最后连白名单那三刀一起验。
全程只用 tempfile 造的假板根，绝不碰真实 D:\Q。

行尾一律用 bytes([13]) / bytes([10]) 拼，不写转义字面量：
25 日我就是被「反斜杠加字母经命令行转发变真制表符」咬了三次，这里不再赌任何一层转义。

运行： python -m unittest discover -s tests
"""
import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import check_paperwork as CP

CR = bytes([13])
LF = bytes([10])
CRLF = CR + LF
TAB = bytes([9])
TAB_CH = chr(9)
CR_CH = chr(13)
LF_CH = chr(10)
CRLF_CH = CR_CH + LF_CH
OLD_MTIME = datetime.datetime(2000, 1, 1).timestamp()
LATE_CUTOFF = "2026-01-01 00:00:00"
# 测试默认把上线线推到 1999，让造出来的每件都落在比对面里；划界本身另有两条测试单独验。
ALWAYS = "1999-01-01 00:00:00"

BOARD_REL = "00_协作规程与接口契约.txt"
SHA_SENTENCE = "成品 sha256_12=2b18ff3e0629 前后一致"
GOOD = "正文一行，取数时刻 2026-10-25 21:00:00，" + SHA_SENTENCE


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._add(BOARD_REL, GOOD)

    def _add(self, rel, text, pure_crlf=True, mtime=None):
        """写一件假板根文件。pure_crlf=True 把正文折成纯 CRLF；False 则照原样落盘（造歪件用）。"""
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        body = text.encode("utf-8")
        if pure_crlf:
            norm = body.replace(CRLF, LF).replace(CR, LF)
            parts = norm.split(LF)
            if parts and parts[-1] == b"":
                parts = parts[:-1]
            body = CRLF.join(parts) + CRLF
        p.write_bytes(body)
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    def _rel(self, p):
        return p.relative_to(self.root).as_posix()

    def _run(self, cutoff=ALWAYS, known_debt=None):
        return CP.run(self.root, cutoff=cutoff, known_debt=known_debt)


class TestRealDefects(_Fixture):
    """25 日我真栽过的三类形状，每一种都要当场红。"""

    def test_bare_lf_is_red(self):
        self._add("留底_20261025_x/读数.txt", "甲" + LF_CH + "乙" + LF_CH, pure_crlf=False)
        v, _d, _n, st = self._run()
        self.assertEqual(len(v), 1, v)
        self.assertIn("[H1]", v[0])
        self.assertIn("H1-lf", v[0])
        self.assertEqual(st["checked"], 2)

    def test_double_cr_lineend_is_red(self):
        p = self._add("留底_20261025_x/读数.txt", GOOD, pure_crlf=False)
        p.write_bytes("甲".encode("utf-8") + CR + CR + LF + GOOD.encode("utf-8") + CRLF)
        v, _d, _n, _st = self._run()
        self.assertEqual(len(v), 1, v)
        self.assertIn("双写换行=1", v[0])

    def test_bare_cr_is_red(self):
        p = self._add("留底_20261025_x/读数.txt", GOOD, pure_crlf=False)
        p.write_bytes("甲".encode("utf-8") + CR + GOOD.encode("utf-8") + CRLF)
        v, _d, _n, _st = self._run()
        self.assertEqual(len(v), 1, v)
        self.assertIn("H1-cr", v[0])

    def test_tab_is_red(self):
        self._add("留底_20261025_x/读数.txt", "路径 C:" + TAB_CH + "tmp 被转义了")
        v, _d, _n, _st = self._run()
        self.assertEqual(len(v), 1, v)
        self.assertIn("[H2]", v[0])
        self.assertIn("制表符", v[0])

    def test_concat_residue_is_red(self):
        self._add("留底_20261025_x/读数.txt", '开头 " + B + " 结尾')
        v, _d, _n, _st = self._run()
        self.assertEqual(len(v), 1, v)
        self.assertIn("拼接残渣", v[0])

    def test_sha_without_moment_is_red(self):
        self._add("留底_20261025_x/读数.txt", SHA_SENTENCE)
        v, _d, _n, _st = self._run()
        self.assertEqual(len(v), 1, v)
        self.assertIn("[H3]", v[0])

    def test_sha_with_moment_passes(self):
        self._add("留底_20261025_x/读数.txt", GOOD)
        v, _d, _n, _st = self._run()
        self.assertEqual(v, [], v)

    def test_non_hex_run_does_not_trigger_h3(self):
        """判据别宽到把中文串或含 y/z 的编号也算成摘要——那是假红，会让我下次直接跳过这句绿。"""
        self._add("留底_20261025_x/读数.txt", "编号 zz11yy22xx33 与时间无关，也没提时刻")
        v, _d, _n, _st = self._run()
        self.assertEqual(v, [], v)


class TestScopeDecisions(_Fixture):
    """豁免与划界本身就是判据：漏判和过判都要被钉住。"""

    def test_master_archive_is_exempt_from_line_endings(self):
        self._add("留底_20261025_x/母本_pre_mirror_读数.txt", "甲" + LF_CH + "乙" + LF_CH, pure_crlf=False)
        v, _d, _n, _st = self._run()
        self.assertEqual(v, [], v)

    def test_py_archives_are_not_scanned_at_all(self):
        self._add("留底_20261025_x/探针.py", "x = 1" + LF_CH, pure_crlf=False)
        v, _d, _n, st = self._run()
        self.assertEqual(st["files"], 1, "只该扫到黑板那件")
        self.assertEqual(v, [], v)

    def test_letters_are_scanned_at_root_and_inside_archives_only(self):
        self._add("给DeepSeek-新信.txt", "甲" + LF_CH, pure_crlf=False)
        self._add("留底_20261025_x/给DeepSeek-旧信.txt", "甲" + LF_CH, pure_crlf=False)
        self._add("别处/给DeepSeek-夹在中间的信.txt", "甲" + LF_CH, pure_crlf=False)
        v, _d, _n, st = self._run()
        self.assertEqual(st["files"], 3, "板 + 板根信 + 留底里的信；「别处/」不在口径内")
        self.assertEqual(len(v), 2, v)
        for line in v:
            self.assertNotIn("别处", line, line)

    def test_before_cutoff_is_frozen_not_checked(self):
        self._add("留底_20261025_x/读数.txt", "甲" + LF_CH, pure_crlf=False, mtime=OLD_MTIME)
        v, _d, _n, st = self._run(cutoff=LATE_CUTOFF)
        self.assertEqual(v, [], "上线线之前的历史件按令不回头改，不该报红")
        self.assertEqual(st["frozen"], 1)
        self.assertEqual(st["checked"], 1, "只剩黑板")

    def test_board_copies_are_checked_regardless_of_cutoff(self):
        """黑板是活面，不受上线线保护：否则明天改歪板子就查不着了。"""
        self._add(BOARD_REL, "甲" + LF_CH, pure_crlf=False, mtime=OLD_MTIME)
        v, _d, _n, st = self._run(cutoff=LATE_CUTOFF)
        self.assertEqual(len(v), 1, v)
        self.assertIn(BOARD_REL, v[0])
        self.assertEqual(st["checked"], 1)

    def test_other_ai_log_is_notice_not_violation(self):
        self._add("日志/2026-10-25-DeepSeek.txt", "甲" + LF_CH, pure_crlf=False)
        v, _d, notice, st = self._run()
        self.assertEqual(v, [], v)
        self.assertEqual(len(notice), 1, notice)
        self.assertIn("对方件", notice[0])
        self.assertEqual(st["checked"], 1)

    def test_my_own_log_still_gets_checked(self):
        self._add("日志/2026-10-25-Qoder.txt", "甲" + LF_CH, pure_crlf=False)
        v, _d, notice, _st = self._run()
        self.assertEqual(len(v), 1, v)
        self.assertEqual(notice, [], notice)


class TestWholeFaceFailure(unittest.TestCase):
    """H4：只测「该拒的」盖不住「整面不跑」。静默的绿与亲手关掉闸门都得红。"""

    def test_missing_root_is_red(self):
        root = Path(tempfile.mkdtemp())
        v, _d, _n, st = CP.run(root / "并不存在")
        self.assertEqual(len(v), 1, v)
        self.assertIn("[H4]", v[0])
        self.assertEqual(st["files"], 0)

    def test_no_txt_at_all_is_red(self):
        root = Path(tempfile.mkdtemp())
        (root / "readme.md").write_bytes(b"x")
        v, _d, _n, _st = CP.run(root)
        self.assertIn("[H4]", v[0])

    def test_everything_frozen_is_red(self):
        """板上没有活件、历史面又整个被划走，则比对面塌成零件。这不叫通过。"""
        root = Path(tempfile.mkdtemp())
        p = root / "留底_20261025_x" / "读数.txt"
        p.parent.mkdir(parents=True)
        p.write_bytes(GOOD.encode("utf-8") + CRLF)
        os.utime(p, (OLD_MTIME, OLD_MTIME))
        v, _d, _n, st = CP.run(root, cutoff=LATE_CUTOFF)
        self.assertEqual(st["checked"], 0, st)
        self.assertEqual(len(v), 1, v)
        self.assertIn("[H4]", v[0])
        self.assertIn("比对面塌成零件", v[0])

    def test_missing_archive_dirs_on_clean_tree_are_skipped(self):
        """干净 clone 没有本机归档片，不该替投递机背 H4。"""
        root = Path(tempfile.mkdtemp())
        (root / "readme.txt").write_bytes(GOOD.encode("utf-8") + CRLF)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PAI_PAPERWORK_REQUIRE_ARCHIVES", None)
            v, _d, _n, st = CP.run(root, cutoff=ALWAYS)
        self.assertFalse([x for x in v if "归档登记" in x], v)
        self.assertEqual(st["arch_skipped"], 2)


class TestWhitelistTeeth(_Fixture):
    """白名单三刀：形状一致只登记、变脏要红、销账要签字。少一刀它就退化成自我豁免。"""

    def test_matching_shape_is_listed_not_violated(self):
        p = self._add("留底_20261025_x/读数.txt", "甲" + LF_CH, pure_crlf=False)
        debt = {self._rel(p): (("H1-lf",), "Boss 令这件不动，黑板 P60")}
        v, d, _n, st = self._run(known_debt=debt)
        self.assertEqual(v, [], v)
        self.assertEqual(len(d), 1, d)
        self.assertIn("欠账", d[0])
        self.assertEqual((st["debt_hit"], st["debt_total"]), (1, 1))

    def test_new_dirt_on_whitelisted_file_still_red(self):
        p = self._add("留底_20261025_x/读数.txt", "甲" + LF_CH, pure_crlf=False)
        debt = {self._rel(p): (("H1-lf",), "旧形状冻结")}
        p.write_bytes(("丙" + TAB_CH + "丁" + LF_CH).encode("utf-8"))
        v, d, _n, _st = self._run(known_debt=debt)
        self.assertEqual(len(v), 1, v)
        self.assertIn("[H5]", v[0])
        self.assertIn("H2-tab", v[0])
        self.assertEqual(len(d), 1, "旧账仍要念出来，不能因为变脏就整个不念")

    def test_silent_settlement_is_red(self):
        p = self._add("留底_20261025_x/读数.txt", GOOD)
        debt = {self._rel(p): (("H1-lf",), "本该是裸 LF 的旧件")}
        v, d, _n, _st = self._run(known_debt=debt)
        self.assertEqual(len(v), 1, v)
        self.assertIn("[H0]", v[0])
        self.assertIn("签字", v[0])
        self.assertEqual(d, [], "账已销掉时不许再念成欠账还在")

    def test_debt_line_carries_reason_and_shape(self):
        p = self._add("留底_20261025_x/读数.txt", "甲 " + SHA_SENTENCE + LF_CH, pure_crlf=False)
        debt = {self._rel(p): (("H1-lf", "H3-sha-no-moment"), "为什么不动：等一句令")}
        v, d, _n, _st = self._run(known_debt=debt)
        self.assertEqual(v, [], v)
        self.assertIn("等一句令", d[0])
        self.assertIn("H3-sha-no-moment", d[0])


class TestCleanTreeAndExitCode(_Fixture):
    def test_clean_tree_zero_violations(self):
        self._add("留底_20261025_x/读数.txt", GOOD)
        self._add("日志/2026-10-25-Qoder.txt", GOOD)
        self._add("personal-ai-system/" + BOARD_REL, GOOD)
        v, d, n, st = self._run()
        self.assertEqual((v, d, n), ([], [], []))
        self.assertEqual((st["files"], st["checked"]), (4, 4))

    def test_main_exit_codes(self):
        bad = self._add("留底_20261025_x/读数.txt", GOOD, pure_crlf=False)
        bad.write_bytes(("甲" + LF_CH).encode("utf-8"))
        self.assertEqual(CP.main(["--root", str(self.root), "--cutoff", ALWAYS]), 1)
        bad.write_bytes((GOOD + CRLF_CH).encode("utf-8"))
        self.assertEqual(CP.main(["--root", str(self.root), "--cutoff", ALWAYS]), 0)

    def test_green_sentence_carries_both_faces(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = CP.main(["--root", str(self.root), "--cutoff", LATE_CUTOFF])
        self.assertEqual(rc, 0)
        text = buf.getvalue()
        self.assertIn("比对面", text)
        self.assertIn("冻结", text)
        self.assertIn("没查", text, "冻结面必须明说「没查」，不能念成查过且干净")

    def test_known_debt_entries_all_carry_shape_and_reason(self):
        """真那张白名单也要过这一关：空理由＝自我豁免。"""
        for rel, (shape, why) in CP.KNOWN_DEBT.items():
            self.assertTrue(why.strip(), rel + " 白名单每条必须写为什么不动")
            self.assertTrue(shape, rel + " 白名单每条必须登记形状指纹")
            for token in shape:
                self.assertIn(token, CP.SHAPES, rel + " 用了没登记的指纹：" + token)

class TestMasterExemptEntirely(unittest.TestCase):
    """母本_ 是原字节留底：按令不许改，整件不进判据；同样字节换个名字必须照样红。"""

    DIRTY = ("正文一行 sha256_12=2b18ff3e0629 前后一致" + LF_CH + "第二行" + CR_CH
             + "拼接残渣 " + TAB_CH + "收尾").encode("utf-8")

    def test_master_exempt(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "母本_改前_x.txt"
            p.write_bytes(self.DIRTY)
            self.assertEqual(CP.check_one("母本_改前_x.txt", p), [])

    def test_same_bytes_other_name_is_red(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "读数_改前_x.txt"
            p.write_bytes(self.DIRTY)
            sigs = {s for sig, _m in CP.check_one("读数_改前_x.txt", p) for s in sig}
            for want in ("H1-lf", "H1-cr", "H2-tab", "H3-sha-no-moment"):
                self.assertIn(want, sigs)

if __name__ == "__main__":
    unittest.main()
