# -*- coding: utf-8 -*-
"""B 线 · 递归自我模型 v0：对自己“会什么/不会什么/多确定”的显式建模。

三条边界：
  · 零新存储：校准结果只写 M2 已有的 meta 键 `self.calibration`，不建表；
  · 不宣称：能力表里“有意识/有感受”如实写 False，不美化；
  · 默认关：关着时 report() 直接拒绝，不读库、不写库。

它不“感受”自己；它只是把可核对的事实摊开：哪些能力有代码、哪些缺硬件、
最近一次校准的 Brier 是多少。递归的部分是：它把“自己的测量结果”也当成
一条关于自己的事实来报告。
"""
import datetime
import json
import os

ENV_BACKEND = "PAI_SELF_KNOWLEDGE"
BACKEND_OFF = "off"
BACKEND_ON = "on"
CALIBRATION_KEY = "self.calibration"

CAPABILITY = {
    "name": "B 线 递归自我模型 v0",
    "switch_env": ENV_BACKEND,
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "关=拒绝报告；校准未测=如实说未测量；坏数据=忽略并说明",
    "probe_cost": "只读环境变量与 meta，不起进程、不联网、不写库（record 除外）",
    "approval": "未获单独批准：由 DeepSeek 自选推进；默认关。Boss 在对话中提过，但 D:\\Q 无书面留底，不作为授权。",
}

# 能力表：status 只有三种——"有" / "部分" / "没有"；evidence 指向代码或缺口。
CAPABILITIES = (
    ("记住对话与事实", "有", "M2"),
    ("按关键词召回记忆", "有", "M2 recall"),
    ("主动提醒（到点推送）", "有", "M6 + M14"),
    ("本机语音输出", "有", "M14（默认关）"),
    ("按键语音输入", "部分", "X6 1a 代码就绪，缺麦克风"),
    ("单据 OCR 看图", "部分", "多模态 v0 已申请，未实现"),
    ("读懂产品/现场照片", "没有", "需视觉模型，未接"),
    ("联网查资料", "没有", "默认不出网（S3）"),
    ("替你做重大决定", "没有", "边界：不替代判断"),
    ("有意识 / 有主观感受", "没有", "按功能态设计，不宣称"),
)


def backend():
    raw = (os.environ.get(ENV_BACKEND) or "").strip().lower()
    return raw if raw in (BACKEND_OFF, BACKEND_ON) else BACKEND_OFF


def set_enabled(on):
    os.environ[ENV_BACKEND] = BACKEND_ON if on else BACKEND_OFF


def calibration(mem):
    """读最近一次校准；没有就如实说未测量。"""
    raw = mem.get_meta(CALIBRATION_KEY)
    if not raw:
        return {"measured": False, "reason": "还没有跑过校准"}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {"measured": False, "reason": "校准记录坏了，已忽略"}
    if not isinstance(data, dict):
        return {"measured": False, "reason": "校准记录形状不对，已忽略"}
    return {"measured": True, **{k: data.get(k) for k in ("brier", "n", "accuracy", "ts")}}


def record_calibration(mem, brier, n, accuracy):
    """把一次校准的汇总写进 meta（零新表）；参数非法直接拒绝。"""
    try:
        b = float(brier)
        cnt = int(n)
        acc = float(accuracy)
    except (TypeError, ValueError):
        raise ValueError("brier/n/accuracy 必须是数字")
    if not (0.0 <= b <= 1.0):
        raise ValueError("brier 必须在 0..1")
    if cnt <= 0:
        raise ValueError("n 必须大于 0")
    if not (0.0 <= acc <= 1.0):
        raise ValueError("accuracy 必须在 0..1")
    mem.set_meta(CALIBRATION_KEY, json.dumps({
        "brier": round(b, 4), "n": cnt, "accuracy": round(acc, 4),
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False))
    return calibration(mem)


def report(mem):
    """返回一份可核对的自我描述；关着时拒绝。"""
    if backend() != BACKEND_ON:
        return {"ok": False, "reason": "自我模型开关是关的（PAI_SELF_KNOWLEDGE=on）"}
    return {
        "ok": True,
        "capabilities": [{"name": n, "status": s, "evidence": e} for n, s, e in CAPABILITIES],
        "calibration": calibration(mem),
        "note": "这是功能清单，不是体验声明；'没有' 的项不许说成 '有'。",
    }
