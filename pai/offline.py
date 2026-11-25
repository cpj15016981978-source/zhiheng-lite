# -*- coding: utf-8 -*-
"""本地离线闸：PAI_OFFLINE=1 时，所有【云端出网】能力一律拒绝。

不对外、不出网不是界面提示，是后端硬闸。
─────────────────────────────────────────────────────────────────────
2026-11-17 收敛为单一入口（评审第 4 条）：
  之前每个出网模块各写一遍 `if OFF.enabled()`（vision / web_search /
  model_gateway / mail_calendar 各一套），日历侧漏写导致离线闸被 urlopen 绕过。
  现在所有"这个服务离线时能不能用"的判断集中在本文件的 SERVICES 注册表，
  各模块只调 is_allowed() / assert_allowed()，不再自己判 enabled。

本地回环服务（Ollama / Vosk / BGE / embedding / rerank / semantic / local_model）
走 127.0.0.1 或本地模型文件，不出网卡，离线时允许——登记在 LOCAL。
"""
from __future__ import annotations

import os

CAPABILITY = {
    "name": "本地离线闸（单一入口）",
    "switch_env": "PAI_OFFLINE",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "开=云端模型/视觉/联网搜索/邮箱/远程日历在入口拒绝；本地 Ollama、BGE、Vosk、附件解析、SQLite 不受影响",
    "approval": "ADR-046 常开边界；离线优先由本机单用户部署定义",
}

# ── 单一真源：服务注册表 ──────────────────────────────────────────────────
# key = 传给 is_allowed/assert_allowed 的 service 名
# value = 报错时给人看的中文名；None 表示本地服务（离线时允许）
SERVICES = {
    # 云端能力：离线一律拒绝（value 是中文 label）
    "cloud_model": "云端模型",
    "vision":      "图片视觉",
    "web_search":  "联网搜索",
    "mail":        "邮箱读取",
    "calendar":    "日历读取",   # 2026-11-17 补，原先漏掉
    "browser":     "浏览器",     # P0-5：可开任意 URL
    "asr":         "HTTP 语音识别",  # P0-5：HTTP ASR 后端
    "router_vision": "模型视觉路由", # P0-5：未接线的 vision
    # 本地能力：value=None，走 127.0.0.1 或本地模型文件，离线允许
    "ollama":      None,
    "local_model": None,
    "bge":         None,
    "embedding":   None,
    "rerank":      None,
    "semantic":    None,
    "vosk":        None,
    "mock":        None,
}


def enabled():
    return (os.environ.get("PAI_OFFLINE") or "").strip().lower() in (
        "1", "on", "true", "yes", "offline")


def is_allowed(service) -> bool:
    """这个服务在离线模式下能不能用？UI 与 configured() 走这里（不抛异常）。

    未登记的服务名一律按【云端】处理（fail-closed）：新模块忘了登记，
    离线时也会被拒绝，而不是悄悄放行——这是防"日历侧那种漏写"的兜底。
    """
    if not enabled():
        return True
    return SERVICES.get(str(service).strip().lower(), "未知服务") is None


def assert_allowed(service, exc=RuntimeError, label=None):
    """唯一出网闸门：离线时若服务不允许，抛 exc 类型的异常。

    exc / label 由调用方传入，保持各模块原有异常类型与文案（兼容现有测试）：
        OFF.assert_allowed("vision", VisionError, "图片视觉")
        OFF.assert_allowed("calendar", RuntimeError, "远程日历 ICS")
    未登记服务名 fail-closed，label 缺省用注册名或服务名。
    """
    if is_allowed(service):
        return
    key = str(service).strip().lower()
    who = label or SERVICES.get(key) or key or "该服务"
    raise exc("离线模式：%s已关闭（PAI_OFFLINE=1）" % who)


def guard(service, exc=RuntimeError, label=None):
    """向后兼容旧调用：语义等同 assert_allowed。"""
    return assert_allowed(service, exc=exc, label=label)


def provider_service(provider: str) -> str:
    """provider 名 -> 服务键：给 model_gateway 用，让白名单逻辑也走单一入口。

    本地 provider（ollama/mock 及 SERVICES 里登记为本地的）返回其名下键；
    其余一律归到 cloud_model（fail-closed）。
    """
    p = str(provider or "").strip().lower()
    if p in SERVICES:
        return p
    return "cloud_model"


def registered_services():
    """供测试/自检枚举：确认所有云端能力都登记在册。"""
    return dict(SERVICES)
