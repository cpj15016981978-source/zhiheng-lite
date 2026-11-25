# -*- coding: utf-8 -*-
"""M9 · 文化根基知识库（Cultural Grounding KB）

契约（见《00_协作规程与接口契约.txt》PART B / 上岗指令第 2 节）：
    retrieve_culture(query, context) -> [ {"ref": str, "text": str, "weight": float} ]

定位：grounded 透镜的“深度来源”。通用模型有世界知识，但没有**你策展的**
这一小份——活着的历史 × 文化 × 人情世故 × 生死观。它只负责“找出相关的几条”，
不负责生成回答，也不写 M2 的库（纯读、纯函数）。

设计取舍（为什么这么写）：
1. 零第三方依赖、零模型调用：检索必须离线可验证，且不能引入分词库。
   中文无空格，用【二元切分 + 集合重叠】打分，够用且完全确定。
2. 语料内嵌在模块里，同时允许外部 JSON 覆盖（load_corpus）。内嵌是为了
   “一个文件就能跑”，外部是为了以后你亲手策展而不改代码。
3. 只返回契约里的三个键，不加额外字段——多一个键就可能让调用方解析失败。
4. 不做有组织宗教内容：只取儒释道里关于处世与生死观的**智慧文本**，
   不涉仪轨、教团、信仰宣告。

用法：
    from pai.cultural_kb import retrieve_culture
    refs = retrieve_culture("亲人去世后一直走不出来", context={"stage": "grief"})
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

__all__ = [
    "retrieve_culture",
    "get_corpus",
    "load_corpus",
    "CULTURAL_KB_VERSION",
]


CULTURAL_KB_VERSION = "0.1"

# 能力契约登记（ADR-036），字段口径唯一住址 = tools/check_capability.py。
# 既成常开登记见 ADR-046：M9 是纯读纯函数，被 M1 每轮拿去检索，从来没有开关。
CAPABILITY = {
    "name": "M9 文化词库",
    "switch_env": "NONE",
    "default_on": True,
    # query 只进来打分，不写任何表；语料内嵌在模块里，load_corpus 换的是内存副本。
    "stores_user_text": False,
    "network": False,
    "degrade": ("打分低于 MIN_WEIGHT = 返回空列表，不硬凑一条塞进 prompt；"
                "load_corpus 文件格式不对 = 抛 ValueError（带第几条），不静默退回内嵌语料"),
    "probe_cost": "只读模块常量，不建库、不起进程、不联网",
    "approval": ("未获单独批准：并入脊柱时起既成常开；它不写库、不出网，"
                 "要补开关或停掉，需 Boss 另行定（ADR-046、ADR-044）"),
}


DEFAULT_K = 5
MAX_QUERY_CHARS = 400

# 生死语境的触发词：出现这些词时，生死类条目加权。
# 刻意写得克制——宁可少给一条，也不要把日常话题误判成丧亲。
_DEATH_CUES = ("死", "丧", "去世", "走了", "永别", "临终", "离别", "失去")

# 口语 → 主题词。没有它，“朋友”“没结果”这类词只能靠字面撞运气。
# 刻意做小：每条都是真实会出现的说法，不追求覆盖全部同义表达。
EXPANSIONS: dict[str, list[str]] = {
    "走不出来": ["生死", "放下"],
    "放不下": ["放下", "执念"],
    "想不开": ["心境", "放下"],
    "去世": ["生死", "丧亲"],
    "走了": ["生死"],
    "丧事": ["生死", "丧亲"],
    "临终": ["生死"],
    "被坑": ["人情", "识人"],
    "被骗": ["人情", "识人"],
    "背叛": ["人情", "识人"],
    "合伙": ["人情", "识人"],
    "朋友": ["人情", "识人"],
    "同事": ["人情"],
    "亲戚": ["人情"],
    "没结果": ["无常", "逆境"],
    "白费": ["无常", "逆境"],
    "徒劳": ["无常", "逆境"],
    "焦虑": ["心境"],
    "压力": ["心境"],
    "睡不着": ["心境"],
    "反省": ["自省", "修身"],
    "自责": ["自省", "修身"],
    "识人": ["识人", "人情"],
    "靠谱": ["识人"],
}

# 低于这个分的不返回：宁可少给，不可给噪音。
# 0.10 是校准值——“朋友”这类高频词的偶然命中通常只有 0.04—0.09，会被挡掉。
MIN_WEIGHT = 0.10

# ---------------------------------------------------------------- 语料
# 每条：ref（出处）/ text（原文＋一句白话）/ tags（触发标签）/ themes（主题）
# 文字均为公版典籍，白话为自写释义，不带教义宣讲。
CORPUS: list[dict[str, Any]] = [
    {
        "ref": "论语·学而",
        "text": "「吾日三省吾身」——每天三次问自己：替人办事尽心了吗？与朋友交往守信了吗？学到的东西温习了吗？",
        "tags": ["自省", "修身", "反省"],
        "themes": ["修身"],
        "tradition": "儒家",
    },
    {
        "ref": "论语·为政",
        "text": "「知之为知之，不知为不知，是知也。」——知道就说知道，不知道就承认不知道，这才是真明白。",
        "tags": ["认知", "诚实", "边界"],
        "themes": ["认知"],
        "tradition": "儒家",
    },
    {
        "ref": "论语·卫灵公",
        "text": "「君子求诸己，小人求诸人。」——出了事先在自己身上找原因，而不是先怪别人。",
        "tags": ["归因", "自省", "责任"],
        "themes": ["修身"],
        "tradition": "儒家",
    },
    {
        "ref": "孟子·离娄上",
        "text": "「行有不得者，皆反求诸己。」——做不成的事，回头看看自己哪里没做到。",
        "tags": ["逆境", "归因", "挫折"],
        "themes": ["修身"],
        "tradition": "儒家",
    },
    {
        "ref": "论语·颜渊",
        "text": "「己所不欲，勿施于人。」——自己不愿意承受的，不要加在别人身上。",
        "tags": ["人情", "共情", "分寸"],
        "themes": ["处世"],
        "tradition": "儒家",
    },
    {
        "ref": "道德经·三十三章",
        "text": "「知人者智，自知者明。」——看懂别人算聪明，看懂自己才算通透。",
        "tags": ["自知", "识人"],
        "themes": ["认知"],
        "tradition": "道家",
    },
    {
        "ref": "道德经·五十八章",
        "text": "「祸兮福之所倚，福兮祸之所伏。」——坏事里藏着转机，好事里埋着隐患。",
        "tags": ["逆境", "辩证", "得失"],
        "themes": ["无常"],
        "tradition": "道家",
    },
    {
        "ref": "道德经·八章",
        "text": "「上善若水，水善利万物而不争。」——最好的品性像水：滋养万物，却不争高低。",
        "tags": ["处世", "不争", "柔和"],
        "themes": ["处世"],
        "tradition": "道家",
    },
    {
        "ref": "庄子·至乐",
        "text": "「庄子妻死，惠子吊之，庄子则方箕踞鼓盆而歌。」——庄子丧妻不哭反歌，不是无情，是把生死看作四季更替。",
        "tags": ["生死", "丧亲", "悲伤"],
        "themes": ["生死"],
        "tradition": "道家",
    },
    {
        "ref": "庄子·大宗师",
        "text": "「死生，命也，其有夜旦之常，天也。」——生死如同昼夜交替，是自然的常态，不是意外。",
        "tags": ["生死", "无常"],
        "themes": ["生死"],
        "tradition": "道家",
    },
    {
        "ref": "陶渊明·形影神",
        "text": "「纵浪大化中，不喜亦不惧。」——把自己放进天地的大化里，不因生而狂喜，不因死而恐惧。",
        "tags": ["生死", "豁达"],
        "themes": ["生死"],
        "tradition": "魏晋",
    },
    {
        "ref": "坛经",
        "text": "「本来无一物，何处惹尘埃。」——心里本不装东西，烦恼也就无处落脚。",
        "tags": ["放下", "执念", "心境"],
        "themes": ["心境"],
        "tradition": "禅",
    },
    {
        "ref": "菜根谭",
        "text": "「宠辱不惊，闲看庭前花开花落。」——被抬举不被贬损都不惊动，像看花开花落一样看待得失。",
        "tags": ["得失", "心境", "荣辱"],
        "themes": ["心境"],
        "tradition": "明代清言",
    },
    {
        "ref": "增广贤文",
        "text": "「画虎画皮难画骨，知人知面不知心。」——看人只看得到表面，人心要经事才看得清。",
        "tags": ["识人", "人情", "谨慎"],
        "themes": ["人情"],
        "tradition": "民间",
    },
    {
        "ref": "苏轼·前赤壁赋",
        "text": "「逝者如斯，而未尝往也；盈虚者如彼，而卒莫消长也。」——水天天流走，河却还在；月有圆缺，本质未增减。",
        "tags": ["无常", "生死", "变化"],
        "themes": ["无常"],
        "tradition": "宋代",
    },
    {
        "ref": "曾国藩家书",
        "text": "「家俭则兴，人勤则健。」——家里肯节俭就会兴旺，人肯勤力身体就硬朗。",
        "tags": ["勤俭", "家风", "持家"],
        "themes": ["持家"],
        "tradition": "清代家书",
    },
]


# ---------------------------------------------------------------- 内部工具

def _strip_punct(s: str) -> str:
    """去掉标点与空白，只留文字、数字、字母。中文正则 \\w 已覆盖汉字。"""
    return re.sub(r"[^\w]+", "", s or "", flags=re.UNICODE)


def _bigrams(s: str) -> set[str]:
    """二元切分。中文没有空格，两个字一组是最轻的相关性单位。"""
    s = _strip_punct(s)
    if not s:
        return set()
    if len(s) == 1:
        return {s}
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _entry_text(entry: dict[str, Any]) -> str:
    return "%s%s%s%s" % (
        entry.get("ref", ""),
        entry.get("text", ""),
        "".join(entry.get("tags", [])),
        "".join(entry.get("themes", [])),
    )


def _idf_table() -> tuple[dict[str, float], float]:
    """按语料现算 IDF：越常见的二元词，越不该主导打分。

    没有这一步，“朋友”这种高频词会把论语·学而顶上来，而真正相关的
    增广贤文反而排不上——这是词面匹配最典型的假阳性。
    """
    docs = [_bigrams(_entry_text(e)) for e in CORPUS]
    n = len(docs)
    df: dict[str, int] = {}
    for doc in docs:
        for b in doc:
            df[b] = df.get(b, 0) + 1
    idf = {b: math.log((n + 1.0) / (c + 1.0)) + 1.0 for b, c in df.items()}
    worst = math.log(n + 1.0) + 1.0  # 完全没出现过的词，权重最高
    return idf, worst


def _score(query_bi: set[str], query_text: str, entry: dict[str, Any],
           idf: dict[str, float], unseen_idf: float) -> float:
    if not query_bi:
        return 0.0

    doc_bi = _bigrams(_entry_text(entry))
    matched = query_bi & doc_bi
    score = 0.0
    if matched:
        num = sum(idf.get(b, unseen_idf) for b in matched)
        den = sum(idf.get(b, unseen_idf) for b in query_bi)
        score = num / den if den else 0.0

    bonus = 0.0

    # 标签直接出现在查询里，说明是强意图，给固定加成。
    for tag in entry.get("tags", []):
        if tag and tag in query_text:
            bonus += 0.18
    for theme in entry.get("themes", []):
        if theme and theme in query_text:
            bonus += 0.12

    # 生死语境：只在明确出现丧亲/永别类词时加权，避免日常话题误判。
    if "生死" in entry.get("themes", []):
        if any(cue in query_text for cue in _DEATH_CUES):
            bonus += 0.25

    # 只命中一个二元词，不足以支撑一次“文化引用”。
    # 例：“今天天气不错”会因为白话里的“水天天流走”撞上“天天”，
    # 这种偶然命中必须挡掉——引错典比不引更糟。
    if bonus == 0.0 and len(matched) < 2:
        return 0.0

    return min(score + bonus, 1.0)


def _context_text(context: Any) -> str:
    if context is None:
        return ""
    if isinstance(context, str):
        return context
    try:
        return json.dumps(context, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(context)


# ---------------------------------------------------------------- 对外接口

def retrieve_culture(query: str, context: Any = None, k: int = DEFAULT_K) -> list[dict[str, Any]]:
    """按相关性返回文化条目。

    参数：
        query   —— 用户原话或提炼后的主题
        context —— 可选上下文（str 或 dict），与 query 合并参与打分
        k       —— 最多返回几条，默认 5

    返回：
        [{"ref": str, "text": str, "weight": 0.0~1.0}]，按 weight 降序；
        无匹配时返回 []（不返回兜底条目——宁可空，不可塞无关内容）。
    """
    raw = (query or "") + _context_text(context)
    raw_text = _strip_punct(raw)[:MAX_QUERY_CHARS]

    # 口语扩展：只补主题词，不改原句——改原句会把意思带偏。
    extra: list[str] = []
    for spoken, canon in EXPANSIONS.items():
        if spoken in raw_text:
            extra.extend(canon)

    query_text = raw_text + "".join(extra)
    query_bi = _bigrams(raw_text)
    for term in extra:
        query_bi |= _bigrams(term)

    if not query_bi:
        return []

    idf, unseen = _idf_table()
    scored: list[tuple[float, dict[str, Any]]] = []
    for entry in CORPUS:
        s = round(_score(query_bi, query_text, entry, idf, unseen), 3)
        if s >= MIN_WEIGHT:
            scored.append((s, entry))

    # 权重降序；同分按 ref 排，保证结果稳定可复现。
    scored.sort(key=lambda pair: (-pair[0], pair[1]["ref"]))

    limit = max(1, int(k))
    return [
        {"ref": entry["ref"], "text": entry["text"], "weight": weight}
        for weight, entry in scored[:limit]
    ]


def get_corpus() -> list[dict[str, Any]]:
    """返回语料副本，供调用方列举/审计（改副本不影响模块内语料）。"""
    return [dict(entry) for entry in CORPUS]


def load_corpus(path: str) -> list[dict[str, Any]]:
    """从 JSON 文件替换内存语料（用户策展入口）。

    文件格式：与 CORPUS 相同的对象数组，至少含 ref / text。
    返回加载后的条目数；格式不对时抛 ValueError，带着行号方便定位。
    """
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    if not isinstance(data, list):
        raise ValueError("语料文件必须是 JSON 数组")

    cleaned: list[dict[str, Any]] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError("第 %d 条不是对象" % idx)
        if not item.get("ref") or not item.get("text"):
            raise ValueError("第 %d 条缺少 ref 或 text" % idx)
        cleaned.append({
            "ref": str(item["ref"]),
            "text": str(item["text"]),
            "tags": [str(t) for t in item.get("tags", [])],
            "themes": [str(t) for t in item.get("themes", [])],
            "tradition": str(item.get("tradition", "")),
        })

    if not cleaned:
        raise ValueError("语料为空")

    global CORPUS
    CORPUS = cleaned
    return len(cleaned)
