# -*- coding: utf-8 -*-
"""长期写作风格：按 profile 保存风格名、语气、偏好和样例，不联网。"""
from __future__ import annotations

import datetime
import json
import os
import pathlib

from pai import atomic as AT

CAPABILITY = {
    "name": "长期写作风格",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "文件坏=按空风格处理；样例最多 5 条、每条 2000 字；只读本机 JSON",
    "approval": "ADR-046：本地单用户风格登记，不发明新开关",
}

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _path():
    return pathlib.Path(os.environ.get("PAI_WRITING_STYLE")
                        or ROOT / "data" / "writing_style.json")


def _read():
    p = _path()
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write(data):
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    AT.write_text(tmp, json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def load(profile):
    d = _read().get(str(profile) or "work") or {}
    return {"name": d.get("name", ""), "tone": d.get("tone", ""),
            "notes": d.get("notes", ""), "samples": d.get("samples", []),
            "updated": d.get("updated", "")}


def save(profile, name="", tone="", notes=""):
    data = _read()
    key = str(profile) or "work"
    cur = data.get(key) or {}
    data[key] = {"name": str(name or "")[:40], "tone": str(tone or "")[:120],
                 "notes": str(notes or "")[:2000],
                 "samples": cur.get("samples", [])[:5],
                 "updated": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    _write(data)
    return load(profile)


def add_sample(profile, text):
    t = str(text or "").strip()[:2000]
    if not t:
        raise ValueError("样例为空")
    data = _read()
    key = str(profile) or "work"
    cur = data.get(key) or {}
    samples = cur.get("samples") or []
    samples = (samples + [t])[-5:]
    cur.update({"samples": samples,
                "updated": datetime.datetime.now().astimezone().isoformat(timespec="seconds")})
    data[key] = cur
    _write(data)
    return load(profile)


def clear(profile):
    data = _read()
    data.pop(str(profile) or "work", None)
    _write(data)
    return load(profile)


def block(profile):
    s = load(profile)
    if not (s["name"] or s["tone"] or s["notes"] or s["samples"]):
        return ""
    lines = ["[长期写作风格]"]
    if s["name"]:
        lines.append("风格名：%s" % s["name"])
    if s["tone"]:
        lines.append("固定语气：%s" % s["tone"])
    if s["notes"]:
        lines.append("偏好：%s" % s["notes"])
    for i, sample in enumerate(s["samples"], 1):
        lines.append("样例%d（只学语气/结构，不抄事实）：%s" % (i, sample[:800]))
    lines.append("遵守上面的风格；事实仍以用户提供为准。")
    return "\n".join(lines)
