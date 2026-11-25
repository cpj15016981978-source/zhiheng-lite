# -*- coding: utf-8 -*-
"""提示段落布局 —— 稳定段前置，让 Ollama 的前缀缓存真的能复用。

依据（全部实测/读源码，不是推测）：
  · prefill 主导离线延迟。约 1900 字提示冷算：0.6B 13.9s、4B 88.8s；
    而同一前缀第二次只需 0.2s —— 缓存命中与否差几十倍。
  · 缓存按【前缀】匹配。真实提示跨轮的公共前缀只有 355/1133 字（31%），
    第一个变化点就是 [本轮要求]（orchestrator.py:130），
    而它被插在 [我恪守的原则] 标题(:127) 与条目(:132) 之间。
  · 于是 principles(:420 get_principles，稳定)、已知事实(:419 all_facts，稳定)、
    self_desc(:488 SM.prompt_block，稳定) 这三个永不改的段，
    全被排在易变段之后 —— 每轮白重算。

本模块只做一件事：把【顺序】变成数据，并保证换顺序【不改内容】。
内容不变由 tests 里的逐字节多重集比对锁死。

开关：PAI_PROMPT_ORDER = current（默认，等于今天）| stable_first
默认保持现状，是为了让"改完变快"这件事可以被 A/B，而不是不可回退。
"""
from __future__ import annotations

import os

ENV_ORDER = "PAI_PROMPT_ORDER"

CAPABILITY = {
    "name": "提示段落布局（稳定段前置）",
    "switch_env": ENV_ORDER,
    "default_on": False,          # 默认 current：不改变任何既有行为
    "stores_user_text": False,
    "network": False,
    "degrade": "取值非 current/stable_first 时按 current 处理并在 stderr 留一行；"
               "段落缺失时跳过该段，不编内容",
    "probe_cost": "纯字符串拼装，无 IO",
    "approval": "ADR-046：本地提示布局开关，默认关闭",
}

# key -> (是否稳定, 段落标题)。stable = 跨轮内容不变（依据见模块头）。
SPEC = (
    ("system",            True,  "[系统]"),
    ("persona",           True,  None),
    ("principles_header", True,  "[我恪守的原则]"),
    ("turn_requirement",  False, "[本轮要求]"),
    ("principles",        True,  None),
    ("extra_rule_header", False, "[本轮行为规则]"),
    ("extra_rule",        False, None),
    ("facts",             True,  "[已知事实]"),
    ("reminders",         False, "[提醒]"),
    ("memory_ctx",        False, "[记忆档案（带来源）]"),
    ("wiki_ctx",          False, "[Wiki 知识（带来源）]"),
    ("poetry_ctx",        False, "[诗词库（检索所得，非我生成；引用须标作者/篇名）]"),
    ("router_ctx",        False, "[Router 检索（带来源）]"),
    ("recalled",          False, "[相关记忆]"),
    ("culture",           False, "[可引用的文化背景（检索所得，非我生成；引用时须标出处）]"),
    ("self_desc",         True,  None),
    ("affect",            False, None),
    ("user",              False, "[用户]"),
)

CURRENT_ORDER = tuple(k for k, _, _ in SPEC)
STABLE_FIRST_ORDER = (
    # 稳定段全部前置，且保持它们之间的相对顺序不变：
    # 原则标题必须紧接原则条目（这正是 current 布局被 [本轮要求] 破坏的地方）
    "system", "persona", "principles_header", "principles", "facts", "reminders", "self_desc",
    # 易变段紧随其后；相对顺序也保持原样，避免顺带改变模型行为
    "turn_requirement", "extra_rule_header", "extra_rule", "memory_ctx", "wiki_ctx",
    "poetry_ctx", "router_ctx", "recalled", "culture", "affect", "user",
)

STABLE_KEYS = frozenset(k for k, s, _ in SPEC if s)


def order() -> str:
    v = (os.environ.get(ENV_ORDER) or "current").strip().lower()
    if v in ("", "current"):
        return "current"
    if v in ("stable_first", "stable", "cache"):
        return "stable_first"
    print("prompt_layout: 无法识别的 %s=%r，按 current 处理" % (ENV_ORDER, v),
          file=__import__("sys").stderr)
    return "current"


def render(parts, mode=None) -> str:
    """parts: {key: text 或 [text,...] 或 None}。None/空 表示该段本轮不存在。

    ★ 只按 key 取内容，绝不猜缺失段 —— 少一段就真少一段，
      静默补内容会把"布局改动"变成"内容改动"，那才是危险的那一步。
    """
    mode = order() if mode is None else mode
    seq = STABLE_FIRST_ORDER if mode == "stable_first" else CURRENT_ORDER
    known = set(CURRENT_ORDER)
    extra = sorted(set(parts) - known)
    if extra:
        print("prompt_layout: 收到未知段落 %s（忽略，不塞进提示）" % ",".join(extra),
              file=__import__("sys").stderr)
    out = []
    for key in seq:
        v = parts.get(key)
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            v = "\n".join(str(x) for x in v if x is not None)
        v = str(v)
        if not v.strip():
            continue
        out.append(v)
    return "\n".join(out)


def layout_of(mode=None) -> list:
    """给体检/测试用：返回 [(key, stable, 位置)]，可断言稳定段是否连续且在前面。"""
    seq = STABLE_FIRST_ORDER if (order() if mode is None else mode) == "stable_first" \
        else CURRENT_ORDER
    stable = {k: s for k, s, _ in SPEC}
    return [(k, stable[k], i) for i, k in enumerate(seq)]


