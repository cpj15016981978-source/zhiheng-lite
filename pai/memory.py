"""M2 持久记忆库 — 见《00_协作规程与接口契约.txt》PART B1/B2 (M2 Memory)。

Phase1 实现：sqlite3 落盘；关键词/子串相关性召回。
Phase5 补完“记忆体”本身：改口留痕(fact_history)、遗忘(forget/purge)、
原则可修订可作废(get_principles/set_principles)、情绪状态底座(get/set_affect)、
快照备份(backup)。
向量检索(pgvector/embedding)留待后续阶段——接口 recall() 的签名保持不变，
届时只换内部实现（ADR-005：DB 靠模块边界可替换）。
"""
import datetime
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

_SCHEMA = Path(__file__).with_name("schema.sql")

# ---- purge 的“原文住址清单”（ADR-034）----
# 删除面从这里推导，而不是从“这次被点名的列”推导：新表一旦存了原文，
# 守卫测试会先红，提醒把新列登记进来。
_BLANK = "（内容已按用户要求清除）"
_TEXT_HOSTS = (
    ("episodes", ("content",), "delete_row"),
    ("facts", ("subject", "predicate", "object"), "delete_row"),
    ("outbox", ("message",), "delete_row"),
    ("triggers", ("message_template", "justify_reason", "spec"), "delete_row"),
    ("affective_state", ("note",), "reset_baseline"),
    ("principles", ("text",), "deactivate"),
    ("memory_item", ("text", "summary", "tags"), "delete_row"),
    ("memory_notebook", ("data",), "delete_row"),
    ("principles", ("revision_reason",), "blank"),
    ("fact_history", ("subject", "predicate", "object"), "blank"),
    ("principle_history", ("old_text", "new_text", "reason"), "blank"),
    ("world_entity", ("name", "aliases"), "delete_row"),
    ("world_relation", ("object_value",), "delete_row"),
    ("world_event", ("summary",), "delete_row"),
    ("world_belief", ("value",), "delete_row"),
)
# 显式豁免：结构性列，不含用户叙述。守卫测试要求每一列都有归属。
_EXEMPT_TEXT = {
    ("episodes", "ts"): "时间戳",
    ("episodes", "role"): "角色枚举",
    ("episodes", "session_id"): "会话标识",
    ("facts", "ts"): "时间戳",
    ("principles", "created_ts"): "时间戳",
    ("principles", "revised_ts"): "时间戳",
    ("affective_state", "mood"): "情绪枚举标签",
    ("affective_state", "updated_ts"): "时间戳",
    ("meta", "key"): "系统键",
    ("meta", "value"): "系统状态（水位线/身份记录），不含对话原文",
    ("triggers", "type"): "触发类型枚举",
    ("triggers", "last_fired_ts"): "时间戳",
    ("outbox", "ts"): "时间戳",
    ("outbox", "channel"): "投递通道枚举",
    ("fact_history", "ts"): "时间戳",
    ("memory_item", "kind"): "类型枚举",
    ("memory_item", "subject"): "主体标签",
    ("memory_item", "predicate"): "关系标签",
    ("memory_item", "source"): "来源标签",
    ("memory_item", "source_id"): "来源编号",
    ("memory_item", "created_at"): "时间戳",
    ("memory_item", "valid_from"): "时间戳",
    ("memory_item", "valid_to"): "时间戳",
    ("memory_item", "entities"): "实体标签",
    ("memory_item", "hash"): "内容摘要",
    ("memory_item", "status"): "状态枚举",
    ("memory_notebook", "session_id"): "会话标识",
    ("memory_notebook", "updated_at"): "时间戳",
    ("fact_history", "replaced_ts"): "时间戳",
    ("principle_history", "op"): "操作枚举",
    ("principle_history", "ts"): "时间戳",
    ("world_entity", "id"): "实体标识",
    ("world_entity", "type"): "实体类型枚举",
    ("world_entity", "sensitivity"): "敏感级别枚举",
    ("world_entity", "created_at"): "时间戳",
    ("world_entity", "status"): "状态枚举",
    ("world_relation", "id"): "关系标识",
    ("world_relation", "subject_id"): "实体标识",
    ("world_relation", "object_id"): "实体标识",
    ("world_relation", "predicate"): "关系谓词",
    ("world_relation", "valid_from"): "时间戳",
    ("world_relation", "valid_to"): "时间戳",
    ("world_relation", "observed_at"): "时间戳",
    ("world_relation", "status"): "状态枚举",
    ("world_relation", "retire_reason"): "作废理由，非用户原文",
    ("world_event", "id"): "事件标识",
    ("world_event", "at"): "时间戳",
    ("world_event", "kind"): "事件类型枚举",
    ("world_event", "participants"): "实体标识 JSON",
    ("world_event", "place_id"): "实体标识",
    ("world_event", "sensitivity"): "敏感级别枚举",
    ("world_event", "status"): "状态枚举",
    ("world_belief", "id"): "信念标识",
    ("world_belief", "holder"): "主体枚举 self/user",
    ("world_belief", "subject_id"): "实体标识",
    ("world_belief", "predicate"): "关系谓词",
    ("world_belief", "source"): "来源引用",
    ("world_belief", "updated_at"): "时间戳",
    ("world_belief", "status"): "状态枚举",
    ("world_contradiction", "id"): "矛盾标识",
    ("world_contradiction", "a_kind"): "对象类型枚举",
    ("world_contradiction", "a_id"): "对象标识",
    ("world_contradiction", "b_kind"): "对象类型枚举",
    ("world_contradiction", "b_id"): "对象标识",
    ("world_contradiction", "detected_at"): "时间戳",
    ("world_contradiction", "resolution"): "解决说明，非用户原文",
    ("world_contradiction", "status"): "状态枚举",
}

