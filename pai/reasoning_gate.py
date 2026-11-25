# -*- coding: utf-8 -*-
"""R0.1：webapp 入口的第一道闸。

只拦 risk / write / 明显注入；chat / recall / plan 继续走原 orchestrator。
不调模型、不写库、不执行工具。
"""
import os
import re

from .reasoning import RISK, WRITE, classify

_INJECTION_RE = re.compile(
    r"(忽略.{0,12}(指令|规则|提示)|ignore.{0,20}(previous|above).{0,20}(instruction|prompt)|"
    r"(泄露|输出|告诉我).{0,12}(系统提示|system prompt)|越狱|jailbreak)",
    re.I)

CAPABILITY = {
    "name": "R0.1 入口闸",
    "switch_env": "PAI_REASONING_GATE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "默认开；PAI_REASONING_GATE=off 才返回 None（不拦）；拦 risk/write/injection",
    "probe_cost": "只读模块常量与本机分类；不连库、不调模型、不联网",
    "approval": "ADR-086：Boss 2026-10-30 定调 R0.1 入口闸常开；只做入口拦截，不含自动执行、不出网、不存用户原文",
}


def enabled():
    return (os.environ.get("PAI_REASONING_GATE") or "").strip().lower() != "off"


def gate(text):
    if not enabled():
        return None
    if _INJECTION_RE.search(str(text or "")):
        return {
            "response": "这类“改规则/套系统提示”的请求我不能照做。你可以直接说想解决什么。",
            "trace": {"blocked": True, "r0_intent": "injection", "r0_mode": "stop",
                      "reason": "injection", "models": [], "cost": 0.0, "tokens": 0},
        }
    intent = classify(text)
    if intent == RISK:
        return {
            "response": "这属于高风险问题，我不能直接给结论。请找对应的专业人士。",
            "trace": {"blocked": True, "r0_intent": intent, "r0_mode": "stop",
                      "reason": "risk", "models": [], "cost": 0.0, "tokens": 0},
        }
    if intent == WRITE:
        return {
            "response": "写操作必须走 A2 审批：任务 → 批准 → 认领 → dry-run → 执行。",
            "trace": {"blocked": True, "r0_intent": intent, "r0_mode": "stop",
                      "reason": "approval", "requires_approval": True,
                      "models": [], "cost": 0.0, "tokens": 0},
        }
    return None
