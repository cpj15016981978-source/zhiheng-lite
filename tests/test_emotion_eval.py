# -*- coding: utf-8 -*-
"""情绪标注/评测流水线测试。

守的四条不变量：
  ① 分类学与后端 affect.MOODS 不得漂移（Mirrai 的 warm 缺失就是这类问题的实例）
  ② 训练与评测必须物理隔离，近似重复也算泄漏
  ③ 样本不足的类不得进 macro 平均，必须显式标「不可测」
  ④ 不高于零信息对照的候选，判 FAIL —— 不许出分

全部离线，不联网、不调模型、不写真实 data/。
"""
import json
import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals import emotion_taxonomy as TX              # noqa: E402
from tools import make_emotion_evalset as MK          # noqa: E402
from tools import eval_emotion as EV                  # noqa: E402
from tools import label_emotion as LE                 # noqa: E402


class Taxonomy(unittest.TestCase):
    def test_labels_are_closed_enum(self):
        self.assertEqual(len(set(TX.MOODS)), len(TX.MOODS), "标签有重复")
        self.assertEqual(TX.FALLBACK_MOOD in TX.MOODS, True)
        self.assertEqual(set(TX.DEFINITIONS), set(TX.MOODS), "定义与标签不同源")

    def test_matches_backend_affect(self):
        """①核心：与后端枚举逐字同序。这是防 warm 那类缺失的守卫。"""
        from pai import affect
        self.assertEqual(tuple(affect.MOODS), TX.MOODS,
                         "分类学与 pai/affect.py:89 漂移了 —— 标注、评测、运行时三方口径不一致")

    def test_validate_labels_rejects_outsiders(self):
        ok, bad = TX.validate_labels(["疲惫", "emo", "平静", "狂喜"])
        self.assertEqual(ok, 2)
        self.assertEqual(set(bad), {"狂喜", "emo"})

    def test_every_mood_has_a_boundary_rule_touching_it(self):
        covered = set()
        for a, b, _ in TX.BOUNDARIES:
            covered.add(a); covered.add(b)
        self.assertEqual(set(TX.MOODS) - covered, set(),
                         "有标签没有任何边界规则，标注口径必漂")


class DupDetection(unittest.TestCase):
    def test_jaccard_bounds(self):
        self.assertEqual(MK.jaccard(MK.ngrams("今天好累"), MK.ngrams("今天好累")), 1.0)
        self.assertEqual(MK.jaccard(set(), MK.ngrams("x")), 0.0)

    def test_near_duplicate_is_detected(self):
        a, b = "我今天真的好累", "我今天真的好累啊"
        self.assertGreaterEqual(MK.jaccard(MK.ngrams(a), MK.ngrams(b)), 0.6)

    def test_different_sentences_not_flagged(self):
        self.assertLess(MK.jaccard(MK.ngrams("项目上线了真开心"),
                                   MK.ngrams("爷爷去世了我很难受")), 0.3)


CORPUS = [
    ("疲惫", "加班到三点，整个人都空了"),
    ("疲惫", "连续一周没睡好，撑不住"),
    ("疲惫", "开了一天会，脑子转不动"),
    ("疲惫", "下班只想躺着什么都不干"),
    ("疲惫", "这周太耗人了，累到不想说话"),
    ("疲惫", "早上起不来，晚上睡不着"),
    ("低落", "做什么都没意思，提不起劲"),
    ("低落", "忙了半年，结果还是不行"),
    ("低落", "说不上难过，就是很沉"),
    ("低落", "对什么都高兴不起来"),
    ("低落", "感觉自己在原地打转"),
    ("低落", "期待了很久的事黄了"),
    ("焦虑", "明天要汇报，今晚肯定睡不着"),
    ("焦虑", "总担心项目要出问题"),
    ("焦虑", "账上的钱只够撑两个月"),
    ("焦虑", "心跳很快，静不下来"),
    ("焦虑", "一直刷手机等消息"),
    ("焦虑", "怕做错，反复检查了三遍"),
    ("孤独", "没人可以说这些"),
    ("孤独", "周末两天没跟任何人说话"),
    ("孤独", "他们都不理解我在做什么"),
    ("孤独", "翻遍通讯录找不到能打的电话"),
    ("孤独", "一个人在外地，生病没人知道"),
    ("孤独", "说了也白说，不如不说"),
    ("愤怒", "凭什么功劳都是他的"),
    ("愤怒", "说了三遍还是不改，真的过分"),
    ("愤怒", "这合同明显坑人"),
    ("愤怒", "被当众甩锅，气到手抖"),
    ("愤怒", "我忍了很久了"),
    ("愤怒", "这种处理方式就是在糊弄人"),
    ("轻快", "中了两张票，晚上去看电影"),
    ("轻快", "项目终于上线了，超开心"),
    ("轻快", "今天状态特别好，想出去走走"),
    ("轻快", "女儿会叫爸爸了，笑得不行"),
    ("轻快", "好久没这么痛快过"),
    ("轻快", "老板今天居然夸我了"),
    ("丧痛", "我爷爷上周去世了"),
    ("丧痛", "母亲走了半年了，还是不适应"),
    ("丧痛", "葬礼办完了，家里空了一块"),
    ("丧痛", "姥姥过世，赶没赶上最后一面"),
    ("丧痛", "头七今天，一个人在阳台站了很久"),
    ("丧痛", "他病逝了，走得很突然"),
    ("平静", "帮我看下这份报价单的格式"),
    ("平静", "明天上午十点有个会"),
    ("平静", "把这个文件转成 PDF"),
    ("平静", "今天的天气怎么样"),
    ("平静", "记一下：周五前要交对账表"),
    ("平静", "这份报表的口径是按自然月还是账期"),
]