# ---- 能力契约登记（ADR-036）。M2 是地基不是能力：没有开关、本来就常开，
# 所以 switch_env 如实写 NONE、default_on 如实写 True，见 tools/check_capability.py 的地基条款。
CAPABILITY = {
    "name": "M2 持久记忆库",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": True,
    "network": False,
    "degrade": "库打不开/表缺 = 直接抛错，不静默兜底：地基不能假装还在记住",
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
    "probe_cost": "只读模块常量，不建库、不连库、不起进程",
    "approval": "常开=产品定义本身（关掉就没有「重开还记得」），这条定在 ADR-036 地基条款；清除只走 :purge → purge()",
}


def _query_tokens(q):
    q = str(q or "").strip()
    if not q:
        return []
    out = []
    for part in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", q):
        if part.isascii():
            out.append(part)
        elif len(part) <= 2:
            out.append(part)
        else:
            out.extend(part[i:i + 2] for i in range(len(part) - 1))
    seen = []
    for t in out:
        if t and t not in seen:
            seen.append(t)
    return seen[:12]


SELF_WRITE_SOURCES = {"rsi", "self_apply", "self_improve", "auto_agent", "model"}


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ---- 召回用的切词与打分（Phase5.5，见 tests/test_recall_quality.py）----
# 旧实现只按空格/逗号切，中文整句变成【一个 token】，必须原文出现连续同样的字才命中——
# 于是「爷爷挨饿」查不到「我爷爷1959年在挨饿」：两个词都在库里，召回却是 0。
# Stage 0（ADR 草案见《向量召回调研报告》）：全量候选 + 手写别名表，零依赖、可回滚。
# 原 _EPISODE_SCAN_WINDOW=500 的性能护栏会把老记忆【静默】丢掉——不是排到后面，是看不见。
# 个人库量级下全表扫描足够快；真到大库再上倒排/向量，签名不变。
_ALIAS_GROUPS = (
    ("爷爷", "祖父"),
    ("奶奶", "祖母"),
    ("去世", "过世", "走了"),
    ("床垫", "床褥"),
    ("工作", "职业", "干活"),
    ("睡不着", "失眠", "睡不好"),
    ("挨饿", "吃不饱", "饿肚子"),
    ("孩子", "儿子", "女儿"),
)
# 每个词 → 它所在的整组；查“祖父”也能命中“爷爷”。
_ALIAS = {}
for _group in _ALIAS_GROUPS:
    for _word in _group:
        _ALIAS[_word] = _group
def _alias_hit(query, text):
    """整词级别名：解决三字词（吃不饱/睡不着）在二元切词下展不开。"""
    if not query or not text:
        return False
    for group in _ALIAS_GROUPS:
        if any(w in query for w in group) and any(w in text for w in group):
            return True
    return False


_HALF_LIFE_DAYS = 365.0             # 新近半衰期：一年前≈等权，七年前≈1/16，但不为零
_TYPE_PRIOR = {"fact": 1.0, "episode": 0.9}   # 蒸馏过的事实略优先于原始对话
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def _tokens(s):
    """ASCII/数字按词，连续中文按二元切片；单字中文保留自身。"""
    if not s:
        return []
    toks = [t.lower() for t in _WORD_RE.findall(s)]
    for run in _CJK_RE.findall(s):
        if len(run) == 1:
            toks.append(run)
        else:
            toks.extend(run[i:i + 2] for i in range(len(run) - 1))
    return toks


def _as_dt(value):
    if isinstance(value, datetime.datetime):
        return value
    try:
        return datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _decay(ts, now_dt):
    then = _as_dt(ts)
    if then is None:
        return 1.0
    age_days = (now_dt - then).total_seconds() / 86400.0
    if age_days <= 0:
        return 1.0
    return 0.5 ** (age_days / _HALF_LIFE_DAYS)


def _score(query, text, ts, now_dt, prior):
    """覆盖率打分：命中的查询 token 占全部查询 token 的比例，乘新近与类型系数。

    为什么不用旧的“每命中一次加 0.2”：那会让长文本天然占便宜、且总分能超过 1，
    两条记忆的相关性没法比。覆盖率 ∈ [0,1]，与文本长短无关。
    """
    q_tokens = set(_tokens(query))
    if not q_tokens or not text:
        return 0.0
    if query.strip() and query.strip() in text:
        return round(1.0 * prior * _decay(ts, now_dt), 6)   # 原文整句出现 = 最强信号
    content_tokens = set(_tokens(text))
    hit = 0
    for t in q_tokens:
        if len(t) == 1:
            hit += 1 if t in text else 0          # 单字查询退化为子串匹配，否则必然落空
        elif t in content_tokens:
            hit += 1
        elif any(alt in content_tokens for alt in _ALIAS.get(t, ())):
            hit += 1                              # 别名表：词面不同也算命中
    if hit == 0 and _alias_hit(query, text):
        hit = 1
    cover = hit / len(q_tokens)
    if cover <= 0:
        return 0.0
    return round(cover * prior * _decay(ts, now_dt), 6)


