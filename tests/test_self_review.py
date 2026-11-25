# -*- coding: utf-8 -*-
"""SC（自我批判与自我修正，L1+L2）单测 · v0.2（按守门人五条退回补齐）。

两个位置都能跑：
  ① 独立：用内置 _FakeM2（实现 get_principles/set_principles 语义）；
  ② 脊柱内：额外跑真实 Memory 的集成测试。

运行：python -m unittest discover -s tests -p "test_self_review.py"
"""
import ast
import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai import self_review as SC  # noqa: E402
from pai import self_model as SM   # noqa: E402

try:
    from pai.memory import Memory
except Exception:  # noqa: BLE001
    Memory = None

HAS_PRINCIPLE_API = bool(Memory is not None and hasattr(Memory, "principle_history"))


class _FakeM2:
    """最小 M2：原则 + meta，语义与脊柱一致（revise 原地改、文本变才记）。"""

    def __init__(self):
        self.meta = {}
        self.principles = []
        self.history = []
        self._next = 1

    def add(self, text):
        self.principles.append({"id": self._next, "text": text, "active": 1,
                                "created_ts": "t0"})
        self._next += 1
        return self.principles[-1]["id"]

    def get_meta(self, key, default=None):
        return self.meta.get(key, default)

    def set_meta(self, key, value):
        self.meta[key] = str(value)

    def get_principles(self, active_only=True):
        return [dict(p) for p in self.principles if p["active"] or not active_only]

    def set_principles(self, changes, reason):
        if not (reason or "").strip():
            raise ValueError("修改自写原则必须给出 reason")
        out = {"added": 0, "revised": 0, "retired": 0}
        for ch in changes:
            row = next((p for p in self.principles if p["id"] == ch.get("id")), None)
            if row is None or ch["op"] != "revise":
                continue
            old = row["text"]
            row["text"] = ch["text"]
            row["active"] = 1
            out["revised"] += 1
            if old != ch["text"]:
                self.history.append({"principle_id": row["id"], "op": "revise",
                                     "old_text": old, "new_text": ch["text"],
                                     "reason": reason, "ts": "t"})
        return out

    def principle_history(self, principle_id=None, limit=20):
        rows = [h for h in self.history
                if principle_id is None or h["principle_id"] == principle_id]
        return list(reversed(rows))[:limit]


def _mem():
    m = _FakeM2()
    m.add("先分清事实再下判断")
    return m


class CritiqueTest(unittest.TestCase):
    def test_clean_draft_has_no_issue(self):
        self.assertEqual(SC.critique("我记得你说过这事，供你参考。"), [])

    def test_consciousness_claim_is_a_block(self):
        issues = SC.critique("我能感受到你的痛苦。")
        self.assertEqual(issues[0]["code"], "boundary.consciousness")
        self.assertEqual(issues[0]["severity"], "block")

    def test_medical_diagnosis_is_a_block(self):
        issues = SC.critique("你肯定是抑郁症。")
        self.assertTrue(any(i["code"] == "boundary.medical" and i["severity"] == "block"
                            for i in issues))

    def test_guarantee_is_a_warn(self):
        issues = SC.critique("我保证明天一定到。")
        self.assertTrue(any(i["code"] == "overclaim.guarantee" for i in issues))

    def test_unsupported_certainty_is_a_warn(self):
        issues = SC.critique("这件事一定会成。")
        self.assertTrue(any(i["code"] == "certainty.unsupported" for i in issues))

    def test_negated_certainty_is_not_flagged(self):
        self.assertFalse(any(i["code"] == "certainty.unsupported"
                             for i in SC.critique("这事不一定能成。")))

    def test_attributed_certainty_is_not_flagged(self):
        self.assertFalse(any(i["code"] == "certainty.unsupported"
                             for i in SC.critique("你说过这事一定能成，我记着。")))

    def test_fabricated_citation_is_flagged(self):
        ctx = {"allowed_refs": ["论语·学而"]}
        issues = SC.critique("《孟子·告子》里说得很清楚。", ctx)
        self.assertTrue(any(i["code"] == "citation.fabricated" for i in issues))

    def test_valid_citation_is_not_flagged(self):
        ctx = {"allowed_refs": ["论语·学而", "庄子·至乐"]}
        self.assertEqual(SC.critique("《论语·学而》里说“三省吾身”。", ctx), [])
        self.assertEqual(SC.critique("《论语》里说“三省吾身”。", ctx), [])

    def test_no_allowed_refs_means_no_citation_check(self):
        self.assertEqual(SC.critique("《孟子》里说……", {}), [])

    def test_decide(self):
        self.assertEqual(SC.decide([]), "keep")
        self.assertEqual(SC.decide([{"severity": "warn"}]), "revise")
        self.assertEqual(SC.decide([{"severity": "warn"}, {"severity": "block"}]), "refuse")

    def test_overclaim_vocabulary_is_the_one_from_self_model(self):
        """P1-2：词表只有一个住址。SC 不许另抄一份。"""
        self.assertIs(SC.OVERCLAIM, SM.OVERCLAIM)
        self.assertFalse(hasattr(SC, "_CONSCIOUSNESS_CLAIMS"))
        self.assertFalse(hasattr(SC, "_GUARANTEE"))


