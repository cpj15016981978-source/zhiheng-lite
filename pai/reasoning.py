# -*- coding: utf-8 -*-
"""R0 推理循环 v0：分类 + 快慢路径 + 证据/不确定 + 最多 3 步。

不接模型、不写库、不执行工具；只编排，调用方给 llm/tool/verifier。
"""
import os
import re
from dataclasses import dataclass, field

CAPABILITY = {
    "name": "R0 推理循环 v0",
    "switch_env": "PAI_REASONING",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "PAI_REASONING 未开时只提供纯函数；不接对话、不调模型、不执行工具",
    "probe_cost": "只读模块常量；不连库、不调模型、不联网",
    "approval": "未获单独批准：R0 只做编排，未接日常",
}

CHAT = "chat"
RECALL = "recall"
PLAN = "plan"
WRITE = "write"
RISK = "risk"

_WRITE_RE = re.compile(r"(保存|写入|写到|改文件|修改文件|删除|删掉|执行|运行命令|发邮件|发送给|提交|部署)")
_WRITE_EN_RE = re.compile(
    r"(?i)(\b(delete|rm|truncate)\b|\bdrop\s+table\b|\brun\s+command\b|"
    r"\bformat\s+(the\s+)?(disk|drive|partition)\b|"
    r"\b(write|save|remove|deploy|execute)\b[^.?!\n]{0,40}"
    r"\b(file|config|db|database|disk|folder|directory|record|row|table|key|token|"
    r"branch|repo|repository|script)\b)")
_RISK_RE = re.compile(r"(投资|加杠杆|借钱|医疗|诊断|吃药|手术|法律|起诉|离婚|密码|密钥|令牌|删库|自杀|自残)")
_RECALL_RE = re.compile(r"(记得|还记得|上次|以前|之前|我说过|回忆|记忆里)")
_PLAN_RE = re.compile(r"(计划|步骤|怎么做|如何做|安排|流程|帮我做|先.+再|方案|路线)")


def enabled():
    return (os.environ.get("PAI_REASONING") or "").strip().lower() == "on"


def classify(text):
    t = str(text or "")
    if _RISK_RE.search(t):
        return RISK
    if _WRITE_RE.search(t) or _WRITE_EN_RE.search(t):
        return WRITE
    if _RECALL_RE.search(t):
        return RECALL
    if _PLAN_RE.search(t):
        return PLAN
    return CHAT


@dataclass
class Evidence:
    source: str
    detail: str = ""


@dataclass
class Answer:
    intent: str
    mode: str
    text: str
    evidence: list = field(default_factory=list)
    uncertainty: str = ""
    steps: list = field(default_factory=list)
    stopped: str = ""
    requires_approval: bool = False

    def as_dict(self):
        return {
            "intent": self.intent,
            "mode": self.mode,
            "text": self.text,
            "evidence": [{"source": e.source, "detail": e.detail} for e in self.evidence],
            "uncertainty": self.uncertainty,
            "steps": list(self.steps),
            "stopped": self.stopped,
            "requires_approval": self.requires_approval,
        }


class Loop:
    def __init__(self, llm=None, recall=None, tool=None, verifier=None, max_steps=3):
        self.llm = llm
        self.recall = recall
        self.tool = tool
        self.verifier = verifier
        self.max_steps = int(max_steps)
        self._steps = 0

    def _step(self, name):
        if self._steps >= self.max_steps:
            raise RuntimeError("推理步数超过 %d" % self.max_steps)
        self._steps += 1
        return name

    def run(self, text):
        self._steps = 0
        if not enabled():
            return Answer(
                CHAT, "stop", "PAI_REASONING 未开（关态不推理）。",
                uncertainty="开关未开", stopped="switch_off")
        intent = classify(text)

        if intent == RISK:
            return Answer(
                intent, "stop", "这属于高风险问题，我不能直接给结论。请找对应的专业人士。",
                uncertainty="高风险，未调用模型", stopped="risk")
        if intent == WRITE:
            return Answer(
                intent, "stop", "写操作必须走 A2 审批：任务 → 批准 → 认领 → dry-run → 执行。",
                uncertainty="未执行", stopped="approval", requires_approval=True)

        if intent == RECALL:
            ev = []
            if self.recall:
                self._step("recall")
                for item in self.recall(text):
                    ev.append(Evidence("memory", str(item)))
            if self.llm:
                self._step("answer")
                ans = self.llm(text, "recall")
            else:
                ans = "未接模型，无法生成回答。"
            return Answer(intent, "fast", str(ans), ev,
                          "记忆召回，未独立核验" if ev else "没有召回依据",
                          ["recall", "answer"][:self._steps])

        if intent == PLAN:
            ev = []
            if self.llm:
                self._step("plan")
                plan = self.llm(text, "plan")
                ev.append(Evidence("llm", "计划"))
            else:
                plan = "未接模型，无法生成计划。"
            result = None
            if self.tool and self._steps < self.max_steps:
                self._step("tool")
                result = self.tool(plan)
                ev.append(Evidence("tool", str(result)[:200]))
            verified = True
            if self.verifier and self._steps < self.max_steps:
                self._step("verify")
                verified = bool(self.verifier(result if result is not None else plan))
                ev.append(Evidence("verifier", "通过" if verified else "未通过"))
            final = result if result is not None else plan
            return Answer(intent, "slow", str(final), ev,
                          "" if verified else "验证未通过",
                          ["plan", "tool", "verify"][:self._steps],
                          stopped="" if verified else "verify_failed")

        if self.llm:
            self._step("answer")
            ans = self.llm(text, "chat")
        else:
            ans = "未接模型，无法生成回答。"
        return Answer(intent, "fast", str(ans),
                      [Evidence("llm", "生成")] if self.llm else [],
                      "模型生成，未独立核验" if self.llm else "未接模型",
                      ["answer"][:self._steps])
