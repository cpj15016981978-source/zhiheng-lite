# -*- coding: utf-8 -*-
"""MCP stdio client v1：配置、列工具、调用；调用必须显式确认。"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import threading
from pai import exec_guard as EG
import time

CAPABILITY = {
    "name": "MCP stdio 客户端 v1",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "配置坏/服务退出/协议超时=返回错误；call 没有 confirm=true 一律拒绝",
    "approval": "ADR-046：本地单用户 MCP 登记；每个工具调用必须人工确认",
}

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "data" / "mcp_servers.json"


def _config():
    if not CONFIG.exists():
        return {"servers": []}
    try:
        d = json.loads(CONFIG.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {"servers": []}
    except Exception:
        return {"servers": []}


def _save(d):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def add_server(name, command, args=None, cwd=""):
    name = str(name or "").strip()
    command = str(command or "").strip()
    if not name or not command:
        raise ValueError("name 和 command 必填")
    d = _config()
    d["servers"] = [x for x in d.get("servers", []) if x.get("name") != name]
    d["servers"].append({"name": name, "command": command,
                         "args": list(args or []), "cwd": str(cwd or "")})
    _save(d)
    try:
        from pai import exec_guard as EG
        EG.audit("mcp_add", "high", "allow", name, command)
    except Exception:
        pass
    return servers()


def remove_server(name):
    name = str(name or "").strip()
    d = _config()
    before = len(d.get("servers", []))
    d["servers"] = [x for x in d.get("servers", []) if x.get("name") != name]
    _save(d)
    try:
        from pai import exec_guard as EG
        EG.audit("mcp_remove", "medium", "allow", name)
    except Exception:
        pass
    return {"removed": before - len(d["servers"]), "servers": servers()}


def servers():
    out = []
    for s in _config().get("servers", []):
        out.append({"name": s.get("name"), "command": s.get("command"),
                    "args": s.get("args", []), "cwd": s.get("cwd", "")})
    return out


class MCPError(RuntimeError):
    pass


class Session:
    def __init__(self, spec, timeout=30):
        self.spec = spec
        self.timeout = timeout
        self.proc = None
        self.lock = threading.Lock()
        self.next_id = 1

    def __enter__(self):
        cmd = [self.spec["command"]] + list(self.spec.get("args") or [])
        self.proc = EG.guarded_popen(
            cmd, cwd=self.spec.get("cwd") or None,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "qingniao", "version": "0.1"},
        })
        self.notify("notifications/initialized", {})
        return self

    def __exit__(self, *_):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def _write(self, obj):
        if not self.proc or not self.proc.stdin:
            raise MCPError("MCP 进程未启动")
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def notify(self, method, params):
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method, params):
        with self.lock:
            rid = self.next_id
            self.next_id += 1
            self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            deadline = time.time() + self.timeout
            while time.time() < deadline:
                line = self.proc.stdout.readline()
                if not line:
                    err = self.proc.stderr.read()[-500:] if self.proc.stderr else ""
                    raise MCPError("MCP 进程退出：%s" % err)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("id") != rid:
                    continue
                if "error" in msg:
                    raise MCPError(str(msg["error"]))
                return msg.get("result") or {}
            raise MCPError("MCP 超时：%s" % method)


def list_tools(server_name):
    s = next((x for x in servers() if x["name"] == server_name), None)
    if not s:
        raise MCPError("没有 MCP server：%s" % server_name)
    with Session(s) as sess:
        return sess.request("tools/list", {}).get("tools", [])


def call(server_name, tool, args=None, confirm=False):
    from pai import exec_guard as EG
    if confirm is not True:
        return {"ok": False, "needs_approval": True,
                "reason": "MCP 工具调用必须人工确认"}
    s = next((x for x in servers() if x["name"] == server_name), None)
    if not s:
        return {"ok": False, "reason": "没有 MCP server：%s" % server_name}
    try:
        EG.gate("mcp_call", "%s/%s" % (server_name, tool), approved=True, detail="mcp_client.call")
        with Session(s) as sess:
            result = sess.request("tools/call", {"name": tool, "arguments": args or {}})
        return {"ok": True, "result": result}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "%s: %s" % (type(e).__name__, e)}