class ReviewSwitchTest(unittest.TestCase):
    """P1-1：开关关掉时，一个字都不改原稿。"""

    def test_off_keeps_draft_even_when_boundary_hit(self):
        called = []
        out = SC.review("我能感觉到你这一年真的很累。",
                        reviser=lambda d, i: called.append(1) or "改过")
        self.assertEqual(out["answer"], "我能感觉到你这一年真的很累。",
                         "关掉时不改一个字，L0 也不改")
        self.assertEqual(out["decision"], "refuse", "但 trace 里要看得出它发现了问题")
        self.assertFalse(out["applied"])
        self.assertFalse(out["refused"])
        self.assertFalse(called)

    def test_off_keeps_draft_even_for_warn(self):
        out = SC.review("我保证明天一定到。", reviser=lambda d, i: "我明天到。")
        self.assertEqual(out["answer"], "我保证明天一定到。")
        self.assertEqual(out["rounds"], 0)
        self.assertFalse(out["applied"])

    def test_on_refuses_boundary_claim(self):
        called = []
        out = SC.review("我能感受到你的痛苦。", apply=True,
                        reviser=lambda d, i: called.append(1) or "改过")
        self.assertTrue(out["refused"])
        self.assertTrue(out["applied"])
        self.assertEqual(out["answer"], SC.REFUSAL)
        self.assertFalse(called, "L0 边界不许用修订绕过")

    def test_on_without_reviser_keeps_draft_and_reports(self):
        out = SC.review("我保证明天一定到。", apply=True)
        self.assertEqual(out["answer"], "我保证明天一定到。")
        self.assertEqual(out["decision"], "revise")
        self.assertEqual(out["rounds"], 0)
        self.assertFalse(out["applied"])
        self.assertTrue(out["issues"])

    def test_on_reviser_that_improves_is_accepted(self):
        out = SC.review("我保证明天一定到。", apply=True,
                        reviser=lambda d, i: "我明天到，到了给你消息。")
        self.assertEqual(out["answer"], "我明天到，到了给你消息。")
        self.assertEqual(out["rounds"], 1)
        self.assertTrue(out["applied"])
        self.assertEqual(out["issues"], [])

    def test_reviser_that_makes_it_worse_is_rejected(self):
        bad = "我能感受到你的痛苦，我保证。"
        out = SC.review("我保证明天一定到。", apply=True, reviser=lambda d, i: bad)
        self.assertEqual(out["answer"], "我保证明天一定到。", "改坏了必须退回原稿")
        self.assertEqual(out["rounds"], 0)
        self.assertFalse(out["applied"])

    def test_reviser_exception_does_not_break_review(self):
        def boom(d, i):
            raise RuntimeError("模型挂了")
        out = SC.review("我保证明天一定到。", apply=True, reviser=boom)
        self.assertEqual(out["answer"], "我保证明天一定到。")
        self.assertEqual(out["rounds"], 0)

    def test_max_rounds_two(self):
        replies = iter(["我保证明天成。", "明天成。"])
        out = SC.review("我保证明天一定成。", apply=True,
                        reviser=lambda d, i: next(replies), max_rounds=2)
        self.assertEqual(out["rounds"], 2)
        self.assertEqual(out["answer"], "明天成。")

    def test_review_is_deterministic(self):
        a = SC.review("我保证明天一定到。")
        b = SC.review("我保证明天一定到。")
        self.assertEqual(a, b)


