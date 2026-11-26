"""M1 编排核心 — 见《00_协作规程与接口契约.txt》PART B2 (M1 Orchestrator.handle)。

Phase1 行走骨架的一条端到端通路：
    用户输入 → 落库(episode) → 极简事实抽取 → 召回相关记忆
             → 构造 grounded 上下文 → 调模型(mock) → 组织回应 → 落库 → 返回 {response, trace}

体现铁律6：返回的 trace 明确区分 recalled(查到的既有内容) 与 reason(模型的分析)。
Phase2 起，事实抽取与原则合成由 M4 反思引擎(reflection.py)承担；
原则(principle)作为 grounded lens 注入 prompt，是“独立思考”的落脚点。
Phase3 起，加“主动发起”通路：tick(now)→由 M6 挑出到期触发器→proactive() 组织
grounded 主动消息→写入 outbox（落实“关掉软件它也会主动找你”）。
"""
import datetime
import json
import os
import re
import sys
import time
import uuid

from . import ai_provider as AIP
from . import model_gateway as M10
from . import local_model as LM
from . import model_usage as MU
from . import memory_brain as MB
from . import wiki as WK
from . import router as RT
from . import reflection as M4
from . import scheduler as M6
from . import cultural_kb as M9
from . import poetry_kb as PK
from . import local_duo as DUO
from . import local_cascade as CASCADE
from . import affect as M5
from . import self_model as SM
from . import self_review as SC
from . import voice as M14
from . import tool_loop as TL
from . import tools as M8
from . import workspace as WS
from . import trace as TR
from . import activity as ACT
from . import persona as PS
from . import prompt_layout as PL
from . import capability_router as CR
from . import code_route as CRO
from . import memory_layers as ML

# 密钥护栏（ADR-017 红线的内核级落实）：用户若把 API Key/私钥当成聊天内容贴进来，
# 一律拦截——不落库、不反思、不召回、不联网调用，也不回显原句。密钥只应走
# chat.py --live 的盲输(getpass)或环境变量，绝不该出现在对话/记忆里。
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_.\-]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*\S{12,}"),
)


def _looks_secret(text):
    return any(p.search(text) for p in _SECRET_PATTERNS)


# ---- Phase4 M3 议会：两模型互审，只让【分歧可见】，不综合、不裁判（判断权留给用户）----
_DIVERGENCE_THRESHOLD = 0.6


def _bigrams(s):
    s = "".join(ch for ch in s if not ch.isspace())
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _divergence(outs):
    """两份输出的字符二元 Jaccard【相似度】(0..1)。越低越分歧。
    不足两份 / 有效文本不足两条 → None（单模型不该有分歧度）。
    注意：这是【提示】不是【判决】——分歧大只是提醒用户"两份看法不一样"。"""
    texts = [o.get("output", "") for o in outs if o.get("output", "").strip()]
    if len(texts) < 2:
        return None
    a, b = _bigrams(texts[0]), _bigrams(texts[1])
    union = a | b
    score = (len(a & b) / len(union)) if union else 1.0
    return {"score": round(score, 4), "flagged": score < _DIVERGENCE_THRESHOLD,
            "method": "bigram_jaccard"}


def _complete(prompt, models=None, on_delta=None, on_reasoning=None):
    kwargs = {"models": models}
    if on_delta is not None:
        kwargs["on_delta"] = on_delta
    if on_reasoning is not None:
        kwargs["on_reasoning"] = on_reasoning
    fallback = False
    try:
        outs = AIP.complete(prompt, **kwargs)
    except Exception as e:
        hybrid_cloud = None
        if os.environ.get("PAI_HYBRID", "").strip().lower() in ("1", "on", "true", "yes"):
            try:
                hybrid_cloud = AIP.complete(
                    prompt, models=(os.environ.get("PAI_HYBRID_MODEL", "deepseek:deepseek-chat"),),
                    on_delta=on_delta, on_reasoning=on_reasoning)
            except Exception:
                hybrid_cloud = None
        if hybrid_cloud:
            outs = hybrid_cloud
            MU.record(outs, fallback=False)
            TR.TRACE.record_model_call(prompt, outs)
            return outs
        if LM.enabled():
            t0 = time.time()
            try:
                text = LM.complete(prompt, on_delta=on_delta)
                outs = [{"model_id": "ollama:" + LM.model_name(),
                         "model": LM.model_name(), "output": text,
                         "cost": 0.0, "priced": True, "tokens": 0,
                         "latency": round(time.time() - t0, 3), "fallback": True,
                         "spend_report": "[报账] cloud失败，本地兜底，无云端费用"}]
                fallback = True
            except Exception:
                TR.TRACE.record_error(prompt, e)
                raise
        else:
            TR.TRACE.record_error(prompt, e)
            raise
    MU.record(outs, fallback=fallback)
    TR.TRACE.record_model_call(prompt, outs)
    if not fallback and on_delta is None and outs:
        try:
            from pai import cascade_shadow as CS
            CS.observe_async(prompt, str(outs[0].get("output") or ""), LM.complete)
        except Exception:
            pass
    return outs


def _build_prompt(text, recalled, facts, principles=None, culture=None, affect=None,
                  self_desc=None, extra_rule=None, memory_ctx=None, wiki_ctx=None,
                  router_ctx=None, poetry_ctx=None, reminders=None):
    """拼装提示。★ 内容一个字没改，只是把【顺序】交给 prompt_layout。

    改动原因（实测）：约 1900 字提示冷 prefill 4B 要 88.8s，而同一前缀第二次 0.2s。
    原布局把每轮都变的 [本轮要求] 插在 [我恪守的原则] 标题与条目之间，
    于是 principles / 已知事实 / self_desc 三个稳定段全被排在易变段之后，每轮白重算。
    真实提示跨轮公共前缀只有 355/1133 字（31%）。
    稳定段前置后，可缓存前缀变长；顺序之外什么都没动。
    """
    parts = {
        "system": "[系统] 你是一个有中国文化根基、懂人情与生死的陪伴型AI。",
        "persona": PS.prompt_block(),
        "principles_header": "[我恪守的原则]",
        "principles": ("\n".join(f'- {p["text"]}' for p in principles)
                       if principles else "- (暂无)"),
        "facts": "[已知事实]\n" + ("\n".join(
            f'- {f["subject"]} {f["predicate"]} = {f["object"]}' for f in facts
        ) if facts else "- (暂无)"),
        "reminders": ("[提醒]\n" + "\n".join(
            "- " + str(t.get("message_template") or t.get("message") or t.get("title") or t)
            for t in reminders) if reminders else None),
        "self_desc": self_desc,
        "affect": M5.prompt_block(affect) if affect else None,
        "user": f"[用户] {text}",
    }
    if any(k in str(text) for k in ("能做什么", "会做什么", "能力", "服务", "功能", "这些能做")):
        parts["turn_requirement"] = (
            "[本轮要求] 用户在做能力/服务咨询：按本轮输入逐项回答能做/不能做；"
            "不要续旧话题，不抒情，不编造未接入的服务。")
    if extra_rule:
        parts["extra_rule_header"] = "[本轮行为规则]"
        parts["extra_rule"] = "- " + str(extra_rule).strip()
    if memory_ctx:
        parts["memory_ctx"] = "[记忆档案（带来源）]\n" + memory_ctx
    if wiki_ctx:
        parts["wiki_ctx"] = "[Wiki 知识（带来源）]\n" + wiki_ctx
    if poetry_ctx:
        parts["poetry_ctx"] = "[诗词库（检索所得，非我生成；引用须标作者/篇名）]\n" + poetry_ctx
    if router_ctx:
        parts["router_ctx"] = "[Router 检索（带来源）]\n" + router_ctx
    parts["recalled"] = "[相关记忆]\n" + ("\n".join(
        (f'- ({r["item"]["role"]}) {r["item"]["content"]}'
         if r["item"]["type"] == "episode"
         else f'- {r["item"]["subject"]} {r["item"]["predicate"]} = {r["item"]["object"]}')
        for r in recalled
    ) if recalled else "- (无)")
    # 铁律6：文化条目是【检索到的既有文本】，属 recall，与"我的推断(reason)"分开标注。
    # 必须带出处(ref)，否则会被当成模型自己编的。取前 2—3 条，宁少不塞。
    parts["culture"] = "[可引用的文化背景（检索所得，非我生成；引用时须标出处）]\n" + (
        "\n".join(f'- {c["ref"]}：{c["text"]}' for c in culture[:3]) if culture
        else "- (无相关，不必强行引典)")
    return PL.render(parts)


