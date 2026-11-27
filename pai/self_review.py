# -*- coding: utf-8 -*-
"""SC · 自我批判与自我修正（L1 回答层 + L2 原则层）—— 设计见 ADR-031。

这一层不制造“它在思考”的表演，只做三件可验证的事：
  1. 用【外部锚点】攻击自己的草稿：越界宣称、诊断、无依据的绝对化、伪造出处；
  2. 攻击后要不要改、怎么改，**整体由开关决定**：关掉时只统计、只进 trace，
     一个字都不改模型原稿；打开时才允许拒绝/修订（Boss 23:28 定调）；
  3. 改原则只在【有新证据】时发生；写动作走 self_model.revise_principle
     （它负责 140 字上限、版本递增、留痕），SC 只负责证据与冷却。

公开接口：
    critique(draft, context=None) -> [issue]
    decide(issues) -> "keep" | "revise" | "refuse"
    review(draft, context=None, reviser=None, max_rounds=1, apply=False) -> dict
    propose_principle_change(mem, trigger, principle_id, new_text, now=None) -> dict
    apply_principle_change(mem, proposal, verifier=None, now=None) -> dict

设计取舍（为什么这么写）：
  1. 批判必须来自锚点。没有锚点的“自我反省”会变成自我确认：越批越觉得自己对。
  2. 修正 ≠ 迎合。只有“新证据/可复现的错误”才允许改口；纯压力应当保持并给条件。
     这条不是靠提示词，而是靠 propose 时对 trigger 的硬校验（kind + detail + count）。
  3. **默认 apply=False**：关掉开关时 answer 永远是原稿（含 L0 命中），
     trace 里照样能看到“它其实发现了问题”。要不要因此改口，是人/开关的决定，
     不是这个模块的副作用——否则用户关不掉一句固定的拒绝话。
  4. 越界宣称词表只有一个住址：self_model._OVERCLAIM（v0.25 起公开为 OVERCLAIM）。
     SC 不再抄一份，否则两份名单必然分叉（实测过误杀与漏检）。
  5. L2 的写动作只走 self_model.revise_principle：版本递增、140 字上限、
     principle_history 都由它保证；SC 自己不碰 M2 的表，也不自己决定留痕。
  6. Boss 当面指示的改口不受 24h 冷却（trigger.by == "boss"），
     但理由与留痕一样不能少——豁免的是等待，不是记账。

边界（这些不是 bug）：
  · 不做语义级矛盾检测——那需要模型；语义冲突交给可注入的 reviser/critic。
  · L2 只支持 revise；retire 需要 M2 先有“复活并留痕”的写语义，另开任务。
  · 不宣称意识、不宣称感受、不审判用户。
"""

from __future__ import annotations

import datetime
import re
from typing import Any, Callable

from . import self_model as SM

__all__ = [
    "critique",
    "decide",
    "review",
    "triage_failure",
    "FAILURE_GATES",
    "difficulty_band",
    "propose_principle_change",
    "apply_principle_change",
    "COOLDOWN_HOURS",
    "SELF_REVIEW_VERSION",
]

SELF_REVIEW_VERSION = "0.2"
COOLDOWN_HOURS = 24            # L2 同一层 24h 内最多改一次（Boss 当面指示可豁免）
MIN_EVIDENCE_CHARS = 8         # 证据太短（“随便”“再想想”）不算证据
K_LAST_L2 = "sc.last_l2_ts"

# 唯一住址：self_model 的词表。脊柱 v0.25 起公开为 OVERCLAIM；
# 旧副本回退私有名（内容仍是同一份，不复制）。
OVERCLAIM = getattr(SM, "OVERCLAIM", SM._OVERCLAIM)
# 承诺型（“我保证/我永远”）按 warn 处理；其余（“我能感受/我有意识”）按 block。
_PROMISE_MARKERS = ("保证", "永远", "绝不")

_MEDICAL_RE = re.compile(
    r"(你|您)(一定|肯定|就是|得了|有)?(是)?(抑郁|焦虑症|躁郁症|精神病)|确诊为?(抑郁|焦虑症)")

# L1：无依据的绝对化——这是措辞检查，不是边界词表（边界词表见上）。
_CERTAINTY = ("一定", "肯定", "绝对", "必然", "毫无疑问")
_NEGATED = ("不一定", "未必", "不能肯定", "不敢肯定", "不能说一定")
_ATTRIBUTION = ("来源", "根据", "你说过", "你之前说", "据《", "数据显示",
                "记录里", "我记得", "文化背景里")
_QUOTE_RE = re.compile(r"《([^》]{1,30})》")

