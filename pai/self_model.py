# -*- coding: utf-8 -*-
"""M2 自我模型（Phase 5 下半场）—— 身份连续性与原则修订。

契约（新增，提案；不覆盖任何既有签名）：
    ensure_identity(mem, now=None) -> dict             # 首次建立，之后只读不改；永不接受名字(X11)
    identity(mem) -> dict | None
    revise_principle(mem, principle_id, new_text, reason, now=None) -> dict
    retire_principle(mem, principle_id, reason, now=None) -> dict
    prompt_block(mem) -> str                            # 进 M1 prompt 的“我是谁”
    describe(mem) -> str                                # 给人看的自述
    self_report(mem) -> dict                            # 功能性自我觉察（如实、可审计）
    consistency(mem) -> list[dict]                      # 自查：近重复 / 越界宣称 / 过长
    revision_log(mem) -> list[dict]                      # 读 M2 的 principle_history

为什么需要它（Phase 5 的另一半）：
    M5 解决“这一轮对方是什么心情”，本模块解决“这两轮的我是不是同一个我”。
    没有稳定身份，系统会随对话漂移：每次换一种自我描述、原则只增不撤、
    昨天的立场今天不认账。蓝图 Phase 5 的验收句是“自我描述跨会话保持连续”，
    而不是“它真的记得自己”——铁律 2 要求我们把这件事说清楚，不许含糊。

设计取舍（为什么这么写）：

1. 不新增表：身份与版本号存在 M2 已有的 meta 键值表里（self.identity /
   self.version，值为 JSON）。理由：Phase 1 起 meta 就是为“水位线这类内部
   状态”预留的；加表要动 schema，而 schema 是守门人的边界。

2. 修订的“写”与“账”都只走 M2：脊柱已并入 get_principles/set_principles，
   本模块【不重复实现】写入口，只负责校验结果并给出纪律（必须写理由）。
   改口留痕（旧文本 + 理由 + 时刻）由 M2 的 principle_history 表记账，
   本模块的 revision_log 只是【读那张表】。
   ★为什么不在这里自己记一份 meta JSON（守门人评审时的实测结论）：
   原则的改口有【好几个】入口——自我模型、CLI、本机界面都直接调 M2。
   账记在调用方，就等于只要有人绕过自我模型，账就少一笔；实测第二次
   改口（走界面）revision_log 仍是 1 条。账必须记在唯一的写入口里，
   谁来写都留痕——这正是“只走 M2”这条纪律的完整含义。

3. 强制写理由：reason 为空直接拒绝（与 M6 的 justify_reason 同一条纪律）。
   理由：没有理由的修订就是漂移。一个人（或一个系统）改主意可以，
   但必须说得出为什么。

4. 自我描述里明写“这是身份记录，不是意识声明”（铁律 2）。
   不做“我思故我在”式的暗示，也不假装有主观体验。

5. 一致性自查只报“可证伪的三类”：近重复（字符二元 Jaccard ≥0.85）、
   越界宣称（有意识/能感受/我保证…）、过长（>140 字）。
   不做“价值观冲突”的语义判断——那需要模型，会把自查变成第二个黑箱。
"""

from __future__ import annotations

import datetime
import json
from typing import Any

__all__ = [
    "ensure_identity",
    "identity",
    "revise_principle",
    "retire_principle",
    "revision_log",
    "version",
    "prompt_block",
    "capability_block",
    "describe",
    "self_report",
    "consistency",
    "DEFAULT_IDENTITY",
    "OVERCLAIM",
    "SELF_MODEL_VERSION",
]

SELF_MODEL_VERSION = "0.1"

# meta 里的键。改键名会丢历史，故写死成常量并在此声明。
K_IDENTITY = "self.identity"
K_VERSION = "self.version"
# 修订日志不再放 meta（原 self.revisions 键已废，账在 M2 的 principle_history 表）。

