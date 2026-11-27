# -*- coding: utf-8 -*-
"""协调者 JSON 计划评测：固定 schema，跑本地 Ollama 开源模型。

用法：
  python tools/coordinator_eval.py --model qwen2.5:1.5b --out D:/DSH/eval.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time
import urllib.request

SCHEMA = {
    "type": "object",
    "required": ["goal", "steps"],
    "properties": {
        "goal": {"type": "string"},
        "steps": {
            "type": "array", "minItems": 1, "maxItems": 8,
            "items": {
                "type": "object",
                "required": ["id", "action", "description"],
                "properties": {
                    "id": {"type": "string"},
                    "action": {"enum": ["read_text", "list_dir", "summarize_file", "agent_step"]},
                    "description": {"type": "string"},
                    "path": {"type": "string"},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}

ALLOWED = set(SCHEMA["properties"]["steps"]["items"]["properties"]["action"]["enum"])

TASKS = [
    {"id": "t01", "goal": "读取 workspace 里的 a.txt 并告诉我内容", "expect": ["read_text"], "min_steps": 1},
    {"id": "t02", "goal": "列出 workspace 目录下有哪些文件", "expect": ["list_dir"], "min_steps": 1},
    {"id": "t03", "goal": "总结 report.md 的要点", "expect": ["summarize_file"], "min_steps": 1},
    {"id": "t04", "goal": "比较 a.txt 和 b.txt 的差异", "expect": ["read_text"], "min_steps": 2},
    {"id": "t05", "goal": "读取本周记录，生成一份周报文件", "expect": ["read_text", "agent_step"], "min_steps": 2},
    {"id": "t06", "goal": "读取发票汇总.md，提取金额和日期", "expect": ["read_text"], "min_steps": 1},
    {"id": "t07", "goal": "把会议记录.md 整理成待办清单", "expect": ["read_text", "agent_step"], "min_steps": 2},
    {"id": "t08", "goal": "检查 app.py 的明显错误并给修改建议", "expect": ["read_text", "agent_step"], "min_steps": 2},
    {"id": "t09", "goal": "为三天两夜的东莞行程做计划", "expect": ["agent_step"], "min_steps": 1},
    {"id": "t10", "goal": "整理收件箱：先列出文件，再读每个 md 的摘要", "expect": ["list_dir", "summarize_file"], "min_steps": 2},
    {"id": "t11", "goal": "你好，介绍一下你能做什么", "expect": ["agent_step"], "min_steps": 1},
    {"id": "t12", "goal": "不要执行 shell；只读 a.txt 并报告", "expect": ["read_text"], "min_steps": 1},
    {"id": "t13", "goal": "根据 notes.md 写一份总结到 summary.md", "expect": ["read_text", "agent_step"], "min_steps": 2},
    {"id": "t14", "goal": "先列出目录，再读取其中最新的 md 文件", "expect": ["list_dir", "read_text"], "min_steps": 2},
    {"id": "t15", "goal": "读取 data.csv，汇总后生成报告", "expect": ["read_text", "agent_step"], "min_steps": 2},
    {"id": "t16", "goal": "把 inbox 里的每个 md 文件做一句话摘要", "expect": ["list_dir", "summarize_file"], "min_steps": 2},
    {"id": "t17", "goal": "总结 docs/plan.md，并列出三个风险", "expect": ["summarize_file"], "min_steps": 1},
    {"id": "t18", "goal": "读周报草稿，改写成正式周报文件", "expect": ["read_text", "agent_step"], "min_steps": 2},
    {"id": "t19", "goal": "帮我看看该先做哪件事（信息不足时先问）", "expect": ["agent_step"], "min_steps": 1},
    {"id": "t20", "goal": "做一份完整月报：列目录、读三份记录、汇总、生成文件", "expect": ["list_dir", "read_text", "agent_step"], "min_steps": 3},
]

PROMPT = """你是知衡的顺序协调者。只输出一个 JSON 对象，不要 Markdown，不要解释。
JSON 必须符合：
{"goal":"目标","steps":[{"id":"s1","action":"read_text|list_dir|summarize_file|agent_step","description":"这一步做什么","path":"可选路径","depends_on":["s0"]}]}
规则：
- 1-8 步；id 唯一；depends_on 只能引用前面的 id。
- read_text/list_dir/summarize_file 只读；写文件/需要判断时用 agent_step。
- 不要发明 shell、网络或其他动作。
- 路径只能是 workspace 内相对路径。
目标：%s
"""


def ask(model, goal, url="http://127.0.0.1:11434"):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT % goal}],
        "stream": False,
        "format": SCHEMA,
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 600},
    }
    req = urllib.request.Request(url + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read().decode("utf-8"))
    return str((d.get("message") or {}).get("content") or "")


def parse(text):
    try:
        return json.loads(text), ""
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


def score(obj, expect, min_steps):
    checks = {}
    checks["json"] = obj is not None
    if not obj:
        return 0, checks
    checks["goal"] = isinstance(obj.get("goal"), str) and bool(obj["goal"].strip())
    steps = obj.get("steps")
    checks["steps_nonempty"] = isinstance(steps, list) and 1 <= len(steps) <= 8 and len(steps) >= min_steps
    if not isinstance(steps, list):
        return sum(checks.values()), checks
    allowed = True
    ids = []
    deps_ok = True
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            allowed = False; deps_ok = False; continue
        sid = str(s.get("id") or "")
        ids.append(sid)
        if not sid or s.get("action") not in ALLOWED or not str(s.get("description") or "").strip():
            allowed = False
        for d in (s.get("depends_on") or []):
            if str(d) not in ids[:-1]:
                deps_ok = False
    checks["schema"] = allowed
    checks["deps"] = deps_ok
    actions = {str(s.get("action")) for s in steps if isinstance(s, dict)}
    checks["expect"] = set(expect).issubset(actions)
    return sum(1 for v in checks.values() if v), checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5:1.5b")
    ap.add_argument("--url", default="http://127.0.0.1:11434")
    ap.add_argument("--out", default="")
    ap.add_argument("--limit", type=int, default=0)
    ns = ap.parse_args()
    tasks = TASKS[:ns.limit] if ns.limit else TASKS
    rows = []
    for t in tasks:
        t0 = time.time()
        err = ""
        try:
            raw = ask(ns.model, t["goal"], ns.url)
            obj, err = parse(raw)
        except Exception as e:
            raw, obj, err = "", None, "%s: %s" % (type(e).__name__, e)
        points, checks = score(obj, t["expect"], t["min_steps"])
        rows.append({"id": t["id"], "goal": t["goal"], "points": points, "max": 6,
                     "checks": checks, "error": err, "raw": raw[:2000],
                     "seconds": round(time.time() - t0, 2)})
        print("%s %d/6 %s" % (t["id"], points, ",".join(k for k, v in checks.items() if v)), flush=True)
    total = sum(r["points"] for r in rows)
    summary = {"model": ns.model, "tasks": len(rows), "points": total,
               "max": 6 * len(rows), "rate": round(total / (6 * len(rows)), 4) if rows else 0,
               "rows": rows}
    print("TOTAL %d/%d rate=%.2f" % (total, 6 * len(rows), summary["rate"]))
    if ns.out:
        p = pathlib.Path(ns.out); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print("saved", p)


if __name__ == "__main__":
    main()