def write_jsonl(path, rows):
    pathlib.Path(path).write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")


def labeled():
    return [{"id": LE.text_id(t), "text": t, "label": lab,
             "taxonomy": TX.TAXONOMY_VERSION, "teacher": "fixture"}
            for lab, t in CORPUS]


class Split(unittest.TestCase):
    def setUp(self):
        self.rows = labeled()
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="ems_"))
        self.src = self.d / "lab.jsonl"
        write_jsonl(self.src, self.rows)

    def test_no_id_overlap(self):
        ev, tr, *_ = MK.split(self.rows, per_class=5, threshold=0.75)
        e_ids = {self.rows[i]["id"] for i in ev}
        t_ids = {self.rows[i]["id"] for i in tr}
        self.assertEqual(e_ids & t_ids, set(), "②同一句同时进了训练与评测")

    def test_near_dupe_cluster_not_split(self):
        """同簇（近似重复）必须整簇去同一侧，否则评测集里就是训练句的换皮。"""
        rows = self.rows + [
            {"id": LE.text_id(CORPUS[0][1] + "啊"), "text": CORPUS[0][1] + "啊",
             "label": "疲惫", "taxonomy": TX.TAXONOMY_VERSION, "teacher": "fixture"},
            {"id": LE.text_id(CORPUS[1][1] + "呀"), "text": CORPUS[1][1] + "呀",
             "label": "疲惫", "taxonomy": TX.TAXONOMY_VERSION, "teacher": "fixture"}]
        ev, tr, _, _, _ = MK.split(rows, per_class=6, threshold=0.6)
        e_ids = {rows[i]["id"] for i in ev}
        t_ids = {rows[i]["id"] for i in tr}
        grp = [rows[-1]["id"], rows[-2]["id"], rows[0]["id"], rows[1]["id"]]
        in_ev = [g for g in grp if g in e_ids]
        in_tr = [g for g in grp if g in t_ids]
        self.assertTrue(not (in_ev and in_tr),
                        "近似重复簇被拆到两侧：评测集里出现了训练句的换皮版本")

    def test_zero_sample_class_marked(self):
        """③某类完全没有数据时，split 不硬造，eval 不把它算进 macro。"""
        rows = [r for r in self.rows if r["label"] != "孤独"]
        ev, tr, stats, _, _ = MK.split(rows, per_class=4, threshold=0.75)
        self.assertEqual(stats.get("孤独", {"eval": 0})["eval"], 0)
        m = EV.score([rows[i]["label"] for i in sorted(ev)],
                     [rows[i]["label"] for i in sorted(ev)])
        self.assertIn("孤独", m["unmeasurable_classes"])
        self.assertNotIn("孤独", m["measurable_classes"])


