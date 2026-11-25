"""契约文档单一真源一致性守门（Quick 档常驻）。

复用 tools/check_contract_master.py 的 check()，确保：
  1) 主本存在且有 sha256
  2) 所有 policy=mirror 副本与主本逐字节一致（漂移会让本测试红）
  3) frozen/counterparty 不影响判定

这样任何人重新打包把 spine 重置成旧快照，日常 Quick 档立刻抓到。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_contract_master as C  # noqa: E402


class ContractMasterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.decl, cls.mf = C.load_declaration(str(ROOT))
        cls.r = C.check(cls.decl, cls.mf)

    def test_master_present(self):
        self.assertTrue(self.r["master_exists"], "主本不存在")
        self.assertEqual(len(self.r["master_sha256"] or ""), 64, "主本 sha256 异常")

    def test_no_mirror_drift(self):
        drifts = [c["path"] for c in self.r["copies"] if c["status"] == "FAIL"]
        self.assertEqual(drifts, [], "mirror 副本漂移: %s" % drifts)

    def test_fail_counter_zero(self):
        self.assertEqual(self.r["fail"], 0, "一致性闸门判定 FAIL")

    def test_policies_covered(self):
        policies = {c["policy"] for c in self.r["copies"]}
        self.assertIn("mirror", policies)
        self.assertIn("frozen", policies)


if __name__ == "__main__":
    unittest.main()
