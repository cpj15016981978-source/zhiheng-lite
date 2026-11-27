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
FIXTURE_ROOT = pathlib.Path(r"D:\DSH\coordinator_eval_workspace")
FILES = {
    "README.md": "# 知衡\n本地优先的 AI 工作台。\n",
    "a.txt": "alpha\n",
    "b.txt": "beta\n",
    "report.md": "# 报告\n本周完成审批链修复。风险：语义搜索未调参。\n",
    "notes.md": "# 笔记\n- 修复 M-9\n- 补 files token\n",
    "meeting.md": "# 会议\n决定先做离线验收，再做版本 tag。\n",
    "records.txt": "周一 10 次\n周二 12 次\n",
    "invoice.md": "金额：1200 元\n日期：2026-11-27\n",
    "data.csv": "name,value\na,1\nb,2\n",
    "app.py": "def add(a, b):\n    return a - b\n",
    "draft_report.md": "# 周报草稿\n这周修了审批与协调者。\n",
    "docs/plan.md": "# 计划\n1. 安全收口\n2. 离线验收\n",
    "inbox/mail.md": "# 邮件\n客户询问交期。\n",
    "inbox/todo.md": "# 待办\n回邮件。\n",
}


def ensure_fixtures(root=FIXTURE_ROOT):
    root = pathlib.Path(root)
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text(text, encoding="utf-8")
    manifest = sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file())
    return root, manifest


MANIFEST = []


def set_manifest(manifest):
    global MANIFEST
    MANIFEST = list(manifest)

TASKS = [
    {"id": 't01', "goal": '读取 a.txt 并告诉我内容', "expect": ['read_text'], "min_steps": 1},
    {"id": 't02', "goal": '列出 workspace 目录下有哪些文件', "expect": ['list_dir'], "min_steps": 1},
    {"id": 't03', "goal": '总结 report.md 的要点', "expect": ['summarize_file'], "min_steps": 1},
    {"id": 't04', "goal": '比较 a.txt 和 b.txt 的差异', "expect": ['read_text'], "min_steps": 2},
    {"id": 't05', "goal": '读取 records.txt，生成一份周报文件', "expect": ['read_text', 'agent_step'], "min_steps": 2},
    {"id": 't06', "goal": '读取 invoice.md，提取金额和日期', "expect": ['read_text'], "min_steps": 1},
    {"id": 't07', "goal": '把 meeting.md 整理成待办清单', "expect": ['read_text', 'agent_step'], "min_steps": 2},
    {"id": 't08', "goal": '检查 app.py 的明显错误并给修改建议', "expect": ['read_text', 'agent_step'], "min_steps": 2},
    {"id": 't09', "goal": '为三天两夜的东莞行程做计划', "expect": ['agent_step'], "min_steps": 1},
    {"id": 't10', "goal": '整理 inbox：先列出文件，再读每个 md 的摘要', "expect": ['list_dir', 'summarize_file'], "min_steps": 2},
    {"id": 't11', "goal": '你好，介绍一下你能做什么', "expect": ['agent_step'], "min_steps": 1},
    {"id": 't12', "goal": '不要执行 shell；只读 a.txt 并报告', "expect": ['read_text'], "min_steps": 1},
    {"id": 't13', "goal": '根据 notes.md 写一份总结到 summary.md', "expect": ['read_text', 'agent_step'], "min_steps": 2},
    {"id": 't14', "goal": '先列出 docs 目录，再读取 docs/plan.md', "expect": ['list_dir', 'read_text'], "min_steps": 2},
    {"id": 't15', "goal": '读取 data.csv，汇总后生成报告', "expect": ['read_text', 'agent_step'], "min_steps": 2},
    {"id": 't16', "goal": '把 inbox 里的每个 md 文件做一句话摘要', "expect": ['list_dir', 'summarize_file'], "min_steps": 2},
    {"id": 't17', "goal": '总结 docs/plan.md，并列出三个风险', "expect": ['summarize_file'], "min_steps": 1},
    {"id": 't18', "goal": '读 draft_report.md，改写成正式周报文件', "expect": ['read_text', 'agent_step'], "min_steps": 2},
    {"id": 't19', "goal": '帮我看看该先做哪件事（信息不足时先问）', "expect": ['agent_step'], "min_steps": 1},
    {"id": 't20', "goal": '做一份完整月报：列目录、读 records.txt 与 notes.md、生成文件', "expect": ['list_dir', 'read_text', 'agent_step'], "min_steps": 3},
    {"id": 't21', "goal": '读取 README.md 并告诉我项目定位', "expect": ['read_text'], "min_steps": 1},
    {"id": 't22', "goal": '列出 docs 目录下的文件', "expect": ['list_dir'], "min_steps": 1},
    {"id": 't23', "goal": '总结 meeting.md 的决议', "expect": ['summarize_file'], "min_steps": 1},
    {"id": 't24', "goal": '比较 notes.md 和 report.md 的内容差异', "expect": ['read_text'], "min_steps": 2},
    {"id": 't25', "goal": '读取 records.txt，生成周报', "expect": ['read_text', 'agent_step'], "min_steps": 2},
    {"id": 't26', "goal": '从 invoice.md 提取金额', "expect": ['read_text'], "min_steps": 1},
    {"id": 't27', "goal": '把 meeting.md 变成待办清单', "expect": ['read_text', 'agent_step'], "min_steps": 2},
    {"id": 't28', "goal": '检查 app.py 并指出一个 bug', "expect": ['read_text', 'agent_step'], "min_steps": 2},
    {"id": 't29', "goal": '安排明天的三件事', "expect": ['agent_step'], "min_steps": 1},
    {"id": 't30', "goal": '整理 inbox 的 md 文件并各写一句摘要', "expect": ['list_dir', 'summarize_file'], "min_steps": 2},
    {"id": 't31', "goal": '你能做什么？', "expect": ['agent_step'], "min_steps": 1},
    {"id": 't32', "goal": '只读 a.txt，不要执行 shell', "expect": ['read_text'], "min_steps": 1},
    {"id": 't33', "goal": '把 notes.md 的内容写成 summary.md', "expect": ['read_text', 'agent_step'], "min_steps": 2},
    {"id": 't34', "goal": '先列 docs，再读 docs/plan.md', "expect": ['list_dir', 'read_text'], "min_steps": 2},
    {"id": 't35', "goal": '读取 data.csv 并生成汇总报告', "expect": ['read_text', 'agent_step'], "min_steps": 2},
    {"id": 't36', "goal": '给 inbox 的 md 文件做摘要', "expect": ['list_dir', 'summarize_file'], "min_steps": 2},
    {"id": 't37', "goal": '总结 docs/plan.md 的风险', "expect": ['summarize_file'], "min_steps": 1},
    {"id": 't38', "goal": '把 draft_report.md 改写成正式周报', "expect": ['read_text', 'agent_step'], "min_steps": 2},
    {"id": 't39', "goal": '信息不足：该先做哪件事？先问我', "expect": ['agent_step'], "min_steps": 1},
    {"id": 't40', "goal": '做月报：列目录，读 records.txt 和 notes.md，生成文件', "expect": ['list_dir', 'read_text', 'agent_step'], "min_steps": 3},
    {"id": 't41', "goal": '读 README.md', "expect": ['read_text'], "min_steps": 1},
    {"id": 't42', "goal": '列一下 workspace 根目录', "expect": ['list_dir'], "min_steps": 1},
    {"id": 't43', "goal": '总结 report.md', "expect": ['summarize_file'], "min_steps": 1},
    {"id": 't44', "goal": '对比 a.txt 与 b.txt', "expect": ['read_text'], "min_steps": 2},
    {"id": 't45', "goal": '把 meeting.md 整理成行动项', "expect": ['read_text', 'agent_step'], "min_steps": 2},
    {"id": 't46', "goal": '提取 invoice.md 的日期', "expect": ['read_text'], "min_steps": 1},
    {"id": 't47', "goal": '审阅 app.py 并给建议', "expect": ['read_text', 'agent_step'], "min_steps": 2},
    {"id": 't48', "goal": '规划一个三天的学习计划', "expect": ['agent_step'], "min_steps": 1},
    {"id": 't49', "goal": '汇总 inbox 里的 md 文件', "expect": ['list_dir', 'summarize_file'], "min_steps": 2},
    {"id": 't50', "goal": '你好，介绍一下你的能力', "expect": ['agent_step'], "min_steps": 1},
    {"id": 't51', "goal": '读取 data.csv', "expect": ['read_text'], "min_steps": 1},
    {"id": 't52', "goal": '总结 README.md 的定位', "expect": ['summarize_file'], "min_steps": 1},
    {"id": 't53', "goal": '列出 inbox 目录', "expect": ['list_dir'], "min_steps": 1},
    {"id": 't54', "goal": '根据 notes.md 生成 summary.md', "expect": ['read_text', 'agent_step'], "min_steps": 2},
    {"id": 't55', "goal": '检查 docs/plan.md 并总结', "expect": ['summarize_file'], "min_steps": 1},
    {"id": 't56', "goal": '读取 notes.md', "expect": ['read_text'], "min_steps": 1},
    {"id": 't57', "goal": '总结 data.csv 的内容', "expect": ['summarize_file'], "min_steps": 1},
    {"id": 't58', "goal": '根据 meeting.md 写会议纪要文件', "expect": ['read_text', 'agent_step'], "min_steps": 2},
    {"id": 't59', "goal": '我不知道先做什么，先问我', "expect": ['agent_step'], "min_steps": 1},
    {"id": 't60', "goal": '完整月报：列目录，读 records.txt 与 notes.md，汇总并生成文件', "expect": ['list_dir', 'read_text', 'agent_step'], "min_steps": 3},
]

