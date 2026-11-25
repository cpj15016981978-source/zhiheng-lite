# -*- coding: utf-8 -*-
"""情绪小模型七步流水线（CPU、纯 Python）。

1. 标签卡：六态 warm/playful/nostalgic/melancholy/happy/distant
2. 数据：evals/emotion_seed.json
3. 基线：现有关键词规则
4. 训练：字符 1-2 gram + 多项式朴素贝叶斯
5. 评测：留出集准确率/混淆矩阵
6. 导出：pai/emotion_model.json
7. 接入：pai/emotion_model.py，低置信回退规则
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import math
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pai import mirrai_emotion as E  # noqa: E402

SEED = ROOT / "evals" / "emotion_seed.json"
MODEL = ROOT / "pai" / "emotion_model.json"
REPORT = ROOT / "reports" / "emotion_model_report.json"
LABELS = list(E.STATES)


def norm(text):
    s = str(text or "").lower()
    s = re.sub(r"[\s，。！？、,.!?;；:：\"'“”‘’（）()\[\]【】]", "", s)
    return s


def tokens(text):
    s = norm(text)
    out = list(s)
    out += [s[i:i + 2] for i in range(len(s) - 1)]
    return out


def split(rows):
    train, test = [], []
    by = collections.defaultdict(list)
    for r in rows:
        by[r["label"]].append(r)
    for label in LABELS:
        items = by[label]
        for i, r in enumerate(items):
            (test if i % 3 == 2 else train).append(r)
    return train, test


def train(rows):
    counts = {label: collections.Counter() for label in LABELS}
    totals = {label: 0 for label in LABELS}
    priors = {label: 0 for label in LABELS}
    for r in rows:
        label = r["label"]
        priors[label] += 1
        for tok in tokens(r["text"]):
            counts[label][tok] += 1
            totals[label] += 1
    n = sum(priors.values())
    log_prior = {label: math.log((priors[label] + 1) / (n + len(LABELS))) for label in LABELS}
    return {"labels": LABELS, "log_prior": log_prior, "totals": totals,
            "counts": {k: dict(v) for k, v in counts.items()},
            "vocab": sorted({t for v in counts.values() for t in v}),
            "trained_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": str(SEED)}


def predict(model, text):
    toks = [t for t in tokens(text) if t in set(model["vocab"])]
    vocab_size = max(1, len(model["vocab"]))
    scores = {}
    for label in model["labels"]:
        score = model["log_prior"][label]
        total = model["totals"][label] + vocab_size
        cnt = model["counts"].get(label, {})
        for t in toks:
            score += math.log((cnt.get(t, 0) + 1) / total)
        scores[label] = score
    best = max(scores, key=scores.get)
    mx = max(scores.values())
    ex = {k: math.exp(v - mx) for k, v in scores.items()}
    z = sum(ex.values())
    return best, ex[best] / z


def baseline(text):
    return E.match_state(text) or "warm"


def evaluate(model, rows):
    conf = collections.Counter()
    detail = []
    ok = base = hyb = 0
    for r in rows:
        got, p = predict(model, r["text"])
        b = baseline(r["text"])
        h = b if b != "warm" else (got if p >= 0.22 else "warm")
        ok += got == r["label"]
        base += b == r["label"]
        hyb += h == r["label"]
        conf[(r["label"], got)] += 1
        detail.append({"text": r["text"], "want": r["label"], "got": got,
                       "hybrid": h, "p": round(p, 4)})
    return {"n": len(rows), "model_acc": round(ok / len(rows), 4),
            "baseline_acc": round(base / len(rows), 4),
            "hybrid_acc": round(hyb / len(rows), 4),
            "hybrid_threshold": 0.22,
            "confusion": {"%s->%s" % k: v for k, v in sorted(conf.items())},
            "detail": detail}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    rows = json.loads(SEED.read_text(encoding="utf-8"))
    train_rows, test_rows = split(rows)
    model = train(train_rows)
    report = evaluate(model, test_rows)
    model["metrics"] = report
    report["train_n"] = len(train_rows)
    report["test_n"] = len(test_rows)
    report["pipeline"] = ["labels", "data", "baseline", "train", "evaluate", "export", "wire"]
    if not args.dry_run:
        MODEL.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
        REPORT.parent.mkdir(exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("model_acc=%.3f baseline_acc=%.3f hybrid_acc=%.3f n=%d -> %s" % (
        report["model_acc"], report["baseline_acc"], report["hybrid_acc"], report["test_n"], MODEL))
    return 0 if report["hybrid_acc"] >= report["baseline_acc"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
