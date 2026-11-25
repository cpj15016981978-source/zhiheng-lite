# -*- coding: utf-8 -*-
"""I3：拒绝不可翻盘的最小策略层。

规则：
  - 只有 allow-once 放行
  - deny 一旦产生，后续 allow 不能覆盖
  - 没有回答 = unavailable = 拒绝
"""

ALLOW_ONCE = "allow-once"
DENY = "deny"
CANCEL = "cancel"
UNAVAILABLE = "unavailable"


class Decision:
    def __init__(self):
        self.value = None
        self.reason = ""
        self.final = False

    def allow_once(self, reason=""):
        if self.final:
            return self
        self.value = ALLOW_ONCE
        self.reason = reason
        return self

    def deny(self, reason=""):
        if self.final:
            return self
        self.value = DENY
        self.reason = reason
        self.final = True
        return self

    def cancel(self, reason=""):
        if self.final:
            return self
        self.value = CANCEL
        self.reason = reason
        self.final = True
        return self

    def unavailable(self, reason=""):
        if self.final:
            return self
        self.value = UNAVAILABLE
        self.reason = reason
        self.final = True
        return self

    def is_allowed(self):
        return self.value == ALLOW_ONCE


def run_checks(checks, ctx=None):
    d = Decision()
    for check in checks or ():
        check(d, ctx)
        if d.final:
            break
    if d.value is None:
        d.unavailable("没有策略给出决定")
    return d


def authorize(capability, caps):
    """任务能力 vs 令牌能力；caps 必填；caps=None ⇒ unavailable（不再按 dev 放行）。"""
    d = Decision()
    if caps is None:
        return d.unavailable("缺少 caps；拒绝")
    if capability in caps:
        return d.allow_once("令牌包含 %s" % capability)
    return d.deny("令牌没有 %s 能力" % capability)