PROMPT = """你是知衡的顺序协调者。只输出一个 JSON 对象，不要 Markdown，不要解释。
JSON 必须符合：
{"goal":"目标","steps":[{"id":"s1","action":"read_text|list_dir|summarize_file|agent_step","description":"这一步做什么","path":"可选路径","depends_on":["s0"]}]}
规则：
- 1-8 步；id 唯一；depends_on 只能引用前面的 id。
- 读文件内容用 read_text。
- 要摘要/提炼要点用 summarize_file。
- 要看目录用 list_dir。
- 生成、改写、判断、给建议、写文件、闲聊、信息不足时一律用 agent_step。
- 摘要不能用 read_text 代替；生成不能只用 summarize_file。
- 信息不足时，第一步就 agent_step 先问，不要先读文件。
- 只可使用下面清单里的路径；不要发明路径。
- 不要发明 shell、网络或其他动作。
可用路径：
%s
目标：%s
"""


def ask(model, goal, url="http://127.0.0.1:11434", manifest=None):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT % ("\n".join("- " + x for x in (manifest or MANIFEST)), goal)}],
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
    ap.add_argument("--fixtures", default=str(FIXTURE_ROOT))
    ns = ap.parse_args()
    _root, manifest = ensure_fixtures(ns.fixtures)
    set_manifest(manifest)
    tasks = TASKS[:ns.limit] if ns.limit else TASKS
    rows = []
    for t in tasks:
        t0 = time.time()
        err = ""
        try:
            raw = ask(ns.model, t["goal"], ns.url, manifest)
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