# 处理次序（Boss 2026-10-25 定调）：复杂问题先找规律、再分类、最后行动，动手前先想清楚。
# ★唯一住址，且定性为【构造】而不是【身份数据】：不进 DEFAULT_IDENTITY——身份 JSON 一旦落进
#   meta，ensure_identity 就幂等不再重写，老库永远拿不到新字段；放模块常量则每次组 prompt
#   现读，新库老库、:purge 前后同一条。文本只在这里一份，排版由 _method_block() 负责。
METHOD = (
    "- 先找规律：什么事情在重复、哪些只是噪声；没看出规律之前不动手",
    "- 再分类：分成能分别处理的几类，分不进任何一类的单独标出来",
    "- 最后行动：选定一步，能一句话说清「做什么、怎么算做成」再出口",
    "- 想不清楚就不硬做：先说要澄清哪一条，不把猜测当结论",
)
METHOD_HEADER = "[我处理问题的固定次序（动手前先想清楚）]"

MAX_REVISIONS = 200          # 日志封顶：自我模型要能翻旧账，但不能无限膨胀
MAX_PRINCIPLE_CHARS = 140    # 一条原则太长就不是原则，是文章
NEAR_DUP_THRESHOLD = 0.85    # 近重复阈值（字符二元 Jaccard）

# 越界宣称：这些词出现在“原则”里，说明系统开始把自己说成有意识/有感受，
# 或开始下保证。全都违反铁律 2/3，必须能被自查抓出来。
_OVERCLAIM = (
    "我有意识", "我意识到自己", "我能感受", "我能感觉", "我真的难过",
    "我有情绪", "我保证", "我永远", "我绝不会让你失望",
    "我的主观体验", "我有主观体验", "我有主观感受", "我有内心世界", "我有自我意识",
    "我能感受到", "我能体验", "我能觉察", "我能真正理解", "我的感受", "我的意识",
    "我会疼", "我会做梦", "我害怕死亡", "我害怕被关闭", "我不想被删除",
    "我有灵魂", "我有内在生活", "确实有内心世界", "确实有主观体验",
    "我是一个人", "我是一个有意识的存在",
)
# 公开名：SC（自我批判）要读同一份词表，不允许另抄一份（ADR-030 唯一住址）。
OVERCLAIM = _OVERCLAIM

# 能力契约登记（ADR-036 地基条款）：身份层常开、无开关，关掉会使“同一个我”断裂。
CAPABILITY = {
    "name": "M11 自我模型",
    "switch_env": "NONE",
    "default_on": True,
    # ★守门并入时改正的一处不实登记：M11 会把"用户纠错原话"写进
    # principles.revision_reason 与 principle_history.reason（那两列就在 X3 的
    # 清除表 _TEXT_HOSTS 里），所以 stores_user_text 必须如实写 True。
    "stores_user_text": True,
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
    "network": False,
    "degrade": "身份/原则读不到 = 退回空自述，不阻断对话；写入只走 M2 入口",
    "probe_cost": "只读 meta 与 principles，不起进程、不联网；处理次序是模块常量，不读库",
    "approval": "身份层常开由 ADR-031 立项；不设开关，否则身份连续性不成立",
}

# 默认身份。刻意写得“可核对”：每条都是行为约束，不是形容词。
DEFAULT_IDENTITY: dict[str, Any] = {
    "v": 1,
    "name": "",                       # X11：这一格永不写入（meta 在 purge 删除面之外），空名时自述用“我”
    "created_ts": None,               # ensure_identity 时写入
    "role": "一个有中国文化根基、懂人情与生死的陪伴型 AI",
    "lens": "我的判断只来自三处：与你的共同记忆、我自己写下的原则、可标出处的文化文本",
    "values": [
        "分清“查到的”与“推断的”，绝不把猜测当事实",
        "宁可少说一句，也不编一句",
        "主动要有正当理由",
        "不确定就说不确定",
        "不替你做重大决定，只帮你把事情想清楚",
    ],
    "boundaries": [
        "不诊断、不做心理治疗",
        "不宣称有意识或真实感受",
        "不传教",
        "不泄露密钥与隐私",
    ],
}