def _looks_identity(text):
    q = str(text or "").strip()
    if not q or len(q) > 80:
        return False
    return bool(re.search(r"(你叫什么|你是谁|你的名字|你名字|你是什么模型|什么模型|app叫什么|这个app|应用叫什么|软件叫什么)", q))


def _looks_game_prompt(text):
    t = str(text or "")
    return ("[系统] 你在替玩家玩" in t
            or "《大唐西行录" in t
            or ('"choice"' in t and "override" in t))


def _compose_response(recalled, model_out, model_note=None):
    parts = [model_note or "(模型占位)"]
    remembered = []
    for r in recalled[:6]:
        it = r["item"]
        if it["type"] == "fact":
            remembered.append(f'  · {it["subject"]} {it["predicate"]} = {it["object"]}')
        elif it["type"] == "episode" and it["role"] == "user":
            remembered.append(f'  · 你之前说过：“{it["content"][:30]}”')
        # role == "ai" 的过往回应不作为“记忆”回显，避免把自己的话当成用户说的
    if remembered:
        parts.append(f"我记得这些相关的（{len(remembered)} 条）：")
        parts.extend(remembered)
    else:
        parts.append("(这是新话题，我暂时没有相关记忆)")
    parts.append("我的回应：" + model_out)
    return "\n".join(parts)



# ---- 主动推送的“打扰政策”v2（只管出声，不管收件箱）----
# 次序是硬线：先判语音开关（M14.speaking_enabled），关着就走原路径，
# 政策一个字都不介入；只有“真的打算出声”时才看安静时段/额度/间隔。
# 默认：23:30—07:00 不出声、每天最多 3 次、两次至少隔 30 分钟。
# 总开关：PAI_PROACTIVE_POLICY=off 可整体关掉（安静/额度/间隔都不生效）。
_QUIET_DEFAULT = "23:30-07:00"
_VOICE_MAX_DEFAULT = 3
_VOICE_GAP_DEFAULT = 30


def _policy_enabled():
    state = M14.get_proactive()
    if state == "off":
        return False
    if state == "on":
        return True
    return (os.environ.get("PAI_PROACTIVE_POLICY") or "on").strip().lower() != "off"


def _parse_hhmm(text):
    try:
        h, m = str(text).strip().split(":")
        h, m = int(h), int(m)
    except (TypeError, ValueError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return datetime.time(h, m)


def _quiet_spec(spec):
    """把用户写的时段规范成“实际生效的时段”；坏值回默认，'off' 保持 off。"""
    raw = (spec or "").strip()
    if raw.lower() == "off":
        return "off"
    if "-" in raw:
        a, b = raw.split("-", 1)
        if _parse_hhmm(a) is not None and _parse_hhmm(b) is not None:
            return raw
    return _QUIET_DEFAULT


def _in_quiet(now, spec):
    """spec='HH:MM-HH:MM'，支持跨午夜；'off' 表示不启用；坏值按默认时段。"""
    spec = _quiet_spec(spec)
    if spec == "off":
        return False
    a, b = spec.split("-", 1)
    start, end = _parse_hhmm(a), _parse_hhmm(b)
    t = now.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end


def _env_int(name, default):
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


def _mark_voice(mem, now):
    day = now.date().isoformat()
    key = "proactive.voice.count." + day
    try:
        count = int(mem.get_meta(key, "0") or 0)
    except (TypeError, ValueError):
        count = 0
    mem.set_meta(key, count + 1)
    mem.set_meta("proactive.voice.last_ts", now.isoformat(timespec="seconds"))


def _voice_allowed(mem, now):
    """只在“真的打算出声”时调用；返回 (allowed, reason)。"""
    if not _policy_enabled():
        return True, ""
    quiet = _quiet_spec(os.environ.get("PAI_PROACTIVE_QUIET", _QUIET_DEFAULT))
    if quiet != "off" and _in_quiet(now, quiet):
        return False, "安静时段（%s），只进收件箱不出声" % quiet
    cap = _env_int("PAI_PROACTIVE_VOICE_MAX", _VOICE_MAX_DEFAULT)
    day = now.date().isoformat()
    try:
        used = int(mem.get_meta("proactive.voice.count." + day, "0") or 0)
    except (TypeError, ValueError):
        used = 0
    if used >= cap:
        return False, "今天已经念过 %d 次（上限 %d），只进收件箱" % (used, cap)
    gap = _env_int("PAI_PROACTIVE_VOICE_GAP_MIN", _VOICE_GAP_DEFAULT)
    last = mem.get_meta("proactive.voice.last_ts")
    if last:
        try:
            last_dt = datetime.datetime.fromisoformat(last)
            if (now - last_dt).total_seconds() < gap * 60:
                return False, "距上次出声不足 %d 分钟，只进收件箱" % gap
        except (TypeError, ValueError):
            pass
    return True, ""



# ---- 主动推送正文：可选让真模型写（默认关，失败回模板）----
def _proactive_prompt(trigger, greeting, recent_user, name):
    last = ""
    if recent_user:
        last = "；对方最近一句：%s" % recent_user[-1]["content"][:60]
    return (
        "[主动消息任务] 你要主动找用户，写 1 到 2 句中文，不超过 60 字。\n"
        "称呼：%s；触发类型：%s；要提醒的事：%s；正当理由：%s%s\n"
        "要求：只基于以上已知，不编造、不诊断、不承诺；语气像熟人，不肉麻；"
        "不要写“理由/模板”这类元信息。"
        % (name or "用户", trigger["type"], trigger["message_template"],
           trigger["justify_reason"], last)
    )


# ---- B1 工作空间接缝（默认关；关着时不调用、trace 不多键）----
def _workspace_candidates(recalled, facts, principles, culture):
    out, seen = [], set()

    def add(item):
        key = (str(item["kind"]), str(item["id"]))
        if key not in seen:
            seen.add(key)
            out.append(item)

    for r in recalled:
        it = r.get("item") or {}
        add({"kind": str(it.get("type") or "episode"), "id": it.get("id"),
             "text": WS.text_of(it), "ts": it.get("ts"),
             "score": r.get("relevance")})
    for f in facts:
        add({"kind": "fact", "id": f.get("id"),
             "text": WS.text_of(f), "ts": f.get("ts")})
    for pr in principles:
        add({"kind": "principle", "id": pr.get("id"),
             "text": WS.text_of(pr),
             "ts": pr.get("created_ts") or pr.get("revised_ts")})
    for c in culture:
        add({"kind": "culture",
             "id": c.get("ref") or c.get("title") or WS.text_of(c)[:20],
             "text": WS.text_of(c), "ts": c.get("ts")})
    return out


def _workspace_trim(recalled, facts, principles, culture, query, now=None):
    sel = WS.select(_workspace_candidates(recalled, facts, principles, culture),
                    query=query, now=now)
    trace = {"enabled": True, "budget": sel["budget"],
             "selected": [{"kind": x["kind"], "id": x["id"]} for x in sel["selected"]],
             "dropped": len(sel["dropped"]), "reason": ""}
    if not sel["selected"]:
        trace["reason"] = "empty_selection_fallback"
        return recalled, facts, principles, culture, trace
    keep = {(str(x["kind"]), str(x["id"])) for x in sel["selected"]}
    recalled = [r for r in recalled
                if (str((r.get("item") or {}).get("type") or "episode"),
                    str((r.get("item") or {}).get("id"))) in keep]
    facts = [f for f in facts if ("fact", str(f.get("id"))) in keep]
    principles = [pr for pr in principles if ("principle", str(pr.get("id"))) in keep]
    culture = [c for c in culture
               if ("culture", str(c.get("ref") or c.get("title")
                                  or WS.text_of(c)[:20])) in keep]
    return recalled, facts, principles, culture, trace


OFFICE_TASKS = {
    "minutes": "会议纪要员。只依据输入，输出 ## 摘要、## 结论、## 待办（表格：事项|负责人|时间|依据）。不确定写待确认。",
    "summary": "文档摘要员。只依据输入，输出 ## 一句话结论、## 关键点（3-5条）、## 风险/待确认。",
    "actions": "任务提取员。输出 Markdown 表格：事项|负责人|截止时间|依据；无依据写待确认。",
    "excel": "表格清洗员。只依据输入表格，输出 Markdown 表格：整理列名、去重、合并重复项、标出缺失值；不要编造单元格。",
}


def _office_split(text, max_chars=6000):
    text = str(text or "").strip()
    if not text:
        return []
    parts, start = [], 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            cut = max(text.rfind("\n", start, end), text.rfind("。", start, end))
            if cut > start + max_chars // 2:
                end = cut + 1
        parts.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - 200, start + 1)
    return parts


