# -*- coding: utf-8 -*-
"""A2 工具执行层：只执行白名单里的本地工具，路径锁在 D:\\DSH 与沙箱内。

- read_text / list_dir：只读，根 = D:\\DSH（不含密钥/库文件）
- write_text：只写 agent_layer/data/sandbox，必须先经 /approve
- 不提供联网工具；capability=network 的任务不能 run
"""
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pai import exec_guard as EG  # noqa: E402

ROOT = Path(os.environ.get("AGENT_TOOL_ROOT") or (Path.home() / "ZhihengTools")).resolve()
SANDBOX = (Path(__file__).resolve().parent / "data" / "sandbox").resolve()
MAX_BYTES = 100_000
DENY_NAMES = {"tokens.json", "memory.db", "agentd.db", "audit.jsonl", "keys.json"}
DENY_SUFFIX = {".key", ".pem", ".env", ".db"}

TOOLS = {
    "read_text": {"capability": "read", "write": False},
    "list_dir": {"capability": "read", "write": False},
    "write_text": {"capability": "write", "write": True},
}


def _resolve(path, write):
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("path 不能为空")
    p = Path(raw)
    if not p.is_absolute():
        p = (SANDBOX if write else ROOT) / p
    p = p.resolve()
    base = SANDBOX if write else ROOT
    if not str(p).lower().startswith(str(base).lower() + os.sep) and p != base:
        raise PermissionError("路径越界：%s" % p)
    low = p.name.lower()
    if (low in {x.lower() for x in DENY_NAMES}
            or low.endswith((".env", ".key", ".pem", ".db", "-wal", "-shm", "-journal"))):
        raise PermissionError("禁止访问：%s" % p.name)
    if write and EG.is_executable(p):
        raise PermissionError("拒绝写可执行体：%s" % p.name)
    return p


def execute(tool, args):
    args = args or {}
    spec = TOOLS.get(tool)
    if not spec:
        raise ValueError("未知工具：%s" % tool)
    if tool == "read_text":
        p = _resolve(args.get("path"), False)
        raw = p.read_bytes()
        truncated = len(raw) > MAX_BYTES
        data = raw[:MAX_BYTES]
        return {"path": str(p), "text": data.decode("utf-8", "replace"),
                "bytes": len(data), "truncated": truncated}
    if tool == "list_dir":
        p = _resolve(args.get("path"), False)
        return {"path": str(p), "entries": sorted(x.name for x in p.iterdir())[:200]}
    if tool == "write_text":
        p = _resolve(args.get("path"), True)
        text = str(args.get("text") or "")
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise ValueError("内容超过 %d 字节" % MAX_BYTES)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="")
        return {"path": str(p), "bytes": len(text.encode("utf-8"))}
    raise ValueError("未实现：%s" % tool)
