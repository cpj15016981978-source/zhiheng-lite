"""投递纪律自检器自己的测试。

自检器一旦失真就比没有更糟（它会让我以为镜像是干净的），所以每条规则都要能【真的失败】：
先造一个故意违规的临时目录，确认它报错；再确认干净目录 0 违规。
全程只用 tempfile 造的假工作区/假投递区，绝不碰真实 D:\\Q。

运行： python -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import check_relay as CR


class _Fixture(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.ws = root / "personal-ai-system"
        self.mirror = root / "Q" / "personal-ai-system"
        self.bb = root / "00_协作规程与接口契约.txt"
        self.ws.mkdir(parents=True)
        self.mirror.mkdir(parents=True)
        self._write(self.ws / "chat.py", "print(1)\n")
        self._write(self.ws / "pai" / "memory.py", "x = 1\n")
        self._mirror_copy_all()
        self._write(self.bb, "      M5  情感层  [开放]\n")

    def _write(self, p, text):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def _mirror_copy_all(self):
        for rel, path in CR._walk_files(self.ws).items():
            self._write(self.mirror / rel, path.read_text(encoding="utf-8"))

    def _violations(self):
        return CR.run(self.ws, self.mirror, self.bb)


class TestCleanPasses(_Fixture):

    def test_clean_mirror_has_no_violations(self):
        self.assertEqual(self._violations(), [], "干净状态必须 0 违规，否则自检器会制造假警报")

    def test_quarantine_and_caches_are_ignored(self):
        """隔离区/缓存/隐私目录本就该只出现在某一侧，不该被当成不一致。"""
        self._write(self.mirror / "外部AI投递_待评审_Phase4" / "平行council投递" / "council.py",
                    "junk\n")
        self._write(self.ws / "pai" / "__pycache__" / "memory.cpython-313.pyc", "junk\n")
        self._write(self.ws / "data" / "memory.db", "private\n")
        self._write(self.mirror / "_backup_20261020_2133" / "old.py", "junk\n")
        self.assertEqual(self._violations(), [])


class TestMirrorEquality(_Fixture):

    def test_missing_file_in_mirror_is_reported(self):
        (self.mirror / "pai" / "memory.py").unlink()
        v = self._violations()
        self.assertTrue(any("缺文件" in s and "pai/memory.py" in s for s in v), v)

    def test_extra_file_in_mirror_is_reported(self):
        self._write(self.mirror / "pai" / "council.py", "parallel spine\n")
        v = self._violations()
        self.assertTrue(any("多出文件" in s and "pai/council.py" in s for s in v), v)

    def test_content_drift_is_reported(self):
        self._write(self.ws / "chat.py", "print(2)\n")
        v = self._violations()
        self.assertTrue(any("内容不一致" in s and "chat.py" in s for s in v), v)


class TestPrivateData(_Fixture):

    def test_runtime_db_in_drop_box_is_blocked(self):
        """正是 ADR-024 那次事故的形状：有人把 chat.py 跑在投递目录里。"""
        self._write(self.mirror / "data" / "memory.db", "\x00\x01sqlite\n")
        v = self._violations()
        self.assertTrue(any("[R2]" in s and "memory.db" in s for s in v), v)

    def test_empty_data_dir_alone_is_not_a_leak(self):
        (self.mirror / "data").mkdir()
        self.assertEqual([s for s in self._violations() if "[R2]" in s], [])


class TestStaleIsNotALease(_Fixture):
    """钉死一个我踩过的设计错误：正常发布=工作区→投递区复制，镜像 mtime 必然更新。
    若把 mtime 当违规，每次发布都会 self-flag，自检器就变成假警报制造机。"""

    def _touch_mirror_newer(self):
        st = (self.ws / "chat.py").stat()
        os.utime(self.mirror / "chat.py", (st.st_atime, st.st_mtime + 600))

    def test_newer_but_identical_mirror_is_not_a_violation(self):
        self._touch_mirror_newer()
        self.assertEqual(self._violations(), [], "内容一致的发布不得报成入侵")

    def test_newer_file_shows_up_as_a_lead_not_a_violation(self):
        self._touch_mirror_newer()
        notes = CR.note_recent_touches(self.ws, self.mirror)
        self.assertTrue(any("chat.py" in n for n in notes), notes)
        self.assertEqual([s for s in self._violations() if "chat.py" in s], [])

    def test_newer_AND_different_content_is_caught_by_r1(self):
        """真被篡改时，靠内容就能抓到——不需要也不该靠 mtime。"""
        self._write(self.mirror / "chat.py", "print(999)\n")
        st = (self.ws / "chat.py").stat()
        os.utime(self.mirror / "chat.py", (st.st_atime, st.st_mtime + 600))
        self.assertTrue(any("内容不一致" in s for s in self._violations()))


class TestClaimLocks(_Fixture):

    def test_two_owners_on_one_line_is_a_collision(self):
        self._write(self.bb,
                    "      M5  情感层  [锁:守门人]  [锁:DeepSeek]  ← 撞车\n")
        v = CR.check_claim_locks(self.bb)
        self.assertTrue(any("[R4]" in s and "撞车" in s for s in v), v)

    def test_same_owner_repeated_is_fine(self):
        self._write(self.bb, "      M2  记忆库  [锁:守门人]（Phase5 进行中）[锁:守门人]\n")
        self.assertEqual(CR.check_claim_locks(self.bb), [])

    def test_blackboard_missing_is_reported_not_crash(self):
        v = CR.check_claim_locks(Path(tempfile.mkdtemp()) / "nope.txt")
        self.assertTrue(any("[R4]" in s for s in v))


class TestLockOwnership(_Fixture):
    """R4 加强版（Boss 2026-11-06 点头：「锁主才能改自己那行的锁」）。

    这几条测的是【闸门有没有把判据接上并跑到总判定与那句绿话里】；
    哪些行算锁行、锁主名怎么认、缩进多深算明细、[已并入/[结案/[退回/[已关闭 算不算活动，
    判据只住 agent_layer/agentd.py（ADR-083）——这里不抄第二份，抄了就会长回板上 P44 那笔「两把尺不同面」。
    """

    def _pair(self, before_text, after_text, actor):
        bef = self.bb.parent / "黑板_上一版.txt"
        bef.write_text(before_text, encoding="utf-8")
        self._write(self.bb, after_text)
        return CR.check_lock_ownership(self.bb, bef, actor)

    def test_non_owner_editing_others_lock_line_is_red(self):
        v, note = self._pair("      M5  情感层  [锁:Qoder]\n",
                             "      M5  情感层  [锁:DeepSeek]\n", "DeepSeek")
        self.assertEqual(len(v), 1, v)
        self.assertIn("[R4]", v[0])
        self.assertIn("非锁主", v[0], v[0])
        self.assertIn("R4 加强版跑过", note)

    def test_owner_may_release_own_lock_line(self):
        v, _note = self._pair("      M5  情感层  [锁:Qoder]\n",
                              "      M5  情感层  [开放]\n", "Qoder")
        self.assertEqual(v, [], "锁主动自己那行不该红，否则这一刀会拦掉正常的结案")

    def test_ripping_others_lock_is_red_and_says_so_empty(self):
        v, _note = self._pair("      M5  情感层  [锁:DeepSeek]\n",
                              "      M5  情感层  [开放]\n", "Qoder")
        self.assertEqual(len(v), 1, v)
        self.assertIn("【撤空】", v[0], v[0])

    def test_first_lock_on_an_unowned_line_is_not_judged(self):
        """上一版这行没锁 → 不判。否则「把活派给别人」「新格第一次挂锁」这两件日常动作会被自己拦死。"""
        v, note = self._pair("      M9  新格  [开放]\n",
                             "      M9  新格  [锁:DeepSeek]\n", "Qoder")
        self.assertEqual(v, [], v)
        self.assertIn("R4 加强版跑过", note)

    def test_missing_actor_or_before_says_not_run(self):
        for actor, before in ((None, "x"), ("Qoder", None), (None, None)):
            v, note = CR.check_lock_ownership(self.bb, before, actor)
            self.assertEqual(v, [], (actor, before))
            self.assertIn("【没跑】", note, (actor, before, note))

    def test_unreadable_board_is_red_not_idle_green(self):
        """给了参数却取不到黑板 → 必须响一声「空转」，不许把这一刀念成通过。"""
        missing = self.bb.parent / "根本没有这块板.txt"
        v, note = CR.check_lock_ownership(missing, missing, "Qoder")
        self.assertTrue(any("[R4]" in s and "空转" in s for s in v), v)
        self.assertIn("空转", note)

    def test_knife_is_wired_into_the_gate(self):
        """单元里红不算数：这一刀必须进 run() 的总违规，否则入口被拔了测试还全绿。"""
        bef = self.bb.parent / "黑板_上一版.txt"
        bef.write_text("      M5  情感层  [锁:Qoder]\n", encoding="utf-8")
        self._write(self.bb, "      M5  情感层  [锁:DeepSeek]\n")
        self.assertEqual(CR.run(self.ws, self.mirror, self.bb), [],
                         "不带 actor／上一版时老调用点行为不变")
        v = CR.run(self.ws, self.mirror, self.bb, None, "DeepSeek", bef)
        self.assertTrue(any("非锁主" in s for s in v), v)

    def _main_out(self, extra=()):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = CR.main(["--ws", str(self.ws), "--mirror", str(self.mirror),
                          "--blackboard", str(self.bb)] + list(extra))
        return rc, buf.getvalue()

    def test_green_line_reads_out_that_this_knife_did_not_run(self):
        rc, out = self._main_out()
        self.assertEqual(rc, 0, out)
        self.assertIn("R4 加强版【没跑】", out)

    def test_verdict_line_reads_out_the_caliber_when_it_ran(self):
        bef = self.bb.parent / "黑板_上一版.txt"
        bef.write_text("      M5  情感层  [锁:Qoder]\n", encoding="utf-8")
        self._write(self.bb, "      M5  情感层  [锁:Qoder]（Phase6 进行中）\n")
        rc, out = self._main_out(["--as", "Qoder", "--bb-before", str(bef)])
        self.assertEqual(rc, 0, out)
        self.assertIn("R4 加强版跑过", out)
        self._write(self.bb, "      M5  情感层  [锁:DeepSeek]\n")
        rc2, out2 = self._main_out(["--as", "DeepSeek", "--bb-before", str(bef)])
        self.assertEqual(rc2, 1, out2)
        self.assertIn("非锁主", out2)


class TestExcludedSurface(_Fixture):
    """R5：P31 实测过「往镜像 __pycache__ 塞一件东西，check_relay rc=0、一个字不报」。
    这几条守卫就是要把那句话变成会红的判定。全程只动临时副本，不碰真实 D:\\Q。"""

    def _plant(self, rel, raw):
        p = self.mirror / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        return p

    def test_clean_tree_reports_no_r5(self):
        """没有缓存件的临时树必须 0 违规，否则这条守卫自己就是假警报源。"""
        self.assertEqual(CR.check_excluded_surface(self.mirror), [])

    def test_source_file_planted_in_pycache_is_caught(self):
        """P31 那一发：往不参与比对的缓存目录里塞 .py，旧版静默通过。"""
        self._plant("pai/__pycache__/council.py", b"parallel spine\n")
        v = CR.check_excluded_surface(self.mirror)
        self.assertTrue(any("[R5]" in s and "council.py" in s for s in v), v)

    def test_sqlite_body_inside_a_pyc_is_caught(self):
        """后缀写成 .pyc 也不放过：按库体头部判，且这条与 R2 共用同一份隐私判据。"""
        self._plant("pai/__pycache__/memory.cpython-313.pyc",
                    b"SQLite format 3\x00" + b"\x1f" * 64)
        v = CR.check_excluded_surface(self.mirror)
        hits = [s for s in v if "[R5]" in s and "SQLite" in s]
        self.assertEqual(len(hits), 1, v)

    def test_pyc_without_source_is_caught(self):
        self._plant("pai/__pycache__/ghost.cpython-313.pyc", b"\x00\x0b\r\njunk")
        v = CR.check_excluded_surface(self.mirror)
        self.assertTrue(any("查无源件" in s and "ghost" in s for s in v), v)

    def test_bare_pyc_outside_pycache_is_caught(self):
        """不在 __pycache__ 里的 .pyc 可以被直接 import，而 R1 按名字整块排除它。"""
        self._plant("council.pyc", b"\x00\x0b\r\njunk")
        v = CR.check_excluded_surface(self.mirror)
        self.assertTrue(any("裸字节码" in s and s.endswith("council.pyc") for s in v), v)

    def test_unreadable_file_is_reported_not_skipped(self):
        """读不了的那一支不能静默放过（旧版整个排除面就是被静默放过的）。"""
        p = self._plant("pai/__pycache__/memory.cpython-313.pyc", b"ok")
        real = CR._read

        def boom(q):
            if q == p:
                raise OSError("locked")
            return real(q)

        CR._read = boom
        try:
            v = CR.check_excluded_surface(self.mirror)
        finally:
            CR._read = real
        self.assertTrue(any("读不了" in s and "memory.cpython-313.pyc" in s for s in v), v)

    def test_oversized_file_is_flagged_for_human_eyes(self):
        self._plant("pai/__pycache__/memory.cpython-313.pyc", b"x" * 50)
        old = CR._R5_MAX_BYTES
        CR._R5_MAX_BYTES = 8
        try:
            v = CR.check_excluded_surface(self.mirror)
        finally:
            CR._R5_MAX_BYTES = old
        self.assertTrue(any("超过扫描上限" in s for s in v), v)

    def test_data_and_backups_are_left_to_r2(self):
        """隐私目录由 R2 报，R5 不重复报第二遍（同一份判据不许有第二个声音）。"""
        self._plant("data/__pycache__/memory.cpython-313.pyc",
                    b"SQLite format 3\x00" + b"\x1f" * 64)
        self.assertEqual(CR.check_excluded_surface(self.mirror), [])

    def test_r5_reaches_the_final_verdict(self):
        """守卫要接进 run()，否则单元测试绿、闸门照旧 rc=0。"""
        self._plant("pai/__pycache__/council.py", b"parallel spine\n")
        v = self._violations()
        self.assertTrue(any("[R5]" in s for s in v), v)


class TestPycSourceDerivation(unittest.TestCase):
    """这一层我实测错过两次（先没剥 tag、后没退回上一层），把 75 件全报成孤儿。
    判据错了比没有判据更糟——所以单独钉住推导规则。"""

    def test_plain_and_tagged_names(self):
        self.assertEqual(CR._pyc_source_rel("pai/__pycache__/memory.cpython-313.pyc"),
                         "pai/memory.py")
        self.assertEqual(CR._pyc_source_rel("__pycache__/chat.cpython-313.pyc"), "chat.py")
        self.assertEqual(
            CR._pyc_source_rel("tests/__pycache__/test_x.cpython-313-pytest-8.3.2.pyc"),
            "tests/test_x.py")
        self.assertEqual(CR._pyc_source_rel("pai/__pycache__/__init__.cpython-313.pyc"),
                         "pai/__init__.py")

    def test_pyc_not_in_a_cache_dir_has_no_expected_source(self):
        self.assertIsNone(CR._pyc_source_rel("council.pyc"))


class TestGreenLineStatesCoverage(_Fixture):
    """那句绿话不许再念成「整棵树一致」——这是 P31 的 A 条收口，用输出文本钉住。"""

    def _stdout_of_main(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = CR.main(["--ws", str(self.ws), "--mirror", str(self.mirror),
                          "--blackboard", str(self.bb)])
        return rc, buf.getvalue()

    def test_green_line_carries_both_counts_and_the_exclusion_warning(self):
        self.assertEqual(self._violations(), [])
        rc, out = self._stdout_of_main()
        self.assertEqual(rc, 0, out)
        verdict = [s for s in out.splitlines() if s.startswith("通过")]
        self.assertEqual(len(verdict), 1, out)
        self.assertIn("比对面", verdict[0])
        self.assertIn("排除面", verdict[0])
        self.assertIn("不参与逐字节比对", out)

    def test_pass_word_is_not_printed_alone(self):
        """不许出现一句不带口径的「通过：镜像==工作区逐字节一致」（旧版那句就是）。"""
        rc, out = self._stdout_of_main()
        self.assertNotIn("通过：镜像==工作区逐字节一致", out)


class TestPlaintextCredentials(_Fixture):
    """R6：把 P31 那半格「密钥塞进 pyc 里 rc=0、一个字不报」变成会红的判定。

    判据=【凭据类环境变量的真实值逐字节比】，不是形状扫法（sk- 那类对我们自己
    六件测试字节码必假红）。这里全程只用自己造的假值，绝不碰真凭据，也不把值抄进报告。
    """

    FAKE_NAME = "PAI_R6_FAKE_TOKEN"
    FAKE_VALUE = "sk-r6-fake-value-0123456789abcdef"

    def setUp(self):
        super().setUp()
        self.samples = [(self.FAKE_NAME, self.FAKE_VALUE.encode("utf-8"))]

    def _plant(self, rel, raw):
        p = self.mirror / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        return p

    def _plant_board(self, rel, raw):
        """种在【板根】里、镜像之外：日志/信件/留底/黑板副本那一层（Boss 2026-10-28 令扩的面）。"""
        p = self.mirror.parent / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        return p

    def test_clean_tree_reports_no_r6(self):
        self.assertEqual(CR.check_plaintext_credentials(self.mirror, self.samples), [])

    def test_name_judgment_has_exactly_one_address(self):
        """名字判据必须就是 check_key.SECRET_NAME_RE——两份名单必然漂移，这是本项目记过的账。"""
        from tools import check_key as CK
        self.assertIs(CR.CRED_NAME_RE, CK.SECRET_NAME_RE)

    def test_value_on_compared_face_is_caught(self):
        self._plant("pai/council.py", ("x = %s\n" % self.FAKE_VALUE).encode("utf-8"))
        hits = [s for s in CR.check_plaintext_credentials(self.mirror, self.samples)
                if "[R6]" in s]
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("pai/council.py", hits[0])
        self.assertIn(self.FAKE_NAME, hits[0])

    def test_value_inside_a_pyc_in_the_cache_dir_is_caught(self):
        """正是 P31 那一发：源件在位（R5 不响），值只躺在不参与逐字节比对的字节码里。"""
        self._plant("pai/__pycache__/memory.cpython-313.pyc",
                    b"\x00\x0b\r\n" + self.FAKE_VALUE.encode("utf-8") + b"\x1f" * 32)
        v = CR.check_plaintext_credentials(self.mirror, self.samples)
        self.assertTrue(any("[R6]" in s and "memory.cpython-313.pyc" in s for s in v), v)
        self.assertEqual(CR.check_excluded_surface(self.mirror), [], "这条必须由 R6 报，不是 R5")

    def test_report_never_carries_the_value(self):
        self._plant("pai/council.py", self.FAKE_VALUE.encode("utf-8"))
        joined = " ".join(CR.check_plaintext_credentials(self.mirror, self.samples))
        self.assertIn("[R6]", joined)
        self.assertNotIn(self.FAKE_VALUE, joined)
        self.assertNotIn("fake-value", joined)

    def test_two_hits_are_two_lines_not_one_summary(self):
        self._plant("pai/a.py", self.FAKE_VALUE.encode("utf-8"))
        self._plant("pai/__pycache__/memory.cpython-313.pyc", b"x" + self.FAKE_VALUE.encode())
        hits = [s for s in CR.check_plaintext_credentials(self.mirror, self.samples)
                if "[R6]" in s]
        self.assertEqual(len(hits), 2, hits)

    def test_switch_shaped_values_are_not_samples(self):
        """开关值（off / 1 / 一段路径）名字再像凭据也不能进底样，否则闸门天天假红。"""
        env = {"PAI_MODEL_API_KEY": "off", "MY_TOKEN": "1", "SOME_SECRET": "D:/Q/x",
               self.FAKE_NAME: self.FAKE_VALUE}
        got = dict(CR.credential_samples(env))
        self.assertEqual(list(got), [self.FAKE_NAME], got)
        self.assertEqual(got[self.FAKE_NAME].decode(), self.FAKE_VALUE)

    def test_persisted_layer_counts_not_only_process_env(self):
        """ADR-038 那一课：setx 写进注册表的那份，进程环境里看不见，但它才是真在外面的值。"""
        self.assertEqual(CR.credential_samples({}, {self.FAKE_NAME: self.FAKE_VALUE}),
                         [(self.FAKE_NAME, self.FAKE_VALUE.encode("utf-8"))])
        self.assertEqual(CR.credential_samples({self.FAKE_NAME: self.FAKE_VALUE}, {}),
                         [(self.FAKE_NAME, self.FAKE_VALUE.encode("utf-8"))])

    def test_blind_persisted_layer_rings_instead_of_passing(self):
        """持久层读不到时必须响：静默绿一次，就等于替这台撒了个「凭据面干净」的谎。"""
        class _Blind:
            SECRET_NAME_RE = CR.CRED_NAME_RE

            @staticmethod
            def _read_persisted_env():
                return {}, "读不到：用户级（PermissionError）"

        real = CR._CK
        CR._CK = _Blind
        try:
            v = CR.check_plaintext_credentials(self.mirror)
        finally:
            CR._CK = real
        self.assertTrue(any("[R6]" in s and "没扫成" in s for s in v), v)

    def test_missing_judge_address_rings_not_passes(self):
        real_ck, real_note = CR._CK, CR._CK_IMPORT_NOTE
        CR._CK, CR._CK_IMPORT_NOTE = None, "取不到判据住址 tools/check_key.py"
        try:
            v = CR.check_plaintext_credentials(self.mirror)
        finally:
            CR._CK, CR._CK_IMPORT_NOTE = real_ck, real_note
        self.assertTrue(any("[R6]" in s and "空转" in s for s in v), v)

    def test_privacy_face_is_skipped_by_name_and_never_read(self):
        """data/ 与库体按名字先跳过：这一条一次都不许打开记忆库，泄漏由 R2 报。"""
        seen = []
        real = CR._read

        def spy(q):
            seen.append(str(q).lower())
            return real(q)

        self._plant("data/notes.txt", self.FAKE_VALUE.encode("utf-8"))
        self._plant("pai/secret.db", self.FAKE_VALUE.encode("utf-8"))
        CR._read = spy
        try:
            v = CR.check_plaintext_credentials(self.mirror, self.samples)
        finally:
            CR._read = real
        self.assertEqual([s for s in v if "[R6]" in s], [], v)
        self.assertFalse([s for s in seen if "secret.db" in s or "\\data\\" in s], seen)

    def test_unreadable_file_is_reported_not_skipped(self):
        p = self._plant("pai/council.py", b"ok")
        real = CR._read

        def boom(q):
            if q == p:
                raise OSError("locked")
            return real(q)

        CR._read = boom
        try:
            v = CR.check_plaintext_credentials(self.mirror, self.samples)
        finally:
            CR._read = real
        self.assertTrue(any("读不了" in s and "council.py" in s for s in v), v)

    def test_oversized_file_is_flagged_for_human_eyes(self):
        self._plant("pai/council.py", b"x" * 50)
        old = CR._R6_MAX_BYTES
        CR._R6_MAX_BYTES = 8
        try:
            v = CR.check_plaintext_credentials(self.mirror, self.samples)
        finally:
            CR._R6_MAX_BYTES = old
        self.assertTrue(any("超过扫描上限" in s for s in v), v)

    def test_r6_reaches_the_final_verdict(self):
        """守卫必须接进 run()：否则单元测试绿、闸门照旧 rc=0（R5 当年就栽过一次）。"""
        old = os.environ.get(self.FAKE_NAME)
        os.environ[self.FAKE_NAME] = self.FAKE_VALUE
        try:
            self._plant("pai/council.py", self.FAKE_VALUE.encode("utf-8"))
            v = self._violations()
            self.assertTrue(any("[R6]" in s and "pai/council.py" in s for s in v), v)
        finally:
            if old is None:
                del os.environ[self.FAKE_NAME]
            else:
                os.environ[self.FAKE_NAME] = old

    def test_green_line_states_r6_coverage(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = CR.main(["--ws", str(self.ws), "--mirror", str(self.mirror),
                          "--blackboard", str(self.bb)])
        out = buf.getvalue()
        self.assertEqual(rc, 0, out)
        self.assertIn("R6", out)
        self.assertIn("凭据底样", out)
        self.assertNotIn(self.FAKE_VALUE, out)

    # -------------------------------------------------- 板根扩面（Boss 2026-10-28 令）

    def test_board_value_outside_the_mirror_is_caught(self):
        """明文躺在镜像之外、板根之内（信件/日志/留底/黑板副本那一层）：旧 R6 一个字不报，现在必须响。"""
        self._plant_board("日志/2026-10-28.txt",
                          ("顺手记一下：" + self.FAKE_VALUE + "\n").encode("utf-8"))
        hits = [s for s in CR.check_plaintext_credentials(
            self.mirror, self.samples, self.mirror.parent) if "[R6]" in s]
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("日志/2026-10-28.txt", hits[0])
        self.assertIn(self.FAKE_NAME, hits[0])
        self.assertNotIn(self.FAKE_VALUE, hits[0])

    def test_without_board_root_the_face_stays_mirror_only(self):
        """板根是可选项而不是永久生效：这条同时证明上面那发【是扩面带来的】，不是本来就红的。"""
        self._plant_board("给DeepSeek.txt", self.FAKE_VALUE.encode("utf-8"))
        self.assertEqual(CR.check_plaintext_credentials(self.mirror, self.samples), [])
        hits = [s for s in CR.check_plaintext_credentials(
            self.mirror, self.samples, self.mirror.parent) if "[R6]" in s]
        self.assertEqual(len(hits), 1, hits)

    def test_same_file_is_counted_once_with_the_mirror_label(self):
        """镜像本身住在板根里：同一件被两棵树各列一次，必须按绝对路径只算一次，且沿用老件名。"""
        before = CR.r6_counts(self.mirror)[0]
        self.assertEqual(CR.r6_counts(self.mirror, self.mirror.parent)[0], before,
                         "板根里只有镜像这一棵树时，扩面不该凭空多算一件")
        labels = {rel for rel, _p in CR._r6_faces(self.mirror, self.mirror.parent)[0].values()}
        self.assertIn("pai/memory.py", labels)
        self.assertNotIn("personal-ai-system/pai/memory.py", labels)
        self._plant_board("日志/x.txt", b"hello\n")
        self.assertEqual(CR.r6_counts(self.mirror, self.mirror.parent)[0], before + 1)

    def test_privacy_named_board_dirs_are_skipped_and_never_read(self):
        """扩面不覆盖隐私那一格：板根里的 data/ 与库体仍按名字先跳过、一次都不打开（ADR-024）。"""
        seen = []
        real = CR._read

        def spy(q):
            seen.append(str(q).lower())
            return real(q)

        self._plant_board("data/notes.txt", self.FAKE_VALUE.encode("utf-8"))
        self._plant_board("logs/memory.db", self.FAKE_VALUE.encode("utf-8"))
        CR._read = spy
        try:
            v = CR.check_plaintext_credentials(self.mirror, self.samples, self.mirror.parent)
            _kept, skipped = CR._r6_faces(self.mirror, self.mirror.parent)
        finally:
            CR._read = real
        self.assertEqual([s for s in v if "[R6]" in s], [], v)
        self.assertFalse([s for s in seen if "notes.txt" in s or "memory.db" in s], seen)
        self.assertEqual(skipped, 2, "这两件必须被计入「按名字跳过」，不能当作扫过")

    def test_board_widening_reaches_the_final_verdict(self):
        """扩面必须接进 run()：否则单元测试绿、闸门照旧 rc=0（R5 当年就栽过一次）。"""
        old = os.environ.get(self.FAKE_NAME)
        os.environ[self.FAKE_NAME] = self.FAKE_VALUE
        try:
            self._plant_board("信件/给DeepSeek.txt", self.FAKE_VALUE.encode("utf-8"))
            v = CR.run(self.ws, self.mirror, self.bb, self.mirror.parent)
            self.assertTrue(any("[R6]" in s and "信件/给DeepSeek.txt" in s for s in v), v)
            self.assertEqual(CR.run(self.ws, self.mirror, self.bb), [],
                             "不传板根=旧面，这一句证明上面那发红来自扩面本身")
        finally:
            if old is None:
                del os.environ[self.FAKE_NAME]
            else:
                os.environ[self.FAKE_NAME] = old

    def test_main_defaults_to_the_board_root(self):
        """真跑（双击/命令行）不许靠记得传 --board-root：默认就得把板根并进面。"""
        old = os.environ.get(self.FAKE_NAME)
        os.environ[self.FAKE_NAME] = self.FAKE_VALUE
        try:
            self._plant_board("日志/2026-10-28.txt", self.FAKE_VALUE.encode("utf-8"))
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = CR.main(["--ws", str(self.ws), "--mirror", str(self.mirror),
                              "--blackboard", str(self.bb)])
            out = buf.getvalue()
        finally:
            if old is None:
                del os.environ[self.FAKE_NAME]
            else:
                os.environ[self.FAKE_NAME] = old
        self.assertEqual(rc, 1, out)
        self.assertIn("[R6]", out)
        self.assertIn("日志/2026-10-28.txt", out)
        self.assertNotIn(self.FAKE_VALUE, out)

    def test_green_line_names_the_widened_face(self):
        """那句绿话必须念出「面=投递区两面+板根」和实际用的板根：只报件数会念成还在扫镜像。"""
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = CR.main(["--ws", str(self.ws), "--mirror", str(self.mirror),
                          "--blackboard", str(self.bb)])
        out = buf.getvalue()
        self.assertEqual(rc, 0, out)
        self.assertIn("面=投递区两面+板根", out)
        self.assertIn(str(self.mirror.parent), out)


class TestForwardCoversWorkspace(_Fixture):
    """P119 那一刀：正向那遍必须真扫到发布位（旧版 R5/R6 的入参钉死在 mirror 槽）。

    Boss 2026-10-31 令「闸门那一刀要长进 tools/check_relay.py，让正向真能盖住发布位」。
    这六条钉六件事：① 发布位排除面被 R5 扫到；② 两棵分开各念各行；③ 发布位明文被 R6 扫到
    且值不外抄；④ R2 没被顺手扩过来（那是合法运行态，扩了就天天假红）；
    ⑤ 那句绿话必须念出两棵各自的面；⑥ run() 收 str 路径不再崩（本轮实测踩过）。
    全程只动 tempfile 造的假树，不碰真实 D:\\Q。
    """

    FAKE_NAME = "PAI_P119_FAKE_TOKEN"
    FAKE_VALUE = "sk-p119-fake-drill-value-0123456789"

    def _plant_ws(self, rel, raw):
        p = self.ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        return p

    def _plant(self, rel, raw):
        p = self.mirror / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        return p

    def test_workspace_cache_dir_file_is_caught_forward(self):
        """塞进发布位 __pycache__ 的一件 .py：旧版正向一个字不报（那一面只有反向才看得见）。"""
        self._plant_ws("pai/__pycache__/council.py", b"parallel spine\n")
        v = self._violations()
        hits = [s for s in v if "[R5]" in s and "面=发布位" in s]
        self.assertEqual(len(hits), 1, v)
        self.assertIn("council.py", hits[0])

    def test_the_two_faces_are_reported_apart(self):
        """两棵都响时各念各的行：投递区那一面的行不带『面=』，不许混成一锅。"""
        self._plant_ws("pai/__pycache__/council.py", b"parallel spine\n")
        self._plant("pai/__pycache__/ghost.py", b"junk\n")
        v = self._violations()
        ws_hits = [s for s in v if "[R5]" in s and "面=发布位" in s]
        mir_hits = [s for s in v if "[R5]" in s and "面=" not in s]
        self.assertEqual(len(ws_hits), 1, v)
        self.assertEqual(len(mir_hits), 1, v)
        self.assertIn("council.py", ws_hits[0])
        self.assertIn("ghost.py", mir_hits[0])

    def test_workspace_plaintext_value_is_caught_forward(self):
        """真实值躺在发布位的字节码里（源件在位＝R5 不响）：正向必须响，且带树名、值不外抄。"""
        old = os.environ.get(self.FAKE_NAME)
        os.environ[self.FAKE_NAME] = self.FAKE_VALUE
        try:
            self._plant_ws("pai/__pycache__/memory.cpython-313.pyc",
                           b"\x00\x0b\r\n" + self.FAKE_VALUE.encode("utf-8") + b"\x1f" * 32)
            v = self._violations()
            hits = [s for s in v if "[R6]" in s and "面=发布位" in s]
            self.assertEqual(len(hits), 1, v)
            self.assertNotIn(self.FAKE_VALUE, " ".join(v))
            self.assertEqual([s for s in v if "[R5]" in s], [], "这条必须由 R6 报，不是 R5")
        finally:
            if old is None:
                del os.environ[self.FAKE_NAME]
            else:
                os.environ[self.FAKE_NAME] = old

    def test_running_state_in_workspace_is_not_a_violation(self):
        """R2 不许被顺手扩过来：发布位的 data\\memory.db 是自家运行态（反向那 6 条红全在 data\\ 里）。"""
        self._plant_ws("data/memory.db", b"SQLite format 3\x00" + b"\x1f" * 64)
        v = self._violations()
        self.assertEqual([s for s in v if "[R2]" in s], [], v)
        self.assertEqual([s for s in v if "[R5]" in s], [], v)

    def test_green_line_names_both_excluded_faces(self):
        import contextlib
        import io
        self.assertEqual(self._violations(), [])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = CR.main(["--ws", str(self.ws), "--mirror", str(self.mirror),
                          "--blackboard", str(self.bb)])
        out = buf.getvalue()
        self.assertEqual(rc, 0, out)
        self.assertIn("面=投递区两面+板根", out)
        self.assertIn("另并发布位两面", out)
        self.assertIn(str(self.ws), out)

    def test_run_accepts_str_paths_not_only_path(self):
        """旧版 run() 接到 str 的板根会在 _face_of 里 AttributeError 崩（本轮实测踩到）。"""
        self.assertEqual(
            CR.run(str(self.ws), str(self.mirror), str(self.bb), str(self.mirror.parent)), [])
        self.assertTrue(CR._walk_files(str(self.ws)))


if __name__ == "__main__":
    unittest.main()
