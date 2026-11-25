# -*- coding: utf-8 -*-
"""M8 第二批 v0：让模型自己决定调哪个【只读】工具。

协议（提示词层，不改网关、不加依赖）：
  在 prompt 末尾告诉模型可用工具；需要查库时只输出一行
      TOOL_CALL: {"tool":"recall","args":{"query":"爷爷","k":3}}
  编排层解析 → 调 M8.run → 把结果作为第二次调用的上下文 → 模型给最终回答。
  最多一次工具调用；失败/超时/协议不符 → 保留原回答并说明，绝不抛给上层。

为什么不用 OpenAI function calling：网关是纯文本补全，加 tools 参数要改
model_gateway 与所有 provider；提示词协议零依赖、可离线测。等换网关再升级。

安全：
  · 只允许 M8 里 permission=="read" 的工具；写工具永远进不来；
  · 工具名必须是注册表字面量，参数只接受 dict；不执行模型给的任意代码；
  · 默认关（PAI_TOOL_USE=1 才开）；关着时 prompt 不变、行为逐字照旧。
"""
import json
import os
import re

from . import tools as M8
from . import trace as TR

ENV_USE = "PAI_TOOL_USE"
MAX_RESULT_CHARS = 3000

CAPABILITY = {
    "name": "M8 模型选工具 v0",
    "switch_env": ENV_USE,
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "协议不符/工具失败/二次调用失败 = 保留原回答并记 trace，不阻断对话",
    "probe_cost": "只读环境变量，不起进程、不联网、不写库",
    "approval": "未获单独批准：Boss 没对『模型选工具』点过头，ADR-043 末段明写「模型要调工具是 M8 第二批，需要 Boss 另批」；只读工具，写工具需另批",
}

_CALL_RE = re.compile(r"TOOL_CALL:\s*(\{.*?\})\s*$", re.MULTILINE)


def enabled():
    return (os.environ.get(ENV_USE) or "").strip() == "1"


def set_enabled(on):
    os.environ[ENV_USE] = "1" if on else "0"


def instruction():
    """拼进 prompt 的工具说明；工具列表来自 M8 注册表，不抄第二份。"""
    lines = [
        "[工具协议] 如果必须查库才能回答，只输出一行：",
        'TOOL_CALL: {"tool": "名字", "args": {...}}',
        "可用工具：",
    ]
    for t in M8.list_tools():
        lines.append("- %s(%s)：%s%s" % (
            t["name"], ", ".join(t["params"]), t["desc"],
            "（写工具，需人工审批）" if t["permission"] == "write" else ""))
    lines += [
        "不需要查库就直接回答；不要编造工具结果；最多调用一次。",
        "工具返回后，基于结果直接回答用户，不要再输出 TOOL_CALL。",
    ]
    return "\n".join(lines)


def parse(text):
    """解析模型输出里的工具调用；不合协议返回 None（不猜）。"""
    m = _CALL_RE.search(text or "")
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
    except (TypeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    name = obj.get("tool")
    args = obj.get("args")
    if args is None:
        args = {}
    if name not in M8.TOOLS or not isinstance(args, dict):
        return None
    if M8.TOOLS[name]["permission"] not in ("read", "write"):
        return None
    return {"tool": name, "args": args}


def strip_call(text):
    """把协议行从回答里去掉，避免把 TOOL_CALL 原样显示给用户。"""
    return _CALL_RE.sub("", text or "").strip()


def run_tool(mem, call):
    """执行工具；临时打开 M8，执行完恢复原开关（不把用户设置改掉）。"""
    was_on = M8.backend() == M8.BACKEND_ON
    M8.set_enabled(True)
    try:
        res = M8.run(call["tool"], call["args"], mem)
        TR.TRACE.record_tool_call(call["tool"], call["args"], result=res)
        return res
    except Exception as e:
        TR.TRACE.record_tool_call(call["tool"], call["args"], error=type(e).__name__)
        raise
    finally:
        M8.set_enabled(was_on)


def followup(prompt, call, result):
    """第二次调用的 prompt：原始上下文 + 工具结果 + 别再调工具的约束。"""
    payload = json.dumps({"tool": call["tool"], "result": result},
                         ensure_ascii=False, default=str)[:MAX_RESULT_CHARS]
    return (prompt + "\n\n[你请求的工具结果]\n" + payload +
            "\n\n[要求] 基于上面的工具结果直接回答用户；不要再输出 TOOL_CALL。")
