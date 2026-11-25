# -*- coding: utf-8 -*-
"""多模型对比：同题问多个 provider:model，串行、失败隔离、先报账。

报账唯一住址 = pai.model_gateway.estimate()/guard_spend()（ADR-080）。
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SPINE = HERE
if not (SPINE / "pai").is_dir():
    raise SystemExit("找不到 pai/：拒绝启动，不兜底到别的树")
sys.path.insert(0, str(SPINE))
from pai import model_gateway as M10  # noqa: E402
if not Path(M10.__file__).resolve().is_relative_to(SPINE):
    raise SystemExit("model_gateway 不在本树：拒绝启动")

RUNS = HERE / "runs"


def estimate(spec, prompt="", opts=None):
    provider, model = M10.resolve_model(spec)
    if provider == "mock":
        return 0.0, True
    est = M10.estimate(prompt, provider, model, opts)
    return est["cost_cap"], est["priced"]


def report(prompt, specs, spend_ok=False, opts=None):
    rows, total, ok = [], 0.0, True
    for spec in specs:
        provider, model = M10.resolve_model(spec)
        est = M10.estimate(prompt, provider, model, opts) if provider != "mock" else None
        cap = 0.0 if est is None else est["cost_cap"]
        priced = True if est is None else est["priced"]
        line = "[报账] provider=mock 不花钱、不出网" if est is None else est["line"]
        rows.append({"spec": spec, "provider": provider, "model": model,
                     "cap": cap, "priced": priced, "line": line})
        if not priced:
            ok = False
        else:
            total += cap
    print(json.dumps({"models": rows, "total_cap": round(total, 6),
                      "all_priced": ok}, ensure_ascii=False, indent=2))
    if not ok:
        print("有模型未登记价目：未登记 = 不许跑。")
        return False
    print("上限合计：%.6f 元（每模型 1 次，按题干实测字符数）" % total)
    if not spend_ok:
        print("未报账/未确认：加 --spend-ok 才允许真调用。")
        return False
    return True


def run(prompt, specs, dry_run=False, spend_ok=False):
    if dry_run:
        report(prompt, specs, spend_ok=False)
        return 0
    if not report(prompt, specs, spend_ok):
        return 2
    if not RUNS.is_dir():
        print("运行目录不存在：%s（不静默自建，先手动建）" % RUNS)
        return 3
    outs, total = [], 0.0
    for s in specs:
        provider, model = M10.resolve_model(s)
        item = {"spec": s, "provider": provider, "model": model}
        try:
            one = M10.complete(prompt, models=(s,))[0]
            item.update({"output": one.get("output", ""), "cost": one.get("cost", 0),
                         "tokens": one.get("tokens", 0), "priced": one.get("priced", False),
                         "latency": one.get("latency", 0),
                         "cost_estimate": one.get("cost_estimate"),
                         "spend_report": one.get("spend_report")})
            total += one.get("cost", 0)
        except Exception as e:
            item["error"] = "%s: %s" % (type(e).__name__, e)
        outs.append(item)
    result = {"time": datetime.datetime.now().isoformat(timespec="seconds"),
              "prompt": prompt, "models": outs, "cost": round(total, 6)}
    path = RUNS / ("multi_%s.json" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("实付：%.6f 元" % total)
    print("明细：%s" % path)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--models", nargs="+", required=True,
                    help="provider:model，例如 openai:MiniMax-M3 deepseek:deepseek-chat")
    ap.add_argument("--spend-ok", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    return run(args.prompt, args.models, args.dry_run, args.spend_ok)


if __name__ == "__main__":
    sys.exit(main())