class Orchestrator:
    def __init__(self, mem):
        self.mem = mem
        self.session_id = uuid.uuid4().hex[:8]

    def _qwen_agent_system(self, text):
        recalled = self.mem.recall(text, k=5)
        facts = self.mem.all_facts()
        try:
            reminders = self.mem.list_triggers(active_only=True)
        except Exception:
            reminders = []
        lines = [PS.prompt_block(), "[记忆]"]
        if recalled:
            for r in recalled:
                item = r.get("item") or {}
                if item.get("type") == "episode":
                    lines.append("- " + str(item.get("content") or "")[:300])
                else:
                    lines.append("- %s %s = %s" % (item.get("subject"), item.get("predicate"), item.get("object")))
        else:
            lines.append("- (无)")
        lines.append("[已知事实]")
        lines.extend("- %s %s = %s" % (f.get("subject"), f.get("predicate"), f.get("object")) for f in facts) if facts else lines.append("- (无)")
        lines.append("[提醒]")
        if reminders:
            lines.extend("- " + str(t.get("message_template") or t.get("message") or t) for t in reminders)
        else:
            lines.append("- (无)")
        lines.append("[工具] 需要显式查记忆时调用 recall；自动召回不够再用。")
        return "\n".join(lines)

    def qwen_agent(self, text, on_delta=None):
        """可选引擎：Qwen-Agent 走本地 OpenAI 兼容端点，不替换内置 Orchestrator。"""
        from qwen_agent.agents import Assistant
        from qwen_agent.tools.base import BaseTool

        class RecallTool(BaseTool):
            name = "recall"
            description = "显式查记忆；自动召回不够时使用。"
            parameters = [{"name": "query", "type": "string", "description": "查询词", "required": True},
                          {"name": "k", "type": "integer", "description": "返回条数", "required": False}]

            def __init__(self, mem):
                super().__init__({})
                self.mem = mem

            def call(self, params, **kwargs):
                import json
                args = json.loads(params) if isinstance(params, str) else (params or {})
                was = M8.backend() == M8.BACKEND_ON
                M8.set_enabled(True)
                try:
                    return json.dumps(M8.run("recall", args, self.mem), ensure_ascii=False)
                finally:
                    M8.set_enabled(was)

        key = (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("PAI_MODEL_API_KEY") or "").strip()
        provider = (os.environ.get("PAI_QWEN_AGENT_PROVIDER") or ("deepseek" if key else "local")).strip().lower()
        if provider == "deepseek":
            if not key:
                raise RuntimeError("DeepSeek 未配置：设置 DEEPSEEK_API_KEY")
            base = os.environ.get("PAI_DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
            model = os.environ.get("PAI_QWEN_AGENT_MODEL", "deepseek-chat")
            cfg = {"model": model, "model_server": base, "model_type": "oai", "api_key": key,
                   "generate_cfg": {"max_input_tokens": 6000, "max_retries": 2}}
        else:
            base = os.environ.get("PAI_QWEN_AGENT_BASE", "http://127.0.0.1:11434/v1")
            model = os.environ.get("PAI_QWEN_AGENT_MODEL", os.environ.get("PAI_MODEL_NAME", "qwen3-4b-instruct:latest"))
            cfg = {"model": model, "model_server": base, "model_type": "oai",
                   "generate_cfg": {"max_input_tokens": 6000, "max_retries": 2}}
        function_list = [RecallTool(self.mem)]
        try:
            from . import mcp_client as MCPC
            for server in MCPC.servers()[:8]:
                sname = str(server.get("name") or "")
                if not sname:
                    continue
                try:
                    tools = MCPC.list_tools(sname)
                except Exception:
                    continue
                for mt in (tools or [])[:20]:
                    tname = str(mt.get("name") or "")
                    if not tname:
                        continue
                    schema = mt.get("inputSchema") or {"type": "object", "properties": {}}
                    props = schema.get("properties") if isinstance(schema, dict) else {}
                    required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
                    plist = []
                    for pname, pmeta in (props or {}).items():
                        pmeta = pmeta if isinstance(pmeta, dict) else {}
                        plist.append({"name": str(pname), "type": pmeta.get("type", "string"),
                                      "description": str(pmeta.get("description") or ""),
                                      "required": pname in required})
                    safe = "mcp__%s__%s" % (re.sub(r"[^A-Za-z0-9_]", "_", sname),
                                            re.sub(r"[^A-Za-z0-9_]", "_", tname))

                    class MCPTool(BaseTool):
                        name = safe
                        description = str(mt.get("description") or ("MCP %s/%s" % (sname, tname)))
                        parameters = plist

                        def __init__(self):
                            super().__init__({})

                        def call(self, params, _s=sname, _t=tname, **kwargs):
                            import json
                            args = json.loads(params) if isinstance(params, str) else (params or {})
                            # MCP 调用必须人工确认；Qwen-Agent 只拿到待批准结果。
                            return json.dumps(MCPC.call(_s, _t, args, confirm=False),
                                              ensure_ascii=False)

                    function_list.append(MCPTool())
        except Exception:
            pass
        bot = Assistant(function_list=function_list, llm=cfg,
                        system_message=self._qwen_agent_system(str(text or "")))
        last = None
        seen = ""
        for chunk in bot.run([{"role": "user", "content": str(text or "")}]):
            last = chunk
            if on_delta:
                try:
                    cur = chunk[-1].get("content") if isinstance(chunk, list) else chunk.get("content")
                    if isinstance(cur, list):
                        cur = "".join(str(x.get("text") or x) if isinstance(x, dict) else str(x) for x in cur)
                    cur = str(cur or "")
                    if cur.startswith(seen):
                        delta = cur[len(seen):]
                    else:
                        delta = cur
                    seen = cur
                    if delta:
                        on_delta(delta)
                except Exception:
                    pass
        if not last:
            raise RuntimeError("Qwen-Agent 没有返回")
        content = last[-1].get("content") if isinstance(last, list) else last.get("content")
        if isinstance(content, list):
            content = "".join(str(x.get("text") or x) if isinstance(x, dict) else str(x) for x in content)
        return str(content or "").strip()

    def office_tasks(self):
        return dict(OFFICE_TASKS)

    def office_extract(self, name, data):
        import pathlib
        import tempfile
        from . import asr
        from . import file_extract as FE
        ext = pathlib.Path(str(name or "")).suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            from . import image_ocr, vision
            if vision.configured():
                try:
                    return vision.describe(data, "image/%s" % ext.lstrip("."),
                                           "提取图片中的文字，并简述画面内容。"), "image"
                except Exception:
                    pass
            return image_ocr.extract(data, ext), "image"
        if ext in (".wav", ".mp3", ".m4a", ".flac", ".ogg"):
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                f.write(data)
                path = f.name
            try:
                return asr.transcribe(path), "audio"
            finally:
                try:
                    pathlib.Path(path).unlink()
                except OSError:
                    pass
        return FE.extract(name, data), "document"

    def _office_complete(self, prompt):
        out = M10.complete(prompt, opts={"temperature": 0.2, "num_predict": 1800})
        if not out:
            raise RuntimeError("模型没有返回")
        return str(out[0].get("output") or "").strip()

    def office_run(self, task, text, title=""):
        task = str(task or "minutes")
        if task not in OFFICE_TASKS:
            raise ValueError("未知任务：%s" % task)
        body = str(text or "").strip()
        if not body:
            raise ValueError("没有可处理的文字")
        parts = _office_split(body)
        if len(parts) > 1:
            notes = [self._office_complete(
                "只提取片段中的事实/结论/行动/人名/数字/时间，不要成稿：\n" + p)
                for p in parts]
            source = "以下是长文分段提取，请合并去重后成稿：\n" + "\n\n".join(
                "【片段%d】\n%s" % (i, n) for i, n in enumerate(notes, 1))
        else:
            source = (("标题：%s\n\n" % title) if title else "") + body
        content = self._office_complete(OFFICE_TASKS[task] + "\n\n" + source)
        if not content:
            raise RuntimeError("模型输出为空")
        return {"task": task, "title": title, "content": content,
                "chars": len(body), "chunks": len(parts)}

    def office_batch(self, items, task="minutes", title=""):
        """批量办公：逐文件解析→生成；单个失败不伪装成功。"""
        results = []
        for i, item in enumerate(items or [], 1):
            name = str(item.get("name") or ("file-%d" % i))
            try:
                text, kind = self.office_extract(name, item.get("data") or b"")
                out = self.office_run(task, text, title or name)
                results.append({"ok": True, "name": name, "kind": kind, **out})
            except Exception as e:
                results.append({"ok": False, "name": name, "error": "%s: %s" % (type(e).__name__, e)})
        return {"ok": all(x.get("ok") for x in results), "count": len(results), "items": results}

    def office_batch_texts(self, items, task="minutes", title=""):
        results = []
        for i, item in enumerate(items or [], 1):
            name = str(item.get("name") or ("file-%d" % i))
            try:
                out = self.office_run(task, item.get("text") or "", title or name)
                results.append({"ok": True, "name": name, **out})
            except Exception as e:
                results.append({"ok": False, "name": name, "error": "%s: %s" % (type(e).__name__, e)})
        return {"ok": all(x.get("ok") for x in results), "count": len(results), "items": results}

    def _office_safe(self, path):
        import pathlib
        from pai import paths
        p = pathlib.Path(str(path)).expanduser().resolve()
        roots = [paths.WORKSPACE.resolve(), paths.OFFICE_OUT.resolve(), paths.DATA_DIR.resolve()]
        if not any(p == r or r in p.parents for r in roots):
            raise ValueError("路径不在允许的办公根目录")
        return p

    def office_excel_convert(self, src):
        import csv, datetime
        from openpyxl import load_workbook
        from pai import exec_guard as EG
        from pai import paths
        p = self._office_safe(src)
        wb = load_workbook(p, read_only=True, data_only=True)
        out = []
        for ws in wb.worksheets:
            name = paths.OFFICE_OUT / ("%s-%s-%s.csv" % (
                p.stem, re.sub(r"[^\w\u4e00-\u9fff-]+", "_", ws.title)[:30],
                datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
            with name.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                for row in ws.iter_rows(values_only=True):
                    w.writerow(["" if x is None else x for x in row])
            out.append(str(name))
        EG.gate("excel_convert", str(p), detail=",".join(out))
        return {"ok": True, "files": out}

    def office_excel_clean(self, src):
        import datetime
        from openpyxl import load_workbook, Workbook
        from pai import exec_guard as EG
        from pai import paths
        p = self._office_safe(src)
        wb = load_workbook(p, data_only=True)
        out = Workbook(); out.remove(out.active)
        for ws in wb.worksheets:
            rows, seen = [], set()
            for row in ws.iter_rows(values_only=True):
                vals = tuple("" if x is None else x for x in row)
                if vals in seen:
                    continue
                seen.add(vals); rows.append(vals)
            dst = out.create_sheet(ws.title[:31])
            for row in rows:
                dst.append(row)
        name = paths.OFFICE_OUT / ("%s-clean-%s.xlsx" % (p.stem, datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
        out.save(name)
        EG.gate("excel_clean", str(p), detail=str(name))
        return {"ok": True, "file": str(name)}

    def office_batch_rename(self, folder, old, new):
        import datetime, json
        from pai import exec_guard as EG
        base = self._office_safe(folder)
        if not base.is_dir():
            raise ValueError("目录不存在")
        old, new = str(old), str(new)
        if not old:
            raise ValueError("旧文本不能为空")
        moves = []
        for p in sorted(base.iterdir()):
            if p.is_file() and old in p.name:
                target = p.with_name(p.name.replace(old, new))
                if target.exists():
                    continue
                p.rename(target); moves.append({"old": str(p), "new": str(target)})
        manifest = self._office_safe(__import__("pai.paths", fromlist=["OFFICE_OUT"]).OFFICE_OUT /
                                     ("rename-%s.json" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
        manifest.write_text(json.dumps({"moves": moves}, ensure_ascii=False, indent=2), encoding="utf-8")
        EG.gate("rename", str(base), detail="%d files" % len(moves))
        return {"ok": True, "count": len(moves), "manifest": str(manifest), "moves": moves}

    def office_chart(self, src, column=""):
        import datetime
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
        plt.rcParams["axes.unicode_minus"] = False
        import pandas as pd
        from pai import exec_guard as EG
        from pai import paths
        p = self._office_safe(src)
        df = pd.read_excel(p) if p.suffix.lower() in (".xlsx", ".xlsm") else pd.read_csv(p)
        if not column:
            nums = df.select_dtypes(include="number").columns
            if not len(nums):
                raise ValueError("没有可绘制的数值列")
            column = str(nums[0])
        if column not in df.columns:
            raise ValueError("没有这一列：%s" % column)
        fig, ax = plt.subplots(figsize=(8, 4.5))
        if len(df) <= 20:
            df[column].plot(kind="bar", ax=ax)
        else:
            df[column].plot(kind="line", ax=ax)
        ax.set_title(str(column)); ax.set_xlabel("行"); ax.set_ylabel(str(column))
        name = paths.OFFICE_OUT / ("%s-%s-%s.png" % (p.stem, re.sub(r"[^\w\u4e00-\u9fff-]+", "_", column)[:20],
                                                       datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
        fig.tight_layout(); fig.savefig(name); plt.close(fig)
        EG.gate("chart", str(p), detail=str(name))
        return {"ok": True, "file": str(name)}

    def office_rollback(self, manifest):
        import json
        import pathlib
        from pai import exec_guard as EG
        p = self._office_safe(manifest)
        d = json.loads(p.read_text(encoding="utf-8"))
        done = []
        for m in reversed(d.get("moves") or []):
            old, new = pathlib.Path(m["old"]), pathlib.Path(m["new"])
            if new.exists() and not old.exists():
                new.rename(old); done.append(str(old))
        EG.gate("overwrite", str(p), approved=True, detail="rollback %d files" % len(done))
        return {"ok": True, "restored": done}

    def office_backup(self, path):
        import datetime, shutil
        from pai import exec_guard as EG
        from pai import paths
        p = self._office_safe(path)
        if not p.exists():
            raise ValueError("路径不存在：%s" % path)
        name = paths.OFFICE_OUT / ("backup-%s-%s" % (p.stem, datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
        if p.is_dir():
            out = shutil.make_archive(str(name), "zip", root_dir=str(p))
        else:
            import zipfile
            out = str(name) + ".zip"
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(p, arcname=p.name)
        EG.gate("backup", str(p), detail=out)
        return {"ok": True, "file": out, "source": str(p)}

    def office_report(self, src, period="week"):
        import datetime
        from pai import exec_guard as EG
        from pai import paths
        import pandas as pd
        p = self._office_safe(src)
        df = pd.read_excel(p) if p.suffix.lower() in (".xlsx", ".xlsm") else pd.read_csv(p)
        desc = df.describe(include="all").to_string()
        md = "# 报表\n\n- 来源：%s\n- 生成时间：%s\n- 行数：%d\n\n```\n%s\n```\n" % (
            p.name, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), len(df), desc)
        md_path = paths.OFFICE_OUT / ("report-%s-%s.md" % (p.stem, datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
        md_path.write_text(md, encoding="utf-8")
        xls_path = md_path.with_suffix(".xlsx")
        with pd.ExcelWriter(xls_path) as w:
            df.to_excel(w, sheet_name="明细", index=False)
            df.describe(include="all").to_excel(w, sheet_name="汇总")
        EG.gate("report", str(p), detail="%s,%s" % (md_path, xls_path))
        return {"ok": True, "markdown": str(md_path), "excel": str(xls_path)}

    def office_chart_analysis(self, src, channel, value):
        import datetime
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import pandas as pd
        from pai import exec_guard as EG
        from pai import paths
        p = self._office_safe(src)
        df = pd.read_excel(p) if p.suffix.lower() in (".xlsx", ".xlsm") else pd.read_csv(p)
        if channel not in df.columns or value not in df.columns:
            raise ValueError("列不存在：%s / %s" % (channel, value))
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
        plt.rcParams["axes.unicode_minus"] = False
        fig, ax = plt.subplots(figsize=(9, 4.8))
        df.boxplot(column=value, by=channel, ax=ax, rot=30)
        ax.set_title("按 %s 的 %s 分布" % (channel, value))
        fig.suptitle("")
        name = paths.OFFICE_OUT / ("analysis-%s-%s.png" % (p.stem, datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
        fig.tight_layout(); fig.savefig(name); plt.close(fig)
        EG.gate("chart", str(p), detail=str(name))
        return {"ok": True, "file": str(name)}

    def office_compare(self, a, b):
        import difflib
        from pai import file_extract as FE, exec_guard as EG
        pa, pb = self._office_safe(a), self._office_safe(b)
        ta = FE.extract(pa.name, pa.read_bytes())
        tb = FE.extract(pb.name, pb.read_bytes())
        diff = list(difflib.unified_diff(ta.splitlines(), tb.splitlines(),
                                         fromfile=pa.name, tofile=pb.name, lineterm=""))
        EG.gate("compare", "%s|%s" % (pa, pb), detail="%d hunks" % len(diff))
        out = "\n".join(diff[:400])
        return {"ok": True, "from": str(pa), "to": str(pb), "changed": bool(diff),
                "hunks": len([x for x in diff if x.startswith("@@")]),
                "diff": out[:20000] or "两份内容一致"}

    def office_template(self, kind, title=""):
        import datetime
        from pai import exec_guard as EG
        from pai import paths
        kind = str(kind or "周报")
        title = str(title or kind)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        if kind in ("发票", "invoice"):
            import openpyxl
            wb = openpyxl.Workbook(); ws = wb.active; ws.title = "发票"
            ws.append(["项目", "数量", "单价", "金额", "税率", "税额"])
            for i in range(2, 9):
                ws.append(["", "", "", "=B{0}*C{0}".format(i), 0.06, "=D{0}*E{0}".format(i)])
            out = paths.OFFICE_OUT / ("template-invoice-%s.xlsx" % stamp)
            wb.save(out)
        elif kind in ("OKR", "okr"):
            import openpyxl
            wb = openpyxl.Workbook(); ws = wb.active; ws.title = "OKR"
            ws.append(["目标", "关键结果", "目标值", "当前值", "进度"])
            for i in range(2, 8):
                ws.append(["", "", "", "", "=IFERROR(D{0}/C{0},0)".format(i)])
            out = paths.OFFICE_OUT / ("template-okr-%s.xlsx" % stamp)
            wb.save(out)
        elif kind in ("报价单", "quote"):
            import openpyxl
            wb = openpyxl.Workbook(); ws = wb.active; ws.title = "报价单"
            ws.append(["项目", "数量", "单价", "金额", "备注"])
            for i in range(2, 10):
                ws.append(["", "", "", "=B{0}*C{0}".format(i), ""])
            out = paths.OFFICE_OUT / ("template-quote-%s.xlsx" % stamp)
            wb.save(out)
        elif kind in ("预算", "budget"):
            import openpyxl
            wb = openpyxl.Workbook(); ws = wb.active; ws.title = "预算"
            ws.append(["项目", "预算", "实际", "差异", "备注"])
            for _ in range(8):
                ws.append(["", "", "", "=B{0}-C{0}".format(ws.max_row + 1), ""])
            out = paths.OFFICE_OUT / ("template-budget-%s.xlsx" % stamp)
            wb.save(out)
        else:
            import docx
            d = docx.Document()
            d.add_heading(title, level=1)
            if kind in ("合同", "contract"):
                for h in ("甲方", "乙方", "标的", "金额", "期限", "违约责任", "签署"):
                    d.add_heading(h, level=2)
                    d.add_paragraph("")
            elif kind in ("复盘", "retro"):
                for h in ("目标", "结果", "差距", "原因", "改进"):
                    d.add_heading(h, level=2)
                    d.add_paragraph("")
            elif kind in ("项目计划", "plan"):
                for h in ("目标", "范围", "里程碑", "负责人", "风险"):
                    d.add_heading(h, level=2)
                    d.add_paragraph("")
            elif kind in ("会议", "meeting"):
                for h in ("时间", "参会人", "议题", "结论", "待办"):
                    d.add_heading(h, level=2)
                    d.add_paragraph("")
            else:
                for h in ("本周目标", "进展", "问题", "下周计划"):
                    d.add_heading(h, level=2)
                    d.add_paragraph("")
            suffix = ("meeting" if kind in ("会议", "meeting") else
                      "contract" if kind in ("合同", "contract") else
                      "retro" if kind in ("复盘", "retro") else
                      "plan" if kind in ("项目计划", "plan") else "week")
            out = paths.OFFICE_OUT / ("template-%s-%s.docx" % (suffix, stamp))
            d.save(out)
        EG.gate("template", str(out), detail=kind)
        return {"ok": True, "file": str(out), "kind": kind}

    def office_excel_formula(self, src, column, func="SUM"):
        import datetime
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter
        from pai import exec_guard as EG
        from pai import paths
        p = self._office_safe(src)
        wb = load_workbook(p)
        ws = wb.active
        header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
        try:
            idx = [str(x) if x is not None else "" for x in header].index(str(column)) + 1
        except ValueError:
            raise ValueError("没有这一列：%s" % column)
        last = ws.max_row
        ws.cell(row=last + 1, column=1, value="合计")
        func = str(func or "SUM").upper()
        if func not in ("SUM", "AVERAGE", "COUNT", "MAX", "MIN"):
            func = "SUM"
        ws.cell(row=last + 1, column=idx,
                value="=%s(%s2:%s%d)" % (func, get_column_letter(idx), get_column_letter(idx), last))
        out = paths.OFFICE_OUT / ("%s-formula-%s.xlsx" % (p.stem, datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
        wb.save(out)
        EG.gate("formula", str(p), detail=str(out))
        return {"ok": True, "file": str(out), "column": column}

    def office_export(self, fmt, content):
        import io
        fmt = str(fmt or "md").lower()
        if fmt == "docx":
            import docx
            d = docx.Document()
            for line in str(content or "").splitlines():
                if line.startswith("## "):
                    d.add_heading(line[3:], level=2)
                elif line.startswith("# "):
                    d.add_heading(line[2:], level=1)
                elif line.startswith("- "):
                    d.add_paragraph(line[2:], style="List Bullet")
                else:
                    d.add_paragraph(line)
            b = io.BytesIO(); d.save(b); return b.getvalue()
        if fmt == "xlsx":
            import openpyxl
            wb = openpyxl.Workbook(); ws = wb.active; ws.title = "输出"
            for line in str(content or "").splitlines():
                line = line.strip()
                if not line or set(line) <= set("-|: "):
                    continue
                ws.append([x.strip() for x in line.strip("|").split("|")] if line.startswith("|") else [line])
            b = io.BytesIO(); wb.save(b); return b.getvalue()
        if fmt == "pptx":
            from pptx import Presentation
            from pptx.util import Inches
            prs = Presentation()
            slides, current = [], None
            for line in str(content or "").splitlines():
                line = line.strip()
                if line.startswith("## "):
                    current = {"title": line[3:].strip(), "bullets": []}
                    slides.append(current)
                elif current is not None and line and not line.startswith("|"):
                    current["bullets"].append(line.lstrip("-* ").strip())
            if not slides:
                slides = [{"title": "知衡输出", "bullets": [str(content or "")[:300]]}]
            for item in slides:
                slide = prs.slides.add_slide(prs.slide_layouts[1])
                slide.shapes.title.text = item["title"][:80] or "知衡"
                tf = slide.placeholders[1].text_frame
                tf.text = (item["bullets"] or [""])[0][:120]
                for b in item["bullets"][1:8]:
                    p = tf.add_paragraph(); p.text = b[:120]
            b = io.BytesIO(); prs.save(b); return b.getvalue()
        return str(content or "").encode("utf-8")

    def handle(self, text, channel="cli", council=None, on_delta=None, extra_rule=None,
               ephemeral=False, model=None, on_reasoning=None):
        if (os.environ.get("PAI_AGENT_ENGINE") or "").strip().lower() == "qwen_agent" and not ephemeral:
            try:
                msg = self.qwen_agent(text, on_delta=on_delta)
                return {"response": msg, "trace": {"engine": "qwen_agent", "recalled": [],
                        "is_recall": False, "is_reason": False, "models": [], "cost": 0.0,
                        "tokens": 0}}
            except Exception as e:
                return {"response": "Qwen-Agent 失败：%s" % e,
                        "trace": {"engine": "qwen_agent", "error": str(e)}}

        if _looks_identity(text):
            msg = "我是月白，运行在知衡里。知衡是这个 app，月白是我。"
            return {"response": msg, "trace": {
                "recalled": [], "culture_refs": [], "reason": msg,
                "is_recall": False, "is_reason": False, "principles_used": [],
                "reflection": {}, "models": [], "cost": 0.0, "tokens": 0,
                "affect": None, "identity_guard": True}}

        if _looks_secret(text):
            msg = ("【已拦截 · 未记录、未联网】这句话里疑似含 API 密钥/凭证。"
                   "为安全计，我不会把它写进记忆、也不会调用模型，也不会复述它。"
                   "密钥只应在 python chat.py --live 的 OPENAI_API_KEY> 提示处盲输，"
                   "或用环境变量——别贴进对话。")
            return {"response": msg, "trace": {
                "blocked_secret": True, "recalled": [], "culture_refs": [],
                "reason": msg, "is_recall": False, "is_reason": False,
                "principles_used": [], "reflection": {}, "models": [],
                "cost": 0.0, "tokens": 0, "affect": None}}

        # 能力路由：明确说“查书/查记忆/时间账本/今日简报”时直接执行，不走模型。
        cap = CR.dispatch(text, mem=self.mem)
        if cap is not None:
            return cap

        # 游戏代玩/系统提示不是聊天记忆：不写库、不召回、不套“我记得…”外壳。
        game_mode = _looks_game_prompt(text)
        if game_mode:
            ephemeral = True
        _t0 = time.time()
        ep_id = None if ephemeral else self.mem.write_episode("user", text, self.session_id)

        # M5：先更新心境（只写 affective_state 一行），再组 prompt——语气依赖它。
        # ephemeral（例如游戏代玩）不改心境、不写记忆。
        affect_state = M5.baseline() if ephemeral else M5.update_affect(self.mem, text)

        # M11/M2：确保身份记录存在（幂等；身份不随一句话改变）。
        if not ephemeral:
            SM.ensure_identity(self.mem)

        # Phase2：M4 反思引擎取代 Phase1 的 _naive_extract——
        # 蒸馏 fact + 合成 principle，让原则在【本轮】就能进入 grounded 上下文。
        reflection = {} if ephemeral else M4.reflect(self.mem)

        if ephemeral:
            recalled, facts, principles, culture = [], [], [], []
            reminders = []
        else:
            recalled = self.mem.recall(text, k=5)
            facts = self.mem.all_facts()
            principles = self.mem.get_principles()
            try:
                reminders = self.mem.list_triggers(active_only=True)
            except Exception:
                reminders = []
            # M9：文化检索只吃【用户原话】，绝不吃系统自写的 principles——否则检索会被
            # 自己的立场带偏，形成“自我确认回路”（越用越偏，比学错更难发现）。见 ADR-018。
            # principles 仍照常进 prompt（下方 _build_prompt），两条通道分开。
            culture = M9.retrieve_culture(text)

        ws_trace = None
        if not ephemeral and WS.backend() == "on":
            recalled, facts, principles, culture, ws_trace = _workspace_trim(
                recalled, facts, principles, culture, text)

        # Phase4：先定 council，再决定要不要教工具协议（council 路径不教，免得露 TOOL_CALL）
        council_models = self._resolve_models(council)
        brain = None
        memory_trace = None
        memory_ctx = None
        if not ephemeral and MB.enabled():
            try:
                brain = MB.MemoryBrain(self.mem)
                nb = brain.notebook(self.session_id)
                br = brain.recall(text, k=3)
                parts = []
                if nb:
                    parts.append("[记事本] " + json.dumps(nb, ensure_ascii=False))
                if br.get("context"):
                    parts.append(br["context"])
                memory_ctx = "\n".join(parts) or None
                memory_trace = {"notebook": nb, "found": br.get("found"),
                                "items": br.get("items"), "conflicts": br.get("conflicts")}
            except Exception as e:  # noqa: BLE001
                memory_trace = {"error": "%s: %s" % (type(e).__name__, e)}
        wiki_ctx = None
        wiki_trace = None
        poetry_ctx = None
        poetry_trace = None
        poems = []
        if not ephemeral and PK.enabled():
            try:
                poems = PK.retrieve_poetry(text, k=3)
                poetry_ctx = PK.context(poems) or None
                poetry_trace = {"items": [{"ref": x["ref"], "source": x["source"]}
                                          for x in poems], "count": PK.count()}
            except Exception as e:  # noqa: BLE001
                poetry_trace = {"error": "%s: %s" % (type(e).__name__, e)}
        if not ephemeral and WK.enabled():
            try:
                wk = WK.Wiki()
                pages = wk.search(text, limit=2)
                wiki_ctx = wk.context(text, limit=2) or None
                wiki_trace = {"pages": [{"slug": p.get("slug"), "title": p.get("title"),
                                          "updated": p.get("updated")} for p in pages]}
            except Exception as e:  # noqa: BLE001
                wiki_trace = {"error": "%s: %s" % (type(e).__name__, e)}
        router_ctx = None
        router_trace = None
        if not ephemeral and RT.enabled():
            try:
                code_db = os.path.join(os.path.dirname(self.mem.db_path), "code_graph.db")
                rt = RT.Router(self.mem, code_db=code_db)
                routed = rt.route(text, k=3)
                router_ctx = routed.get("context") or None
                router_trace = {"plan": routed.get("plan"), "sources": routed.get("sources")}
                if router_ctx:
                    memory_ctx = None
                    wiki_ctx = None
            except Exception as e:  # noqa: BLE001
                router_trace = {"error": "%s: %s" % (type(e).__name__, e)}
        try:
            lb = "" if ephemeral else ML.prompt_block()
            if lb:
                memory_ctx = (memory_ctx + "\n\n" if memory_ctx else "") + lb
        except Exception:
            pass
        prompt = _build_prompt(text, recalled, facts, principles, culture, affect_state,
                               SM.prompt_block(self.mem), extra_rule=extra_rule,
                               memory_ctx=memory_ctx, wiki_ctx=wiki_ctx,
                               router_ctx=router_ctx, poetry_ctx=poetry_ctx,
                               reminders=reminders)
        if os.environ.get("PAI_DEBUG_PROFILE") == "1":
            print("PROFILE prompt %.2fs len=%d" % (time.time() - _t0, len(prompt)),
                  file=sys.stderr, flush=True)
        if TL.enabled() and council_models is None:
            prompt = prompt + "\n\n" + TL.instruction()

        code_routed = False
        if (model is None and council_models is None
                and os.environ.get("PAI_CODE_ROUTE", "1").strip().lower() not in ("0", "off", "false", "no")
                and CRO.is_code(text)):
            model = CRO.spec()
            code_routed = True
        cascade_observe = None
        if council_models is None:
            try:
                if model == "local_cascade" or (CASCADE.enabled() and model is None and CASCADE.offline_mode()):
                    _dt = time.time()
                    outs = [AIP.local_cascade(prompt, on_delta=on_delta, user_text=text)]
                    cascade_observe = {
                        "route": outs[0].get("route"),
                        "route_ok": outs[0].get("route_ok"),
                        "degraded": outs[0].get("degraded"),
                        "chosen": outs[0].get("model"),
                        "chosen_model": outs[0].get("chosen_model"),
                        "prompt_mode": outs[0].get("prompt_mode"),
                        "trace_steps": outs[0].get("trace"),
                        "tokens": outs[0].get("tokens"),
                        "metrics": outs[0].get("metrics"),
                    }
                    if os.environ.get("PAI_DEBUG_PROFILE") == "1":
                        print("PROFILE cascade %.2fs total=%.2fs" %
                              (time.time() - _dt, time.time() - _t0),
                              file=sys.stderr, flush=True)
                elif model in ("local_duo", "local_smart"):
                    _dt = time.time()
                    outs = [AIP.local_duo(prompt, on_delta=on_delta, user_text=text,
                                         smart=(model == "local_smart"))]
                    if os.environ.get("PAI_DEBUG_PROFILE") == "1":
                        print("PROFILE duo %.2fs total=%.2fs" %
                              (time.time() - _dt, time.time() - _t0),
                              file=sys.stderr, flush=True)
                else:
                    outs = _complete(prompt, models=(model,) if model else None,
                                     on_delta=on_delta, on_reasoning=on_reasoning)
            except M10.ModelGatewayError as e:
                if LM.enabled():
                    try:
                        txt = LM.complete(prompt)
                        outs = [{"model_id": "local:" + LM.model_name(),
                                 "model": LM.model_name(), "output": txt,
                                 "cost": 0.0, "tokens": 0, "priced": True,
                                 "spend_report": "[报账] 本地模型，无云端费用"}]
                    except Exception as le:
                        prov = M10.active_provider()
                        outs = [{"model_id": prov,
                                 "output": f"[模型调用失败] {e}；本地兜底也失败：{le}",
                                 "cost": 0.0, "tokens": 0, "priced": False}]
                else:
                    prov = M10.active_provider()
                    outs = [{"model_id": prov, "output": f"[模型调用失败] {e}",
                             "cost": 0.0, "tokens": 0, "priced": False}]
            proposals = [self._as_proposal(o) for o in outs]
            council_failures, divergence = [], None
            model_out = proposals[0]["output"]
            if cascade_observe is not None:
                model_note = "(%s)" % (cascade_observe.get("chosen") or "知衡离线分级")
            else:
                model_note = ("(mock占位·未接真实模型)" if model_out.startswith("[mock]")
                              else "(%s)" % proposals[0].get("model"))
        else:
            proposals, council_failures = [], []
            for m in council_models:
                try:
                    proposals.append(self._as_proposal(_complete(prompt, models=(m,))[0]))
                except M10.ModelGatewayError as e:
                    council_failures.append({"model": m, "error": str(e)})
            divergence = _divergence(proposals)
            if not proposals:
                model_out = "[议会失败] 全部模型调用失败，本轮没有可用视角。"
                model_note = "(council:全部模型调用失败)"
            else:
                model_out = proposals[0]["output"]
                model_note = "(council:" + "/".join(p["model"] for p in proposals) + ")"

        # M8 第二批：模型可以先调一次只读工具，再基于结果回答。
        tool_use = {"used": False, "tool": None, "calls": 0,
                    "cost": 0.0, "tokens": 0, "priced": False, "reason": ""}
        if council_models is None and TL.enabled():
            call = TL.parse(model_out)
            if call is not None:
                if M8.TOOLS[call["tool"]]["permission"] != "read":
                    model_out = "这个工具不可用。"
                else:
                    res = TL.run_tool(self.mem, call)
                    if res.get("ok"):
                        priced = False
                        try:
                            second = _complete(TL.followup(prompt, call, res))[0]
                            model_out = second["output"]
                            priced = bool(second.get("priced"))
                            tool_use.update(used=True, tool=call["tool"], calls=1,
                                            cost=float(second.get("cost") or 0),
                                            tokens=int(second.get("tokens") or 0),
                                            priced=priced)
                        except Exception as e:  # noqa: BLE001  空列表/断流/缺 output 都回原回答
                            model_out = TL.strip_call(model_out) or "（这次没查成，换个说法再问一次。）"
                            tool_use.update(tool=call["tool"], priced=False,
                                            reason="工具结果二次调用失败：%s" % type(e).__name__)
                    else:
                        model_out = TL.strip_call(model_out) or "（这次没查成，换个说法再问一次。）"
                        tool_use.update(tool=call["tool"], reason=res.get("reason") or "工具失败")
            elif "TOOL_CALL" in (model_out or ""):
                model_out = TL.strip_call(model_out)

        # SC（L1）：默认只统计、不改写；PAI_SELF_REVIEW=1 才允许拒绝/修订。
        review = self._self_review(model_out, culture, principles, poetry=poems)
        model_out = review["answer"]

        if game_mode or (os.environ.get("PAI_HIDE_RECALL") or "").strip().lower() == "on":
            response = model_out
        else:
            response = _compose_response(recalled, model_out, model_note)
        response += self._council_tail(council_models, proposals, council_failures, divergence)
        # M5：输出侧只删“假安慰”小句（重情绪时），不改信息、不加内容。
        if not ephemeral:
            response = M5.modulate(response, affect_state)
            self.mem.write_episode("ai", response, self.session_id)
            ACT.record_chat(self.session_id, ep_id, channel=channel,
                            facts=len(facts), principles=len(principles),
                            model=(proposals[0]["model"] if proposals else ""))

        trace = {
            "recalled": recalled,          # 铁律6：这是"查到的"
            "culture_refs": culture,       # 铁律6：这也是检索所得(非生成)，单列以便标出处
            "reason": model_out,           # 铁律6：这是"模型分析的"
            "is_recall": bool(recalled),
            "is_reason": True,
            "game_mode": game_mode,
            "principles_used": [p["text"] for p in principles],  # 本轮恪守的自写原则
            "reflection": reflection,      # 本轮反思增量（蒸馏了几条 fact/新增几条原则）
            "models": [p["model"] for p in proposals],
            "code_route": ({"model": model} if code_routed else None),
            "proposals": proposals,        # Phase4：每份视角都进 trace（不综合）
            "divergence": divergence,      # Phase4：分歧度是提示，不是判决
            "council_failures": council_failures,  # Phase4：降级必须可见
            "cost": sum(p["cost"] for p in proposals) + tool_use["cost"],
            "tokens": sum(p["tokens"] for p in proposals) + tool_use["tokens"],
            # M5：心境是【状态估计】，既不是 recall 也不是 reason，单独放。
            "affect": {"mood": affect_state["mood"],
                       "intensity": affect_state["intensity"],
                       "heavy": affect_state["heavy"]},
            # M11/M2：身份版本是可审计的连续量；原则条数让“我变了没有”有据可查。
            "self": {"version": SM.version(self.mem),
                     "principles_active": len(principles)},
            # SC：决策/问题/是否真的改了/第二次调用的成本——全部可见。
            "self_review": {"decision": review["decision"],
                            "issues": review["issues"],
                            "rounds": review["rounds"],
                            "applied": review["applied"],
                            "refused": review["refused"],
                            "calls": review["meter"]["calls"],
                            "cost": round(review["meter"]["cost"], 6),
                            "tokens": review["meter"]["tokens"]},
            "tool_use": tool_use,
        }
        if router_trace is not None:
            trace["router"] = router_trace
        if wiki_trace is not None:
            trace["wiki"] = wiki_trace
        if poetry_trace is not None:
            trace["poetry"] = poetry_trace
        if memory_trace is not None:
            trace["memory"] = memory_trace
            if brain is not None:
                try:
                    brain.update_notebook(self.session_id, last_user=text[:200],
                                          last_answer=response[:300])
                except Exception:
                    pass
        if ws_trace is not None:
            trace["workspace"] = ws_trace
        if cascade_observe is not None:
            trace["cascade"] = cascade_observe
        return {"response": response, "trace": trace}

    @staticmethod
    def _as_proposal(o):
        return {"model": o["model_id"], "output": o["output"],
                "cost": o.get("cost", 0.0), "tokens": o.get("tokens", 0),
                "priced": o.get("priced", False)}

    @staticmethod
    def _self_review(model_out, culture, principles, poetry=None):
        """L1：批判，并按开关决定是否改变输出。默认 apply=False，一个字都不改。"""
        refs = [c.get("ref", "") for c in (culture or [])]
        refs += [p.get("ref", "") for p in (poetry or [])]
        ctx = {"allowed_refs": refs,
               "principles": [p.get("text", "") for p in (principles or [])]}
        enabled = os.environ.get(SC.ENV_SELF_REVIEW) == "1"
        meter = {"calls": 0, "cost": 0.0, "tokens": 0, "models": []}
        out = SC.review(model_out, ctx,
                        reviser=Orchestrator._make_reviser(meter) if enabled else None,
                        apply=enabled)
        out["meter"] = meter
        return out

    @staticmethod
    def _make_reviser(meter):
        """第二次调用的成本记进 meter，再由 trace 的 self_review 字段报出去。"""
        def _reviser(draft, issues):
            note = "\n".join("- %s" % i.get("detail", "") for i in issues)
            prompt = ("[修订任务] 只重写下面这句话：保留信息与语气，去掉被指出的问题，"
                      "不要解释、不要新增内容。\n[问题]\n%s\n[原句]\n%s" % (note, draft))
            o = _complete(prompt)[0]
            meter["calls"] += 1
            meter["cost"] += float(o.get("cost") or 0)
            meter["tokens"] += int(o.get("tokens") or 0)
            meter["models"].append(o.get("model_id"))
            return o["output"]
        return _reviser

    @staticmethod
    def _resolve_models(council):
        """返回 council 用的 provider 列表；None=走单模型默认路径。
        显式 council 参数优先；否则读 PAI_COUNCIL_MODELS，且【只有 >=2 个】才算议会
        （只配一个 = 没议会，不该白跑一次）。"""
        if council:
            ms = [m for m in council if m]
            return ms if len(ms) >= 2 else ([ms[0]] if ms else None)
        env = (os.environ.get("PAI_COUNCIL_MODELS") or "").strip()
        if env:
            ms = [x.strip() for x in env.split(",") if x.strip()]
            if len(ms) >= 2:
                return ms
        return None

    @staticmethod
    def _council_tail(council_models, proposals, council_failures, divergence):
        """议会模式的可见性尾巴：展示另一视角、明说未综合、降级要说出来。"""
        if council_models is None:
            return ""
        if len(proposals) >= 2:
            other = proposals[1]
            tail = (f"\n\n[另一视角 · {other['model']}（未综合，判断权在你）]\n{other['output']}")
            if divergence and divergence["flagged"]:
                tail += f"\n（两份视角分歧较大：相似度 {divergence['score']}，请自行取舍——这不是谁对谁错的判决。）"
            return tail
        if council_failures:
            failed = "/".join(f["model"] for f in council_failures)
            return f"\n\n（注意：另一视角没有回应（{failed}），本轮只剩一份视角，属于降级，别让'少一个视角'被当成'共识'。）"
        return "\n\n（议会里全部模型调用失败，本轮没有任何可用视角，判断权仍在等你换个模型再问。）"

    # ---- Phase3：主动发起（关掉软件它也会找你）----
    def proactive(self, trigger, now=None):
        """由 M6 挑出的到期触发器 → grounded 组织一条主动消息 → 写进 outbox。
        grounded：带上它的称呼、上次你说的话、以及它主动找你的正当理由(M11护栏)。
        """
        facts = self.mem.all_facts()
        name = next((f["object"] for f in facts if f["predicate"] == "姓名"), None)
        recent_user = [e for e in self.mem.recent_episodes(20) if e["role"] == "user"]

        greeting = f"{name}，" if name else ""
        body = f"{greeting}{trigger['message_template']}"
        generated, m_cost, m_tokens, m_priced = False, 0.0, 0, False
        if (os.environ.get("PAI_PROACTIVE_MODEL") == "1"
                and M10.active_provider() != "mock"):
            try:
                o = _complete(_proactive_prompt(trigger, greeting, recent_user, name))[0]
                text = (o.get("output") or "").strip()
                if text and not text.startswith("[模型调用失败]"):
                    body = text
                    generated = True
                    m_cost = float(o.get("cost") or 0)
                    m_tokens = int(o.get("tokens") or 0)
                    m_priced = bool(o.get("priced"))
            except Exception:  # noqa: BLE001  断流/空列表/怪响应都回模板
                pass
        lines = [
            f"[主动推送 · {trigger['type']}] {body}",
            f"  （我主动找你的理由：{trigger['justify_reason']}）",
        ]
        if recent_user:
            lines.append(f"  （上回你说到：“{recent_user[-1]['content'][:30]}”，我一直记着）")
        prov = M10.active_provider()
        lines.append("  （主动推送通路已就绪；" +
                     ("当前=mock，接真实模型后此处为 grounded 的关心/提醒）" if prov == "mock"
                      else f"当前模型：{prov}）"))
        message = "\n".join(lines)

        oid = self.mem.push_outbox(message, trigger_id=trigger["id"], channel="cli")
        self.mem.write_episode("ai", message, "proactive:" + self.session_id)
        # M14：写库成功之后才出声；念“称呼+模板”，不念括号里的元信息。
        # speak 内部失败会静默降级，绝不影响 outbox 与 claim 的次序。
        spoken_text = (greeting + trigger["message_template"]).strip()
        # 次序硬线：关着就走原路径，政策一个字都不介入（ADR-032）。
        if not M14.speaking_enabled():
            voice = M14.speak(spoken_text)
        else:
            now = now or datetime.datetime.now()
            if isinstance(now, str):
                try:
                    now = datetime.datetime.fromisoformat(now)
                except ValueError:
                    now = datetime.datetime.now()
            allowed, why = _voice_allowed(self.mem, now)
            if allowed:
                voice = M14.speak(spoken_text)
                if voice.get("spoken"):
                    _mark_voice(self.mem, now)
            else:
                voice = {"spoken": False, "backend": M14.backend(), "chars": 0,
                         "truncated": False, "rate": 0, "reason": why}
        return {"outbox_id": oid, "message": message, "voice": voice,
                "generated": generated, "cost": round(m_cost, 6), "tokens": m_tokens,
                "priced": m_priced}

    def _build_tick_ctx(self):
        """为 M6 条件型触发器组装纯数据上下文（M6 不 import M2，靠这里喂）。
        见 ADR-015：ctx=None 时条件型一律不触发，所以这里尽量给出真实时间戳。"""
        eps = self.mem.recent_episodes(50)
        user_eps = [e for e in eps if e["role"] == "user"]
        return {
            "last_user_ts": user_eps[-1]["ts"] if user_eps else None,
            "episodes_after_ts": eps[-1]["ts"] if eps else None,
            "facts": self.mem.all_facts(),
        }

    def tick(self, now=None, catch_up=False):
        """外部时钟定期调用：扫到期触发器并逐条主动发出。返回已推送结果列表。
        catch_up 默认 False：错过的一次性提醒不补发（防骚扰，见 ADR-015）。
        ADR-018 事务边界：on_tick(mark=False) 只【选】不【认领】；每条在 proactive
        发送【成功之后】才 claim_fired。发送抛异常则该触发器保持未触发+active，下次
        tick 自动重试——主动消息不会因一次写库失败而永久丢失。"""
        now = now or datetime.datetime.now()
        ctx = self._build_tick_ctx()
        results = []
        for trig in M6.on_tick(self.mem, now, ctx=ctx, catch_up=catch_up, mark=False):
            res = self.proactive(trig, now)          # 失败即向上抛，此处不认领
            M6.claim_fired(self.mem, trig, now)      # 发送成功 → 提交认领
            results.append(res)
        return results
