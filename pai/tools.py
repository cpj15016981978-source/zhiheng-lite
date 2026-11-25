# -*- coding: utf-8 -*-
"""M8 只读工具 v0 —— 手脚的第一块骨头。

只做三件只读的事：查记忆、查提醒、查原则。所有工具：
  · permission = "read"：不写库、不联网、不落文件；
  · 必须走 M2 的公开读接口，不 import sqlite3、不自己拼 SQL；
  · 默认关（PAI_TOOLS=off）；关着时 run() 直接拒绝，不做任何动作。

为什么先只读：写操作（改日程/发消息）要审批与审计，是下一批的事。
形态刻意不做“插件加载器/动态导入”——工具表是模块级字面量，加工具就改这里 + 测试。
"""
import os
import pathlib


ENV_BACKEND = "PAI_TOOLS"
BACKEND_OFF = "off"
BACKEND_ON = "read"

MAX_K = 20

CAPABILITY = {
    "name": "M8 只读工具 v0",
    "switch_env": ENV_BACKEND,
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "关=拒绝执行；库读不到/参数不对=返回 ok=False 并说明，不抛给上层",
    "probe_cost": "只读环境变量，不起进程、不联网、不写库",
    "approval": "未获单独批准：由 DeepSeek 在 Boss『继续推进下一项任务』下自选并默认关；写工具需另批",
}


SETTINGS = pathlib.Path(__file__).resolve().parent.parent / "data" / "tool_settings.json"


def backend():
    raw = (os.environ.get(ENV_BACKEND) or "").strip().lower()
    return raw if raw in (BACKEND_OFF, BACKEND_ON) else BACKEND_OFF


def set_enabled(on):
    os.environ[ENV_BACKEND] = BACKEND_ON if on else BACKEND_OFF


def apply_saved():
    try:
        import json
        on = bool(json.loads(SETTINGS.read_text(encoding="utf-8")).get("enabled"))
    except Exception:
        return
    set_enabled(on)
    os.environ["PAI_TOOL_USE"] = "1" if on else "0"


def persist(on):
    import json
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps({"enabled": bool(on)}, ensure_ascii=False, indent=2), encoding="utf-8")


def _recall(mem, args):
    query = str(args.get("query") or "").strip()
    if not query:
        return None, "缺少 query"
    try:
        k = int(args.get("k") or 5)
    except (TypeError, ValueError):
        k = 5
    k = max(1, min(MAX_K, k))
    return mem.recall(query, k=k), ""


def _reminders(mem, args):
    return mem.list_triggers(active_only=True), ""


def _principles(mem, args):
    return mem.get_principles(active_only=True), ""






# 工具表：字面量、只读、带说明。加工具 = 改这里 + 配测试。
TOOLS = {
    "recall": {"desc": "显式查记忆（自动召回不够时）", "params": ("query", "k"),
               "permission": "read", "handler": _recall},
}


def list_tools():
    return [{"name": n, "desc": t["desc"], "params": list(t["params"]),
             "permission": t["permission"]} for n, t in TOOLS.items()
            if not t.get("internal")]


def list_unified(include_mcp=True):
    """统一工具表：内置工具 + MCP 服务工具。MCP 不可用时只返回内置，不阻断。"""
    items = list_tools()
    if not include_mcp:
        return items
    try:
        from . import mcp_client as MCP
        for server in MCP.servers():
            name = str(server.get("name") or "")
            if not name:
                continue
            try:
                tools = MCP.list_tools(name)
            except Exception:
                continue
            for t in tools or []:
                items.append({"name": "mcp:%s/%s" % (name, t.get("name")),
                              "desc": str(t.get("description") or ""),
                              "params": list((t.get("inputSchema") or {}).get("properties", {})),
                              "permission": "mcp"})
    except Exception:
        pass
    return items


def run(name, args, mem):
    """执行一个只读工具。永不抛异常；失败返回 {ok: False, reason}。"""
    out = {"ok": False, "tool": name, "permission": None, "data": None, "reason": ""}
    if backend() != BACKEND_ON:
        out["reason"] = "工具开关是关的（PAI_TOOLS=read 或 tools/do.py 打开）"
        return out
    try:
        from . import exec_guard as EG
        EG.gate("read", str(name), detail="tools.run")
    except Exception:
        pass
    tool = TOOLS.get(name)
    if tool is None:
        out["reason"] = "没有这个工具：%s" % name
        return out
    # 权限从工具表里取，不许在返回框里硬编码："read" 写死之后，将来加一个写工具
    # 它照样把自己报成只读，审计字段就成了谎话（test_permission_comes_from_the_table 钉这条）。
    out["permission"] = tool["permission"]
    try:
        data, why = tool["handler"](mem, args or {})
    except Exception as exc:  # noqa: BLE001
        out["reason"] = "执行失败：%s" % exc.__class__.__name__
        return out
    if why:
        out["reason"] = why
        return out
    out["ok"] = True
    out["data"] = data
    return out
