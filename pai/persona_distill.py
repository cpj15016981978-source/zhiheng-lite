# -*- coding: utf-8 -*-
"""Mirrai 七层人设蒸馏：启发式兜底 + LLM 两阶段。

移植自 Ch1rpys/Mirrai（MIT）。LLM 不可用时退回启发式，不伪造成功。
"""
from __future__ import annotations

from pai import ai_provider as AIP

import collections
import datetime
import json
import pathlib
import re

from pai import atomic as AT

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "personas"

CAPABILITY = {
    "name": "七层人设蒸馏",
    "switch_env": "PAI_PERSONA_DISTILL",
    "default_on": True,
    "stores_user_text": True,
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
    "network": True,
    "degrade": "LLM 不可用=启发式兜底；素材不足=字段留空，不编造",
    "approval": "ADR-046：本地文本分析，显式运行",
}

LAYERS = ("personality", "speechStyle", "catchphrases", "attachmentStyle",
          "loveLanguage", "conflictStyle", "triggeredMoments")
COMMON = ("哈哈", "嗯嗯", "好吧", "行吧", "知道了", "晚安", "早安", "想你", "没事",
          "好的", "收到", "随便", "算了", "别急", "我在", "辛苦", "厉害")


def _msgs(messages):
    out = []
    for m in messages or []:
        if isinstance(m, dict):
            c = str(m.get("content") or "").strip()
        else:
            c = str(m or "").strip()
        if c:
            out.append(c)
    return out


def _fmt(messages):
    return "\n".join(_msgs(messages))


def _count(text, words):
    return sum(text.count(w) for w in words)


def _first(text, pat, default):
    m = re.search(pat, text)
    return m.group(0) if m else default


def distill(messages, name="未命名"):
    texts = _msgs(messages)
    text = "\n".join(texts)
    if not texts:
        raise ValueError("没有聊天素材")

    avg_len = sum(len(t) for t in texts) / len(texts)
    speech = "短句、直接" if avg_len < 18 else ("中等句长" if avg_len < 45 else "长句、解释多")
    if _count(text, ("哈哈", "笑死", "哈哈哈")) >= 2:
        speech += "，偏俏皮"
    if _count(text, ("其实", "就是", "然后")) >= 3:
        speech += "，口语连接词多"

    counts = collections.Counter()
    for w in COMMON:
        n = text.count(w)
        if n:
            counts[w] = n
    catch = [w for w, _ in counts.most_common(5)]

    attachment = "安全型"
    if _count(text, ("别走", "不要离开", "你回我", "在吗", "想我")) >= 2:
        attachment = "偏焦虑型：需要回应确认"
    elif _count(text, ("自己", "空间", "不用管", "一个人")) >= 2:
        attachment = "偏回避型：需要空间"

    love = "精心时刻" if _count(text, ("陪我", "一起", "时间")) >= 2 else "服务行动"
    if _count(text, ("礼物", "买给", "送你")) >= 2:
        love = "礼物"
    elif _count(text, ("夸", "厉害", "你真")) >= 2:
        love = "肯定言语"

    conflict = "先冷静再沟通"
    if _count(text, ("冷战", "不理", "沉默")) >= 1:
        conflict = "冲突时先退开，容易冷战"
    elif _count(text, ("吵", "生气", "吼")) >= 1:
        conflict = "冲突时情绪先上来"

    emotional = [t for t in texts if re.search(r"难过|开心|害怕|想你|生气|委屈|谢谢|对不起", t)]
    triggers = []
    for t in emotional[:5]:
        if t not in triggers:
            triggers.append(t[:120])

    return {
        "name": str(name or "未命名"),
        "distilledAt": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "method": "heuristic",
        "sourceCount": len(texts),
        "personality": "偏重关系与感受，愿意解释" if _count(text, ("因为", "觉得", "感受")) >= 2 else "偏直接、就事论事",
        "speechStyle": speech,
        "catchphrases": catch,
        "attachmentStyle": attachment,
        "loveLanguage": love,
        "conflictStyle": conflict,
        "triggeredMoments": triggers,
        "evidence": {"avgChars": round(avg_len, 1), "charCount": len(text)},
    }


def clean_slug(slug):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(slug or "")).strip("_")[:64] or "persona"