class Scoring(unittest.TestCase):
    def setUp(self):
        self.rows = labeled()
        ev, tr, *_ = MK.split(self.rows, per_class=5, threshold=0.75)
        self.ev_rows = [self.rows[i] for i in sorted(ev)]
        self.tr_rows = [self.rows[i] for i in sorted(tr)]
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="emsc_"))
        self.evp = self.d / "eval.jsonl"
        self.trp = self.d / "train.jsonl"
        write_jsonl(self.evp, self.ev_rows)
        write_jsonl(self.trp, self.tr_rows)

    def test_perfect_predictor_wins(self):
        m = EV.score([r["label"] for r in self.ev_rows], [r["label"] for r in self.ev_rows])
        self.assertEqual(m["macro_f1"], 1.0)
        self.assertEqual(m["accuracy"], 1.0)

    def test_majority_candidate_is_not_usable(self):
        """④只会猜多数类的候选，与多数类基线同分 → 必须判不可用，而不是给个高分。"""
        from collections import Counter
        maj = Counter(r["label"] for r in self.ev_rows).most_common(1)[0][0]
        rc = EV.main(["--eval", str(self.evp), "--train", str(self.trp),
                      "--predictor", "const", "--const", maj, "--json"])
        self.assertEqual(rc, 1, "候选与多数类基线同分，却被判可用")

    def test_leakage_blocks_scoring(self):
        """②把评测集原样当训练池传进去，必须拒绝出分。"""
        rc = EV.main(["--eval", str(self.evp), "--train", str(self.evp),
                      "--predictor", "const"])
        self.assertEqual(rc, 2, "泄漏没被挡住")

    def test_missing_train_blocks_scoring(self):
        rc = EV.main(["--eval", str(self.evp), "--predictor", "const"])
        self.assertEqual(rc, 2, "没做泄漏检查就出分 —— 这正是本项目反复犯的错")

    def test_near_dupe_leakage_detected(self):
        fake_train = self.tr_rows + [{"id": "x1", "label": "疲惫",
                                      "text": self.ev_rows[0]["text"] + "啊"}]
        p = self.d / "train_dupe.jsonl"
        write_jsonl(p, fake_train)
        rc = EV.main(["--eval", str(self.evp), "--train", str(p), "--predictor", "const"])
        self.assertEqual(rc, 2, "近似重复泄漏没被发现")


class CoverageFloor(unittest.TestCase):
    """macro 只覆盖一两个类时，不得当成整体结论。"""
    SRC = (ROOT / "tools" / "eval_emotion.py").read_text(encoding="utf-8")

    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="emcov_"))
        rows = [r for r in labeled() if r["label"] in ("丧痛", "平静")]
        ev, tr, *_ = MK.split(rows, per_class=5, threshold=0.75)
        ev_rows = [dict(rows[i], teacher="human") for i in sorted(ev)]
        self.a = self.d / "eval.jsonl"
        self.b = self.d / "train.jsonl"
        write_jsonl(self.a, ev_rows)
        write_jsonl(self.b, [rows[i] for i in sorted(tr)])

    def test_two_class_macro_is_inconclusive(self):
        rc = EV.main(["--eval", str(self.a), "--train", str(self.b),
                      "--predictor", "rules"])
        self.assertEqual(rc, 3,
                         "只有 2 个类可测却给出了结论性判定 —— 单类高分不等于能力强")

    def test_disabling_coverage_floor_is_caught(self):
        old = "MIN_MEASURABLE_CLASSES = 4"
        self.assertIn(old, self.SRC)
        src = self.SRC.replace(old, "MIN_MEASURABLE_CLASSES = 1")
        # 必须落在 tools/ 下：脚本内部用 __file__.parents[1] 反推包根，换目录就 import 不到
        p = ROOT / "tools" / "_ev_tampered.py"
        p.write_text(src, encoding="utf-8")
        try:
            r = subprocess.run([sys.executable, str(p), "--eval", str(self.a),
                                "--train", str(self.b), "--predictor", "rules", "--json"],
                               cwd=str(ROOT), capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
        finally:
            p.unlink(missing_ok=True)
            for q in (ROOT / "tools" / "__pycache__").glob("_ev_tampered*"):
                q.unlink(missing_ok=True)
        try:
            out = json.loads(r.stdout)
        except Exception:
            self.fail("篡改版没出 JSON：%s" % (r.stderr or "")[:300])
        self.assertFalse(out.get("thin_coverage"),
                         "覆盖度下限调到 1 后仍报 thin_coverage —— 说明这条规则不承重")
        self.assertNotEqual(r.returncode, 3,
                            "关掉覆盖度下限后 rc 仍是 3，判定并非由它产生")


class SameSource(unittest.TestCase):
    """⑤候选不得与产生 gold 的教师同源 —— 本 kit 第一版自己犯过的错。"""

    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="emss_"))
        rows = labeled()
        ev, tr, *_ = MK.split(rows, per_class=5, threshold=0.75)
        self.evp = self.d / "eval.jsonl"
        self.trp = self.d / "train.jsonl"
        write_jsonl(self.evp, [rows[i] for i in sorted(ev)])
        write_jsonl(self.trp, [rows[i] for i in sorted(tr)])

    def _retag(self, teacher):
        """把评测集的 gold 全部改成由某个教师产生，模拟同源场景。"""
        rows = [json.loads(l) for l in self.evp.read_text(encoding="utf-8").splitlines() if l.strip()]
        for r in rows:
            r["teacher"] = teacher
        p = self.d / ("eval_%s.jsonl" % abs(hash(teacher)))
        write_jsonl(p, rows)
        return p

    def test_guard_detects_same_tag(self):
        tag = "rules:pai.affect.detect_affect"
        self.assertEqual(EV.same_source_check([{"teacher": tag}], tag), [tag])

    def test_guard_passes_independent_source(self):
        self.assertEqual(EV.same_source_check([{"teacher": "human"}],
                                              "rules:pai.affect.detect_affect"), [])
        self.assertEqual(EV.same_source_check([{"teacher": "cmd:abc"}],
                                              "rules:pai.affect.detect_affect"), [])

    def test_scoring_refuses_same_source_candidate(self):
        p = self._retag("rules:pai.affect.detect_affect")
        rc = EV.main(["--eval", str(p), "--train", str(self.trp), "--predictor", "rules"])
        self.assertEqual(rc, 2, "候选与教师同源却仍出了分 —— 这正是我第一版犯的错")

    def test_scoring_allows_human_gold(self):
        p = self._retag("human")
        rc = EV.main(["--eval", str(p), "--train", str(self.trp), "--predictor", "rules"])
        self.assertNotEqual(rc, 2, "gold 来自人工却被同源守卫挡住，误伤")


