# -*- coding: utf-8 -*-
"""知衡离线分级调度（local cascade）——断网也能干活的核心框架。

设计铁律：
  1. 只用本地 Ollama（127.0.0.1:11434），全程走 PAI_OFFLINE 闸，不出网、不烧钱。
  2. 路由小模型（qwen3:0.6b）只判"这个任务该谁答"，不产出给用户的内容；
     解析不出来 / 超时 / 模型不在 = fail-closed 落到主力，绝不瞎猜。
  3. 每一级失败都记录降级原因，不静默吞错。
  4. 一键回退：PAI_CASCADE_ROUTE=off 时本模块完全旁路，交给旧链路。

三级阶梯：simple -> 小模型快答(qwen2.5:1.5b)；normal/complex -> 主力(qwen3-4b)；
主力失败 -> 小模型兜底；小模型也失败 -> 明确抛错（由 orchestrator 既有兜底接手）。
"""
from __future__ import annotations

import os
import re
import time

from . import model_gateway as MG
from . import offline as OFF

CAPABILITY = {
    "name": "知衡离线分级调度",
    "switch_env": "PAI_CASCADE_ROUTE",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": ("路由小模型不可用/解析失败=落主力；主力失败=落小模型；"
                "两级都失败=抛错交上层兜底。任一步都不出网、不伪造通过"),
    "approval": "ADR-046 常开登记；本地零成本，复用 M10 报账口径",
}

ROUTER_MODEL = lambda: (os.environ.get("PAI_CASCADE_ROUTER_MODEL") or "qwen3:0.6b").strip()
SMALL_MODEL = lambda: (os.environ.get("PAI_CASCADE_SMALL_MODEL") or "qwen2.5:1.5b").strip()
MAIN_MODEL = lambda: (os.environ.get("PAI_CASCADE_MAIN_MODEL")
                      or os.environ.get("PAI_LOCAL_MODEL")
                      or "qwen3-4b-instruct:latest").strip()

ROUTE_PROMPT = (
    "你是任务分类器，只输出一个英文单词，不要解释、不要标点。\n"
    "simple = 仅限打招呼/寒暄/道谢/极短闲聊，或一句话就能答完、"
    "且不需要解释、计算、判断的事实问答（如“今天几号”）\n"
    "normal = 解释概念、业务计算/报价、普通问答、短文案、摘要抽取，"
    "或任何需要举例/理由/步骤才能答清的问题\n"
    "complex = 分析、规划、代码、对比、推理、长文、重要决策、多步任务\n"
    "铁律：解释/什么是/为什么/怎么算/多少钱/区别/建议/风险/方案/流程，一律不是 simple。\n"
    "任务：\n%s"
)
_VALID = ("simple", "normal", "complex")

# 口径护栏：0.6B 会把“解释 BOM / 出厂价怎么算”稳定判成 simple（多轮复现），
# 只改提示词不够；这里用零依赖关键词做二级拦截，宁可落主力慢一点，
# 不可让 1.5B 在解释/计算/比较类任务上答非所问。
_NEEDS_NORMAL = (
    re.compile(r"(?i)\bBOM\b|BOM表|BOM 表"),
    re.compile(r"(解释|说明|什么是|什么叫|含义|定义|为什么|怎么算|如何算|"
               r"多少钱|什么价|报价|价格|出厂价|成本|区别|差异|不同|比较|对比|"
               r"建议|方案|流程|步骤|风险|分析|规划|优化|排产)"),
)

# 1.5B 快答档专用壳：只留身份与直答规则，不把完整人格/情绪/记忆块压给小模型。
SMALL_PROMPT = (
    "[快答档 · 月白/知衡]\n"
    "直接回答用户这一句话。问候就简短回一句；其他一句话说清，不铺陈。\n"
    "禁套话：不要安抚式开场或情绪铺垫（稳住、聊聊、别担心这一类），"
    "不复述问题、不反问、不写情绪分析或记忆说明。\n"
    "不确定就说“不确定”。不卖萌、不讨好、不装人。\n"
    "[用户] %s"
)


def _needs_normal(text: str) -> bool:
    t = str(text or "")
    return any(p.search(t) for p in _NEEDS_NORMAL)


def _user_line(prompt: str) -> str:
    """从编排层拼好的完整 prompt 里取最后一条用户原话；取不到则退回原文。"""
    for line in reversed(str(prompt or "").splitlines()):
        s = line.strip()
        if s.startswith("[用户]"):
            return s.split("]", 1)[1].strip()
    return str(prompt or "").strip()[-500:]


def compact_prompt_for_small(prompt: str, user_text=None) -> str:
    """快答档专用：只留用户原话 + 极简直答规则，剥掉 persona/情绪/记忆壳。"""
    raw = str(user_text or "").strip() or _user_line(prompt)
    return SMALL_PROMPT % raw[:500]


