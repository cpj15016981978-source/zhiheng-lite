# -*- coding: utf-8 -*-
"""知衡双脑：本地 qwen 起草 → r1 审稿 → qwen 收口。

不是模型权重合并；是三个串行推理步骤。
任何一步失败都降级，不阻断整轮。
"""
from __future__ import annotations

import os
import time

from . import ai_provider as AIP

DRAFT_MODEL = lambda: (os.environ.get("PAI_DUO_DRAFT_MODEL")
                       or "qwen3-4b-instruct:latest").strip()
REVIEW_MODEL = lambda: (os.environ.get("PAI_DUO_REVIEW_MODEL")
                        or "deepseek-r1:1.5b").strip()

# 智能双脑：只对“值得审”的问题走三步；寒暄/短问答只走单脑。
COMPLEX_CUES = (
    "分析", "判断", "方案", "规划", "风险", "为什么", "怎么", "如何",
    "对比", "取舍", "决策", "设计", "架构", "实现", "代码", "调试",
    "复盘", "评估", "策略", "计划", "帮我想", "难题", "重要", "长期",
    "写一份", "改一版", "优化", "排查", "原因", "影响", "建议",
)

CAPABILITY = {
    "name": "知衡本地双脑",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "起草/收口失败=抛错；审稿失败=降级仍出最终稿；统一下沉到 AIProvider",
    "approval": "ADR-046 常开登记；ADR-080 报账闸门与本地零成本例外",
}


def needs_review(user_text):
    q = str(user_text or "").strip()
    if not q:
        return True
    if len(q) >= 90:
        return True
    return any(c in q for c in COMPLEX_CUES)


def _call(prompt, model, max_tokens=700, on_delta=None):
    """统一走 LocalProvider，不再直连 model_gateway 私有函数。"""
    try:
        d = AIP.local(model).chat([{"role": "user", "content": prompt}],
                                  timeout=300, num_ctx=4096)
        text = d.get("content") or ""
        if on_delta and text:
            on_delta(text)
        return text, 0, 0
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(str(e)) from None


def complete(prompt, on_delta=None, user_text=None, smart=False):
    """返回与 model_gateway.complete 同形的单条结果。

    smart=True 时只对复杂问题走审稿；寒暄/短问答直接单脑，避免每句三倍延迟。
    """
    draft_model = DRAFT_MODEL()
    review_model = REVIEW_MODEL()

    if smart and not needs_review(user_text):
        single, tin, tout = _call(prompt, draft_model, max_tokens=320,
                                 on_delta=on_delta)
        return {
            "model_id": "local_smart",
            "model": "知衡智能双脑 · 单脑（%s）" % draft_model,
            "output": single,
            "cost": 0.0,
            "priced": True,
            "cost_estimate": 0.0,
            "spend_report": "[报账] 智能双脑走单脑，本地无云端费用",
            "tokens": tin + tout,
            "tokens_in": tin,
            "tokens_out": tout,
            "latency": 0.0,
        }

    draft, tin1, tout1 = _call(prompt, draft_model, max_tokens=360)

    review_prompt = (
        "你是严格但简洁的审稿人。下面是一次 AI 回答的任务上下文和草稿。\n"
        "只指出：事实错误、遗漏的关键点、可能误导用户的地方、语气问题。\n"
        "最多 5 条，不要重写全文，不要输出客套话。\n\n"
        "[任务上下文]\n%s\n\n[草稿]\n%s\n" % (prompt[:6000], draft[:6000])
    )
    review, tin2, tout2 = "", 0, 0
    try:
        review, tin2, tout2 = _call(review_prompt, review_model, max_tokens=120)
    except Exception:
        review = ""

    final_prompt = (
        "%s\n\n[双脑审稿意见]\n%s\n\n"
        "请基于审稿意见给出最终回答。保持原本的时中口吻和事实边界；"
        "不要提“审稿”“草稿”“双脑”这些过程词。"
        % (prompt, review or "（审稿模型不可用，请自行复核）")
    )
    try:
        final, tin3, tout3 = _call(final_prompt, draft_model, max_tokens=320,
                                   on_delta=on_delta)
    except Exception:
        final, tin3, tout3 = draft, 0, 0
        if on_delta:
            on_delta(draft)

    return {
        "model_id": "local_smart" if smart else "local_duo",
        "model": "知衡双脑（%s + %s）" % (draft_model, review_model),
        "output": final or draft,
        "cost": 0.0,
        "priced": True,
        "cost_estimate": 0.0,
        "spend_report": "[报账] 双脑本地推理，无云端费用",
        "tokens": tin1 + tout1 + tin2 + tout2 + tin3 + tout3,
        "tokens_in": tin1 + tin2 + tin3,
        "tokens_out": tout1 + tout2 + tout3,
        "latency": 0.0,
    }
