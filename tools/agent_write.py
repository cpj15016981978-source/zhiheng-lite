# -*- coding: utf-8 -*-
"""A2 文件写客户端（沙箱）：只说意图，不直接写盘。

流程：
    submit_text() -> A2 /task（capability=write）
    approve()     -> 有 write 令牌的人批准
    run()         -> 认领者执行 write_text（只写 agent_layer/data/sandbox）

默认关：PAI_AGENT_WRITE=on 且 PAI_AGENT_TOKEN 存在才发请求。
只连回环地址；不存正文。
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8766"


class AgentWriteError(RuntimeError):
    pass


def enabled():
    return (os.environ.get("PAI_AGENT_WRITE") or "").strip().lower() == "on"


def _token():
    return (os.environ.get("PAI_AGENT_TOKEN") or "").strip()


def _base():
    raw = (os.environ.get("PAI_AGENT_BASE") or BASE).strip().rstrip("/")
    p = urllib.parse.urlsplit(raw)
    if p.scheme != "http" or p.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise AgentWriteError("只允许本机回环 http://127.0.0.1")
    return raw


def _post(path, payload):
    if not enabled():
        raise AgentWriteError("PAI_AGENT_WRITE 未开；不代替你写")
    token = _token()
    if not token:
        raise AgentWriteError("缺少 PAI_AGENT_TOKEN；写操作必须由有 write 能力的人批准")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(_base() + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Agent-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:200]
        raise AgentWriteError("%s 返回 HTTP %s：%s" % (path, e.code, detail)) from None
    except urllib.error.URLError as e:
        raise AgentWriteError("连不上 A2：%s" % e.reason) from None


def _check_rel_path(path):
    p = str(path or "").strip()
    if not p:
        raise AgentWriteError("path 不能为空")
    if p.startswith(("/", "\\")) or ":" in p:
        raise AgentWriteError("只接受沙箱相对路径")
    parts = [x for x in p.replace("\\", "/").split("/") if x]
    if not parts or any(x in (".", "..") for x in parts):
        raise AgentWriteError("路径里不许有 . / ..")
    return "/".join(parts)


def submit_text(path, text, goal="写沙箱文件"):
    rel = _check_rel_path(path)
    body = {
        "goal": goal,
        "capability": "write",
        "budget": 1,
        "payload": {"tool": "write_text",
                    "args": {"path": rel, "text": str(text or "")}},
    }
    return _post("/task", body)


def simulate(task_id):
    """只预演，不写盘；返回 {ok, simulation}。"""
    return _post("/simulate", {"id": str(task_id or "")})


def approve(task_id):
    return _post("/approve", {"id": str(task_id or "")})


def run(task_id):
    # 先认领再执行：调用方不用自己记得 claim
    c = _post("/claim", {"id": str(task_id or "")})
    if isinstance(c, dict) and c.get("ok") is False:
        raise AgentWriteError("认领失败：%s" % c.get("error"))
    return _post("/run", {"id": str(task_id or "")})
