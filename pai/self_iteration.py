# -*- coding: utf-8 -*-
"""R3 自我迭代 v0：提案 → 测试 → 人批 → 应用 → 回滚。

不做模型权重自更新，不做自我批准，不改目标/身份/权限。
"""
import datetime
import uuid

CAPABILITY = {
    "name": "R3 自我迭代 v0",
    "switch_env": "PAI_SELF_ITERATION",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "PAI_SELF_ITERATION 未开时只提供纯函数；不自动改代码/提示/工具",
    "probe_cost": "只读模块常量；不连库、不调模型、不联网",
    "approval": "未获单独批准：R3 只做受控提案，L2 代码改动仍需人批",
}

LEVEL = {"prompt": "L0", "tool": "L1", "code": "L2"}
FORBIDDEN = {"goal", "identity", "permission", "weights"}


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _nid():
    return "it_" + uuid.uuid4().hex[:10]


class SelfIteration:
    def __init__(self):
        self.items = {}

    def propose(self, kind, target, reason, patch=""):
        if kind in FORBIDDEN:
            raise ValueError("禁止自我迭代：%s" % kind)
        if kind not in LEVEL:
            raise ValueError("kind 只能是 prompt/tool/code")
        if not str(target or "").strip() or not str(reason or "").strip():
            raise ValueError("target/reason 必填")
        if kind == "code" and not str(patch or "").strip():
            raise ValueError("L2 代码改动必须带 diff/patch")
        iid = _nid()
        self.items[iid] = {
            "id": iid, "kind": kind, "level": LEVEL[kind],
            "target": str(target), "reason": str(reason), "patch": str(patch),
            "status": "draft", "created": _now(), "test": None,
            "approved_by": "", "applied": None, "rolled_back": None,
            "history": [{"at": _now(), "op": "propose"}],
        }
        return iid

    def get(self, iid):
        return self.items.get(iid)

    def test(self, iid, test_fn):
        it = self._need(iid)
        if it["status"] not in ("draft", "test_failed"):
            raise ValueError("当前状态不能测试：%s" % it["status"])
        ok = bool(test_fn(dict(it)))
        it["test"] = {"ok": ok, "at": _now()}
        it["status"] = "tested" if ok else "test_failed"
        it["history"].append({"at": _now(), "op": "test", "ok": ok})
        return ok

    def approve(self, iid, approver):
        it = self._need(iid)
        if it["status"] != "tested":
            raise ValueError("未通过测试，不能批准：%s" % it["status"])
        who = str(approver or "").strip()
        if not who or who.lower() in ("self", "ai", "agent", "deepseek"):
            raise ValueError("不能自我批准")
        it["status"] = "approved"
        it["approved_by"] = who
        it["history"].append({"at": _now(), "op": "approve", "by": who})
        return True

    def reject(self, iid, approver, reason=""):
        it = self._need(iid)
        it["status"] = "rejected"
        it["history"].append({"at": _now(), "op": "reject",
                              "by": str(approver), "reason": str(reason)})

    def apply(self, iid, apply_fn):
        it = self._need(iid)
        if it["status"] != "approved":
            raise ValueError("未批准，不能应用：%s" % it["status"])
        out = apply_fn(dict(it))
        it["status"] = "applied"
        it["applied"] = {"at": _now(), "result": str(out)[:500]}
        it["history"].append({"at": _now(), "op": "apply"})
        return out

    def rollback(self, iid, rollback_fn):
        it = self._need(iid)
        if it["status"] != "applied":
            raise ValueError("只能回滚已应用项：%s" % it["status"])
        out = rollback_fn(dict(it))
        it["status"] = "rolled_back"
        it["rolled_back"] = {"at": _now(), "result": str(out)[:500]}
        it["history"].append({"at": _now(), "op": "rollback"})
        return out

    def _need(self, iid):
        it = self.items.get(iid)
        if not it:
            raise KeyError("提案不存在：%s" % iid)
        return it