def save(data, slug=None):
    slug = clean_slug(slug or data.get("name") or "persona")
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / (slug + ".json")
    AT.write_text(p, json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def prompt_block(data):
    if not data:
        return ""
    return "\n".join([
        "[人设蒸馏 · %s]" % data.get("name", ""),
        "性格：%s" % data.get("personality", ""),
        "表达：%s" % data.get("speechStyle", ""),
        "口头禅：%s" % "、".join(data.get("catchphrases") or []) or "—",
        "依恋：%s" % data.get("attachmentStyle", ""),
        "爱的语言：%s" % data.get("loveLanguage", ""),
        "冲突：%s" % data.get("conflictStyle", ""),
        "只按素材说，不补不存在的细节。",
    ])


ACTIVE = OUT / "active.json"


def _parse_json(text):
    m = re.search(r"\{.*\}", str(text or ""), re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _llm_call(prompt):
    from pai import model_gateway as M10
    outs = AIP.complete(prompt)
    return str(outs[0].get("output") or "") if outs else ""


def llm_distill(messages, name="未命名"):
    """两阶段：分块抽维度 -> 合并成七层。失败退回启发式。"""
    texts = _msgs(messages)
    if not texts:
        raise ValueError("没有聊天素材")
    chunks = []
    buf = []
    size = 0
    for t in texts:
        if size + len(t) > 6000 and buf:
            chunks.append("\n".join(buf)); buf, size = [], 0
        buf.append(t); size += len(t)
    if buf:
        chunks.append("\n".join(buf))
    partial = []
    for c in chunks[:6]:
        prompt = (
            "[任务] 从下面的聊天素材中抽七层人设，只输出 JSON，不要解释。\n"
            "[素材开始]\n" + c + "\n[素材结束]\n"
            "字段：personality, speechStyle, catchphrases(数组), attachmentStyle, "
            "loveLanguage, conflictStyle, triggeredMoments(数组)。"
            "只依据素材，不补不存在的细节。")
        d = _parse_json(_llm_call(prompt))
        if d:
            partial.append(d)
    if not partial:
        return distill(messages, name)
    merged = dict(partial[0])
    for d in partial[1:]:
        for k, v in d.items():
            if k == "catchphrases" or k == "triggeredMoments":
                a = merged.get(k) or []
                if isinstance(a, str): a = [a]
                b = v if isinstance(v, list) else [v]
                merged[k] = list(dict.fromkeys([str(x) for x in a + b]))[:8]
            elif not merged.get(k) and v:
                merged[k] = v
    # 第二段：让模型做一次合并/校正；失败就用 merge 结果
    prompt2 = (
        "[任务] 把下面的人设草稿合并成一份简洁 JSON，只输出 JSON。\n"
        "[草稿开始]\n" + json.dumps(merged, ensure_ascii=False)[:6000] + "\n[草稿结束]\n"
        "保持七层字段；不要新增素材里没有的事实。")
    final = _parse_json(_llm_call(prompt2))
    data = final or merged
    base = distill(messages, name)
    for k in LAYERS:
        if not data.get(k):
            data[k] = base.get(k)
    data["name"] = name
    data["distilledAt"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    data["method"] = "llm"
    data["sourceCount"] = len(texts)
    data.setdefault("evidence", {"avgChars": round(sum(len(t) for t in texts) / len(texts), 1),
                                 "charCount": sum(len(t) for t in texts)})
    for k in LAYERS:
        data.setdefault(k, [] if k in ("catchphrases", "triggeredMoments") else "")
    return data


def import_text(text, name="未命名", use_llm=True, slug=None):
    msgs = [{"content": x.strip()} for x in str(text or "").splitlines() if x.strip()]
    if use_llm:
        try:
            data = llm_distill(msgs, name)
            if not all(data.get(k) for k in ("personality", "speechStyle", "attachmentStyle", "loveLanguage", "conflictStyle")):
                raise ValueError("LLM 人设字段不完整")
        except Exception:
            data = distill(msgs, name)
            data["method"] = "heuristic_fallback"
    else:
        data = distill(msgs, name)
    path = save(data, slug=slug)
    return {"ok": True, "data": data, "path": path, "slug": pathlib.Path(path).stem}


def list_personas():
    OUT.mkdir(parents=True, exist_ok=True)
    return sorted(p.stem for p in OUT.glob("*.json") if p.name != "active.json")


def save_active(slug):
    slug = clean_slug(slug)
    if not (OUT / (slug + ".json")).is_file():
        raise ValueError("人设不存在：%s" % slug)
    AT.write_text(OUT / "active.json", json.dumps({"slug": slug}, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "active": slug}


def active_slug():
    try:
        return json.loads((OUT / "active.json").read_text(encoding="utf-8")).get("slug") or ""
    except Exception:
        return ""


def active_data():
    try:
        slug = json.loads((OUT / "active.json").read_text(encoding="utf-8")).get("slug")
    except Exception:
        return None
    if not slug:
        return None
    try:
        return json.loads((OUT / (slug + ".json")).read_text(encoding="utf-8"))
    except Exception:
        return None


def prompt_block_active():
    return prompt_block(active_data())
