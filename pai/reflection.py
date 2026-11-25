"""M4 反思引擎 — 见《00_协作规程与接口契约.txt》PART B2 (M4 Reflection)。

Phase2 的骨架：把散落的 episode 蒸馏成结构化 fact，再从累积的 fact 提炼出
系统【自己写下的】处世原则(principle)。这就是“独立思考”的落脚点——
判断来自积累的记忆 + 自写原则(grounded lens)，而不是复读网络内容。

设计约束：
- 仍走确定性规则，无需真实模型、无需联网，可离线验证(ADR-006 精神延续)。
  将来 M10 接真模型后，只把 _extract_facts / _synthesize_principles 内部
  换成模型调用，reflect() 的签名与返回结构保持不变。
- 增量 + 幂等：用 meta 水位线 reflect_upto_ep_id 记住“反思到哪条 episode 了”，
  重复调用不会重复蒸馏；principle 按文本去重(add_principle 内部保证)。
- 铁律6：反思产物(fact/principle，属“推断/蒸馏”)与原始记忆(episode，属“查到的”)
  分开存放，fact 保留 source_episode_id 可追溯来源。

已知取舍：反思只会【新增】原则，不会自动作废。作废/修订由用户经 M2 的
  set_principles(op=retire/revise) 决定(见 :retire 命令)——“它自己成长，但改不改判断权在人”。
  作废后不会复活：add_principle 的去重不看 active(见 memory.py 说明)。
"""

# 能力契约登记（ADR-036）。字段口径的唯一住址 = tools/check_capability.py，这里只写事实。
# ★这里没有开关，是因为【从来没有】：M4 从 Phase2 起就在每轮对话后被 M1 调用。
# 按 ADR-046 如实登记成既成常开（NONE + True + approval 引真实 ADR 号），
# 不给它编一个不存在的环境变量，也不塞进 SUBSTRATE（它不是地基）。
CAPABILITY = {
    "name": "M4 反思引擎",
    "switch_env": "NONE",
    "default_on": True,
    # 抽出的 fact.object 取自用户原话片段（「我叫陈默」→ 陈默），落在 facts 三列；
    # 自写原则落在 principles.text。两张表都已在 _TEXT_HOSTS 登记。
    "stores_user_text": True,
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
    "network": False,
    "degrade": ("规则没命中 = 返回空、一个字不写（宁可不抽也不瞎猜）；写库失败 = 抛错，"
                "不假装反思过。水位线 reflect_upto_ep_id 保证幂等，重复调用不重蒸"),
    "probe_cost": "只读模块常量，不建库、不起进程、不联网",
    "approval": ("未获单独批准：Phase2 交付时起既成常开，属【写库】（facts + principles）；"
                 "要补开关或停掉，需 Boss 另行定（ADR-046、ADR-044）"),
}

# 触发“家族记忆”抽取的称谓词
_KIN = ("爷爷", "奶奶", "外公", "外婆", "父亲", "母亲", "爹", "娘",
        "姥姥", "姥爷", "祖先", "老家", "祖父", "祖母")

_STRIP = "。.!！?？，, 、；;：: \n\t"


def _clean(s):
    return s.strip().strip(_STRIP).strip()


