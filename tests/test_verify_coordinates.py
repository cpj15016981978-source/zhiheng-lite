"""tools/verify_coordinates.py 的反向守卫：证明这把尺会红，而且红在该红的行上。

为什么每条都要「脏样本必红」：尺只报「对得上 N 处」是不够的——它对什么都可能对不上，
也可能对什么都对不上（哑尺）。这里用一个一次性目录，把四种读数各自钉死。
"""
import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

WS = Path(r"C:\Users\Administrator\Documents\Qoder\2026-10-19\14868c32\personal-ai-system")
sys.path.insert(0, str(WS / "tools"))
import verify_coordinates as VC  # noqa: E402


def dg(b):
    return hashlib.md5(b).hexdigest()[:12]


class VerifyCoordinatesArms(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="vc_fixture_"))
        self.old_session = VC.SESSION
        self.old_index = VC._INDEX
        VC.SESSION = self.d
        VC._INDEX = None

    def tearDown(self):
        VC.SESSION = self.old_session
        VC._INDEX = self.old_index

    def _fixture(self):
        frozen = b"abc\r\n"
        live = b"print(1)\r\n"
        (self.d / "bb_before_zzq.txt").write_bytes(frozen)
        (self.d / "tools").mkdir()
        (self.d / "tools" / "zzq_live.py").write_bytes(live)
        board = "\r\n".join([
            "干净一句，没坐标。",
            "bb_before_zzq.txt %d B／%s 这处对得上。" % (len(frozen), dg(frozen)),
            "bb_before_zzq.txt %d B／%s 这处是抄错的冻结族。" % (len(frozen), "0" * 12),
            "tools/zzq_live.py %d B／%s 活件对不上只知会。" % (len(live), "1" * 12),
            "zzq_没这个件.txt %d B／%s 不可寻。" % (7, "2" * 12),
        ]) + "\r\n"
        bd = self.d / "zzq_board.txt"
        bd.write_bytes(board.encode("utf-8"))
        return bd, frozen, live

    def test_frozen_mismatch_is_red_and_points_at_the_line(self):
        bd, frozen, _ = self._fixture()
        red, live, missing, ok = VC.scan([bd])
        self.assertEqual(len(red), 1, "冻结族抄错那一处必须唯一判红：%s" % red)
        self.assertIn("第 3 行", red[0], "红要指到行号，不然我还是要自己去翻")
        self.assertIn("bb_before_zzq.txt", red[0])
        self.assertIn("同尺寸不同字节", red[0])

    def test_matching_coordinate_is_not_red(self):
        bd, _, _ = self._fixture()
        red, live, missing, ok = VC.scan([bd])
        self.assertEqual(ok, 1, "对得上那处要计入 ok：%d" % ok)
        self.assertEqual(live, 1, "活件对不上要进知会、不许进红")
        self.assertEqual(missing, 1, "不可寻要单独计数，不许当成对得上")

    def test_sha256_recorded_coordinate_counts_as_matching_not_red(self):
        """旧格（2026-11-08/09/10 那批）用的都是 sha256 前 12 位：只认 md5 就会把真坐标判成一片红。"""
        frozen = b"abc\r\n"
        (self.d / "bb_before_zzq_sha.txt").write_bytes(frozen)
        board = "\r\n".join([
            "bb_before_zzq_sha.txt %d B／%s 这一句记的是 sha256，要认。" % (len(frozen), hashlib.sha256(frozen).hexdigest()[:12]),
            "bb_before_zzq_sha.txt %d B／%s 两种算法都不是，才许红。" % (len(frozen), "ab" * 6),
        ]) + "\r\n"
        bd = self.d / "zzq_board_sha.txt"
        bd.write_bytes(board.encode("utf-8"))
        red, live, missing, ok = VC.scan([bd])
        self.assertEqual(ok, 1, "sha256 那一处要计入对得上：%s" % red)
        self.assertEqual(len(red), 1, "只有两种都不是的那处才许红：%s" % red)
        self.assertIn("sha256 前 12", red[0], "红要把两把算法的现读都念出来，我才判得出是不是抄错")
        self.assertEqual(VC.ALGO_HITS["sha256"], 1, "算法拆分读数要报，不然混写有多严重没人知道")

    def test_live_file_mismatch_is_not_red(self):
        bd, _, live = self._fixture()
        (self.d / "tools" / "zzq_live.py").write_bytes(live + "# 改过\r\n".encode("utf-8"))
        red, _, _, _ = VC.scan([bd])
        self.assertEqual([r for r in red if "zzq_live" in r], [], "活件本来就会变，判它红就是教人忽略红")

    def test_classify_separates_wrong_copy_from_later_rewrite(self):
        import datetime
        import os
        bd, frozen, _ = self._fixture()
        path = self.d / "bb_before_zzq.txt"
        now = datetime.datetime.now()

        def set_mtime(dt):
            os.utime(path, (dt.timestamp(), dt.timestamp()))

        no_date = "bb_before_zzq.txt %s" % ("0" * 12)
        self.assertIn("没有可比的日期", VC.classify(no_date, bd, path), "这句里根本没有时刻，不许编一个日期去比")

        set_mtime(now - datetime.timedelta(hours=1))
        yday = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        early = "%s bb_before_zzq.txt %d B／%s" % (yday, len(frozen), "0" * 12)
        self.assertIn("被动过", VC.classify(early, bd, path), "件比那句时刻新两分钟以上＝记完之后这份字节动过")

        tday = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        late = "%s bb_before_zzq.txt %d B／%s" % (tday, len(frozen), "0" * 12)
        self.assertIn("抄错", VC.classify(late, bd, path), "件不比那句时刻新＝写下那刻它就不是这份字节")

    def test_mute_ruler_exits_two(self):
        bd = self.d / "zzq_empty_board.txt"
        bd.write_bytes("整件没一处坐标\r\n".encode("utf-8"))
        old = VC.SOURCES
        VC.SOURCES = (bd,)
        try:
            self.assertEqual(VC.main(["--quiet"]), 2, "一条坐标都重算不出来的尺＝哑尺，不许给绿")
        finally:
            VC.SOURCES = old

    def test_coordinate_shape_variants_all_parse(self):
        bd = self.d / "zzq_shapes.txt"
        body = ("板根 12 B／aaaa0000bbbb\r\n"
                "《zzq_letter.txt》＝12 B／md5 前 12 位 cccc1111dddd\r\n"
                "bb_x.txt：12 B／eeee2222ffff\r\n")
        bd.write_bytes(body.encode("utf-8"))
        found = [(m.group("name"), m.group(2), m.group(3)) for m in VC.COORD.finditer(body.replace("\r\n", "\n"))]
        self.assertEqual(len(found), 3, "三种真实形状都要认出来：%s" % found)
        self.assertEqual(found[1][0], "《zzq_letter.txt》")
        self.assertEqual(found[0][0], "板根")


if __name__ == "__main__":
    unittest.main()