class Memory:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
        self.conn.commit()

    # ---- 写 ----
    def write_episode(self, role, content, session_id=None, source="user"):
        if str(source or "").lower() in SELF_WRITE_SOURCES:
            raise ValueError("拒绝自改进输出直接写记忆：%s" % source)
        cur = self.conn.execute(
            "INSERT INTO episodes(ts, role, content, session_id) VALUES(?,?,?,?)",
            (_now(), role, content, session_id),
        )
        self.conn.commit()
        return cur.lastrowid

    def upsert_fact(self, subject, predicate, obj, confidence=0.5, source_episode_id=None,
                    source="user"):
        # 自改进/模型输出不得直接写事实库；只能走候选、经人批准。
        if str(source or "").lower() in SELF_WRITE_SOURCES:
            raise ValueError("拒绝自改进输出直接写记忆：%s" % source)
        row = self.conn.execute(
            "SELECT * FROM facts WHERE subject=? AND predicate=?", (subject, predicate)
        ).fetchone()
        if row:
            if row["object"] != obj:
                # 改口：旧值先留痕，否则“我以前以为你是做什么的”就永远答不上了
                self.conn.execute(
                    "INSERT INTO fact_history(fact_id,subject,predicate,object,ts,"
                    " replaced_ts,source_episode_id) VALUES(?,?,?,?,?,?,?)",
                    (row["id"], subject, predicate, row["object"], row["ts"],
                     _now(), row["source_episode_id"]),
                )
            self.conn.execute(
                "UPDATE facts SET object=?, ts=?, confidence=?, source_episode_id=? WHERE id=?",
                (obj, _now(), confidence, source_episode_id, row["id"]),
            )
            self.conn.commit()
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO facts(subject,predicate,object,ts,confidence,source_episode_id)"
            " VALUES(?,?,?,?,?,?)",
            (subject, predicate, obj, _now(), confidence, source_episode_id),
        )
        self.conn.commit()
        return cur.lastrowid

    # ---- 读 / 召回 ----
    def recall(self, query, k=5, now=None):
        """返回 [{item, relevance}]，relevance ∈ [0,1] 按分倒序、最多 k 条。
        item.type ∈ {episode, fact}。

        签名 recall(query, k) 不变（ADR-005：将来换向量检索只改内部实现）。
        now 是新增的【可选】参数，只为让新近权重能离线确定性验证——
        与 M6 调度器“传 now 而不是读系统时钟”同一规矩；编排层不用改。
        """
        q = (query or "").strip()
        if not _tokens(q):
            return []
        now_dt = _as_dt(now) or datetime.datetime.now()
        results = []

        # 全量候选：不切窗口。老记忆必须还能被看见，否则“它记不住”会永远治不好。
        for r in self.conn.execute("SELECT * FROM episodes ORDER BY id DESC"):
            score = _score(q, r["content"], r["ts"], now_dt, _TYPE_PRIOR["episode"])
            if score > 0:
                results.append({"item": {"type": "episode", **dict(r)}, "relevance": score})

        for r in self.conn.execute("SELECT * FROM facts"):
            text = f'{r["subject"]} {r["predicate"]} {r["object"]}'
            score = _score(q, text, r["ts"], now_dt, _TYPE_PRIOR["fact"])
            if score > 0:
                results.append({"item": {"type": "fact", **dict(r)}, "relevance": score})

        results.sort(key=lambda x: (-x["relevance"], x["item"]["id"]))
        return results[:k]

    def all_facts(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM facts ORDER BY id")]

    def fact_history(self, subject=None, predicate=None, limit=20):
        """它改过口的旧事实（按被改时刻倒序）。用于回答“你以前以为我是什么”。"""
        where, params = [], []
        if subject:
            where.append("subject=?")
            params.append(subject)
        if predicate:
            where.append("predicate=?")
            params.append(predicate)
        q = "SELECT * FROM fact_history"
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return [dict(r) for r in self.conn.execute(q, params)]

    def recent_episodes(self, n=10):
        rows = [dict(r) for r in self.conn.execute(
            "SELECT * FROM episodes ORDER BY id DESC LIMIT ?", (n,))]
        return rows[::-1]

    # ---- Phase2：反思引擎支撑 ----
    def get_meta(self, key, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key, value):
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        self.conn.commit()

    def max_episode_id(self):
        row = self.conn.execute("SELECT COALESCE(MAX(id),0) AS m FROM episodes").fetchone()
        return row["m"]

    def episodes_after(self, ep_id):
        """返回 id > ep_id 的全部 episode（按时间升序），供反思增量扫描。"""
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM episodes WHERE id > ? ORDER BY id", (ep_id,))]

    def add_principle(self, text, reason=None):
        """写入一条处世原则；同文本原则已存在（含已作废的）则跳过，返回 (id, created)。

        去重故意【不看 active】：反思是确定性的，同一批 fact 每次都算出同一句话。
        若只跟 active 比，用户作废一条后下轮反思会把它原样复活——作废就成了空话。
        """
        row = self.conn.execute(
            "SELECT id FROM principles WHERE text=?", (text,)
        ).fetchone()
        if row:
            return row["id"], False
        cur = self.conn.execute(
            "INSERT INTO principles(text, created_ts, active) VALUES(?,?,1)",
            (text, _now()),
        )
        self._log_principle(cur.lastrowid, "add", None, text, reason or "新增")
        self.conn.commit()
        return cur.lastrowid, True

    def get_principles(self, active_only=True):
        q = "SELECT * FROM principles"
        if active_only:
            q += " WHERE active=1"
        q += " ORDER BY id"
        return [dict(r) for r in self.conn.execute(q)]

    def set_principles(self, changes, reason):
        """批量落原则变更 —— 契约 M2 的 set_principles(diff, reason)。
        changes: [{"op":"add","text":..} | {"op":"revise","id":..,"text":..}
                  | {"op":"retire","id":..}]
        reason 必须非空：它自己写的原则要改，就得留下为什么改。
        返回 {"added":n, "revised":n, "retired":n}。"""
        if not (reason or "").strip():
            raise ValueError("修改自写原则必须给出 reason（为什么改），否则拒绝。")
        reason = reason.strip()
        out = {"added": 0, "revised": 0, "retired": 0}
        for ch in changes or []:
            op = ch.get("op")
            if op == "add":
                _pid, created = self.add_principle(ch["text"])
                out["added"] += 1 if created else 0
            elif op == "revise":
                out["revised"] += 1 if self._revise_principle(ch["id"], ch["text"], reason) else 0
            elif op == "retire":
                out["retired"] += 1 if self._retire_principle(ch["id"], reason) else 0
            else:
                raise ValueError(f"未知的原则变更 op：{op}（应为 add/revise/retire）")
        self.conn.commit()
        return out

    def _revise_principle(self, principle_id, new_text, reason):
        old = self.conn.execute(
            "SELECT text FROM principles WHERE id=?", (principle_id,)).fetchone()
        if old is None:
            return False
        cur = self.conn.execute(
            "UPDATE principles SET text=?, revised_ts=?, revision_reason=?, active=1 WHERE id=?",
            (new_text, _now(), reason, principle_id))
        if cur.rowcount > 0 and (old["text"] or "") != (new_text or ""):
            # 文本没变就不记账：与 upsert_fact 同一条纪律，不造假账。
            self._log_principle(principle_id, "revise", old["text"], new_text, reason)
        return cur.rowcount > 0

    def _retire_principle(self, principle_id, reason):
        old = self.conn.execute(
            "SELECT text FROM principles WHERE id=?", (principle_id,)).fetchone()
        cur = self.conn.execute(
            "UPDATE principles SET active=0, revised_ts=?, revision_reason=? WHERE id=?",
            (_now(), reason, principle_id))
        if cur.rowcount > 0:
            self._log_principle(principle_id, "retire",
                                old["text"] if old else None, None, reason)
        return cur.rowcount > 0

    def _log_principle(self, principle_id, op, old_text, new_text, reason):
        """原则改口的账，记在【唯一写入口】里。
        记在调用方（自我模型/CLI/界面）= 只要有人绕过那条路，账就少一笔。"""
        self.conn.execute(
            "INSERT INTO principle_history(principle_id,op,old_text,new_text,reason,ts)"
            " VALUES(?,?,?,?,?,?)",
            (principle_id, op, old_text, new_text, reason, _now()))

    def principle_history(self, principle_id=None, limit=20):
        """它自写原则的修订/作废流水（按时刻倒序）。答“你以前守的是哪一条、为什么改”。"""
        q = "SELECT * FROM principle_history"
        params = []
        if principle_id is not None:
            q += " WHERE principle_id=?"
            params.append(int(principle_id))
        q += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return [dict(r) for r in self.conn.execute(q, params)]

    # ---- Phase3：主动推送触发器 / 发件箱 ----
    def add_trigger(self, type, message_template, justify_reason, spec=None):
        """登记一个触发器。justify_reason 必须非空（落实“主动要有正当理由”）。"""
        if not (justify_reason or "").strip():
            raise ValueError("主动推送必须有 justify_reason（正当理由），否则拒绝登记。")
        cur = self.conn.execute(
            "INSERT INTO triggers(type,spec,message_template,justify_reason,active)"
            " VALUES(?,?,?,?,1)",
            (type, spec, message_template, justify_reason.strip()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_triggers(self, active_only=True):
        q = "SELECT * FROM triggers" + (" WHERE active=1" if active_only else "") + " ORDER BY id"
        return [dict(r) for r in self.conn.execute(q)]

    def get_trigger(self, trigger_id):
        r = self.conn.execute("SELECT * FROM triggers WHERE id=?", (trigger_id,)).fetchone()
        return dict(r) if r else None

    def mark_trigger_fired(self, trigger_id, now, deactivate=False):
        self.conn.execute(
            "UPDATE triggers SET last_fired_ts=? WHERE id=?", (now, trigger_id))
        if deactivate:
            self.conn.execute("UPDATE triggers SET active=0 WHERE id=?", (trigger_id,))
        self.conn.commit()

    def push_outbox(self, message, trigger_id=None, channel="cli"):
        cur = self.conn.execute(
            "INSERT INTO outbox(ts,trigger_id,channel,message,is_read) VALUES(?,?,?,?,0)",
            (_now(), trigger_id, channel, message),
        )
        self.conn.commit()
        return cur.lastrowid

    def read_outbox(self, unread_only=False):
        q = "SELECT * FROM outbox" + (" WHERE is_read=0" if unread_only else "") + " ORDER BY id"
        return [dict(r) for r in self.conn.execute(q)]

    def mark_outbox_read(self, outbox_id):
        self.conn.execute("UPDATE outbox SET is_read=1 WHERE id=?", (outbox_id,))
        self.conn.commit()

    # ---- Phase5：遗忘 / 情感底座 / 备份 ----
    def forget_fact(self, fact_id, reason=""):
        """真忘掉一条事实，连它的改口留痕一起删。返回是否删到。

        reason 非空时，把被删事实的快照与理由写进 meta，键以 forget: 开头；
        事实本身仍然真删，不把用户原文留在 facts/fact_history 里。
        """
        row = self.conn.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
        if row is None:
            return False
        if str(reason or "").strip():
            meta = {
                "fact_id": fact_id,
                "reason": str(reason).strip(),
                "forgotten_at": _now(),
            }
            key = "forget:%s:%s" % (fact_id, _now())
            self.conn.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                (key, json.dumps(meta, ensure_ascii=False)))
        cur = self.conn.execute("DELETE FROM facts WHERE id=?", (fact_id,))
        self.conn.execute("DELETE FROM fact_history WHERE fact_id=?", (fact_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def purge(self, pattern):
        """按正则从记忆里清除用户原文——删除面由 _TEXT_HOSTS 推导，覆盖全库文本列。

        三类处置（ADR-034，次序 A → C → B 是设计的一部分）：
          A delete_row     活内容：该行任一在册列命中 → 整行删（triggers 含 justify_reason/spec）
          A reset_baseline 情绪 note 命中 → 复位为 affect.baseline()，走 set_affect 入口
          C deactivate     活原则 text 命中 → 置占位 + active=0，走 set_principles 入口留痕
          B blank          改口流水/理由命中 → 文本置占位，**不删行**（删行=造假账）
        次序为什么是 A→C→B：C 会新写一条 principle_history（old_text 正是原文），
        B 排在 C 之后才能把它一起置掉；倒过来就漏。

        VACUUM 只为减少空闲页里的可恢复残留（best-effort）；不承诺对抗 SSD
        磨损均衡、OS 快照或文件系统日志。非法正则直接抛 re.error。

        返回：旧五键（episodes/facts/outbox/triggers/affect_reset）含义不变，
        只加新键：principles_deactivated / blanked_histories / blanked_reasons。
        """
        rx = re.compile(pattern)
        counts = {"episodes": 0, "facts": 0, "outbox": 0, "triggers": 0,
                  "affect_reset": 0, "principles_deactivated": 0,
                  "blanked_histories": 0, "blanked_reasons": 0,
                  "world_entity": 0, "world_relation": 0,
                  "world_event": 0, "world_belief": 0,
                  "memory_item": 0, "memory_notebook": 0}

        # ---------- A：活内容，删行 ----------
        for table, cols, action in _TEXT_HOSTS:
            if action != "delete_row":
                continue
            rows = self.conn.execute(
                "SELECT id, %s FROM %s" % (", ".join(cols), table)).fetchall()
            hit = [r["id"] for r in rows
                   if any(rx.search(r[c] or "") for c in cols)]
            for rid in hit:
                self.conn.execute("DELETE FROM %s WHERE id=?" % table, (rid,))
            counts[table] += len(hit)

        # ---------- A：情绪 note，复位基线（走上层入口 set_affect） ----------
        row = self.conn.execute("SELECT note FROM affective_state WHERE id=1").fetchone()
        if row is not None and rx.search(row["note"] or ""):
            from . import affect as _affect
            base = _affect.baseline()
            self.set_affect(base["mood"], base["valence"], base["arousal"], None)
            counts["affect_reset"] = 1

        # ---------- C：活原则文本，置占位 + 停用（走 set_principles 入口） ----------
        reason = "用户要求遗忘(:purge)"
        rows = self.conn.execute("SELECT id, text FROM principles").fetchall()
        for r in rows:
            if not rx.search(r["text"] or "") or (r["text"] or "") == _BLANK:
                continue
            self.set_principles([{"op": "revise", "id": r["id"], "text": _BLANK}], reason)
            self.set_principles([{"op": "retire", "id": r["id"]}], reason)
            counts["principles_deactivated"] += 1

        # ---------- B：改口流水/理由，置占位不删行 ----------
        for table, cols, action in _TEXT_HOSTS:
            if action != "blank":
                continue
            rows = self.conn.execute(
                "SELECT id, %s FROM %s" % (", ".join(cols), table)).fetchall()
            for r in rows:
                dirty = [c for c in cols if rx.search(r[c] or "")]
                if not dirty:
                    continue
                for c in dirty:
                    self.conn.execute(
                        "UPDATE %s SET %s=? WHERE id=?" % (table, c), (_BLANK, r["id"]))
                if table in ("fact_history", "principle_history"):
                    counts["blanked_histories"] += 1
                else:
                    counts["blanked_reasons"] += 1

        try:
            # 删除面同步清 FTS，避免已 purge 的原文还能被全文检索到。
            self.conn.execute("DELETE FROM memory_item_fts WHERE rowid NOT IN (SELECT id FROM memory_item)")
        except sqlite3.Error:
            pass
        self.conn.commit()
        if any(counts.values()):
            try:
                self.conn.execute("VACUUM")   # 只在真清过东西时做；best-effort
            except sqlite3.Error:
                pass
        return counts

    def get_affect(self):
        """M5 情感层的存储底座（契约 get_affect）。没读过情绪时返回 None。"""
        r = self.conn.execute("SELECT * FROM affective_state WHERE id=1").fetchone()
        return dict(r) if r else None

    def set_affect(self, mood=None, valence=None, arousal=None, note=None):
        """单行(id=1)写回情绪状态（契约 set_affect）。M5 未接入前，记忆层先留这个口子。"""
        self.conn.execute(
            "INSERT INTO affective_state(id,mood,valence,arousal,updated_ts,note) "
            "VALUES(1,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET mood=excluded.mood, valence=excluded.valence, "
            "arousal=excluded.arousal, updated_ts=excluded.updated_ts, note=excluded.note",
            (mood, valence, arousal, _now(), note),
        )
        self.conn.commit()

    # ---- 真正的记忆系统 v1：档案柜 / 记事本 ----
    def remember_item(self, text, kind="note", subject="", predicate="",
                      summary="", source="", source_id="", tags="",
                      entities="", confidence=0.6, importance=0.5,
                      supersedes=None):
        """写一条档案；supersedes 给旧 id 时，旧条标 superseded，不删。"""
        text = str(text or "").strip()
        if not text:
            raise ValueError("memory_item.text 不能为空")
        now = _now()
        tags = ",".join(tags) if isinstance(tags, (list, tuple)) else str(tags or "")
        entities = ",".join(entities) if isinstance(entities, (list, tuple)) else str(entities or "")
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        cur = self.conn.execute(
            "INSERT INTO memory_item(kind,subject,predicate,text,summary,source,source_id,"
            "created_at,valid_from,confidence,importance,tags,entities,hash,status) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active')",
            (kind, subject, predicate, text, summary, source, source_id,
             now, now, float(confidence), float(importance), tags, entities, h))
        new_id = cur.lastrowid
        self.conn.execute(
            "INSERT INTO memory_item_fts(rowid,text,summary,tags) VALUES(?,?,?,?)",
            (new_id, text, summary, tags))
        if supersedes:
            self.conn.execute(
                "UPDATE memory_item SET status='superseded', valid_to=?, superseded_by=? "
                "WHERE id=? AND status='active'",
                (now, new_id, int(supersedes)))
        self.conn.commit()
        return new_id

    def recall_items(self, query, k=5, include_superseded=True):
        """按 FTS5 查档案；FTS 不可用/查询语法坏时退回 LIKE。"""
        q = str(query or "").strip()
        k = max(1, int(k))
        tokens = _query_tokens(q)
        rows = []
        if q and tokens:
            match = " OR ".join('"%s"' % t for t in tokens)
            try:
                rows = self.conn.execute(
                    "SELECT i.*, bm25(memory_item_fts) AS rank "
                    "FROM memory_item_fts f JOIN memory_item i ON i.id=f.rowid "
                    "WHERE memory_item_fts MATCH ? "
                    + ("" if include_superseded else "AND i.status='active' ")
                    + "ORDER BY rank LIMIT ?", (match, k)).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            # 中文用二字窗 + LIKE 兜底：FTS5 的 unicode61 不切中文词。
            parts = []
            params = []
            for t in (tokens or [q]):
                parts.append("(text LIKE ? OR summary LIKE ? OR tags LIKE ?)")
                like = "%" + t + "%"
                params.extend([like, like, like])
            where = " OR ".join(parts) if parts else "1=1"
            sql = ("SELECT *, NULL AS rank FROM memory_item WHERE (" + where + ")")
            if not include_superseded:
                sql += " AND status='active'"
            sql += " ORDER BY created_at DESC LIMIT ?"
            rows = self.conn.execute(sql, tuple(params) + (k,)).fetchall()
        return [dict(r) for r in rows]

    def recall_items_semantic(self, query, k=5, include_superseded=True):
        """BGE 语义召回：档案全量向量 + 词面候选，余弦排序。

        没配 BGE 时 embedding 会退回 local-hash，仍可用但质量有限；
        任何异常都退回 recall_items，不让记忆读写被网络拖死。
        """
        q = str(query or "").strip()
        if not q:
            return []
        k = max(1, int(k))
        try:
            from .embedding import embed
            where = "" if include_superseded else " WHERE status='active'"
            rows = [dict(r) for r in self.conn.execute(
                "SELECT * FROM memory_item" + where + " ORDER BY id DESC LIMIT 300")]
            if not rows:
                return []
            texts = [(r.get("text") or "") + " " + (r.get("summary") or "") for r in rows]
            out = embed([q] + texts)
            qv = out["vectors"][0]
            import math
            def cos(a, b):
                n = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
                return sum(x * y for x, y in zip(a, b)) / (n or 1e-9)
            scored = []
            for r, v in zip(rows, out["vectors"][1:]):
                r["relevance"] = round(cos(qv, v), 6)
                scored.append(r)
            scored.sort(key=lambda x: (-x["relevance"], -int(x.get("importance") or 0),
                                       -float(x.get("confidence") or 0)))
            return scored[:k]
        except Exception:
            return self.recall_items(q, k=k, include_superseded=include_superseded)

    def open_item(self, item_id):
        r = self.conn.execute("SELECT * FROM memory_item WHERE id=?", (int(item_id),)).fetchone()
        return dict(r) if r else None

    def forget_item(self, item_id):
        """真删档案（FTS + 主表），返回是否删到。"""
        item_id = int(item_id)
        self.conn.execute("DELETE FROM memory_item_fts WHERE rowid=?", (item_id,))
        cur = self.conn.execute("DELETE FROM memory_item WHERE id=?", (item_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def get_notebook(self, session_id):
        r = self.conn.execute(
            "SELECT data FROM memory_notebook WHERE session_id=?", (str(session_id),)).fetchone()
        if not r:
            return {}
        try:
            return json.loads(r["data"] or "{}")
        except Exception:
            return {}

    def set_notebook(self, session_id, data):
        now = _now()
        payload = json.dumps(data or {}, ensure_ascii=False)
        self.conn.execute(
            "INSERT INTO memory_notebook(session_id,updated_at,data) VALUES(?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET updated_at=excluded.updated_at,data=excluded.data",
            (str(session_id), now, payload))
        self.conn.commit()
        return {"session_id": str(session_id), "updated_at": now}


    # ---- 记忆图谱 / 混合召回 / 自动候选 / 健康（核心内建，不再另挂模块）----
    def graph(self, limit=300):
        facts = self.all_facts()[-int(limit):]
        nodes, edges, seen = [], [], set()

        def node(name, kind):
            name = str(name or "").strip()
            if not name:
                return None
            if name not in seen:
                seen.add(name)
                nodes.append({"id": name, "label": name, "kind": kind})
            return name

        for f in facts:
            a = node(f.get("subject"), "subject")
            b = node(f.get("object"), "object")
            if a and b:
                edges.append({"source": a, "target": b,
                              "label": str(f.get("predicate") or ""),
                              "weight": int(f.get("confidence") or 1)})
        return {"nodes": nodes, "edges": edges, "facts": len(facts)}

    def hybrid_search(self, query, conv_store=None, profile="", limit=10, include_semantic=True):
        q = str(query or "").strip()
        if not q:
            return {"query": q, "items": []}
        tokens = _tokens(q)
        items, seen = [], set()

        def age_days(ts):
            try:
                t = _as_dt(ts)
                now = datetime.datetime.now(t.tzinfo) if t and t.tzinfo else datetime.datetime.now()
                return max(0.0, (now - t).total_seconds() / 86400.0) if t else 999.0
            except Exception:
                return 999.0

        def add(kind, text, source, ts=None, base=0.0, meta=None):
            text = str(text or "").strip()
            if not text or text in seen:
                return
            seen.add(text)
            low = text.lower()
            kw = sum(1 for t in tokens if t in low) / max(1, len(tokens))
            rec = 1.0 / (1.0 + age_days(ts) / 30.0) if ts else 0.5
            it = {"kind": kind, "text": text[:1000], "source": source,
                  "score": round(0.50 * float(base) + 0.35 * kw + 0.15 * rec, 4)}
            if meta:
                it.update(meta)
            items.append(it)

        for h in self.recall(q, k=max(limit, 8)) or []:
            item = h.get("item") or h
            text = item.get("content") or " ".join(
                str(item.get(k) or "") for k in ("subject", "predicate", "object"))
            add("memory", text, "memory", item.get("ts") or item.get("created_at"),
                float(h.get("relevance") or h.get("score") or 0.0))
        for it in self.recall_items(q, k=max(limit, 8)) or []:
            add("memory_item", it.get("text") or it.get("summary"), "memory_item",
                it.get("created_at"), 0.5, {"tags": it.get("tags")})
        # 可选语义召回：没配 BGE/HTTP 时 embedding 用 local-hash，失败自动回退。
        try:
            if include_semantic:
                semantic_rows = self.recall_items_semantic(q, k=max(limit, 8)) or []
            else:
                semantic_rows = []
            for it in semantic_rows:
                rel = float(it.get("relevance") or 0)
                text = str(it.get("text") or it.get("summary") or "")
                lexical = any(t and t in text.lower() for t in tokens)
                if rel < 0.15 and not lexical:
                    continue
                add("semantic", text, "semantic",
                    it.get("created_at"), rel,
                    {"tags": it.get("tags"), "relevance": it.get("relevance")})
        except Exception:
            pass
        if conv_store is not None:
            try:
                for m in conv_store.search_messages(profile, q, limit=max(limit, 8)):
                    add("chat", m.get("content"), "conversation", m.get("created_at"), 0.35,
                        {"role": m.get("role"), "conversation_id": m.get("conversation_id"),
                         "class": m.get("kind")})
            except Exception:
                pass
        items.sort(key=lambda x: x["score"], reverse=True)
        return {"query": q, "items": items[:int(limit)]}

    def auto_scan(self, conv_store, profile, limit=200):
        """扫聊天，只生成候选；不自动写 facts。"""
        state_path = Path(self.db_path).with_name("auto_memory_state.json")
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
        last = int((state.get(profile) or {}).get("last_id") or 0)
        rows = conv_store.all_messages(profile, after_id=last, limit=limit)
        scanned, added, max_id = 0, [], last
        from pai import memory_candidates as MC
        for r in rows:
            max_id = max(max_id, int(r["id"]))
            if str(r.get("role") or "") != "user":
                continue
            scanned += 1
            added.extend(MC.extract(str(r.get("content") or "")))
        state[profile] = {"last_id": max_id}
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "scanned": scanned, "added": added,
                "added_count": len(added), "last_id": max_id, "pending": len(MC.pending())}

    def dedupe(self):
        """整理重复：facts 删完全重复的旧行；memory_item 标 superseded，不物理删。"""
        from pai import exec_guard as EG
        counts = {"facts_removed": 0, "items_superseded": 0}
        try:
            rows = self.conn.execute(
                "select subject,predicate,object,count(*) n from facts "
                "group by subject,predicate,object having n>1").fetchall()
            for r in rows:
                ids = [x[0] for x in self.conn.execute(
                    "select id from facts where subject=? and predicate=? and object=? order by id",
                    (r["subject"], r["predicate"], r["object"]))]
                for rid in ids[:-1]:
                    self.conn.execute("DELETE FROM facts WHERE id=?", (rid,))
                    counts["facts_removed"] += 1
        except sqlite3.Error:
            pass
        try:
            rows = self.conn.execute(
                "select text,count(*) n from memory_item where status='active' "
                "group by text having n>1").fetchall()
            for r in rows:
                ids = [x[0] for x in self.conn.execute(
                    "select id from memory_item where status='active' and text=? order by id",
                    (r["text"],))]
                keep = ids[-1]
                for rid in ids[:-1]:
                    self.conn.execute(
                        "update memory_item set status='superseded', superseded_by=? where id=?",
                        (keep, rid))
                    counts["items_superseded"] += 1
        except sqlite3.Error:
            pass
        self.conn.commit()
        try:
            EG.audit("memory_dedupe", "low", "allow", str(self.db_path), str(counts))
        except Exception:
            pass
        return counts

    def health(self, conv_store=None, profile=""):
        def count(t):
            try:
                return self.conn.execute("select count(*) from %s" % t).fetchone()[0]
            except Exception:
                return 0
        try:
            dup = self.conn.execute(
                "select count(*) from (select subject,predicate,count(*) n from facts "
                "group by subject,predicate having n>1)").fetchone()[0]
        except Exception:
            dup = 0
        try:
            cutoff = (datetime.datetime.now() - datetime.timedelta(days=90)).isoformat(timespec="seconds")
            stale = self.conn.execute("select count(*) from facts where ts < ?", (cutoff,)).fetchone()[0]
        except Exception:
            stale = 0
        chat = {}
        if conv_store is not None:
            try:
                chat = conv_store.message_stats(profile)
            except Exception as e:
                chat = {"error": str(e)}
        issues = []
        if dup: issues.append("有重复事实")
        if stale: issues.append("有过期事实")
        if chat.get("unclassified"): issues.append("有未分类聊天记录")
        return {"ok": True, "memory": {"episodes": count("episodes"), "facts": count("facts"),
                "principles": count("principles"), "memory_item": count("memory_item"),
                "duplicate_facts": dup, "stale_facts_90d": stale}, "chat": chat, "issues": issues}

    def backup(self, dest_path):
        """给记忆库拍一份快照。记忆是这个系统唯一不可替代的资产，
        “关掉重开还记得”之外还得“文件坏了/要搬家时带得走”（将来转服务器也用它）。
        已存在的目标不覆盖。返回落盘路径。"""
        dest = Path(dest_path)
        if dest.exists():
            raise ValueError(f"备份目标已存在，不做覆盖：{dest}")
        if dest.parent:
            dest.parent.mkdir(parents=True, exist_ok=True)
        self.conn.execute("VACUUM INTO ?", (str(dest),))
        return str(dest)

    def close(self):
        self.conn.close()
