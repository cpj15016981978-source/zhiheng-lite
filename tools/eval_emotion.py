# -*- coding: utf-8 -*-
"""情绪判定评分 —— 带【强制对照】的评测器。

三条硬规矩，都来自这个项目自己定下但没执行的元规则：
  ① 不高于零信息对照的指标直接作废（METRICS_v2.md:7）
     → 内置三个 baseline：多数类、随机、现有规则引擎。候选必须【全部打过】才算数。
  ② 样本不足的类记「不可测」，不许因为"跑通了"就报平均分（METRICS_v2.md:29）
     → 零样本类从 macro 平均里剔除，并显式列出。
  ③ 训练/评测必须物理隔离
     → 启动即查 id 交集与近似重复，发现泄漏直接退出 2，不给分数。

用法：
    python tools/eval_emotion.py --eval evals/emotion_eval.jsonl --predictor rules
    python tools/eval_emotion.py --eval ... --predictor cmd --cmd "python my_model.py" \
                                 --train evals/emotion_train.jsonl
"""
from __future__ import annotations

import argparse
import collections
import shlex
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import json
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals import emotion_taxonomy as TX      # noqa: E402
from tools import make_emotion_evalset as MK  # noqa: E402

MIN_PER_CLASS = 5          # 低于此，该类的单类指标只作参考，不进 macro 平均
MIN_MEASURABLE_CLASSES = 4  # macro 至少要覆盖这么多类，否则那个数字不代表整体
LEAK_THRESHOLD = 0.75      # 与切分脚本一致


# ---------------------------------------------------------------- 预测器
def predictor_rules():
    from pai import affect
    def run(texts):
        return [str(affect.detect_affect(t).get("mood") or TX.FALLBACK_MOOD) for t in texts]
    return run, "rules:pai.affect.detect_affect"


def predictor_cmd(cmd):
    def run(texts):
        outs = []
        for t in texts:
            import subprocess
            p = subprocess.run(shlex.split(cmd), shell=False, input=t, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120)
            line = (p.stdout or "").strip().splitlines()
            outs.append(line[0] if line else TX.FALLBACK_MOOD)
        return outs
    return run, "cmd:" + cmd[:60]


def predictor_majority(train_labels):
    maj = collections.Counter(train_labels).most_common(1)[0][0] if train_labels else TX.FALLBACK_MOOD
    return (lambda texts: [maj] * len(texts)), "baseline_majority(%s)" % maj


def predictor_random(seed=20261117):
    def run(texts):
        rnd = random.Random(seed)
        return [rnd.choice(list(TX.MOODS)) for _ in texts]
    return run, "baseline_random(seed=%d)" % seed


# ---------------------------------------------------------------- 指标
def score(golds, preds, min_per_class=MIN_PER_CLASS):
    """返回 macro-F1（仅可测类）、逐类 P/R/F1、准确率、混淆对。"""
    labels = list(TX.MOODS)
    per = {}
    for lab in labels:
        tp = sum(1 for g, p in zip(golds, preds) if g == lab and p == lab)
        fp = sum(1 for g, p in zip(golds, preds) if g != lab and p == lab)
        fn = sum(1 for g, p in zip(golds, preds) if g == lab and p != lab)
        n = sum(1 for g in golds if g == lab)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        per[lab] = {"n": n, "precision": round(prec, 4), "recall": round(rec, 4),
                    "f1": round(f1, 4), "measurable": n >= min_per_class}
    meas = [l for l in labels if per[l]["measurable"]]
    macro = round(sum(per[l]["f1"] for l in meas) / len(meas), 4) if meas else None
    acc = round(sum(1 for g, p in zip(golds, preds) if g == p) / len(golds), 4) if golds else None
    conf = collections.Counter((g, p) for g, p in zip(golds, preds) if g != p)
    return {"macro_f1": macro, "accuracy": acc, "per_class": per,
            "measurable_classes": meas,
            "unmeasurable_classes": [l for l in labels if not per[l]["measurable"]],
            "top_confusions": [{"gold": g, "pred": p, "n": n}
                               for (g, p), n in conf.most_common(8)]}


