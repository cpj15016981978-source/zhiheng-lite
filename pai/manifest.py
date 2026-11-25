# -*- coding: utf-8 -*-
"""C0：manifest 事实源。

只回答“我由什么构成”，不靠模型回忆。
默认关；不接 prompt、不联网、不写库。
"""
import datetime
import hashlib
import os
from pathlib import Path

CAPABILITY = {
    "name": "C0 manifest 事实源",
    "switch_env": "PAI_MANIFEST",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "PAI_MANIFEST 未开时只提供只读函数；不阻塞对话",
    "probe_cost": "只读本地文件与进程环境；不连库、不调模型、不联网",
    "approval": "未获单独批准：C0 只做只读事实源，未接日常",
}

_PAI = Path(__file__).resolve().parent
# 只散列下面这 8 个模块，不是整棵 pai/；改注释时别再写成“整棵”。
_INCLUDE = ("__init__.py", "orchestrator.py", "memory.py", "model_gateway.py",
            "self_model.py", "world.py", "trace.py", "activity.py")


def enabled():
    return (os.environ.get("PAI_MANIFEST") or "").strip().lower() == "on"


def _code_sha():
    h = hashlib.sha256()
    for p in sorted(_PAI.glob("*.py")):
        if p.name not in _INCLUDE:
            continue
        h.update(p.name.encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


def _built_at():
    times = [p.stat().st_mtime for p in _PAI.glob("*.py") if p.is_file()]
    if not times:
        return "未登记"
    return datetime.datetime.fromtimestamp(max(times)).isoformat(timespec="seconds")


def build(mem=None):
    if not enabled():
        return {"enabled": False, "reason": "PAI_MANIFEST 未开"}
    from . import model_gateway as M10
    try:
        provider = M10.active_provider()
    except Exception:
        provider = "未登记"
    db = getattr(mem, "db_path", "未登记")
    return {
        "code_sha12": _code_sha(),
        "built_at": _built_at(),
        "builder": os.environ.get("USERNAME") or "未登记",
        "model_provider": provider,
        "model_name": os.environ.get("PAI_MODEL_NAME") or "未登记",
        "db_path": str(db),
        "data_sources": ["memory.db", "data/trace", "data/a1.activity.jsonl"],
        "limits": ["训练数据未登记", "模型权重不可见", "部署拓扑未登记"],
        "unknown": ["训练数据", "模型权重", "部署拓扑"],
    }