def _extract_facts(content):
    """确定性规则：从一句用户话里抽取显式事实。
    返回 [(subject, predicate, object, confidence)]。
    规则命中不到就返回空——宁可不抽，也不瞎猜(铁律6：不把推断当事实)。
    """
    t = content.strip()
    out = []

    # 姓名
    for head in ("我叫", "我的名字是", "我的名字叫"):
        if t.startswith(head):
            name = _clean(t[len(head):])
            if name:
                out.append(("用户", "姓名", name, 0.9))
            break

    # 昵称
    if t.startswith("叫我"):
        nick = _clean(t[2:])
        if nick:
            out.append(("用户", "昵称", nick, 0.85))

    # 年龄：我28岁 / 我今年28
    import re
    m = re.search(r"我(?:今年)?(\d{1,3})\s*岁", t)
    if m:
        out.append(("用户", "年龄", m.group(1), 0.85))

    # 行业 / 职业
    for pat, pred in ((r"我在(.+?)(?:工作|上班|做事)", "行业"),
                      (r"我是做(.+?)的", "职业"),
                      (r"我做(.+?)的", "职业")):
        m = re.search(pat, t)
        if m:
            v = _clean(m.group(1))
            if v:
                out.append(("用户", pred, v, 0.7))
            break

    # 喜好
    for head in ("我喜欢", "我喜爱", "我爱"):
        if t.startswith(head):
            v = _clean(t[len(head):])
            if v:
                out.append(("用户", "喜欢", v, 0.75))
            break

    # 家族记忆 / 先辈经历（整句留存，因为语境本身就是价值所在）
    if any(k in t for k in _KIN) and len(t) >= 6:
        out.append(("用户", "家族记忆", _clean(t), 0.8))

    # 显式备注（沿用 Phase1 约定）
    for head in ("记住", "记一下", "你记着"):
        if t.startswith(head):
            v = _clean(t[len(head):])
            if v:
                out.append(("用户", "备注", v, 0.85))
            break

    return out


def _synthesize_principles(facts):
    """从当前累积的 fact 集合，提炼系统【自己写下的】处世原则。
    返回 [principle_text]。确定性、可读、稳定(同输入同输出)，便于去重与审阅。
    """
    by_pred = {}
    for f in facts:
        by_pred.setdefault(f["predicate"], []).append(f["object"])

    ps = []
    # 核心自省原则(铁律6 内化成一条自我约束)——永远写进去
    ps.append("回应前先分清『我查到的记忆』与『我的推断』，绝不把猜测当事实说给用户。")
    # 定位原则
    ps.append("我陪用户想清楚难问题，落点是陪伴与判断，不是说教。")

    if "姓名" in by_pred:
        ps.append(f"用户叫{by_pred['姓名'][0]}，称呼时带上名字，让对话有人情味。")
    if "家族记忆" in by_pred:
        ps.append("用户在意家族与先辈的经历；谈历史时落到具体的人和事，而不是抽象年代。")
    if "行业" in by_pred or "职业" in by_pred:
        job = (by_pred.get("行业") or by_pred.get("职业"))[0]
        ps.append(f"用户从事与「{job}」相关的行当，这是他多年的一线经验，相关话题要尊重他的判断。")
    if "喜欢" in by_pred:
        ps.append(f"用户喜欢「{by_pred['喜欢'][0]}」，可借此建立共鸣。")

    return ps


def reflect(mem):
    """对水位线之后的新 episode 做一次反思：蒸馏 fact + 合成 principle。
    返回 {scanned, new_facts, updated_facts, new_principles, principles_total}。
    幂等：无新 episode 时什么都不改。
    """
    watermark = int(mem.get_meta("reflect_upto_ep_id", "0") or 0)
    new_eps = [e for e in mem.episodes_after(watermark) if e["role"] == "user"]

    new_facts = 0
    updated_facts = 0
    for e in new_eps:
        existing = {(f["subject"], f["predicate"]): f["object"] for f in mem.all_facts()}
        for (s, p, o, conf) in _extract_facts(e["content"]):
            before = existing.get((s, p))
            fid = mem.upsert_fact(s, p, o, confidence=conf, source_episode_id=e["id"])
            if before is None:
                new_facts += 1
            elif before != o:
                updated_facts += 1

    # 推进水位线到当前最大 episode id（含 ai 的，避免下次重扫）
    mem.set_meta("reflect_upto_ep_id", mem.max_episode_id())

    # 基于全量 fact 合成原则，按文本去重写入
    new_principles = 0
    for text in _synthesize_principles(mem.all_facts()):
        _pid, created = mem.add_principle(text)
        if created:
            new_principles += 1

    return {
        "scanned": len(new_eps),
        "new_facts": new_facts,
        "updated_facts": updated_facts,
        "new_principles": new_principles,
        "principles_total": len(mem.get_principles()),
    }
