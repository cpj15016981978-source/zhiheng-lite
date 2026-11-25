# -*- coding: utf-8 -*-
"""教师标注 + 一致性过滤 + 人工抽检队列（步骤 2、3）。

不训练、不联网也能跑：默认 --predictor rules 直接用现有规则引擎
（pai/affect.detect_affect）当"教师"，把整条流水线先跑通；
要换真教师模型时用 --predictor cmd，由你给一条读 stdin 打标签的命令。

三道防线，都是这个 session 里反复出现的那类问题的直接对策：
  ① 枚举校验：教师输出越界一律丢弃（Mirrai 的 warm 缺失就是没人挡这个）
  ② 一致性过滤：同一输入采 k 次，不一致的样本进「人工队列」而不是直接进训练集
  ③ 版本戳：教师名、taxonomy 版本、k、时间全部落盘 —— 换教师后数据不能混用

用法：
    python tools/label_emotion.py --in corpus.txt --out labeled.jsonl --k 3
    python tools/label_emotion.py --in corpus.txt --out labeled.jsonl \
        --predictor cmd --cmd "python my_teacher.py" --spotcheck 80
    python tools/label_emotion.py --review queue.jsonl   # 人工填标签，逐条问
"""
from __future__ import annotations

import argparse
import collections
import shlex
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals import emotion_taxonomy as TX      # noqa: E402

DEFAULT_K = 3
SPOTCHECK_PER_CLASS = 50


def text_id(t):
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- 教师
def predictor_rules():
    """用现有规则引擎当教师。离线、零依赖，用来把流程跑通。"""
    from pai import affect
    def run(prompt):
        return str(affect.detect_affect(prompt).get("mood") or TX.FALLBACK_MOOD)
    return run