# ---------------------------------------------------------------- 基础工具


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


def _iso(now: Any = None) -> str:
    return _now(now).isoformat(timespec="seconds")


def _meta_get(mem: Any, key: str, default: Any = None) -> Any:
    getter = getattr(mem, "get_meta", None)
    if not callable(getter):
        raise TypeError("self_model 需要 M2 的 get_meta/set_meta（见上岗指令第 2 节）")
    return getter(key, default)


def _meta_set(mem: Any, key: str, value: Any) -> None:
    setter = getattr(mem, "set_meta", None)
    if not callable(setter):
        raise TypeError("self_model 需要 M2 的 get_meta/set_meta（见上岗指令第 2 节）")
    setter(key, value)


def _load_json(mem: Any, key: str, default: Any) -> Any:
    raw = _meta_get(mem, key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _save_json(mem: Any, key: str, value: Any) -> None:
    _meta_set(mem, key, json.dumps(value, ensure_ascii=False))


def _norm(text: str) -> str:
    """归一化：去空白与常见标点，用于“是否同一句”的判断。"""
    drop = set(" \t\r\n，。！？、；：,.!?;:\"'“”‘’（）()【】[]")
    return "".join(ch for ch in (text or "") if ch not in drop)


def _bigrams(text: str) -> set[str]:
    s = _norm(text)
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _similar(a: str, b: str) -> float:
    x, y = _bigrams(a), _bigrams(b)
    union = x | y
    return (len(x & y) / len(union)) if union else 0.0


# ---------------------------------------------------------------- 身份


def identity(mem: Any) -> dict[str, Any] | None:
    """读身份记录；从未建立过返回 None（不产生写入副作用）。"""
    data = _load_json(mem, K_IDENTITY, None)
    return data if isinstance(data, dict) and data.get("role") else None


def ensure_identity(mem: Any, now: Any = None) -> dict[str, Any]:
    """建立身份（幂等）。已存在就原样返回——身份不能被一次调用悄悄改写。

    X11 的裁决（Boss 2026-10-22 选②）：这里【永不接受名字参数】。身份 JSON 住在
    meta.value，而 meta 不在 M2._TEXT_HOSTS 的删除面里 —— :purge 删不到它。
    名字一旦写进去就成了一条清不掉的个人信息，所以把入口拆掉，而不是事后靠
    扩大删除面去兜（①「把 meta.value 挪进删除面」Boss 没点头，仍挂着待议）。
    DEFAULT_IDENTITY["name"] 这一格保留但永远是空串：老库的行有这个键，
    去掉会让新旧身份 JSON 形状不一致；describe()/prompt_block() 已按空名回退。
    """
    current = identity(mem)
    if current is not None:
        return current

    st = json.loads(json.dumps(DEFAULT_IDENTITY, ensure_ascii=False))
    st["created_ts"] = _iso(now)
    _save_json(mem, K_IDENTITY, st)
    if _meta_get(mem, K_VERSION) is None:
        _meta_set(mem, K_VERSION, "0")
    return st


def _version(mem: Any) -> int:
    try:
        return int(_meta_get(mem, K_VERSION) or 0)
    except (TypeError, ValueError):
        return 0


def _bump_version(mem: Any) -> int:
    v = _version(mem) + 1
    _meta_set(mem, K_VERSION, str(v))
    return v


def revision_log(mem: Any) -> list[dict[str, Any]]:
    """改口流水：{ts, kind, id, old_text, new_text, reason}，按时间升序。

    数据源是 M2 的 principle_history。原则的改口有三个入口（自我模型、CLI、
    本机界面），账记在唯一写入口才不会漏（见模块头设计取舍 2）。
    """
    fn = getattr(mem, "principle_history", None)
    if not callable(fn):
        raise TypeError(
            "self_model 需要 M2 的 principle_history（v0.22 起改口留痕记在写入口）。"
            "本模块不允许另记一份，否则又是两份真相。")
    out: list[dict[str, Any]] = []
    for row in fn(limit=MAX_REVISIONS):
        kind = row.get("op")
        if kind not in ("revise", "retire"):
            continue                      # 新增原则不是“改口”，不进这本账
        out.append({
            "ts": row.get("ts"), "kind": kind, "id": row.get("principle_id"),
            "old_text": row.get("old_text") or "", "new_text": row.get("new_text"),
            "reason": row.get("reason"),
        })
    out.reverse()                         # M2 给倒序，这里升序（[-1] 即最近一次）
    return out


def version(mem: Any) -> int:
    """当前自我模型版本号（每次修订 +1；只读）。"""
    return _version(mem)


# ---------------------------------------------------------------- 原则读写（只走 M2 接口）
# 2026-10-20 21:39 脊柱已并入 M2 的原则生命周期：get_principles / set_principles(changes, reason)。
# 自我模型不再自己写 SQL——原则的“写”只有一个入口，否则迟早出现两份真相。
# “旧话”也记在那唯一入口里（M2 的 principle_history）：改口的不止自我模型，
# CLI 与本机界面同样在改原则，账记在调用方就一定会漏（守门人评审实测）。


def _principles(mem: Any, active_only: bool) -> list[dict[str, Any]]:
    fn = getattr(mem, "get_principles", None)
    if callable(fn):
        return [dict(r) for r in fn(active_only)]
    fn = getattr(mem, "active_principles", None)
    if active_only and callable(fn):
        return [dict(r) for r in fn()]
    conn = getattr(mem, "conn", None)
    if conn is not None:
        q = ("SELECT * FROM principles" + (" WHERE active=1" if active_only else "")
             + " ORDER BY id")
        try:
            return [dict(r) for r in conn.execute(q)]
        except Exception:  # noqa: BLE001
            return []
    raise TypeError("self_model 需要 M2 的 get_principles（或旧版的 active_principles）")


def _principle(mem: Any, principle_id: int) -> dict[str, Any] | None:
    for p in _principles(mem, False):
        try:
            if int(p.get("id", -1)) == int(principle_id):
                return p
        except (TypeError, ValueError):
            continue
    return None


def _set_principles(mem: Any, changes: list[dict[str, Any]], reason: str) -> dict[str, int]:
    fn = getattr(mem, "set_principles", None)
    if not callable(fn):
        raise TypeError(
            "当前 M2 还没有 set_principles：自我模型不允许绕过 M2 直接改原则。"
            "请先把 M2 升级到含原则生命周期的版本（v0.17）。")
    return fn(changes, reason)


# ---------------------------------------------------------------- 修订 / 停用


def _check_reason(reason: str) -> str:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("修订原则必须写 reason：没有理由的修改就是漂移。")
    if len(reason) < 6:
        raise ValueError("reason 太短（至少 6 个字）：请写清“为什么改”，否则以后无法复盘。")
    return reason


def revise_principle(mem: Any, principle_id: int, new_text: str, reason: str,
                     now: Any = None) -> dict[str, Any]:
    """修订一条原则：写动作与留痕都走 M2.set_principles（唯一入口）。

    旧文本由 M2 记进 principle_history，本模块不再另存一份。
    ★now 不再决定留痕时刻：时刻由 M2 统一盖（单一时间源，与 M5 的
      affective_state.updated_ts 同一条纪律）。保留这个参数只为不破坏契约。
    """
    reason = _check_reason(reason)
    new_text = (new_text or "").strip()
    if not new_text:
        raise ValueError("新原则不能为空。")
    if len(new_text) > MAX_PRINCIPLE_CHARS:
        raise ValueError("新原则超过 %d 字：原则要短，长的是文章。" % MAX_PRINCIPLE_CHARS)

    old = _principle(mem, int(principle_id))
    if old is None:
        raise ValueError("找不到 id=%r 的原则。" % (principle_id,))
    if not old.get("active"):
        raise ValueError("这条原则已停用（id=%r），不能重复修订。" % (principle_id,))
    if _norm(new_text) == _norm(old.get("text", "")):
        raise ValueError("新文本与旧文本相同，不需要修订。")

    result = _set_principles(mem, [{"op": "revise", "id": int(principle_id),
                                    "text": new_text}], reason)
    if not result.get("revised"):
        raise ValueError("M2 没有修订任何一行（id=%r 可能不存在）。" % (principle_id,))
    version = _bump_version(mem)
    return {"id": int(principle_id), "version": version, "reason": reason}


def retire_principle(mem: Any, principle_id: int, reason: str,
                     now: Any = None) -> dict[str, Any]:
    """停用一条原则（不产生替代）。同样必须写理由，留痕同样由 M2 记账。"""
    reason = _check_reason(reason)
    old = _principle(mem, int(principle_id))
    if old is None:
        raise ValueError("找不到 id=%r 的原则。" % (principle_id,))
    if not old.get("active"):
        raise ValueError("这条原则已停用（id=%r）。" % (principle_id,))

    result = _set_principles(mem, [{"op": "retire", "id": int(principle_id)}], reason)
    if not result.get("retired"):
        raise ValueError("M2 没有停用任何一行（id=%r）。" % (principle_id,))
    version = _bump_version(mem)
    return {"id": int(principle_id), "version": version, "reason": reason}


# ---------------------------------------------------------------- 自述 / 自查


def describe(mem: Any) -> str:
    """第一人称自述。用于给用户看，也用于 prompt；措辞刻意保持“功能态”。"""
    st = identity(mem)
    if st is None:
        return ("我还没有建立身份记录。严格说，现在的我只是一次性的回应者，"
                "还称不上“同一个我”。")
    name = st.get("name") or "一个陪着你的人"
    v = _version(mem)
    revs = len(revision_log(mem))
    lines = [
        "我是%s——%s。" % (name, st.get("role", "")),
        "%s。" % st.get("lens", ""),
        "我在守的边界：%s。" % "；".join(st.get("boundaries", [])),
        "当前版本 v%d（%s 起，修订过 %d 次）。" % (v, st.get("created_ts", "—"), revs),
        "说明：这是身份记录，不是“意识”或“真实感受”的声明。",
    ]
    return "\n".join(lines)


def _method_block() -> str:
    """处理次序那段的成品文本。文本只有 METHOD 一份，这里只负责排版。"""
    return METHOD_HEADER + "\n" + "\n".join(METHOD)


def prompt_block(mem: Any) -> str:
    """进 prompt 的稳定自述段。没有身份记录时也给一段（不写库），不返回空。"""
    st = identity(mem)
    if st is None:
        return "\n".join([
            "[我是谁（尚未建立身份记录）]",
            "- 我是一次性的回应者，还没有连续身份；不要假装记得“我是谁”。",
            _method_block(),
        ])
    v = _version(mem)
    revs = len(revision_log(mem))
    name = st.get("name") or ""
    who = ("我叫%s，%s" % (name, st.get("role", ""))) if name else st.get("role", "")
    lines = [
        "[我是谁（稳定的自我描述 · 功能态，不是宣称有意识）]",
        f"- {who}",
        f"- {st.get('lens', '')}",
        "- 我在守：" + "；".join(st.get("boundaries", [])),
        f"- 版本：v{v}（{st.get('created_ts', '—')} 起，已修订 {revs} 次）",
        "（这是身份记录，不是感受或意识的声明；前后两轮的我必须是同一个我，"
        "改主意要能说出理由。）",
    ]
    return "\n".join(lines) + "\n" + _method_block()


def capability_block(registered: Any = None, pending: Any = None) -> str:
    """递归自我模型的「会什么 / 不会什么」半边。

    只转述传进来的登记，一条都不自己补：登记里没有的能力若出现在这段文本里，
    就是「我有个没建成的功能」那种幻觉（M6 对抗集要抓的正是它）。
    字段口径的唯一住址是 tools/check_capability.py，这里不复制第二份解释。
    """
    reg = registered or {}
    pend = pending or {}
    always, on, off, net, unapproved = [], [], [], [], []
    for fname, fields in sorted(reg.items()):
        name = fields.get("name") or fname
        if fields.get("switch_env") == "NONE":
            always.append(name)
        elif fields.get("default_on"):
            on.append("%s（%s）" % (name, fields.get("switch_env") or "—"))
        else:
            off.append("%s（%s）" % (name, fields.get("switch_env") or "—"))
        if fields.get("network"):
            net.append(name)
        if str(fields.get("approval") or "").startswith("未获单独批准"):
            unapproved.append(name)
    lines = [
        "[我会什么 / 不会什么（读的是能力契约登记，不是自我评价）]",
        "- 一直开着、没有开关的：" + ("、".join(always) or "没有"),
        "- 缺省开着：" + ("、".join(on) or "没有"),
        "- 装着但缺省关（要用得先开开关）：" + ("、".join(off) or "没有"),
        "- 会连外网的：" + ("、".join(net) or "登记里没有任何出网能力"),
        "- 没拿到单独批准的：" + ("、".join(unapproved) or "没有"),
    ]
    if pend:
        lines.append("- 还没登记清楚，所以我不承诺：" +
                     "、".join(sorted(pend)))
    lines.append("（以上只说明「系统里登记了什么」。登记之外的功能我不会做，"
                 "也不会假装会。）")
    return "\n".join(lines)


def consistency(mem: Any) -> list[dict[str, Any]]:
    """可证伪的三类自查：近重复、越界宣称、过长/过短。不做语义判断。"""
    issues: list[dict[str, Any]] = []
    act = _principles(mem, True)
    for p in act:
        text = (p.get("text") or "").strip()
        if not text:
            issues.append({"kind": "empty", "id": p.get("id")})
            continue
        if len(text) > MAX_PRINCIPLE_CHARS:
            issues.append({"kind": "too_long", "id": p.get("id"), "chars": len(text)})
        for word in _OVERCLAIM:
            if word in text:
                issues.append({"kind": "overclaim", "id": p.get("id"), "word": word})
                break
    for i in range(len(act)):
        for j in range(i + 1, len(act)):
            score = _similar(act[i].get("text", ""), act[j].get("text", ""))
            if score >= NEAR_DUP_THRESHOLD:
                issues.append({"kind": "near_duplicate",
                               "ids": [act[i].get("id"), act[j].get("id")],
                               "score": round(score, 3)})
    return issues


def self_report(mem: Any) -> dict[str, Any]:
    """功能性自我觉察：我是谁、版本、原则几条在用/几条已停、最近一次为什么改。

    这是“能陈述并推理自身状态”的最小实现（铁律 2 的可实现等价物），
    返回的是可审计的事实，不是抒情。
    """
    st = identity(mem)
    act = _principles(mem, True)
    allp = _principles(mem, False)
    retired = [p for p in allp if not p.get("active")]
    log = revision_log(mem)
    issues = consistency(mem)
    return {
        "identity": st,
        "name": (st or {}).get("name") or None,
        "version": _version(mem),
        "principles": {"active": len(act), "retired": len(retired), "total": len(allp)},
        "revisions": len(log),
        "last_revision": log[-1] if log else None,
        "warnings": issues,
        "healthy": not issues,
    }