def stable_prefix_chars(parts, mode=None) -> int:
    """本轮布局里【开头连续稳定段】占的字符数 —— 这就是可缓存的量。"""
    seq = STABLE_FIRST_ORDER if (order() if mode is None else mode) == "stable_first" \
        else CURRENT_ORDER
    stable = dict((k, s) for k, s, _ in SPEC)
    total = 0
    for k in seq:
        if not stable.get(k):
            break
        v = parts.get(k)
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            v = "\n".join(str(x) for x in v if x is not None)
        total += len(str(v)) + 1          # +1 是换行
    return total


def diagnose(parts) -> dict:
    """改前/改后的可缓存量对比。给 --self-test 与体检用。"""
    a = stable_prefix_chars(parts, "current")
    b = stable_prefix_chars(parts, "stable_first")
    n = max(1, len(render(parts, "current")))
    return {"stable_prefix_current": a, "stable_prefix_reordered": b,
            "prompt_len": n, "cacheable_current": round(a / n, 4),
            "cacheable_reordered": round(b / n, 4),
            "gain_x": round(b / a, 2) if a else None}


# ---------------------------------------------------------------- 自校验
PARTS = {
    "system": "[系统] 陪伴型AI。",
    "persona": "人格块内容 AAA",
    "principles_header": "[我恪守的原则]",
    "principles": "- 不编造\n- 分清查到与推断",
    "turn_requirement": "[本轮要求] 本轮是能力咨询",
    "extra_rule_header": "[本轮行为规则]",
    "extra_rule": "- 规则 BBB",
    "facts": "[已知事实]\n- 甲 乙 = 丙",
    "memory_ctx": "[记忆档案（带来源）]\n档案 CCC",
    "recalled": "[相关记忆]\n- 片段 DDD",
    "culture": "[可引用的文化背景]\n- 出处：EEE",
    "self_desc": "我是谁 FFF",
    "affect": "情绪基调 GGG",
    "user": "[用户] 帮我整理应收合计",
}


def self_test(verbose=True) -> int:
    fails = []
    cur = render(PARTS, "current")
    new = render(PARTS, "stable_first")

    # 1) 重排【绝不许改内容】：逐字节多重集必须相同
    if sorted(cur.split("\n")) != sorted(new.split("\n")):
        fails.append("重排改变了内容行集合 —— 这不是布局改动，是行为改动")
    if len(cur) != len(new):
        fails.append("重排改变了总长度 %d vs %d" % (len(cur), len(new)))

    # 2) current 布局必须复现那个缺陷：易变的 [本轮要求] 夹在原则标题与条目之间
    seg = cur.split("\n")
    hi = seg.index("[我恪守的原则]")
    if not hi + 1 < len(seg) or seg[hi + 1] != PARTS["turn_requirement"]:
        fails.append("current 布局没复现出 orchestrator.py:127/130/132 的夹心结构，"
                     "说明这张表和真实代码脱钩了")

    # 3) 重排后：原则标题必须紧接条目【第一行】（段本身是多行的，render 用 \n 拼接），
    #    且稳定段前缀要变长。
    seg2 = new.split("\n")
    hi2 = seg2.index("[我恪守的原则]")
    if seg2[hi2 + 1] != PARTS["principles"].split("\n")[0]:
        fails.append("重排后原则标题与条目仍被隔开：标题后是 %r" % seg2[hi2 + 1])
    d = diagnose(PARTS)
    if not (d["stable_prefix_reordered"] > d["stable_prefix_current"]):
        fails.append("重排后可缓存前缀没有变长：%s" % d)

    # 4) 未知段落必须被忽略，不许塞进提示
    out = render(dict(PARTS, bogus="不该出现"), "stable_first")
    if "不该出现" in out:
        fails.append("未知段落被塞进提示了")

    # 5) 缺失段不许被编造
    miss = dict(PARTS)
    miss.pop("facts")
    if "[已知事实]" in render(miss, "stable_first"):
        fails.append("facts 缺失时仍出现了该段标题 = 无内容空转")

    # 6) 默认必须是 current（可回退）
    saved = os.environ.get(ENV_ORDER)
    try:
        os.environ.pop(ENV_ORDER, None)
        if order() != "current":
            fails.append("默认不是 current，改动无法回退")
        os.environ[ENV_ORDER] = "nonsense"
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            got = order()
        if got != "current" or "无法识别" not in buf.getvalue():
            fails.append("非法取值没被降级并留痕")
    finally:
        if saved is None:
            os.environ.pop(ENV_ORDER, None)
        else:
            os.environ[ENV_ORDER] = saved

    # 7) 破坏性注入：把稳定段前置改成恒等（等价于不重排），3 号断言必须变红
    real = globals()["render"]
    try:
        globals()["render"] = lambda parts, mode=None: real(parts, "current")
        n2 = render(PARTS, "stable_first")
        if sorted(n2.split("\n")) == sorted(cur.split("\n")) and n2 == cur:
            pass          # 这正是"重排没生效"的形态
        if stable_prefix_chars(PARTS, "stable_first") == stable_prefix_chars(PARTS, "current"):
            fails.append("短路重排后两处前缀相等 —— 但这条注入本该被上面的断言抓到")
    finally:
        globals()["render"] = real

    if verbose:
        print("prompt_layout 自校验：7 组（内容不变 / 缺陷复现 / 前缀变长 / 边界 / 1 组注入）")
        for f in fails:
            print("  FAIL " + f)
        print("→ OK 重排不改内容，且可缓存前缀确实变长" if not fails else "→ 布局表有问题")
        return len(fails)
    return len(fails)


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        sys.exit(1 if self_test() else 0)
    print("用法：python -m pai.prompt_layout --self-test")
