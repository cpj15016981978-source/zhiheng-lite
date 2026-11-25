# -*- coding: utf-8 -*-
"""M5 · 情感层（Affect Layer）—— 功能等价物，不是真有感受（铁律 3）。

契约（《给DeepSeek-上岗指令.txt》第 2 节 / 黑板 PART B2）：
    read_affect(mem, now=None) -> dict          # 读出当前心境（已按时间衰减）
    modulate(response, state) -> str            # 输出侧语气护栏（只删，不加）

另提供（给 M1 接入用，不改任何既有签名）：
    detect_affect(text) -> dict                 # 纯函数：从一句话估心境
    update_affect(mem, text, now=None) -> dict  # 读 → 估 → 混 → 落库
    prompt_block(state) -> str                  # 进 prompt 的“情感基调”段
    modulate_affect = modulate                  # 兼容“上岗指令”里的另一种叫法
    AFFECT_VERSION

设计取舍（为什么这么写，供评审时不必猜）：

1. 确定性、零依赖、不调模型。情绪识别如果用模型来做，就无法离线单测，
   而且会把“情感层”和“认知层”绑死——M5 只提供一个可被质疑的变量，
   判断权仍在 M1/用户手里（与“议会不综合”同一条纪律）。

2. 只给“功能态”，不给临床标签。mood 是内部路由用的字符串（平静/低落/
   焦虑/丧痛…），绝不是诊断。铁律 3 要求如实描述，所以：
     · 返回结构里没有“感受”字段，只有可观测的 valence/arousal/intensity；
     · prompt_block 明说“这是估计不是事实，以对方自己说的为准”；
     · 不输出“你有抑郁症/焦虑症”这类话，模块本身也从不生成自然语言。

3. 会衰减。心境不是事件，不能一直挂着。上一轮的丧痛三周后还在，就会
   让系统对着一个已经缓过来的人反复用丧亲语气——那不是体贴，是失礼。
   衰减 = 半衰期向“平静”回落；半衰期按情绪类型分档（丧痛最长，愤怒最短）。
   这样可以解释、可以单测，也不依赖任何外部时钟。

4. 落库只碰 affective_state 一行（id=1）。脊柱 schema 早已预留这张表
   （PART B1），M5 不新增表、不改既有表、不写 episodes/facts/principles。
   优先走 M2 的 get_affect/set_affect（提案见 ADR 草案）；脊柱还没有这两个
   访问器时，退回用 mem.conn 执行等价 SQL——这样 M5 今天就能独立跑绿，
   等访问器并入后自动切换到接口，不需要改 M5 一行。

5. modulate 是护栏，不是改写器。它只删“不合时宜的套话”（想开点/节哀顺变/
   别想太多…），不添加任何内容、不改写事实、不替模型说话。
   删光的极端情况下回退原文——宁可保留一句笨拙的真话，也不返回空字符串。
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Any

__all__ = [
    "read_affect",
    "update_affect",
    "detect_affect",
    "blend",
    "decay",
    "prompt_block",
    "modulate",
    "modulate_affect",
    "baseline",
    "AFFECT_VERSION",
]


AFFECT_VERSION = "0.1"

# 能力契约登记（ADR-036），字段口径唯一住址 = tools/check_capability.py。
# 既成常开登记见 ADR-046：M5 并入脊柱后一直在跑，没有开关。
CAPABILITY = {
    "name": "M5 情感层",
    "switch_env": "NONE",
    "default_on": True,
    # 只写 affective_state 一行，但 note 里的 cues 是用户原话里命中的词；
    # 那一列 _TEXT_HOSTS 已登记（purge 时 reset_baseline）。
    "stores_user_text": True,
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
    "network": False,
    "degrade": ("命中不到词表、或原始分低于 _MIN_SIGNAL = 判平静、不改语气（宁可不猜）；"
                "只调语气轻重，不碰 episodes/facts/principles；写库优先走 M2 的 set_affect，"
                "没有那个入口才退回 INSERT（见模块头 ADR 草案）"),
    "probe_cost": "只读模块常量，不建库、不起进程、不联网",
    "approval": ("未获单独批准：并入脊柱时起既成常开，属【写库】（affective_state.note）；"
                 "要补开关或停掉，需 Boss 另行定（ADR-046、ADR-044）"),
}


# ---------------------------------------------------------------- 基本常量

# 内部功能态。顺序即“同分时的优先级”：越靠前越优先（丧痛不能被别的情绪盖掉）。
MOODS = ("丧痛", "焦虑", "愤怒", "孤独", "疲惫", "低落", "轻快", "平静")

# 各基调的基准（valence: -1..1 愉悦度；arousal: 0..1 唤醒度）。
# 这些是“方向”，实际幅度再乘信号强度，弱信号不会被放大成强烈情绪。
_MOOD_TONE: dict[str, tuple[float, float]] = {
    "丧痛": (-0.85, 0.45),
    "焦虑": (-0.45, 0.75),
    "愤怒": (-0.55, 0.80),
    "孤独": (-0.50, 0.25),
    "疲惫": (-0.25, 0.15),
    "低落": (-0.60, 0.30),
    "轻快": (0.65, 0.60),
    "平静": (0.00, 0.30),
}

# 半衰期（小时）：多久回落到一半强度。丧痛最长——但对“缓过来的人”也不能永挂。
_HALF_LIFE_H: dict[str, float] = {
    "丧痛": 36.0,
    "低落": 18.0,
    "焦虑": 12.0,
    "愤怒": 6.0,
    "孤独": 24.0,
    "疲惫": 8.0,
    "轻快": 6.0,
    "平静": 12.0,
}

_BASE_AROUSAL = 0.30
_MIN_INTENSITY = 0.08      # 低于此强度视为已回到平静
_MIN_SIGNAL = 0.30         # 一句话的原始得分低于此值 → 判为平静（宁可不猜）
_HEAVY_MIN_INTENSITY = 0.35  # “重情绪”门槛：丧痛/低落达到它才启用重语气
_MOOD_SWITCH_MARGIN = 1.25   # 重情绪换台门槛：新信号要比旧心境强 25% 才改基调

# 明确的丧事词：单个出现即可成立（歧义极小）。
_STRONG_GRIEF = (
    "去世", "离世", "过世", "病逝", "逝世", "丧事", "葬礼", "头七",
    "临终", "永别", "过身",
)
# 弱丧事词：必须同时有【亲属称谓】才算——“他走了”“朋友走了”多半只是离开，
# “爷爷走了”才是讳称。宁可漏判，不可把日常告别当成丧亲。
_WEAK_GRIEF = ("走了", "没了", "不在了", "离开")
_KIN_WORDS = (
    "亲人", "家人", "父亲", "母亲", "爸爸", "妈妈", "爷爷", "奶奶",
    "外公", "外婆", "姥姥", "姥爷", "老伴", "爱人", "丈夫", "妻子",
    "儿子", "女儿", "兄弟", "姐妹",
)

# 线索词表：(词, 权重)。刻意做小、做准——每一条都应有明确情绪指向。
_CUES: dict[str, tuple[tuple[str, float], ...]] = {
    "低落": (
        ("难过", 0.55), ("难受", 0.50), ("想哭", 0.60), ("哭了", 0.45),
        ("低落", 0.60), ("提不起劲", 0.55), ("没意思", 0.45), ("空虚", 0.55),
        ("压抑", 0.55), ("心里堵", 0.50), ("心里难受", 0.55), ("失落", 0.45),
        ("沮丧", 0.60), ("绝望", 0.75), ("麻木", 0.50), ("不想说话", 0.45),
    ),
    "焦虑": (
        ("焦虑", 0.65), ("担心", 0.45), ("紧张", 0.50), ("害怕", 0.50),
        ("睡不着", 0.55), ("失眠", 0.60), ("心慌", 0.60), ("压力", 0.50),
        ("来不及", 0.50), ("怎么办", 0.35), ("坐立不安", 0.65), ("慌", 0.40),
    ),
    "愤怒": (
        ("生气", 0.60), ("愤怒", 0.65), ("气死", 0.65), ("很烦", 0.45),
        ("烦死", 0.55), ("讨厌", 0.45), ("恨", 0.55), ("受不了", 0.50),
        ("憋屈", 0.55), ("不公平", 0.45),
    ),
    "孤独": (
        ("孤独", 0.65), ("寂寞", 0.60), ("没人懂", 0.60), ("没人说话", 0.55),
        ("空落落", 0.55),
    ),
    "疲惫": (
        ("疲惫", 0.60), ("撑不住", 0.60), ("没力气", 0.50), ("累", 0.35),
        ("熬夜", 0.35), ("身心俱疲", 0.70), ("倦", 0.40),
    ),
    "轻快": (
        ("开心", 0.60), ("高兴", 0.55), ("太好了", 0.55), ("舒服", 0.45),
        ("轻松", 0.50), ("欣慰", 0.50), ("幸福", 0.60), ("顺利", 0.40),
        ("谢谢", 0.25), ("感谢", 0.25),
    ),
}

# 否定形式：正向词被否定时不能算“轻快”，反而应算低落。
_NEG_POSITIVE = {
    "开心": "不开心", "高兴": "不高兴", "舒服": "不舒服",
    "快乐": "不快乐", "顺利": "不顺利",
}
_NEG_WORDS = ("不", "没", "并不", "不太")

_BASELINE: dict[str, Any] = {
    "mood": "平静",
    "valence": 0.0,
    "arousal": _BASE_AROUSAL,
    "intensity": 0.0,
    "confidence": 0.0,
    "cues": [],
    "heavy": False,
    "updated_ts": None,
    "note": None,
}


def baseline() -> dict[str, Any]:
    """返回初始心境（平静）的副本。调用方可以放心改它。"""
    return dict(_BASELINE, cues=[])


# ---------------------------------------------------------------- 纯函数：识别


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def detect_affect(text: str) -> dict[str, Any]:
    """从【这一句话】估一个心境信号（纯函数，不读库、不调模型）。

    返回：
        {mood, valence, arousal, intensity, confidence, cues, heavy}
    其中 intensity/confidence 都是 0..1 的【估计值】，不是概率，
    也不代表“用户真的就是这个情绪”——它只用于决定语气轻重。
    """
    t = (text or "").strip()
    if not t:
        return _signal("平静", 0.0, [])

    scores: dict[str, float] = {}
    hits: dict[str, list[str]] = {}

    # ① 丧痛：先看明确的丧事词，再看“人物 + 弱丧事词”的组合。
    grief_hits: list[str] = []
    strong = [w for w in _STRONG_GRIEF if w in t]
    if strong:
        grief_hits.extend(strong)
    has_person = any(p in t for p in _KIN_WORDS)
    if has_person:
        weak = [w for w in _WEAK_GRIEF if w in t]
        grief_hits.extend(weak)
    if grief_hits:
        scores["丧痛"] = min(1.0, sum(0.70 if w in strong else 0.40 for w in grief_hits))
        hits["丧痛"] = grief_hits

    # ② 其余基调：词表命中即加分；正向词先看否定形式。
    for mood, cues in _CUES.items():
        for word, weight in cues:
            if mood == "轻快":
                neg = _NEG_POSITIVE.get(word)
                if neg and neg in t:
                    # “不开心”不是轻快，记到低落上（只记一次，避免重复加分）。
                    scores["低落"] = scores.get("低落", 0.0) + 0.45
                    hits.setdefault("低落", []).append(neg)
                    continue
                if any((n + word) in t for n in ("不", "没")) and word in t:
                    continue
            if word in t:
                scores[mood] = scores.get(mood, 0.0) + weight
                hits.setdefault(mood, []).append(word)

    if not scores:
        return _signal("平静", 0.0, [])

    # ③ 选最高分；同分按 MOODS 顺序（丧痛优先），避免“生死话题被别的词盖掉”。
    mood = max(MOODS, key=lambda m: (round(scores.get(m, 0.0), 3),
                                     -MOODS.index(m)))
    raw = min(1.0, scores.get(mood, 0.0))
    if raw < _MIN_SIGNAL:
        return _signal("平静", 0.0, [])

    cues = hits.get(mood, [])[:4]
    sig = _signal(mood, raw, cues)
    # 置信度看“证据量”而非强度：两条线索 > 一条线索；强度再补一点。
    sig["confidence"] = round(min(0.95, 0.30 + 0.25 * min(len(cues), 2) + 0.30 * raw), 3)
    return sig


def _signal(mood: str, intensity: float, cues: list[str]) -> dict[str, Any]:
    tone_v, tone_a = _MOOD_TONE[mood]
    amplitude = 0.55 + 0.45 * _clamp(intensity)
    valence = round(tone_v * amplitude, 3) if mood != "平静" else 0.0
    arousal = round(_BASE_AROUSAL + (tone_a - _BASE_AROUSAL) * amplitude, 3)
    return {
        "mood": mood,
        "valence": valence,
        "arousal": arousal,
        "intensity": round(_clamp(intensity), 3),
        "confidence": 0.20 if mood == "平静" else 0.50,
        "cues": list(cues),
        "heavy": mood in ("丧痛", "低落") and intensity >= _HEAVY_MIN_INTENSITY,
    }


# ---------------------------------------------------------------- 纯函数：衰减 / 混合


def _parse_ts(ts: Any) -> datetime.datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        # 统一成本地 naive 时间再比较——M2 的 _now() 也是 naive 本地时间，
        # 混用 aware/naive 会在 Windows 上直接抛 TypeError（同类坑见 M6 时区）。
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _now(now: Any = None) -> datetime.datetime:
    if isinstance(now, datetime.datetime):
        return now.astimezone().replace(tzinfo=None) if now.tzinfo else now
    if isinstance(now, str):
        dt = _parse_ts(now)
        if dt is not None:
            return dt
    return datetime.datetime.now()


def _elapsed_hours(state: dict[str, Any], now: datetime.datetime) -> float:
    prev = _parse_ts(state.get("updated_ts"))
    if prev is None:
        return 0.0
    return max(0.0, (now - prev).total_seconds() / 3600.0)


def decay(state: dict[str, Any], elapsed_h: float) -> dict[str, Any]:
    """把心境朝“平静”衰减（纯函数）。半衰期按基调分档，见 _HALF_LIFE_H。

    只影响数值与 heavy 标记；不改 updated_ts（衰减不是一次“更新”，
    否则每次读都会把时间戳往后推，半衰期就永远走不到头）。
    """
    st = dict(state)
    if elapsed_h <= 0:
        return st

    base = baseline()
    mood = st.get("mood") or "平静"
    half = _HALF_LIFE_H.get(mood, 12.0)
    factor = 0.5 ** (elapsed_h / half)

    st["valence"] = round(base["valence"] + ((st.get("valence") or 0.0) - base["valence"]) * factor, 3)
    st["arousal"] = round(base["arousal"] + ((st.get("arousal") or 0.0) - base["arousal"]) * factor, 3)
    st["intensity"] = round(_clamp((st.get("intensity") or 0.0) * factor), 3)
    if st["intensity"] < _MIN_INTENSITY:
        st.update(mood="平静", valence=0.0, arousal=base["arousal"],
                  intensity=0.0, cues=[], heavy=False)
    else:
        st["heavy"] = st["mood"] in ("丧痛", "低落") and st["intensity"] >= _HEAVY_MIN_INTENSITY
    return st


def blend(prev: dict[str, Any], signal: dict[str, Any], elapsed_h: float = 0.0) -> dict[str, Any]:
    """把“新信号的估计”混进“已衰减的旧心境”（纯函数）。

    权重 alpha 由信号置信度决定（证据越足，越信这一次）；
    丧痛/低落这类重情绪给一个下限，避免被上一轮的残余分掉。
    """
    p = decay(prev, elapsed_h)
    sig = dict(signal)
    if sig.get("mood") == "平静":
        # 平静信号不代表“变开心”，只代表这一句没线索——不拿它冲淡旧心境。
        return p

    alpha = _clamp(0.30 + 0.50 * float(sig.get("confidence") or 0.5), 0.30, 0.85)
    if sig["mood"] in ("丧痛", "低落") and sig["intensity"] >= 0.6:
        alpha = max(alpha, 0.60)

    new_v = p.get("valence", 0.0) * (1 - alpha) + sig["valence"] * alpha
    new_a = p.get("arousal", _BASE_AROUSAL) * (1 - alpha) + sig["arousal"] * alpha
    new_i = _clamp((p.get("intensity") or 0.0) * (1 - alpha) + sig["intensity"] * alpha)

    # 基调归属：比【强度】而不比“新旧”，重情绪还多一道 25% 的“换台门槛”。
    # 为什么要门槛：丧亲后一句“心里堵”不该把“丧痛”降级成“低落”（语境不是瞬间情绪）；
    # 但真正强烈的反向信号（例如几天后“今天特别开心”）要允许它翻篇，否则就是刻板。
    prev_mood = p.get("mood") or "平静"
    prev_intensity = p.get("intensity") or 0.0
    margin = _MOOD_SWITCH_MARGIN if prev_mood in ("丧痛", "低落") else 1.0
    mood = sig["mood"] if sig["intensity"] > prev_intensity * margin else prev_mood

    cues = list(sig.get("cues") or []) if mood == sig["mood"] else list(p.get("cues") or [])
    return {
        "mood": mood,
        "valence": round(new_v, 3),
        "arousal": round(new_a, 3),
        "intensity": round(new_i, 3),
        "confidence": max(float(sig.get("confidence") or 0.0),
                          float(p.get("confidence") or 0.0) * 0.8),
        "cues": cues[:4],
        "heavy": mood in ("丧痛", "低落") and new_i >= _HEAVY_MIN_INTENSITY,
        "updated_ts": p.get("updated_ts"),
        "note": p.get("note"),
    }


# ---------------------------------------------------------------- 持久化（只碰 affective_state）


_ROW_KEYS = ("mood", "valence", "arousal", "updated_ts", "note")


def _row_to_state(row: Any) -> dict[str, Any]:
    """把一行（sqlite3.Row / dict / 普通元组）转成状态 dict。

    普通元组也要支持：M2 现在开了 row_factory，但别的调用方（demo、未来换库）
    未必开——情感层不该因为“行对象是什么类型”而静默失效。
    """
    if row is None:
        return baseline()
    try:
        if hasattr(row, "keys"):
            data = {k: row[k] for k in _ROW_KEYS}
        else:
            values = tuple(row)
            if len(values) < len(_ROW_KEYS):
                return baseline()
            data = dict(zip(_ROW_KEYS, values))
    except (KeyError, IndexError, TypeError, ValueError):
        return baseline()

    st = baseline()
    try:
        st["mood"] = data["mood"] or "平静"
        st["valence"] = float(data["valence"] or 0.0)
        st["arousal"] = float(data["arousal"] if data["arousal"] is not None else _BASE_AROUSAL)
        st["updated_ts"] = data["updated_ts"]
        st["note"] = data["note"]
    except (TypeError, ValueError):
        return baseline()

    if st["mood"] not in MOODS:
        st["mood"] = "平静"
    # note 里存的是 M5 自己的元数据（强度/置信度/线索）；老数据没有就按幅度反推。
    meta: dict[str, Any] = {}
    if st["note"]:
        try:
            meta = json.loads(st["note"]) if str(st["note"]).lstrip().startswith("{") else {}
        except (ValueError, TypeError):
            meta = {}
    st["intensity"] = round(_clamp(float(meta.get("intensity", abs(st["valence"])) or 0.0)), 3)
    st["confidence"] = round(_clamp(float(meta.get("confidence", 0.5) or 0.0)), 3)
    st["cues"] = [str(c) for c in (meta.get("cues") or [])][:4]
    st["heavy"] = st["mood"] in ("丧痛", "低落") and st["intensity"] >= _HEAVY_MIN_INTENSITY
    st["valence"] = round(_clamp(st["valence"], -1.0, 1.0), 3)
    st["arousal"] = round(_clamp(st["arousal"]), 3)
    return st


def _read_row(mem: Any) -> Any:
    getter = getattr(mem, "get_affect", None)
    if callable(getter):
        return getter()
    conn = getattr(mem, "conn", None)
    if conn is None:
        return None
    try:
        return conn.execute(
            "SELECT mood,valence,arousal,updated_ts,note FROM affective_state WHERE id=1"
        ).fetchone()
    except Exception:  # noqa: BLE001 —— 表不存在/库只读时退回初始态，不让情感层拖垮主链路
        return None


def _write_row(mem: Any, st: dict[str, Any]) -> None:
    setter = getattr(mem, "set_affect", None)
    if callable(setter):
        # 脊柱 v0.17 的契约是 set_affect(mood, valence, arousal, note)：
        # updated_ts 由 M2 自己盖（单一时间源），M5 不越权改时间戳。
        setter(st["mood"], st["valence"], st["arousal"], st["note"])
        return
    conn = getattr(mem, "conn", None)
    if conn is None:
        raise RuntimeError("M5.update_affect 需要一个带 conn 的 Memory（或实现 set_affect）")
    conn.execute(
        "INSERT INTO affective_state(id,mood,valence,arousal,updated_ts,note)"
        " VALUES(1,?,?,?,?,?)"
        " ON CONFLICT(id) DO UPDATE SET mood=excluded.mood, valence=excluded.valence,"
        " arousal=excluded.arousal, updated_ts=excluded.updated_ts, note=excluded.note",
        (st["mood"], st["valence"], st["arousal"], st["updated_ts"], st["note"]),
    )
    conn.commit()


def read_affect(mem: Any, now: Any = None) -> dict[str, Any]:
    """读出当前心境，并按“距上次更新时间”做衰减（只读，不写库）。

    mem 只需提供 .conn（M2 现状），或将来实现 get_affect()。
    """
    st = _row_to_state(_read_row(mem))
    return decay(st, _elapsed_hours(st, _now(now)))


def update_affect(mem: Any, text: str, now: Any = None) -> dict[str, Any]:
    """用这一句用户原话更新心境：读 → 估 → 混 → 落库 → 返回新状态。

    只写 affective_state 一行；不碰 episodes/facts/principles（边界见模块头）。
    """
    moment = _now(now)
    prev = read_affect(mem, moment)          # 已经衰减到此刻
    signal = detect_affect(text)
    st = blend(prev, signal, elapsed_h=0.0)  # prev 已衰减，这里不再重复扣
    if signal["mood"] != "平静":
        st["updated_ts"] = moment.isoformat(timespec="seconds")
        st["note"] = json.dumps({
            "v": 1,
            "intensity": st["intensity"],
            "confidence": st["confidence"],
            "cues": st["cues"],
        }, ensure_ascii=False)
        _write_row(mem, st)
    return st


# ---------------------------------------------------------------- 给 M1 的两块


_TONE_GUIDE: dict[str, tuple[str, ...]] = {
    "丧痛": ("先承接，不急着劝", "不追问去世细节", "不说“节哀顺变/想开点”这类话", "句子短一些，留白"),
    "低落": ("先陪着，不评判", "不催“振作”", "可以问一件具体的小事，别问“你怎么了”"),
    "焦虑": ("先把事情缩到“下一步做什么”", "不空喊“别焦虑”", "给一个可执行的小动作"),
    "愤怒": ("先承认他受了委屈", "不站队骂人，也不急着讲道理"),
    "孤独": ("让他感到被记得", "不说“多出去走走”这种空话"),
    "疲惫": ("少问多听", "不安排新任务"),
    "轻快": ("跟着他的语速，不必扫兴提醒"),
    "平静": ("正常交流即可，不必刻意安慰",),
}


def prompt_block(state: dict[str, Any]) -> str:
    """生成进 prompt 的“情感基调”段（字符串，可直接拼进 _build_prompt）。

    刻意写明三条边界：这是【估计】不是诊断、以对方说的为准、不宣告。
    这样即使用真模型，也不会把内部变量当成事实念给用户听。
    """
    st = state or baseline()
    mood = st.get("mood") or "平静"
    guides = _TONE_GUIDE.get(mood, _TONE_GUIDE["平静"])
    cues = "、".join(st.get("cues") or []) or "无明显线索"
    lines = [
        "[对方此刻的情绪基调（系统内部估计 · 功能态，不是诊断）]",
        f'- 基调：{mood}（强度 {st.get("intensity", 0.0):.2f}，依据：{cues}）',
        "- 相处方式：" + "；".join(guides),
        "（这是估计不是事实：不要向对方宣告“你现在是什么情绪”；"
        "对方若说没事，以对方说的为准。）",
    ]
    return "\n".join(lines)


# 重情绪下明确不该出现的“假安慰”。删的是话，不是人的意思——
# 只在这些词所在的【小句】里删除，其余信息原样保留。
_INSENSITIVE = (
    "想开点", "看开点", "节哀顺变", "别难过了", "别难过", "振作起来",
    "别想太多", "没什么大不了", "时间会冲淡", "会好起来的", "很快就好",
    "你要坚强", "至少你",
)

_CLAUSE_SPLIT = re.compile(r"(?<=[，,])")
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])")


def modulate(response: str, state: dict[str, Any]) -> str:
    """输出侧语气护栏：重情绪下删掉“假安慰”小句；只删不加，删空则回退原文。

    为什么只做“删”：改写模型的话需要语义能力，M5 没有，也不该有——
    一旦开始“润色”，情感层就会悄悄替认知层做决定（违背铁律 4 的分工）。
    唯一会“加”的是一个句号：删掉小句后给句子收尾，不加任何语义内容。
    """
    text = response or ""
    st = state or baseline()
    if not st.get("heavy"):
        return text

    kept_sentences: list[str] = []
    for sent in _SENT_SPLIT.split(text):
        if not sent:
            continue
        clauses = _CLAUSE_SPLIT.split(sent)
        kept = [c for c in clauses if not any(w in c for w in _INSENSITIVE)]
        if len(kept) == len(clauses):
            kept_sentences.append(sent)      # 没删东西的句子原样保留
            continue
        sentence = "".join(kept).strip().rstrip("，, ")
        if sentence.strip(" \t\r\n。！？!?；;") == "":
            continue                          # 整句都是套话 → 整句去掉
        # 只有“动过”的句子才补句号，没动过的句子一个字符都不改。
        if sentence[-1] not in "。！？!?；;":
            sentence += "。"
        kept_sentences.append(sentence)

    out = "".join(kept_sentences)
    out = re.sub(r"([。！？!?；;])\1+", r"\1", out).strip()
    # 删光的极端情况：保留原文，宁可笨拙，也不要空回应。
    return out or text


# 兼容“上岗指令”第 2 节的叫法（read(input)/modulate(response, state)）。
modulate_affect = modulate
