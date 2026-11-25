# -*- coding: utf-8 -*-
"""级联路由准确率观察工具（只读，不改任何数据）。

读取 trace_store 落盘的 data/traces/trace-YYYYMMDD.jsonl，统计级联调度：
覆盖率、路由解析成功率、标签分布（仅成功判定）、fail-closed 次数、降级率、
实际模型分布，并列出最近样本供人工抽查"分类判得对不对"。

用法：
    python tools/cascade_stats.py [--dir DIR] [--days N] [--last N] [--json]

说明：
  - 机器只能算"解析成功率/分布"这类硬指标；"分类是否合理"要靠 --last 的
    用户输入摘要人工抽查，本工具不替你判断语义对错。
  - 只连本地文件，不出网；trace_store 未开启或目录不存在时返回空统计。
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import pathlib

DEFAULT_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "traces"


def load_records(directory=None, days=7):
    """读取最近 days 天的 trace 记录；坏行跳过，不抛异常。"""
    base = pathlib.Path(directory or DEFAULT_DIR)
    if not base.is_dir():
        return []
    cutoff = datetime.date.today() - datetime.timedelta(days=max(0, int(days) - 1))
    out = []
    for path in sorted(base.glob("trace-*.jsonl")):
        try:
            day = datetime.datetime.strptime(path.stem.replace("trace-", ""), "%Y%m%d").date()
        except ValueError:
            continue
        if day < cutoff:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def analyze(records):
    """把 trace 记录汇总成观察指标（纯函数，便于回归测试）。"""
    recs = list(records or [])
    casc = []
    for r in recs:
        t = r.get("trace")
        if isinstance(t, dict) and isinstance(t.get("cascade"), dict):
            casc.append((r, t["cascade"]))

    ok_list = [c for _, c in casc if c.get("route_ok") is True]
    fail_n = sum(1 for _, c in casc if c.get("route_ok") is not True)
    levels = collections.Counter(str(c.get("route") or "?") for c in ok_list)
    chosen = collections.Counter(
        str(c.get("chosen_model") or c.get("chosen") or "?") for _, c in casc)
    prompt_modes = collections.Counter(
        str(c.get("prompt_mode") or "full") for _, c in casc)
    degraded = sum(1 for _, c in casc if c.get("degraded") is True)

    def rate(part, whole):
        return round(100.0 * part / whole, 1) if whole else 0.0

    total = len(recs)
    n = len(casc)
    return {
        "records_total": total,
        "cascade_hits": n,
        "coverage_pct": rate(n, total),
        "parse_ok": len(ok_list),
        "parse_fail": fail_n,
        "parse_rate_pct": rate(len(ok_list), n),
        "levels": dict(levels.most_common()),
        "degraded": degraded,
        "degrade_rate_pct": rate(degraded, n),
        "models": dict(chosen.most_common()),
        "prompt_modes": dict(prompt_modes.most_common()),
        "samples": [
            {
                "ts": r.get("ts") or "",
                "prompt": ("(已脱敏)" if r.get("prompt") is None
                           else str(r.get("prompt") or "")[:60]),
                "route": c.get("route") or "?",
                "route_ok": c.get("route_ok") is True,
                "chosen": str(c.get("chosen_model") or c.get("chosen") or "?"),
                "prompt_mode": c.get("prompt_mode") or "full",
                "degraded": c.get("degraded") is True,
                "steps": c.get("trace_steps") or [],
            }
            for r, c in casc[-200:]
        ],
    }


def render(s, last=10):
    """输出人类可读报告。"""
    lines = ["=== 知衡级联路由观察 ==="]
    lines.append("chat 记录 %d 条 | cascade 命中 %d 条 (覆盖率 %.1f%%)" %
                 (s["records_total"], s["cascade_hits"], s["coverage_pct"]))
    lines.append("路由解析成功 %d/%d (%.1f%%) | fail-closed 落主力 %d 次" %
                 (s["parse_ok"], s["cascade_hits"], s["parse_rate_pct"], s["parse_fail"]))
    lines.append("降级 %d 次 (%.1f%%)" % (s["degraded"], s["degrade_rate_pct"]))
    lines.append("标签分布(仅成功判定): " +
                 (", ".join("%s=%d" % (k, v) for k, v in s["levels"].items()) or "无"))
    lines.append("实际模型: " +
                 (", ".join("%s=%d" % (k, v) for k, v in s["models"].items()) or "无"))
    lines.append("prompt 壳: " +
                 (", ".join("%s=%d" % (k, v) for k, v in
                            s.get("prompt_modes", {}).items()) or "无"))
    lines.append("")
    lines.append("--- 最近 %d 条（抽查分类是否合理）---" % int(last))
    samples = s["samples"][-int(last):]
    if not samples:
        lines.append("(暂无 cascade 记录；确认 PAI_TRACE_STORE=on 且已发过对话)")
    for x in samples:
        flag = "OK" if x["route_ok"] else "FB"  # FB = fail-closed
        deg = " 降级" if x["degraded"] else ""
        lines.append("%s | %-7s %s | %-28s | %-13s | %s%s" %
                     (x["ts"][-19:], x["route"], flag, x["chosen"][:28],
                      x.get("prompt_mode") or "full", x["prompt"], deg))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="级联路由准确率观察（只读）")
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help="trace 目录（默认 spine/data/traces）")
    ap.add_argument("--days", type=int, default=7, help="统计最近 N 天（默认 7）")
    ap.add_argument("--last", type=int, default=10, help="列出最近 N 条样本（默认 10）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非文本")
    args = ap.parse_args(argv)

    stats = analyze(load_records(args.dir, args.days))
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print(render(stats, args.last))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
