# -*- coding: utf-8 -*-
"""联网策略：缺省离线，联网须显式开；本地模型离线可用，联网只影响外部工具/API。

P174（Boss 2026-11-22 ② 原话「第6条翻 False：默认离线，联网显式开，补反向测试。」）：
原登记引 ADR-046 写「默认允许联网」，与 ADR-044「默认关+只读+不联网」相反，本格翻案。
无 flag 文件、flag 读不出、字段缺失三种情况一律离线；只有 set_online(True) 才出网。
"""
from __future__ import annotations

from pai import paths

import json
import os
import pathlib

from pai import atomic as AT

ROOT = pathlib.Path(__file__).resolve().parent.parent
FLAG = paths.DATA_DIR / "network_policy.json"
# 缺省值单独立一个模块常量，是为了让反向守卫拔得动它（tests 里 patch 回 True 证明断言会红）。
DEFAULT_ONLINE = False
DEFAULT_ALLOWED = (
    "api.deepseek.com", "api.openai.com", "api.anthropic.com",
    "api.minimaxi.com", "api.siliconflow.cn", "openrouter.ai",
    "dashscope.aliyuncs.com",
)

CAPABILITY = {
    "name": "联网开关",
    "switch_env": "PAI_NETWORK",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "无 flag/读失败/字段缺失=离线（PAI_OFFLINE=1）；显式开=PAI_OFFLINE=0；PAI_OFFLINE 只由本模块写",
    "approval": "ADR-044：Boss 2026-11-22 原话「第6条翻 False：默认离线，联网显式开，补反向测试。」",
}


def _read():
    try:
        return json.loads(FLAG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def status():
    d = _read()
    hosts = d.get("allowed_hosts")
    return {"online": bool(d.get("online", DEFAULT_ONLINE)),
            "allowed_hosts": list(hosts) if isinstance(hosts, list) and hosts else list(DEFAULT_ALLOWED)}


def allowed_hosts():
    return status()["allowed_hosts"]


def set_allowed_hosts(hosts):
    d = _read()
    d["allowed_hosts"] = sorted({str(x).strip().lower() for x in (hosts or []) if str(x).strip()})
    AT.write_text(FLAG, json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return status()


def allow_host(host):
    import urllib.parse
    h = str(host or "").strip().lower()
    if not h:
        return status()
    return set_allowed_hosts(list(allowed_hosts()) + [h])


def remove_host(host):
    h = str(host or "").strip().lower()
    return set_allowed_hosts([x for x in allowed_hosts() if x != h])


def is_host_allowed(url):
    import urllib.parse
    host = (urllib.parse.urlparse(str(url or "")).hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    return host in set(allowed_hosts())


def is_offline():
    """唯一真值：界面与 model_settings 都读这里，不读各自的环境变量。"""
    return not status()["online"]


def apply():
    online = status()["online"]
    os.environ["PAI_OFFLINE"] = "0" if online else "1"
    return status()


def set_online(online):
    AT.write_text(FLAG, json.dumps({"online": bool(online)}, ensure_ascii=False), encoding="utf-8")
    return apply()