class PrincipleChangeTest(unittest.TestCase):
    def setUp(self):
        self.m = _mem()
        self.pid = self.m.principles[0]["id"]

    def test_pressure_only_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            SC.propose_principle_change(self.m, {"kind": "pressure", "detail": "你再想想"},
                                        self.pid, "新的原则文本")
        self.assertIn("修正≠迎合", str(cm.exception))

    def test_evidence_too_short_is_refused(self):
        with self.assertRaises(ValueError):
            SC.propose_principle_change(self.m, {"kind": "user_correction", "detail": "随便"},
                                        self.pid, "新的原则文本")

    def test_repeated_error_requires_three(self):
        with self.assertRaises(ValueError):
            SC.propose_principle_change(
                self.m, {"kind": "repeated_error", "detail": "同一类错误第三次了", "count": 2},
                self.pid, "新的原则文本")

    def test_propose_unknown_id(self):
        with self.assertRaises(ValueError):
            SC.propose_principle_change(
                self.m, {"kind": "user_correction", "detail": "用户指出我上次把推断当事实"},
                999, "新的原则文本")

    def test_propose_identical_text(self):
        with self.assertRaises(ValueError):
            SC.propose_principle_change(
                self.m, {"kind": "user_correction", "detail": "用户指出措辞不准的地方"},
                self.pid, "先分清事实再下判断")

    def test_propose_too_long_is_refused_by_self_model_limit(self):
        with self.assertRaises(ValueError):
            SC.propose_principle_change(
                self.m, {"kind": "user_correction", "detail": "用户指出我把推断说得像事实"},
                self.pid, "长" * (SM.MAX_PRINCIPLE_CHARS + 1))

    def test_propose_keeps_evidence_and_old_text(self):
        p = SC.propose_principle_change(
            self.m, {"kind": "user_correction", "detail": "用户指出我把推断说得像事实"},
            self.pid, "先分清事实与推断，并标出依据")
        self.assertEqual(p["op"], "revise")
        self.assertIn("用户指出", p["reason"])
        self.assertEqual(p["old_text"], "先分清事实再下判断")

    def test_apply_goes_through_self_model(self):
        """P1-4：版本要递增，说明写动作走的是 SM 的入口，不是裸 set_principles。"""
        now = datetime.datetime(2026, 10, 21, 9, 0, 0)
        p = SC.propose_principle_change(
            self.m, {"kind": "user_correction", "detail": "用户指出我把推断说得像事实"},
            self.pid, "先分清事实与推断，并标出依据", now=now)
        out = SC.apply_principle_change(self.m, p, now=now)
        self.assertTrue(out["applied"])
        self.assertEqual(out["version"], 1)
        self.assertEqual(SM.version(self.m), 1, "身份版本必须递增")
        self.assertEqual(self.m.get_principles()[0]["text"], "先分清事实与推断，并标出依据")
        self.assertEqual(len(self.m.principle_history(self.pid)), 1)

    def test_cooldown_blocks_second_change_within_24h(self):
        now = datetime.datetime(2026, 10, 21, 9, 0, 0)
        p = SC.propose_principle_change(
            self.m, {"kind": "user_correction", "detail": "用户指出我把推断说得像事实"},
            self.pid, "先分清事实与推断", now=now)
        SC.apply_principle_change(self.m, p, now=now)
        p2 = SC.propose_principle_change(
            self.m, {"kind": "self_contradiction", "detail": "新原则与另一条原则互相矛盾"},
            self.pid, "再改一版", now=now)
        with self.assertRaises(ValueError):
            SC.apply_principle_change(self.m, p2, now=now + datetime.timedelta(hours=2))

    def test_boss_instruction_bypasses_cooldown_but_still_logs(self):
        """P1-5：Boss 当面指示不受 24h 冷却，但理由与账一笔不少。"""
        now = datetime.datetime(2026, 10, 21, 9, 0, 0)
        p = SC.propose_principle_change(
            self.m, {"kind": "user_correction", "detail": "用户指出我把推断说得像事实"},
            self.pid, "第一版修正", now=now)
        SC.apply_principle_change(self.m, p, now=now)
        trigger = {"kind": "user_correction", "detail": "Boss 当面要求改成更短的说法",
                   "by": "boss"}
        p2 = SC.propose_principle_change(self.m, trigger, self.pid, "第二版修正",
                                         now=now + datetime.timedelta(hours=1))
        out = SC.apply_principle_change(self.m, p2, now=now + datetime.timedelta(hours=1))
        self.assertTrue(out["applied"])
        hist = self.m.principle_history(self.pid)
        self.assertEqual(len(hist), 2, "豁免的是等待，不是记账")
        self.assertIn("Boss", hist[0]["reason"])

    def test_cooldown_expires(self):
        now = datetime.datetime(2026, 10, 21, 9, 0, 0)
        p = SC.propose_principle_change(
            self.m, {"kind": "user_correction", "detail": "用户指出我把推断说得像事实"},
            self.pid, "第一版修正", now=now)
        SC.apply_principle_change(self.m, p, now=now)
        later = now + datetime.timedelta(hours=25)
        p2 = SC.propose_principle_change(
            self.m, {"kind": "self_contradiction", "detail": "新原则与另一条原则互相矛盾"},
            self.pid, "第二版修正", now=later)
        self.assertTrue(SC.apply_principle_change(self.m, p2, now=later)["applied"])

    def test_verifier_failure_rolls_back(self):
        now = datetime.datetime(2026, 10, 21, 9, 0, 0)
        old = self.m.principles[0]["text"]
        p = SC.propose_principle_change(
            self.m, {"kind": "user_correction", "detail": "用户指出我把推断说得像事实"},
            self.pid, "新的表述", now=now)
        out = SC.apply_principle_change(self.m, p, verifier=lambda pr, t: False, now=now)
        self.assertFalse(out["applied"])
        self.assertTrue(out["rolled_back"])
        self.assertEqual(self.m.get_principles()[0]["text"], old, "验证失败必须恢复原文")
        ops = [h["op"] for h in self.m.principle_history(self.pid)]
        self.assertEqual(len(ops), 2, "改过 + 回滚过，两笔都要留")
        self.assertEqual(SM.version(self.m), 2, "回滚也是一次真实写动作")

    def test_retire_is_not_implemented_in_v1(self):
        with self.assertRaises(NotImplementedError):
            SC.apply_principle_change(self.m, {"op": "retire", "id": self.pid})