# ---------------------------------------------------------------- 泄漏检查
def leak_check(eval_rows, train_rows):
    """返回问题列表。空 = 干净。"""
    problems = []
    if not train_rows:
        return ["没有提供 --train，无法做泄漏检查 —— 拒绝给分。"
                "（本评测器不允许在没验证隔离的情况下宣布指标）"]
    ids_e = {r["id"] for r in eval_rows}
    ids_t = {r["id"] for r in train_rows}
    if ids_e & ids_t:
        problems.append("id 交集 %d 条：同一句同时在训练与评测" % len(ids_e & ids_t))
    near = 0
    te = [(r["text"], MK.ngrams(r["text"])) for r in eval_rows]
    tt = [(r["text"], MK.ngrams(r["text"])) for r in train_rows]
    worst = None
    for _, ge in te:
        for tx, gt in tt:
            j = MK.jaccard(ge, gt)
            if j >= LEAK_THRESHOLD:
                near += 1
                if worst is None or j > worst[0]:
                    worst = (j, tx)
                break
    if near:
        problems.append("近似重复 %d 条（阈值 %.2f）：评测集里有训练句的换皮版本。"
                        "最像的一条 Jaccard=%.2f：%s"
                        % (near, LEAK_THRESHOLD, worst[0], worst[1][:40]))
    return problems