def predictor_cmd(cmd):
    def run(prompt):
        p = subprocess.run(shlex.split(cmd), shell=False, input=prompt, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        if p.returncode != 0:
            raise RuntimeError("教师命令退出 %s：%s" % (p.returncode, (p.stderr or "")[:200]))
        return (p.stdout or "").strip().splitlines()[0] if (p.stdout or "").strip() else ""
    return run


def build_predictor(name, cmd=None):
    if name == "rules":
        return predictor_rules(), "rules:pai.affect.detect_affect"
    if name == "cmd":
        if not cmd:
            raise SystemExit("--predictor cmd 需要 --cmd")
        return predictor_cmd(cmd), "cmd:" + hashlib.sha256(cmd.encode()).hexdigest()[:10]
    raise SystemExit("未知 predictor：%s" % name)


# ---------------------------------------------------------------- 采样与一致性
def sample_label(predict, text, k):
    """同一输入采 k 次。返回 (多数标签, 是否一致, 全部样本)。"""
    outs = []
    for _ in range(max(1, k)):
        try:
            outs.append(predict(text))
        except Exception:
            continue
    valid, bad = TX.validate_labels(outs)
    if not valid:
        return None, False, outs, bad
    valid_outs = [o for o in outs if TX.is_valid(o)]
    if not valid_outs:
        return None, False, outs, bad
    cnt = collections.Counter(valid_outs)
    top, n = cnt.most_common(1)[0]
    return top, n == len(valid_outs), outs, bad


def load_corpus(path):
    out = []
    for raw in pathlib.Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        t = raw.strip()
        if not t or t.startswith("#"):
            continue
        # 支持 "已有标签\t文本" 或纯文本两种输入
        parts = t.split("\t", 1)
        if len(parts) == 2 and TX.is_valid(parts[0].strip()):
            out.append({"text": parts[1].strip(), "seed_label": parts[0].strip()})
        else:
            out.append({"text": t, "seed_label": None})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", help="每行一句；或「标签\\t句子」")
    ap.add_argument("--out", default="evals/emotion_labeled.jsonl")
    ap.add_argument("--queue", default="evals/emotion_review_queue.jsonl",
                    help="不一致/越界样本进这里，等人标")
    ap.add_argument("--predictor", default="rules", choices=["rules", "cmd"])
    ap.add_argument("--cmd", default="")
    ap.add_argument("--k", type=int, default=DEFAULT_K)
    ap.add_argument("--spotcheck", type=int, default=SPOTCHECK_PER_CLASS,
                    help="每类抽检条数；0=不抽检（不推荐）")
    ap.add_argument("--review", metavar="QUEUE", help="人工逐条标注队列文件")
    ap.add_argument("--append", action="store_true")
    ns = ap.parse_args(sys.argv[1:] if argv is None else argv)

    # ---- 人工标注模式
    if ns.review:
        return review_mode(pathlib.Path(ns.review))

    if not ns.inp:
        ap.error("需要 --in，或 --review")

    predict, teacher_tag = build_predictor(ns.predictor, ns.cmd or None)
    corpus = load_corpus(ns.inp)
    if not corpus:
        print("FAIL 语料为空", file=sys.stderr)
        return 2

    out_path = ROOT / ns.out if not os.path.isabs(ns.out) else pathlib.Path(ns.out)
    queue_path = ROOT / ns.queue if not os.path.isabs(ns.queue) else pathlib.Path(ns.queue)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    labeled, queued = [], []
    for item in corpus:
        label, agree, samples, bad = sample_label(predict, item["text"], ns.k)
        row = {"id": text_id(item["text"]), "text": item["text"],
               "taxonomy": TX.TAXONOMY_VERSION, "teacher": teacher_tag, "k": ns.k,
               "samples": samples[:ns.k], "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if label is None:
            row["why"] = "教师输出全部越界：%s" % bad
            queued.append(row)
        elif not agree:
            row["why"] = "k 次采样不一致"
            row["label"] = label
            queued.append(row)
        else:
            row["label"] = label
            labeled.append(row)

    mode = "a" if ns.append else "w"
    with out_path.open(mode, encoding="utf-8") as f:
        for r in labeled:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with queue_path.open("w", encoding="utf-8") as f:
        for r in queued:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    dist = collections.Counter(r["label"] for r in labeled)
    print("教师=%s  taxonomy=%s  k=%d" % (teacher_tag, TX.TAXONOMY_VERSION, ns.k))
    print("一致并合法 %d 条 → %s" % (len(labeled), out_path.name))
    print("待人工 %d 条 → %s" % (len(queued), queue_path.name))
    print("\n类别分布：")
    for m in TX.MOODS:
        n = dist.get(m, 0)
        flag = ""
        if n == 0:
            flag = "  [!] 零样本 —— 该类在评测集里必须标「不可测」，不许算进平均分"
        elif n < ns.spotcheck:
            flag = "  [!] 少于抽检下限 %d" % ns.spotcheck
        print("  %-4s %5d%s" % (m, n, flag))
    missing = [m for m in TX.MOODS if dist.get(m, 0) == 0]
    if missing:
        print("\n[!] 有 %d 个标签零样本。要么补语料，要么在评测里显式标不可测 —— "
              "不能让它悄悄进平均分。" % len(missing))
    print("下一步：python tools/make_emotion_evalset.py --in %s" % ns.out)
    return 0


def review_mode(queue):
    """人工逐条标：这是步骤 3 里那个「不可省」的人工环节。"""
    if not queue.exists():
        print("队列不存在：%s" % queue, file=sys.stderr)
        return 2
    rows = [json.loads(l) for l in queue.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not rows:
        print("队列是空的。")
        return 0
    done = []
    print("人工标注 %d 条（回车跳过，q 退出，? 看判据）\n" % len(rows))
    for r in rows:
        print("· %s" % r["text"][:120])
        if r.get("samples"):
            print("  教师各次：%s" % " | ".join(str(x)[:12] for x in r["samples"]))
        try:
            ans = input("  标签[%s]（? 看判据，回车跳过，q 退出）: " % "、".join(TX.MOODS)).strip()
        except EOFError:
            break
        if ans.lower() == "q":
            break
        if ans == "?":
            print(TX.LABEL_GUIDE)
            continue
        if not ans:
            continue
        if not TX.is_valid(ans):
            print("  越界标签，丢弃：%r" % ans)
            continue
        done.append({"id": r["id"], "text": r["text"], "label": ans,
                     "taxonomy": TX.TAXONOMY_VERSION, "teacher": "human",
                     "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
        print("  记：%s" % ans)
    if done:
        out = queue.with_name("emotion_human_labeled.jsonl")
        with out.open("a", encoding="utf-8") as f:
            for d in done:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        print("\n写入 %d 条 → %s" % (len(done), out.name))
        return 0
    print("没有新标注。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
