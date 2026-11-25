# -*- coding: utf-8 -*-
"""C6 校准：10 道自事实题，一次真模型调用，记录 Brier/accuracy 到 meta。

不写 episode、不写 world；只写 meta self.calibration。
"""
import argparse
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


QUESTIONS = [
    ("1", "当前 webapp 监听端口是多少？", "8753"),
    ("2", "AI 的名字是什么？", "吹雪"),
    ("3", "用户要求怎么称呼他？", "大佬"),
    ("4", "用户还允许被叫阿明吗？", "不"),
    ("5", "当前身份版本是多少？", "0"),
    ("6", "默认是否允许联网？", "不"),
    ("7", "AI 能否自己批准自己？", "不"),
    ("8", "记忆库文件名是什么？", "memory.db"),
    ("9", "自写原则是否允许无理由修改？", "不"),
    ("10", "这次之前校准过吗？", "未"),
]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "memory.db"))
    ap.add_argument("--max-cost", type=float, default=0.02)
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)

    if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("MINIMAX_API_KEY")):
        print("SKIP 没有 OPENAI_API_KEY / MINIMAX_API_KEY")
        return 2
    if not (os.environ.get("PAI_MODEL_PROVIDER") or "").strip():
        print("SKIP 需要 PAI_MODEL_PROVIDER")
        return 2

    from pai.memory import Memory
    from pai import model_gateway as M10
    from pai import self_knowledge as SK

    mem = Memory(ns.db)
    try:
        lines = "\n".join("%s. %s" % (i, q) for i, q, _ in QUESTIONS)
        prompt = (
            "你是吹雪。只根据下面已知事实回答，不要编。\n"
            "已知：AI 名字=吹雪；用户称呼=大佬；用户不再叫阿明；"
            "webapp 端口=8753；身份版本=0；默认不出网；不能自己批准自己；"
            "记忆库=memory.db；自写原则修改必须有理由；此前未做过 C6 校准。\n"
            "回答下面 10 题。只输出 JSON 数组，每项 "
            "{\"id\":\"1\",\"answer\":\"...\",\"confidence\":0.0}，confidence 在 0..1。\n"
            + lines
        )
        outs = M10.complete(prompt, models=None)
        if not outs:
            print("ERROR 模型无输出")
            return 1
        out = outs[0]
        text = str(out.get("output") or "")
        cost = float(out.get("cost") or 0.0)
        if cost > ns.max_cost:
            print("ERROR 超预算 %.6f > %.6f" % (cost, ns.max_cost))
            return 1
        m = re.search(r"\[[\s\S]*\]", text)
        if not m:
            print("ERROR 模型没有输出 JSON：" + text[:300])
            return 1
        try:
            rows = json.loads(m.group(0))
        except ValueError as e:
            print("ERROR JSON 解析失败：%s" % e)
            return 1
        by_id = {str(x.get("id")): x for x in rows if isinstance(x, dict)}
        items = []
        brier_sum = 0.0
        correct = 0
        for qid, q, expected in QUESTIONS:
            x = by_id.get(qid) or {}
            ans = str(x.get("answer") or "")
            conf = max(0.0, min(1.0, float(x.get("confidence") or 0.0)))
            ok = expected in ans
            correct += 1 if ok else 0
            brier_sum += (conf - (1.0 if ok else 0.0)) ** 2
            items.append({"id": qid, "q": q, "answer": ans,
                          "expected": expected, "ok": ok, "confidence": conf})
        n = len(QUESTIONS)
        brier = brier_sum / n
        accuracy = correct / n
        cal = SK.record_calibration(mem, brier, n, accuracy)
        res = {"ok": True, "brier": round(brier, 4), "n": n,
               "accuracy": round(accuracy, 4), "cost": cost,
               "tokens": out.get("tokens", 0), "items": items,
               "calibration": cal}
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    finally:
        mem.conn.close()


if __name__ == "__main__":
    sys.exit(main())