class LabelingPipeline(unittest.TestCase):
    def test_consistency_filter_quarantines_disagreement(self):
        calls = {"n": 0}

        def flaky(_t):
            calls["n"] += 1
            return "疲惫" if calls["n"] % 2 else "低落"
        label, agree, samples, bad = LE.sample_label(flaky, "今天好累", k=3)
        self.assertFalse(agree, "k 次不一致却没被判为需人工复核")
        self.assertTrue(label)

    def test_out_of_enum_teacher_output_is_quarantined(self):
        label, agree, samples, bad = LE.sample_label(lambda t: "狂喜", "哈哈哈", k=3)
        self.assertIsNone(label)
        self.assertIn("狂喜", bad)

    def test_consistent_teacher_output_is_accepted(self):
        label, agree, samples, bad = LE.sample_label(lambda t: "疲惫", "好累", k=3)
        self.assertEqual(label, "疲惫")
        self.assertTrue(agree)


class DumbRuler(unittest.TestCase):
    """④刀拔掉必须炸。"""
    SRC = (ROOT / "tools" / "eval_emotion.py").read_text(encoding="utf-8")
    TMP = ROOT / "tools" / "_ev_tampered.py"

    def _run(self, tampered_src, argv):
        self.TMP.write_text(tampered_src, encoding="utf-8")
        try:
            return subprocess.run([sys.executable, str(self.TMP)] + argv,
                                  cwd=str(ROOT), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
        finally:
            self.TMP.unlink(missing_ok=True)
            for p in (ROOT / "tools" / "__pycache__").glob("_ev_tampered*"):
                p.unlink(missing_ok=True)

    def _fixture_files(self):
        rows = labeled()
        ev, tr, *_ = MK.split(rows, per_class=5, threshold=0.75)
        d = pathlib.Path(tempfile.mkdtemp(prefix="emdr_"))
        a, b = d / "eval.jsonl", d / "train.jsonl"
        write_jsonl(a, [rows[i] for i in sorted(ev)])
        write_jsonl(b, [rows[i] for i in sorted(tr)])
        return str(a), str(b)

    def test_disabling_same_source_guard_is_caught(self):
        """短路同源守卫 → 同源场景照样出分。

        这条臂要排除的是「其实是别处顺带挡的」：泄漏检查、覆盖度下限都可能
        恰好让 rc=2，于是守卫看起来在生效、实际没人依赖它。
        实测：短路后 rc=0（正常出分），证明拒绝出分确实由 same_source_check 产生。
        """
        old = "def same_source_check(eval_rows, cand_tag):"
        self.assertIn(old, self.SRC)
        src = self.SRC.replace(old, "def same_source_check(eval_rows, cand_tag):\n    return []\n", 1)

        rows = labeled()
        ev, tr, *_ = MK.split(rows, per_class=5, threshold=0.75)
        ev_rows = [rows[i] for i in sorted(ev)]
        for r in ev_rows:
            r["teacher"] = "rules:pai.affect.detect_affect"      # 制造同源
        d = pathlib.Path(tempfile.mkdtemp(prefix="emdr3_"))
        a, b = d / "eval.jsonl", d / "train.jsonl"
        write_jsonl(a, ev_rows)
        write_jsonl(b, [rows[i] for i in sorted(tr)])

        # 先确认：不篡改时确实被挡（rc=2）
        base = self._run(self.SRC, ["--eval", str(a), "--train", str(b), "--predictor", "rules"])
        self.assertEqual(base.returncode, 2, "同源场景连正常版本都没挡住，守卫本身失效")
        # 再确认：只有这段代码在挡
        r = self._run(src, ["--eval", str(a), "--train", str(b), "--predictor", "rules"])
        self.assertNotEqual(r.returncode, 2,
                            "短路守卫后仍返回 2 —— 说明是别处在挡，这条守卫并不承重")

    def test_disabling_leak_check_is_caught(self):
        old = 'def leak_check(eval_rows, train_rows):\n    """返回问题列表。空 = 干净。"""\n    problems = []'
        self.assertIn(old, self.SRC)
        src = self.SRC.replace(old, old.replace("problems = []", "return []"), 1)
        e, t = self._fixture_files()
        r = self._run(src, ["--eval", e, "--train", t, "--predictor", "const"])
        self.assertNotEqual(r.returncode, 2,
                            "关掉泄漏检查后仍然出分 —— 这条守卫是装饰")

    def test_unmeasurable_included_in_macro_is_caught(self):
        """零样本类恒为 0 分。正确实现只在可测类上取平均；篡改版摊到全部 8 类，
        必然把 macro 拉低。两者给出不同数字，才说明「不可测」这条规则真的在生效。"""
        old = 'meas = [l for l in labels if per[l]["measurable"]]'
        self.assertIn(old, self.SRC)
        src = self.SRC.replace(old, "meas = list(labels)")

        rows = [r for r in labeled() if r["label"] != "孤独"]
        ev, tr, *_ = MK.split(rows, per_class=5, threshold=0.75)
        ev_rows = [rows[i] for i in sorted(ev)]
        tr_rows = [rows[i] for i in sorted(tr)]
        d = pathlib.Path(tempfile.mkdtemp(prefix="emdr2_"))
        a, b = d / "eval.jsonl", d / "train.jsonl"
        write_jsonl(a, ev_rows); write_jsonl(b, tr_rows)

        # 挑一个在评测集里有样本、且能被 const 猜中一部分的类
        from collections import Counter
        present = Counter(r["label"] for r in ev_rows).most_common(1)[0][0]
        good = EV.score([r["label"] for r in ev_rows], [present] * len(ev_rows))["macro_f1"]
        r = self._run(src, ["--eval", str(a), "--train", str(b),
                            "--predictor", "const", "--const", present, "--json"])
        try:
            tampered = json.loads(r.stdout)["candidate_metrics"]["macro_f1"]
        except Exception:
            self.fail("篡改版没出 JSON：%s" % (r.stderr or "")[:300])
        self.assertNotEqual(good, tampered,
                            "零样本类进不进 macro 平均分，结果一样 —— 说明这条规则没在生效")
        self.assertLess(tampered, good,
                        "把零样本类算进来应当拉低分数；实测 tampered=%s good=%s"
                        % (tampered, good))

    def test_losing_to_baseline_still_reports_fail(self):
        e, t = self._fixture_files()
        r = self._run(self.SRC, ["--eval", e, "--train", t, "--predictor", "const",
                                 "--const", "平静", "--json"])
        try:
            out = json.loads(r.stdout)
        except Exception:
            self.fail("const 预测器没出 JSON：%s" % (r.stderr or "")[:400])
        self.assertFalse(out.get("usable"),
                         "一个只会猜「平静」的候选被判可用 —— 对照逻辑没咬住")


if __name__ == "__main__":
    unittest.main()
