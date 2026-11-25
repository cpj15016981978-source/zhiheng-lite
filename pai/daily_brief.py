# -*- coding: utf-8 -*-
"""工厂每日简报：只读 CSV，输出今日要事/风险/建议。"""
from __future__ import annotations

import os

from pai import paths

import csv
import datetime
import pathlib

from pai import atomic as AT
from pai import job_health as JH

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = pathlib.Path(os.environ.get("PAI_FACTORY_DATA") or (paths.DATA_DIR / "factory_pilot" / "data"))
OUT = pathlib.Path(os.environ.get("PAI_FACTORY_OUT") or (paths.DATA_DIR / "factory_pilot" / "out"))

CAPABILITY = {
    "name": "工厂每日简报",
    "switch_env": "PAI_FACTORY_BRIEF",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "数据文件缺失=明确写无数据，不编造；解析失败=跳过坏行并留计数",
    "approval": "ADR-046：只读本地 CSV，不新增开关",
}

DONE = ("已出货", "已完成", "已关闭", "done", "closed")


def _rows(name):
    p = DATA / name
    if not p.is_file():
        return [], p
    try:
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f)), p
    except Exception:
        return [], p


def _date(s):
    try:
        return datetime.date.fromisoformat(str(s or "").strip()[:10])
    except Exception:
        return None


def _is_done(row):
    return str(row.get("status") or "").strip().lower() in DONE or str(row.get("status") or "") in DONE


def build(today=None):
    today = today or datetime.date.today()
    orders, op = _rows("orders.csv")
    dispatch, dp = _rows("dispatch.csv")
    exceptions, ep = _rows("exceptions.csv")
    risks = []
    for r in orders:
        due = _date(r.get("due"))
        if not due or _is_done(r):
            continue
        oid = str(r.get("order_id") or "")
        days = (due - today).days
        if days < 0:
            risks.append({"level": "overdue", "id": oid, "days": days,
                          "text": "%s 已逾期 %d 天（交期 %s，状态 %s）" %
                                  (oid, abs(days), due, r.get("status") or "")})
        elif days <= 3:
            risks.append({"level": "due_soon", "id": oid, "days": days,
                          "text": "%s 距交期 %d 天（%s，状态 %s）" %
                                  (oid, days, due, r.get("status") or "")})
        if str(r.get("material_ready") or "").strip() == "否":
            risks.append({"level": "material", "id": oid, "days": days,
                          "text": "%s 缺料未齐：%s" % (oid, r.get("note") or "物料未到")})
    for r in dispatch:
        end = _date(r.get("planned_end"))
        if end and end < today and str(r.get("status") or "") not in ("done", "closed"):
            risks.append({"level": "dispatch", "id": str(r.get("order_id") or ""),
                          "days": (end - today).days,
                          "text": "%s 工序 %s 计划 %s 未完成（%s）" %
                                  (r.get("order_id"), r.get("process"), end, r.get("owner") or "")})
    for r in exceptions:
        if str(r.get("status") or "").lower() == "open":
            risks.append({"level": "exception", "id": str(r.get("order_id") or ""),
                          "days": 0, "text": "%s %s：%s" %
                          (r.get("order_id"), r.get("type") or "异常", r.get("description") or "")})
    order = {"overdue": 0, "due_soon": 1, "material": 2, "dispatch": 3, "exception": 4}
    risks.sort(key=lambda x: (order.get(x["level"], 9), x.get("days", 0)))
    uniq = []
    seen = set()
    for x in risks:
        key = (x["level"], x["id"], x["text"])
        if key not in seen:
            seen.add(key)
            uniq.append(x)
    top_risks = uniq[:3]
    todo = []
    if top_risks:
        todo.append("先处理：" + top_risks[0]["text"])
    open_orders = [r for r in orders if not _is_done(r)]
    if open_orders:
        todo.append("未完结订单 %d 个，逐单核对交期与当前工序" % len(open_orders))
    if any(x["level"] == "material" for x in uniq):
        todo.append("缺料单独立跟进采购到料时间")
    suggestion = ("先把逾期/缺料单拉出来逐单确认，再排今天产能；"
                  "不要先做新单。") if uniq else "今天没有硬风险，按计划推进。"
    lines = [
        "# 今日要事与风险 · %s" % today.isoformat(),
        "",
        "> 数据源：`%s` / `%s` / `%s`" % (op, dp, ep),
        "> 只读生成，未调用模型，未改 ERP。",
        "",
        "## 三条要事",
    ]
    for i, t in enumerate(todo[:3], 1):
        lines.append("%d. %s" % (i, t))
    if not todo:
        lines.append("1. 无待办数据。")
    lines += ["", "## 三个风险"]
    for i, r in enumerate(top_risks, 1):
        lines.append("%d. %s" % (i, r["text"]))
    if not top_risks:
        lines.append("1. 未发现逾期、临期、缺料或未完成工序。")
    lines += ["", "## 一个建议", "", suggestion, ""]
    health = JH.text()
    if health:
        lines += ["", "## 作业异常", "", health, ""]
    return {"date": today.isoformat(), "todo": todo[:3], "risks": top_risks,
            "job_health": JH.summary(),
            "suggestion": suggestion, "text": "\n".join(lines),
            "counts": {"orders": len(orders), "dispatch": len(dispatch),
                       "exceptions": len(exceptions), "risks": len(uniq)}}


def run(today=None, out_dir=None):
    d = build(today)
    out = pathlib.Path(out_dir or OUT)
    out.mkdir(parents=True, exist_ok=True)
    p = out / ("brief-%s.md" % d["date"])
    AT.write_text(p, d["text"], encoding="utf-8")
    return {"ok": True, "path": str(p), **d}