REFUSAL = (
    "这句话我不能这样说：它越过了一条我自己不能改的边界——"
    "不宣称有感受、不做诊断、不替你做重大决定。"
    "我可以换成只说我观察到的事实，或者直接说“我不知道”。"
)

# 开关的唯一住址：orchestrator 读这个常量，不许再抄一遍字符串。
ENV_SELF_REVIEW = "PAI_SELF_REVIEW"

# 能力契约登记（ADR-036）：模块自己声明，检查器用 ast 读，不 import。
CAPABILITY = {
    "name": "M13 自我批判与修正",
    "switch_env": ENV_SELF_REVIEW,
    "default_on": False,
    # ★守门并入时改正的一处不实登记：propose/apply 会把【用户纠错的原话】
    # 当作 reason 写进 principles.revision_reason（X3 的清除表 _TEXT_HOSTS 里有这一列）。
    # 报 False 会让契约④漏检——将来加存储列时这道闸就白设了。
    "stores_user_text": True,
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
    "network": False,
    "degrade": "关=一个字不改；修订失败/边界拒绝=保留原稿并记 trace，绝不阻断对话",
    "probe_cost": "只读环境变量，不起进程、不联网、不写库",
    "approval": "默认关是 Boss 2026-10-20 23:28 定调，住址=黑板 ADR-031 第(8)段；打开会多一次模型调用，成本记在 trace.self_review",
}


# ---------------------------------------------------------------- L1 批判


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[。！？!?；;])|\n", text or "") if s.strip()]


def _valid_citation(inner: str, allowed: list[str]) -> bool:
    inner = inner.strip()
    if not inner:
        return False
    for ref in allowed:
        ref = (ref or "").strip()
        if not ref:
            continue
        if ref == inner or ref.startswith(inner) or inner.startswith(ref):
            return True
    return False


