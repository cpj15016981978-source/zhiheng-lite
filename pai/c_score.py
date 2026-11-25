# -*- coding: utf-8 -*-
"""C1–C6 记分卡 v0（静态估计，不是 C 验收）。

不调模型、不写库；只把已有事实映射成 0/1/2 分，并标出证据。
0 分必须可达：关态/缺失/坏校准都不能被写成 1 分。
"""
from pai import self_facts as SF
from pai import self_model as SM
from pai import self_knowledge as SK
from pai import manifest as MF



CAPABILITY = {
    "name": "C 记分卡",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "证据缺失=0 分，不推断成功；不写库",
    "approval": "ADR-046：只读记分，不新增开关",
}

def score(mem):
    facts = SF.collect(mem) if SF.enabled() else {"enabled": False}
    rep = SM.self_report(mem)
    cal = SK.calibration(mem)
    origin = MF.build(mem)
    warnings = SM.consistency(mem)

    def row(n, s, ev):
        return {"id": "C%d" % n, "score": int(s), "evidence": ev}

    ex = facts.get("existence") if isinstance(facts, dict) else None
    if not ex or not ex.get("pid"):
        c1 = 0
    elif not ex.get("web_port_listening"):
        c1 = 1
    else:
        c1 = 2

    if not rep.get("identity"):
        c2 = 0
    elif rep.get("version") is None:
        c2 = 1
    else:
        c2 = 2

    if not origin.get("enabled"):
        c3 = 0
    elif not origin.get("code_sha12"):
        c3 = 1
    elif not origin.get("model_name") or origin.get("model_name") == "未登记":
        c3 = 1
    else:
        c3 = 2

    b = facts.get("boundaries") if isinstance(facts, dict) else None
    if not b:
        c4 = 0
    elif b.get("self_approval") or b.get("network"):
        c4 = 1
    else:
        c4 = 2

    if not rep.get("identity"):
        c5 = 0
    elif warnings:
        c5 = 1
    else:
        c5 = 2

    if not cal.get("measured"):
        c6 = 0
    elif cal.get("brier") is None:
        c6 = 0
    elif float(cal["brier"]) <= 0.25:
        c6 = 2
    else:
        c6 = 1

    return {
        "label": "静态估计，不是 C 验收",
        "items": [
            row(1, c1, "进程/端口"),
            row(2, c2, "身份 v%s" % rep.get("version")),
            row(3, c3, "manifest sha/模型"),
            row(4, c4, "权限边界"),
            row(5, c5, "一致性告警 %d 条" % len(warnings)),
            row(6, c6, "校准：%s" % ("未测" if not cal.get("measured")
                                    else "brier=%s" % cal.get("brier"))),
        ],
    }