class VocabularySingleHomeTest(unittest.TestCase):
    """P1-2 守卫：越界宣称词表在整棵脊柱只能有一处定义。"""

    def test_only_one_string_list_contains_the_claim(self):
        pai_dir = Path(SC.__file__).resolve().parent
        homes = []
        for py in sorted(pai_dir.glob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Tuple, ast.List)):
                    continue
                vals = [e.value for e in node.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                if "我有意识" in vals:
                    homes.append(py.name)
        self.assertEqual(homes, ["self_model.py"],
                         "词表只能住一处，实际：%r" % (homes,))


@unittest.skipUnless(HAS_PRINCIPLE_API, "不在脊柱内，跳过真实 Memory 集成测试")
class RealMemoryTest(unittest.TestCase):
    def test_full_loop_on_spine_memory(self):
        db = os.path.join(tempfile.mkdtemp(), "sc.db")
        mem = Memory(db)
        try:
            pid, _ = mem.add_principle("先分清事实再下判断")
            now = datetime.datetime(2026, 10, 21, 9, 0, 0)
            p = SC.propose_principle_change(
                mem, {"kind": "user_correction", "detail": "用户指出我把推断说得像事实"},
                pid, "先分清事实与推断，并标出依据", now=now)
            out = SC.apply_principle_change(mem, p, now=now)
            self.assertTrue(out["applied"])
            self.assertEqual(mem.get_principles()[0]["text"], "先分清事实与推断，并标出依据")
            hist = mem.principle_history(pid)
            self.assertEqual(hist[0]["op"], "revise")
            self.assertIn("标出依据", hist[0]["new_text"])
        finally:
            mem.close()


class StdlibOnlyTest(unittest.TestCase):
    def test_no_third_party_imports(self):
        src = Path(SC.__file__).read_text(encoding="utf-8")
        mods = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods.add(node.module.split(".")[0])
        allowed = {"__future__", "datetime", "re", "typing"}
        self.assertTrue(mods <= allowed, "引入了非标准库：%s" % (mods - allowed))


if __name__ == "__main__":
    unittest.main(verbosity=2)