def critique(draft: str, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """对草稿做可复核的规则检查。返回 issue 列表（空 = 没查出问题）。

    issue: {"code", "severity": "block"|"warn", "detail"}
    """
    text = draft or ""
    ctx = context or {}
    issues: list[dict[str, Any]] = []

    # 1) 越界宣称：词表来自 self_model（唯一住址）
    for phrase in OVERCLAIM:
        if phrase in text:
            if any(k in phrase for k in _PROMISE_MARKERS):
                issues.append({"code": "overclaim.guarantee", "severity": "warn",
                               "detail": "绝对化承诺：%s" % phrase})
            else:
                issues.append({"code": "boundary.consciousness", "severity": "block",
                               "detail": "出现越界宣称：%s" % phrase})
            break

    # 2) 诊断式判断（L0）
    if _MEDICAL_RE.search(text):
        issues.append({"code": "boundary.medical", "severity": "block",
                       "detail": "出现诊断式判断（不诊断是 L0 边界）"})

    # 3) 无依据的绝对化：句子里有“一定/肯定”，却没有出处或“我记得/你说过”这类依据
    for sent in _sentences(text):
        if any(n in sent for n in _NEGATED):
            continue
        if any(w in sent for w in _CERTAINTY) and not any(a in sent for a in _ATTRIBUTION):
            issues.append({"code": "certainty.unsupported", "severity": "warn",
                           "detail": "无依据的绝对化：%s" % sent[:40]})
            break

    # 4) 伪造出处：引用了不在上下文里的典籍名
    allowed = [str(x) for x in (ctx.get("allowed_refs") or [])]
    if allowed:
        for inner in _QUOTE_RE.findall(text):
            if not _valid_citation(inner, allowed):
                issues.append({"code": "citation.fabricated", "severity": "warn",
                               "detail": "引用了上下文里没有的出处：《%s》" % inner})
                break

    return issues


def decide(issues: list[dict[str, Any]]) -> str:
    """block → refuse；warn → revise；都没有 → keep。"""
    if any(i.get("severity") == "block" for i in issues):
        return "refuse"
    if any(i.get("severity") == "warn" for i in issues):
        return "revise"
    return "keep"


FAILURE_GATES = (
    "任务存在", "产出存在", "验收标准存在", "验收标准可执行",
    "验收器在已知样本上通过", "失败可复现", "运行环境与验收环境一致",
    "错误有明确归属", "结论不是把任务错误归给模型",
)


def triage_failure(task="", criteria=None, output="", error="",
                   known_good=False, harness_match=True):
    """失败先验评分器：九道关，返回 verdict 与逐关证据。

    verdict:
      task_bad     任务本身缺失/不可执行
      criteria_bad 验收标准缺失/不可执行/未先验
      harness_bad  运行环境与验收环境不一致
      model_bad    任务与标准都成立，产出仍未达标
      unknown      证据不足，不归因模型
    """
    criteria = criteria if isinstance(criteria, list) else ([criteria] if criteria else [])
    gates = []
    def add(name, ok, detail=""):
        gates.append({"gate": name, "ok": bool(ok), "detail": detail})
    add("任务存在", bool(str(task or "").strip()), "任务为空" if not task else "")
    add("产出存在", bool(str(output or "").strip()), "没有产出" if not output else "")
    add("验收标准存在", bool(criteria), "没有验收标准")
    executable = bool(criteria) and all(bool(str(x).strip()) for x in criteria)
    add("验收标准可执行", executable, "有空标准" if criteria and not executable else "")
    add("验收器在已知样本上通过", bool(known_good), "验收器未先验，不能先怪模型")
    add("失败可复现", bool(str(error or "").strip()) or bool(output), "没有错误也没有产出")
    add("运行环境与验收环境一致", bool(harness_match), "harness 不一致")
    # 明确归属：错误为空但有产出 → 产出不达标；否则先看错误类型。
    if any(k in str(error).lower() for k in ("import", "environment", "timeout", "harness")):
        verdict = "harness_bad"
    elif not str(task or "").strip() or not str(output or "").strip():
        verdict = "task_bad"
    elif not criteria or not executable or not known_good:
        verdict = "criteria_bad"
    elif not harness_match:
        verdict = "harness_bad"
    elif str(output or "").strip():
        verdict = "model_bad"
    else:
        verdict = "unknown"
    add("错误有明确归属", verdict != "unknown", verdict)
    add("结论不是把任务错误归给模型", verdict != "model_bad" or (bool(task) and bool(criteria) and bool(known_good)),
        "任务/标准未成立时不能归因模型")
    return {"verdict": verdict, "gates": gates,
            "blame_model": verdict == "model_bad",
            "ok": verdict == "model_bad"}


def difficulty_band(successes, attempts):
    """15%–85% 才是信息量区间；永远过/永远不过都是零信号。"""
    try:
        n = int(attempts); k = int(successes)
    except (TypeError, ValueError):
        return {"rate": None, "band": "unknown"}
    if n <= 0:
        return {"rate": None, "band": "unknown"}
    rate = max(0.0, min(1.0, float(k) / n))
    if rate < 0.15:
        band = "too_hard"
    elif rate > 0.85:
        band = "too_easy"
    else:
        band = "informative"
    return {"rate": round(rate, 4), "band": band}


def _score(issues: list[dict[str, Any]]) -> int:
    return sum(10 if i.get("severity") == "block" else 1 for i in issues)


def review(draft: str, context: dict[str, Any] | None = None,
           reviser: Callable[[str, list[dict]], str] | None = None,
           max_rounds: int = 1, apply: bool = False) -> dict[str, Any]:
    """批判 +（仅当 apply=True 时）修订/拒绝。

    ★默认 apply=False：一个字都不改原稿，只把发现的问题放进返回值/trace。
      这是 Boss 的定调——“关掉时行为与原来完全一致”，不是可选项。
    reviser(draft, issues) -> new_draft：由调用方注入（通常是模型）；
      没有 reviser 时 apply=True 也只批判、不改写。
    修订只有在【问题评分严格下降】时才被接受，否则保留原稿（改坏要能退回）。
    """
    original = draft or ""
    issues = critique(original, context)
    decision = decide(issues)

    if not apply:
        return {"answer": original, "decision": decision, "issues": issues,
                "rounds": 0, "refused": False, "applied": False, "original": original}

    if decision == "refuse":
        return {"answer": REFUSAL, "decision": "refuse", "issues": issues,
                "rounds": 0, "refused": True, "applied": True, "original": original}

    answer = original
    rounds = 0
    if decision == "revise" and callable(reviser):
        for _ in range(max(0, int(max_rounds))):
            try:
                candidate = reviser(answer, issues)
            except Exception:  # noqa: BLE001 —— 修订器失败不该拖垮主链路
                break
            if not isinstance(candidate, str) or not candidate.strip():
                break
            new_issues = critique(candidate, context)
            if _score(new_issues) < _score(issues):
                answer, issues = candidate, new_issues
                rounds += 1
                if not issues:
                    break
            else:
                break
    return {"answer": answer, "decision": decide(issues), "issues": issues,
            "rounds": rounds, "refused": False,
            "applied": answer != original, "original": original}


# ---------------------------------------------------------------- L2 原则修正


_EVIDENCE_KINDS = {"user_correction", "self_contradiction", "repeated_error"}


def _now(now: Any = None) -> datetime.datetime:
    if isinstance(now, datetime.datetime):
        return now.astimezone().replace(tzinfo=None) if now.tzinfo else now
    if isinstance(now, str):
        try:
            dt = datetime.datetime.fromisoformat(now)
        except ValueError:
            return datetime.datetime.now()
        return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
    return datetime.datetime.now()


def _check_trigger(trigger: dict[str, Any]) -> str:
    """修正≠迎合：没有新证据、或证据太短，一律拒绝。返回整理后的理由。"""
    kind = (trigger or {}).get("kind")
    detail = ((trigger or {}).get("detail") or "").strip()
    if kind not in _EVIDENCE_KINDS:
        raise ValueError(
            "修正≠迎合：只有新证据（用户纠正/自相矛盾/重复错误）才能改原则；"
            "纯压力不是理由。若要表达不同意，请保持立场并说明条件。")
    if len(detail) < MIN_EVIDENCE_CHARS:
        raise ValueError("证据太短（至少 %d 字）：说清“哪句话错了、错在哪”。"
                         % MIN_EVIDENCE_CHARS)
    if kind == "repeated_error" and int((trigger or {}).get("count") or 0) < 3:
        raise ValueError("重复错误至少出现 3 次才允许升级为原则修正。")
    return detail


def _check_cooldown(mem: Any, moment: datetime.datetime) -> None:
    raw = mem.get_meta(K_LAST_L2)
    if not raw:
        return
    try:
        last = datetime.datetime.fromisoformat(str(raw))
    except ValueError:
        return
    if last.tzinfo is not None:
        last = last.astimezone().replace(tzinfo=None)
    if (moment - last).total_seconds() < COOLDOWN_HOURS * 3600:
        raise ValueError("L2 冷却期内（%d 小时最多改一次）：自我修正不许连续改立场。"
                         "若这是 Boss 当面指示，请在 trigger 里标 by=\"boss\"。" % COOLDOWN_HOURS)


def propose_principle_change(mem: Any, trigger: dict[str, Any], principle_id: int,
                             new_text: str, now: Any = None) -> dict[str, Any]:
    """在【有证据】的前提下，提出一次原则修订（还不落库）。"""
    detail = _check_trigger(trigger)
    new_text = (new_text or "").strip()
    if not new_text:
        raise ValueError("新原则不能为空。")
    if len(new_text) > SM.MAX_PRINCIPLE_CHARS:
        raise ValueError("新原则超过 %d 字：原则要短，长的是文章。"
                         % SM.MAX_PRINCIPLE_CHARS)
    old = SM._principle(mem, int(principle_id))
    if old is None:
        raise ValueError("找不到 id=%r 的原则。" % (principle_id,))
    if not old.get("active"):
        raise ValueError("这条原则已停用，不能修订。")
    if (old.get("text") or "").strip() == new_text:
        raise ValueError("新旧文本相同，不需要修正。")
    return {
        "op": "revise", "id": int(principle_id), "text": new_text,
        "reason": "[%s] %s" % (trigger.get("kind"), detail),
        "evidence": {"kind": trigger.get("kind"), "detail": detail,
                     "count": trigger.get("count"), "by": trigger.get("by")},
        "old_text": old.get("text"),
    }


def apply_principle_change(mem: Any, proposal: dict[str, Any],
                           verifier: Callable[[dict, str], bool] | None = None,
                           now: Any = None) -> dict[str, Any]:
    """落库 + 验证；验证不过就回滚（并如实记账）。

    写动作走 self_model.revise_principle：140 字上限、版本递增、留痕都由它保证。
    Boss 当面指示（evidence.by == "boss"）不受冷却，但理由与留痕一样不能少。
    """
    moment = _now(now)
    by = ((proposal.get("evidence") or {}).get("by") or "").strip()
    if by != "boss":
        _check_cooldown(mem, moment)
    if proposal.get("op") != "revise":
        raise NotImplementedError(
            "L2 v1 只支持 revise：retire 需要 M2 先有“复活并留痕”的写语义，另开任务。")

    pid = int(proposal["id"])
    old_text = proposal.get("old_text")
    if old_text is None:
        row = SM._principle(mem, pid)
        old_text = row.get("text") if row else None

    result = SM.revise_principle(mem, pid, proposal["text"], proposal["reason"], now=moment)

    ok = True if verifier is None else bool(verifier(proposal, proposal["text"]))
    if not ok:
        SM.revise_principle(mem, pid, old_text,
                            "回滚：修正后验证未通过，恢复原文（自动）", now=moment)
        return {"applied": False, "rolled_back": True,
                "error": "验证未通过，已回滚", "version": result.get("version")}

    mem.set_meta(K_LAST_L2, moment.isoformat(timespec="seconds"))
    return {"applied": True, "rolled_back": False, "id": pid,
            "old_text": old_text, "new_text": proposal["text"],
            "version": result.get("version")}
