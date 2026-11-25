# -*- coding: utf-8 -*-
"""Mirrai 六态情绪层（移植自 Ch1rpys/Mirrai，MIT）。

只移植「情绪状态判定 + prompt 描述」，不移植 Node/PostgreSQL 外壳。
原项目：https://github.com/Ch1rpys/Mirrai
"""
from __future__ import annotations

import re

CAPABILITY = {
    "name": "Mirrai 六态情绪层",
    "switch_env": "PAI_MIRRAI_EMOTION",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "无匹配=warm；只进 prompt，不写记忆",
    "approval": "ADR-046：本地只读规则，不新增开关",
}

STATES = ("warm", "playful", "nostalgic", "melancholy", "happy", "distant")
TRIGGER_STATES = ("nostalgic", "playful", "melancholy", "happy", "distant")

PATTERNS = {
    "zh": {
        "nostalgic": r"想你|思念|好久|那时候|记得吗|以前",
        "playful": r"哈哈|笑死|好玩|有意思|逗|搞笑",
        "melancholy": r"难过|伤心|哭|委屈|不(?:开心|高兴)",
        "happy": r"开心|高兴|棒|太好了|耶|爱你",
        "distant": r"随便|无所谓|算了|不想说",
    },
    "en": {
        "nostalgic": r"miss you|remember when|used to|reminds me of",
        "playful": r"haha|lol|that'?s funny|joking|kidding",
        "melancholy": r"sad|crying|upset|hurt|heartbroken",
        "happy": r"happy|great|awesome|i love you|so glad",
        "distant": r"whatever|doesn'?t matter|forget it|not talking",
    },
}

DESC = {
    "warm": "温柔：先接住对方，语速慢一点，不抢结论。",
    "playful": "俏皮：可以逗，但不拿对方的痛点开玩笑。",
    "nostalgic": "思念：承认旧事有分量，不急着把话拉回现在。",
    "melancholy": "忧郁：少讲道理，先陪着；不强行乐观。",
    "happy": "开心：跟上对方的好消息，可以一起高兴。",
    "distant": "疏离：对方在退，先不追问；给空间，留一句可回来的话。",
}


def _clean_negation(t):
    """别把“我不难过/别难过/开心不起来”当成正情绪。"""
    if re.search(r"不(开心|高兴)", t):
        return t
    t = re.sub(r"(?:不|没|没有|别)\s*(?:难过|伤心|哭|委屈|开心|高兴|想你)", "", t)
    t = re.sub(r"(?:开心|高兴|难过|伤心)(?:不起来|不了)", "", t)
    return t


def match_state(text, locale="zh"):
    t = _clean_negation(str(text or ""))
    pats = PATTERNS.get(locale) or PATTERNS["zh"]
    for state in TRIGGER_STATES:
        if re.search(pats[state], t, re.I):
            return state
    return None


def compute_state(user_text, ai_text="", current="", locale="zh"):
    combined = "%s\n%s" % (user_text or "", ai_text or "")
    rule = match_state(combined, locale)
    if rule:
        return rule
    if re.search(r"(开心|高兴)(不起来|不了)", combined):
        return "melancholy"
    if re.search(r"(?:不|没|没有|别)\s*(?:难过|伤心|哭|委屈|想你)", combined):
        return current if current in STATES else "warm"
    try:
        from pai import emotion_model as EM
        if EM.enabled():
            label, conf = EM.classify(combined)
            if label in STATES and label != "warm" and conf >= 0.22:
                return label
    except Exception:
        pass
    return current if current in STATES else "warm"


def prompt_block(text, current="", locale="zh"):
    state = compute_state(text, "", current, locale)
    return "\n".join([
        "[镜像情绪 · Mirrai 六态]",
        "当前状态：%s" % state,
        DESC[state],
        "这是内部估计，不是诊断；以对方说的为准。",
    ])
