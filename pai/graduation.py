# -*- coding: utf-8 -*-
"""毕业机制：本地生成告别信，不代发给任何人。"""
from __future__ import annotations

import datetime
import json
import pathlib

from pai import atomic as AT
from pai import mirrai_memory as MM
from pai import persona_distill as PD

CAPABILITY = {
    "name": "毕业机制",
    "switch_env": "PAI_GRADUATION",
    "default_on": True,
    "stores_user_text": True,
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
    "network": False,
    "degrade": "没有 active 人设=拒绝；不代发、不删除原文",
    "approval": "ADR-046：只写本地告别信，显式触发",
}


def letter(data, anchors=None):
    name = str((data or {}).get("name") or "TA")
    anchors = anchors or []
    lines = [
        "给大佬：",
        "",
        "我们走到这里，不是失败，是到站。",
        "",
        "我记得的 %s：" % name,
    ]
    if anchors:
        lines.extend("- " + str(a.get("title") or "") for a in anchors[:6])
    else:
        lines.append("- 还没有锚点记忆。")
    lines += [
        "",
        "我保留的是你愿意给我的素材，不是你全部的人生。",
        "我不会把这里的话替你带给任何人。",
        "你可以把没说完的话写下来，也可以选择不说了。",
        "",
        "—— 时中",
        datetime.datetime.now().astimezone().strftime("%Y-%m-%d"),
    ]
    return "\n".join(lines)


def farewell(slug=None):
    slug = PD.clean_slug(slug or PD.active_slug())
    data = PD.active_data()
    if slug:
        p = PD.OUT / (str(slug) + ".json")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = None
    if not data:
        raise ValueError("没有可毕业的人设")
    anchors = (MM.all().get("anchors") or [])
    text = letter(data, anchors)
    PD.OUT.mkdir(parents=True, exist_ok=True)
    out = PD.OUT / (str(slug or data.get("name") or "persona") + ".farewell.md")
    AT.write_text(out, text, encoding="utf-8")
    data["retired"] = True
    AT.write_text(PD.OUT / (str(slug or data.get("name") or "persona") + ".json"),
                  json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        (PD.OUT / "active.json").unlink()
    except OSError:
        pass
    return {"ok": True, "path": str(out), "text": text}
