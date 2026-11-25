# -*- coding: utf-8 -*-
"""切分训练池 / 独立评测集（步骤 4）—— 重点是【泄漏隔离】。

为什么这个脚本的主要工作不是"随机切一半"，而是查重：
  本 session 在三套系统里看到同一类失败 —— 判分依据出现在被评的 prompt 里
  （eval_suite 的 must 词就在人格段里；C1 把标准答案注入 prompt 再回找）。
  情绪评测最容易以另一种形式重演：训练集里有「我今天好累」，
  评测集里有「我今天真的好累啊」—— 字面不同、实质同一条，
  任何记住表面模式的模型都会拿高分，而那个分数与真实能力无关。

所以这里做三件事：
  ① 精确去重（同 id 只留一条）
  ② 近似去重：字符 n-gram Jaccard ≥ 阈值的，整簇只留一条进评测集，其余踢回训练池
  ③ 分层 + 每类最小样本量：不足的最小样本量的类，在评测里标「不可测」而不是算平均分

用法：
    python tools/make_emotion_evalset.py --in evals/emotion_labeled.jsonl \
        --eval-out evals/emotion_eval.jsonl --train-out evals/emotion_train.jsonl \
        --per-class 10 --threshold 0.75
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals import emotion_taxonomy as TX      # noqa: E402

DEFAULT_PER_CLASS = 10
DEFAULT_THRESHOLD = 0.75
MIN_TEXT_CHARS = 2


def ngrams(t, n=2):
    t = "".join(str(t).split())
    if len(t) < n:
        return {t} if t else set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load(paths):
    rows, seen = [], set()
    for p in paths:
        pp = pathlib.Path(p) if pathlib.Path(p).is_absolute() else ROOT / p
        if not pp.exists():
            print("  ! 跳过不存在的文件：%s" % pp.name)
            continue
        for line in pp.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            d = json.loads(line)
            if not TX.is_valid(d.get("label")):
                continue
            if len(str(d.get("text") or "").strip()) < MIN_TEXT_CHARS:
                continue
            key = d.get("id") or str(d.get("text"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(d)
    return rows


def cluster_dupes(rows, threshold):
    """把近似重复的条目并成一簇。返回 [簇] ，每簇是 rows 的下标列表。"""
    grams = [ngrams(r["text"]) for r in rows]
    parent = list(range(len(rows)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if jaccard(grams[i], grams[j]) >= threshold:
                union(i, j)

    groups = collections.defaultdict(list)
    for i in range(len(rows)):
        groups[find(i)].append(i)
    return list(groups.values())


def split(rows, per_class, threshold):
    """先按近似重复聚簇，再按簇分配到 eval —— 保证同簇不会一半在训练一半在评测。"""
    clusters = cluster_dupes(rows, threshold)
    by_label = collections.defaultdict(list)
    for c in clusters:
        by_label[rows[c[0]]["label"]].append(c)

    eval_idx, train_idx, dup_dropped = [], [], 0
    stats = {}
    for label in TX.MOODS:
        cs = by_label.get(label, [])
        # 每类取 per_class 条进评测：优先取独占簇（不与别的样本近重复的）
        cs.sort(key=lambda c: (len(c), -sum(len(rows[i]["text"]) for i in c)))
        picked, n_eval = set(), 0
        for c in cs:
            if n_eval >= per_class:
                break
            picked.add(id(c))
            eval_idx.extend(c)
            n_eval += 1
            if len(c) > 1:
                dup_dropped += len(c) - 1
        for c in cs:
            if id(c) not in picked:
                train_idx.extend(c)
        stats[label] = {"clusters": len(cs), "eval": sum(1 for i in eval_idx
                                                         if rows[i]["label"] == label),
                        "train": sum(1 for i in train_idx if rows[i]["label"] == label)}
    return eval_idx, train_idx, stats, dup_dropped, len(clusters)


def write(path, rows, idxs):
    p = pathlib.Path(path) if pathlib.Path(path).is_absolute() else ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for i in sorted(idxs):
            f.write(json.dumps(rows[i], ensure_ascii=False) + "\n")
    return p


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", nargs="+", required=True)
    ap.add_argument("--eval-out", default="evals/emotion_eval.jsonl")
    ap.add_argument("--train-out", default="evals/emotion_train.jsonl")
    ap.add_argument("--per-class", type=int, default=DEFAULT_PER_CLASS)
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ns = ap.parse_args(sys.argv[1:] if argv is None else argv)

    rows = load(ns.inp)
    if not rows:
        print("FAIL 没有可用标注（检查 label 是否在枚举内）", file=sys.stderr)
        return 2
    ev, tr, stats, dup_dropped, n_clusters = split(rows, ns.per_class, ns.threshold)
    ep = write(ns.eval_out, rows, ev)
    tp = write(ns.train_out, rows, tr)

    print("总标注 %d 条，近似重复聚成 %d 簇（阈值 %.2f）" % (len(rows), n_clusters, ns.threshold))
    print("评测集 %d 条 → %s" % (len(ev), ep.name))
    print("训练池 %d 条 → %s" % (len(tr), tp.name))
    print("因近重复被挡在评测集外 %d 条（同簇只留一条，防止「换个说法的同一句」冒充独立样本）"
          % dup_dropped)
    print("\n%-4s %6s %6s %6s" % ("类", "簇", "评测", "训练"))
    unmeasurable = []
    for m in TX.MOODS:
        s = stats.get(m, {"clusters": 0, "eval": 0, "train": 0})
        print("%-4s %6d %6d %6d%s" % (m, s["clusters"], s["eval"], s["train"],
                                       "   [!] 不可测" if s["eval"] < 1 else ""))
        if s["eval"] < 1:
            unmeasurable.append(m)
    if unmeasurable:
        print("\n[!] 以下类零评测样本，评分时必须标「不可测」，不得计入平均分：%s"
              % "、".join(unmeasurable))
    overlap = set(rows[i]["id"] for i in ev) & set(rows[i]["id"] for i in tr)
    print("\n自检：训练/评测 id 交集 = %d（必须为 0）" % len(overlap))
    if overlap:
        print("FAIL 泄漏：同一批样本同时出现在训练与评测", file=sys.stderr)
        return 1
    print("下一步：python tools/eval_emotion.py --eval %s --predictor rules" % ns.eval_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