def enabled() -> bool:
    return (os.environ.get("PAI_CASCADE_ROUTE") or "").strip().lower() in (
        "1", "on", "true", "yes")


def offline_mode() -> bool:
    """是否处于离线/本地优先态：PAI_OFFLINE 开启，或未配置任何云端 provider。

    用来决定默认情况下（model is None）是否由本模块接管。有网且显式配了云端
    provider 时不接管，保持原链路；用户仍可用 model=local_cascade 强制走本地。
    """
    if OFF.enabled():
        return True
    provider = (os.environ.get("PAI_MODEL_PROVIDER") or "").strip().lower()
    return provider in ("", "mock", "ollama", "local")


def _parse_route(text: str) -> str:
    """从模型输出里抽一个合法标签；抽不到返回空串（由调用方 fail-closed）。"""
    low = str(text or "").strip().lower()
    if "</think>" in low:
        low = low.rsplit("</think>", 1)[-1].strip()
    if low in _VALID:
        return low
    m = re.search(r"\b(simple|normal|complex)\b", low)
    return m.group(1) if m else ""


def route(text: str, call=None) -> dict:
    """判任务该走哪一级。call 可注入便于单测；返回 {level, model, reason, ok}。"""
    if not enabled():
        return {"level": "off", "model": "", "reason": "未开启分级路由", "ok": False}
    fn = call or (lambda p, m, o: MG._ollama_complete(p, m, o))
    try:
        OFF.assert_allowed("local_model", RuntimeError, "本地路由模型")
        out, _tin, _tout = fn(ROUTE_PROMPT % str(text or "")[:2000], ROUTER_MODEL(),
                              {"temperature": 0.0, "max_tokens": 16, "think": False})
        level = _parse_route(out)
        if not level:
            return {"level": "normal", "model": MAIN_MODEL(),
                    "reason": "路由输出无法解析，fail-closed 落主力", "ok": False}
        if level == "simple" and _needs_normal(text):
            return {"level": "normal", "model": MAIN_MODEL(),
                    "reason": "口径护栏：解释/计算/比较类不进 simple，落主力", "ok": True}
    except Exception as e:
        return {"level": "normal", "model": MAIN_MODEL(),
                "reason": "路由失败(%s)，fail-closed 落主力" % type(e).__name__, "ok": False}
    model = SMALL_MODEL() if level == "simple" else MAIN_MODEL()
    return {"level": level, "model": model, "reason": "路由判定", "ok": True}


def complete(prompt: str, on_delta=None, user_text=None, call=None) -> dict:
    """返回与 model_gateway.complete 同形的单条结果，附带 route/degraded 追踪。"""
    text = user_text if user_text is not None else prompt
    decision = route(text, call=call)
    fn = call or (lambda p, m, o: MG._ollama_complete(p, m, o, on_delta=on_delta))
    chosen = decision["model"]
    trace = [decision["reason"]]
    prompt_mode = "small_compact" if chosen == SMALL_MODEL() else "full"
    call_prompt = (compact_prompt_for_small(prompt, text)
                   if prompt_mode == "small_compact" else prompt)

    try:
        out, tin, tout = fn(call_prompt, chosen, {"max_tokens": 900})
    except Exception as e:
        # 主力失败 -> 小模型兜底；若主力本来就是小模型则明确抛错
        trace.append("主力(%s)失败(%s)" % (chosen, type(e).__name__))
        if chosen == SMALL_MODEL():
            raise
        chosen = SMALL_MODEL()
        prompt_mode = "small_compact"
        call_prompt = compact_prompt_for_small(prompt, text)
        try:
            out, tin, tout = fn(call_prompt, chosen, {"max_tokens": 500})
            trace.append("已降级到小模型(%s)" % chosen)
        except Exception as e2:
            trace.append("小模型也失败(%s)" % type(e2).__name__)
            raise

    return {
        "model_id": "local_cascade",
        "chosen_model": chosen,
        "model": "知衡离线分级（%s · %s）" % (decision["level"], chosen),
        "output": out,
        "cost": 0.0,
        "priced": True,
        "cost_estimate": 0.0,
        "spend_report": "[报账] 分级调度本地推理，无云端费用",
        "tokens": tin + tout,
        "tokens_in": tin,
        "tokens_out": tout,
        "latency": 0.0,
        "route": decision["level"],
        "route_ok": decision["ok"],
        "prompt_mode": prompt_mode,
        "degraded": len(trace) > 1,
        "trace": trace,
        "metrics": MG.last_metrics(),
    }


def status() -> dict:
    return {
        "enabled": enabled(),
        "router": ROUTER_MODEL(),
        "small": SMALL_MODEL(),
        "main": MAIN_MODEL(),
    }