# ---------------------------------------------------------------- 同源检查
def same_source_check(eval_rows, cand_tag):
    """候选不得与产生 gold 的教师同源，否则分数是被构造出来的。

    这是本项目反复踩的同一类坑的通用形态：判分依据出现在被评对象里。
    eval_suite 的 must 词写在人格段中、C1 把标准答案注进 prompt 再回找、
    以及本 kit 第一版「用 detect_affect 标 gold，再拿 detect_affect 当候选」——
    都是同一个错误。这里把它变成硬失败。
    """
    teachers = {str(r.get("teacher") or "") for r in eval_rows}
    teachers.discard("")
    hits = []
    for t in teachers:
        if t and (t == cand_tag or t in cand_tag or cand_tag in t):
            hits.append(t)
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", required=True)
    ap.add_argument("--train", default="", help="训练池，用于泄漏检查与多数类基线")
    ap.add_argument("--predictor", default="rules", choices=["rules", "cmd", "const"])
    ap.add_argument("--cmd", default="")
    ap.add_argument("--const", default=TX.FALLBACK_MOOD, help="固定标签预测器（调试用）")
    ap.add_argument("--min-per-class", type=int, default=MIN_PER_CLASS,
                    help="单类达到此评测样本量才计入 macro")
    ap.add_argument("--allow-same-source", action="store_true",
                    help="仅用于自检/演示；真实评测不要用")
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(sys.argv[1:] if argv is None else argv)

    def load(p):
        pp = pathlib.Path(p) if pathlib.Path(p).is_absolute() else ROOT / p
        if not pp.exists():
            return []
        return [json.loads(l) for l in pp.read_text(encoding="utf-8", errors="replace")
                .splitlines() if l.strip()]

    ev = load(ns.eval)
    if not ev:
        print("FAIL 评测集为空：%s" % ns.eval, file=sys.stderr)
        return 2
    tr = load(ns.train) if ns.train else []

    problems = leak_check(ev, tr)
    if problems:
        print("FAIL 泄漏检查未通过，拒绝出分：", file=sys.stderr)
        for x in problems:
            print("   - " + x, file=sys.stderr)
        return 2

    golds = [r["label"] for r in ev]
    texts = [r["text"] for r in ev]

    if ns.predictor == "rules":
        cand, cand_tag = predictor_rules()
    elif ns.predictor == "const":
        cand, cand_tag = (lambda ts: [ns.const] * len(ts)), "const(%s)" % ns.const
    else:
        cand, cand_tag = predictor_cmd(ns.cmd)

    hits = same_source_check(ev, cand_tag)
    if hits and not ns.allow_same_source:
        print("FAIL 候选与标注教师同源，拒绝出分：", file=sys.stderr)
        for h in hits:
            print("   - gold 由 %s 产生，候选也是 %s" % (h, cand_tag), file=sys.stderr)
        print("   这样的分数是被构造出来的，与真实能力无关。"
              "要拿到可信数字，gold 必须由【另一个】来源产生（人工标注，或不同的教师模型）。"
              "（自检可用 --allow-same-source）", file=sys.stderr)
        return 2

    results = {}
    for tag, fn in (("candidate", cand),
                    ("baseline_majority", predictor_majority(golds)[0]),
                    ("baseline_random", predictor_random()[0]),
                    ("baseline_rules", predictor_rules()[0] if ns.predictor != "rules" else None)):
        if fn is None:
            continue
        results[tag] = score(golds, fn(texts), ns.min_per_class)

    c = results["candidate"]
    rivals = {k: v for k, v in results.items() if k.startswith("baseline")}
    # 候选本身就是 rules 时，rules 基线与候选同源同分，剔除，不参与「跑赢」判定
    beats = {k: (c["macro_f1"] is not None and v["macro_f1"] is not None
                 and c["macro_f1"] > v["macro_f1"]) for k, v in rivals.items()}

    no_measurable = not c["measurable_classes"]
    thin_coverage = (not no_measurable
                     and len(c["measurable_classes"]) < MIN_MEASURABLE_CLASSES)

    if not ns.json:
        print("评测集 %d 条，taxonomy=%s，候选=%s" % (len(ev), TX.TAXONOMY_VERSION, cand_tag))
        if hits:
            print("[!] 同源放行：gold 与候选同为 %s —— 本数字仅可用于自检" % cand_tag)
        print("可测下限：每类 ≥ %d 条；macro 需覆盖 ≥ %d 类才代表整体"
              % (ns.min_per_class, MIN_MEASURABLE_CLASSES))
        print("\n候选：macro-F1=%s（覆盖 %d/%d 类）  准确率=%s（覆盖全部 %d 条）"
              % (c["macro_f1"], len(c["measurable_classes"]), len(TX.MOODS),
                 c["accuracy"], len(ev)))
        print("%-12s %-9s %-9s %s" % ("对照", "macro-F1", "准确率", "候选是否更高"))
        for k, v in rivals.items():
            print("%-12s %-9s %-9s %s" % (k, v["macro_f1"], v["accuracy"],
                                           "[OK]" if beats.get(k) else "[X] 未跑赢"))
        print("\n逐类：")
        print("  %-4s %5s %6s %6s %6s %s" % ("类", "n", "P", "R", "F1", ""))
        for m in TX.MOODS:
            s = c["per_class"][m]
            flag = "" if s["measurable"] else "  ← 样本不足，未计入 macro"
            print("  %-4s %5d %6s %6s %6s%s" % (m, s["n"], s["precision"],
                                                 s["recall"], s["f1"], flag))
        if c["unmeasurable_classes"]:
            print("\n[!] 不可测类（已从 macro 剔除）：%s" % "、".join(c["unmeasurable_classes"]))
        if c["top_confusions"]:
            print("最常见误判：")
            for x in c["top_confusions"][:5]:
                print("   %s → %s  %d 次" % (x["gold"], x["pred"], x["n"]))

    all_beat = bool(beats) and all(beats.values())
    ok = (all_beat and c["macro_f1"] is not None and not no_measurable
          and not thin_coverage)
    if no_measurable:
        verdict = ("INCONCLUSIVE 没有任何一类达到可测下限（每类 ≥ %d 条）—— "
                   "这不是「模型差」，是「测不了」。补数据，别改模型。" % ns.min_per_class)
        rc = 3
    elif thin_coverage:
        verdict = ("INCONCLUSIVE macro 只覆盖 %d/%d 类（下限 %d 类）—— "
                   "这个分数只代表那一两个类，不能当整体结论用。"
                   "「在 1 个类上拿到 1.0」和「分类能力强」是两回事。"
                   % (len(c["measurable_classes"]), len(TX.MOODS), MIN_MEASURABLE_CLASSES))
        rc = 3
    elif not hits and c["macro_f1"] is not None and not all_beat:
        verdict = ("FAIL 按 METRICS_v2:7，不高于对照的指标作废 —— 不能用来支撑迭代方向")
        rc = 1
    else:
        verdict = ("PASS 候选跑赢了全部零信息与现有基线" if ok else "FAIL 判定不通过")
        rc = 0 if ok else 1
    out = {"eval_n": len(ev), "candidate": cand_tag, "candidate_metrics": c,
           "baselines": {k: {"macro_f1": v["macro_f1"], "accuracy": v["accuracy"]}
                         for k, v in rivals.items()},
           "beats_all_baselines": all_beat, "usable": ok,
           "no_measurable_class": no_measurable, "thin_coverage": thin_coverage,
           "macro_covers_classes": len(c["measurable_classes"]),
           "same_source_teachers": hits,
           "unmeasurable_classes": c["unmeasurable_classes"]}
    if ns.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("\n%s" % verdict)
    return rc


if __name__ == "__main__":
    sys.exit(main())
