# -*- coding: utf-8 -*-
"""桌面能力 v1：列窗口、聚焦、输入；必须人工确认。"""
from __future__ import annotations

CAPABILITY = {
    "name": "桌面能力 v1",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "pywinauto 缺失/窗口不存在=返回错误；动作必须 confirm=true",
    "approval": "ADR-046：本地单用户桌面能力；每次动作必须人工确认",
}


def windows():
    try:
        from pywinauto import Desktop
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "pywinauto 不可用：%s" % e}
    out = []
    try:
        for w in Desktop(backend="uia").windows():
            title = (w.window_text() or "").strip()
            if title:
                out.append({"title": title, "visible": bool(w.is_visible())})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "%s: %s" % (type(e).__name__, e)}
    return {"ok": True, "windows": out[:50]}


def controls(title, limit=80):
    """只读：列出窗口里的控件名称/类型，供人工或模型选目标。"""
    try:
        from pywinauto import Desktop
        w = Desktop(backend="uia").window(title=title)
        out = []
        for c in w.descendants()[:int(limit)]:
            try:
                info = c.element_info
                out.append({"name": info.name or "", "control_type": info.control_type or "",
                            "automation_id": info.automation_id or "",
                            "class_name": info.class_name or ""})
            except Exception:
                continue
        return {"ok": True, "title": title, "controls": out}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "%s: %s" % (type(e).__name__, e)}


def _find(title, target):
    from pywinauto import Desktop
    w = Desktop(backend="uia").window(title=title)
    target = str(target or "").strip()
    for c in w.descendants():
        try:
            info = c.element_info
            if target and target in (info.name, info.automation_id, info.control_type):
                return c
        except Exception:
            continue
    raise ValueError("找不到控件：%s" % target)


def click_control(title, target, confirm=False):
    if confirm is not True:
        return {"ok": False, "needs_approval": True, "reason": "桌面点击必须人工确认"}
    from pai import exec_guard as EG
    EG.gate("mcp_call", "desktop-click:%s/%s" % (title, target), approved=True, detail="desktop.click_control")
    try:
        c = _find(title, target)
        c.click_input()
        return {"ok": True, "action": "click", "title": title, "control": target}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "%s: %s" % (type(e).__name__, e)}


def type_control(title, target, text, confirm=False):
    if confirm is not True:
        return {"ok": False, "needs_approval": True, "reason": "桌面输入必须人工确认"}
    from pai import exec_guard as EG
    EG.gate("mcp_call", "desktop-type:%s/%s" % (title, target), approved=True, detail="desktop.type_control")
    try:
        c = _find(title, target)
        c.set_focus()
        c.type_keys(str(text), with_spaces=True)
        return {"ok": True, "action": "type_control", "title": title, "control": target}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "%s: %s" % (type(e).__name__, e)}


def action(kind, title, text="", confirm=False):
    if confirm is not True:
        return {"ok": False, "needs_approval": True,
                "reason": "桌面动作必须人工确认"}
    from pai import exec_guard as EG
    EG.gate("mcp_call", "desktop:%s" % title, approved=True, detail="desktop.action")
    try:
        from pywinauto import Desktop
        w = Desktop(backend="uia").window(title=title)
        w.set_focus()
        if kind == "focus":
            pass
        elif kind == "type":
            w.type_keys(str(text), with_spaces=True)
        else:
            return {"ok": False, "reason": "未知动作：%s" % kind}
        return {"ok": True, "action": kind, "title": title}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "%s: %s" % (type(e).__name__, e)}
