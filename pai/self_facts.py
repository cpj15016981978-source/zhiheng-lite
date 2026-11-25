# -*- coding: utf-8 -*-
"""C0：self_facts 只读事实层。

回答“存在/身份/来源/边界”时只读这里；没有的写“未登记”。
默认关；不接 prompt、不联网、不写库。
"""
import os
import subprocess
import warnings
from pathlib import Path

from . import manifest as MF
from . import self_model as SM

CAPABILITY = {
    "name": "C0 self_facts 只读事实层",
    "switch_env": "PAI_SELF_FACTS",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "collect(mem) 必须传 pai.memory.Memory；mem 缺失/库不可用则身份答“未登记”；PAI_SELF_FACTS 未开则 collect 返回 enabled=false，不做动作",
    "probe_cost": "只读 manifest/身份/路径/端口表；不连库、不调模型、不联网",
    "approval": "未获单独批准：C0 只做只读事实源，未接日常",
}


def enabled():
    return (os.environ.get("PAI_SELF_FACTS") or "").strip().lower() == "on"


def _listening_ports():
    try:
        cp = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3)
        out = cp.stdout
        if out is None:
            warnings.warn("netstat 没有返回 stdout；端口集合为空", RuntimeWarning)
            return set()
    except Exception:
        warnings.warn("netstat 读取失败；端口集合为空", RuntimeWarning)
        return set()
    ports = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0].upper().startswith("TCP") and parts[3] == "LISTENING":
            try:
                ports.add(int(parts[1].rsplit(":", 1)[1]))
            except (ValueError, IndexError):
                pass
    return ports


def _identity(mem):
    try:
        v = SM.identity(mem)
    except Exception:
        v = None
    if not v:
        return {"status": "未登记"}
    try:
        changes = [dict(r) for r in mem.conn.execute(
            "SELECT op, ts, reason FROM principle_history ORDER BY ts DESC LIMIT 5")]
    except Exception:
        changes = []
    return {
        "version": v.get("v"),
        "name": v.get("name") or "",
        "role": v.get("role") or "",
        "created_at": v.get("created_ts") or "未登记",
        "changes": changes,
        "source": "pai/memory.py:meta[self.identity]",
    }


def collect(mem, ports=None, lock_candidates=None):
    if not enabled():
        return {"enabled": False, "reason": "PAI_SELF_FACTS 未开"}
    root = Path(getattr(mem, "db_path", ".")).resolve().parent
    if ports is None:
        ports = _listening_ports()
    web_port = 8753 if 8753 in ports else None
    if lock_candidates is None:
        lock_candidates = [root / "agentd" / "agentd.lock", root / "daemon.lock"]
    lock_path = next((p for p in lock_candidates if p.exists()), None)
    return {
        "enabled": True,
        "existence": {
            "pid": os.getpid(),
            "web_port": web_port,
            "web_port_listening": web_port is not None,
            "db_path": str(getattr(mem, "db_path", "未登记")),
            "daemon_lock": str(lock_path) if lock_path else "未登记",
            "daemon_lock_exists": lock_path is not None,
            "stop_policy": "Boss/Q 可停；AI 不自停、不自起",
            "source": "process+netstat+fs",
        },
        "identity": _identity(mem),
        "origin": MF.build(mem),
        "boundaries": {
            "read": ["D:\\DSH"],
            "write": ["agent_layer/data/sandbox"],
            "network": False,
            "self_approval": False,
            "source": "policy:AI系统_权限与风险边界（静态摘要）",
        },
        "unknown": ["训练数据", "模型权重", "部署拓扑"],
    }
