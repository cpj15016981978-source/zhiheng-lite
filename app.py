# 低配版 Muse v2.2 — 在 v2.1 基础上加三个能力：记忆语义搜索、技能包、文档转 Markdown 导入
# 目标不变：人人都能免费 AI 办公。2核8G电脑能跑；联网用免费大模型（API）是主线，断网用本地小模型（Ollama）是备选。
# v2.2 新增：
#   1. 记忆语义搜索：记忆除了关键词搜，还能按意思搜（sqlite-vec 思路：向量存 BLOB，余弦算相似度；
#      装了 sqlite-vec 就用它的 vec0 虚拟表加速，没装就用 Python 算；嵌入用 Ollama 的 BGE-M3，可选）。
#   2. 技能包：skills/ 目录里放 SKILL.md（规划、文件审批、自我检查、对话、定时任务、文档处理），
#      格式学 anthropics/skills（触发条件 + 操作手册 + 验收标准），内容是我们自己的；任务匹配到技能时自动引用。
#   3. 文档导入：把 PDF/Word/Excel/txt/csv/html 放进 inbox/，转成 Markdown，先审批再进 workspace/。
#      转换优先用 markitdown（装了 markitdown[docx,xlsx,pdf] 才能读 docx/xlsx/pdf），没装时内置转换器只读 txt/md/csv/json/html。
# v2.2 新增 4. 标准对话接口：POST /v1/chat/completions（OpenAI 兼容，本机回环）+ GET /v1/models、/v1/status；
#      接口做法学 PolyStrata（只学接口，不抄引擎/代码）；请求走现有的 run_ai_agent，和网页对话同一条路，
#      文件审批规则不变；stream 暂不支持；load/unload 没做——本地没有要加载/卸载的重型模型。
# v2 的三个能力（规划循环、文件审批、自我检查）和 v2.1 的输入法守卫都在，没动。
# 不做的：MCP（太重）、记忆分层（现有 SQLite 记忆够用）。保持轻、免费、老电脑能跑。
# 依赖：pip install ollama schedule "markitdown[docx,xlsx,pdf]" sqlite-vec（后两个可选，没装也能跑，只是语义搜索和 docx/xlsx/pdf 导入会降级）；
#      Ollama 须启动（本地模式用）；API 模式需要免费 API key（Groq / OpenRouter :free），填在设置里，存在本机 muse_config.json，不上传。
#      语义搜索是可选的：想用就再拉一个嵌入模型 ollama pull bge-m3，不拉也能用关键词搜索。
# 只有 Python 标准库做 Web 服务；API 模式才联网（只连你选的模型服务），其余流量只在你自己电脑里。
# 前端页面存在 index.txt 里（内容是 HTML），由 app.py 读出来当网页发给浏览器。

import math
import os
import re
import json
import sqlite3
import struct
import schedule
import time
import threading
import urllib.request
import urllib.error
import ollama
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# ==================== 0. 配置 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "local_office_memory.db")
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
WORKSPACE_DIR = os.path.join(BASE_DIR, "workspace")
CONFIG_PATH = os.path.join(BASE_DIR, "muse_config.json")
SKILLS_DIR = os.path.join(BASE_DIR, "skills")
INBOX_DIR = os.path.join(BASE_DIR, "inbox")

LOCAL_CODE = "qwen2.5-coder:3b"
LOCAL_MAIN = os.environ.get("OFFICE_MAIN_MODEL", "qwen3:4b")
NUM_THREAD = 2
NUM_CTX = 4096
HOST = "127.0.0.1"
PORT = 8080

# 文件审批：只允许这些后缀，单个文件不超过 1MB
ALLOWED_EXTS = {"txt", "md", "csv", "json", "html", "py"}
MAX_FILE_BYTES = 1024 * 1024

# v2.2 语义搜索：嵌入模型（Ollama 的 BGE-M3；可选，不装也能用关键词搜索）
EMBED_MODEL = os.environ.get("OFFICE_EMBED_MODEL", "bge-m3")
EMBED_DIM = 1024  # bge-m3 的向量维度
# v2.2 文档导入：收件箱里接受的源格式（markitdown 没装时只读 txt/md/csv/json/html）
INGEST_EXTS = {"pdf", "docx", "xlsx", "pptx", "txt", "md", "csv", "json", "html", "htm"}

# 免费 API 默认（模型名可在设置里改）
API_PROVIDERS = {
    "groq": {
        "label": "Groq（免费）",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "default_model": "llama-3.3-70b-versatile",
        "signup": "https://console.groq.com/keys",
    },
    "openrouter": {
        "label": "OpenRouter :free（免费）",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "default_model": "meta-llama/llama-3.3-70b-instruct:free",
        "signup": "https://openrouter.ai/keys",
    },
}

DEFAULT_CONFIG = {
    "mode": "auto",          # auto | api | local
    "provider": "groq",      # groq | openrouter
    "api_key": "",
    "api_model": "",         # 为空则用该服务商的默认模型
    "local_main": LOCAL_MAIN,
    "local_code": LOCAL_CODE,
}

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            for k in cfg:
                if k in saved and saved[k] is not None:
                    cfg[k] = saved[k]
    except (FileNotFoundError, ValueError):
        pass
    return cfg

def save_config(cfg):
    safe = {k: cfg.get(k, DEFAULT_CONFIG[k]) for k in DEFAULT_CONFIG}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(safe, f, ensure_ascii=False, indent=2)
    return safe

# ==================== 1. 长期记忆 (SQLite，和 v1/v2 同一套) ====================
def _connect():
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memories'")
    if cursor.fetchone() is None:
        cursor.execute('''
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL DEFAULT 'user',
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        ''')
    else:
        cols = [r[1] for r in cursor.execute("PRAGMA table_info(memories)")]
        if "content" not in cols:
            cursor.execute("ALTER TABLE memories ADD COLUMN kind TEXT NOT NULL DEFAULT 'user'")
            cursor.execute("ALTER TABLE memories ADD COLUMN content TEXT NOT NULL DEFAULT ''")
            cursor.execute("ALTER TABLE memories ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")
            cursor.execute("UPDATE memories SET content = key_info WHERE content = ''")
            cursor.execute("UPDATE memories SET created_at = datetime('now', 'localtime') WHERE created_at = ''")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            time_str TEXT NOT NULL,
            prompt TEXT NOT NULL,
            last_run TEXT,
            enabled INTEGER NOT NULL DEFAULT 1
        )
    ''')
    # v2 新增：任务计划、计划步骤、待审批文件
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS plan_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            idx INTEGER NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            result TEXT NOT NULL DEFAULT ''
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pending_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            step_idx INTEGER NOT NULL,
            filename TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        )
    ''')
    # v2.2 新增：记忆向量表（语义搜索）。向量以 float32 BLOB 存，dim 随每条记。
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS memory_vectors (
            memory_id INTEGER PRIMARY KEY,
            dim INTEGER NOT NULL,
            vec BLOB NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def save_memory(info, kind="user"):
    if not info or not str(info).strip():
        return
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO memories (kind, content, created_at) VALUES (?, ?, ?)",
        (kind, str(info).strip(), datetime.now().strftime("%Y-%m-%d %H:%M")),
    )
    mid = cur.lastrowid
    conn.commit()
    conn.close()
    # v2.2：顺手给这条记忆算向量（失败也不影响记忆本身；嵌入模型没装时直接跳过）。
    try:
        store_memory_vector(mid, str(info).strip())
    except Exception:
        pass

def get_memories_list(limit=20):
    conn = _connect()
    rows = conn.execute(
        "SELECT kind, content, created_at FROM memories ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [{"kind": k, "content": c, "created_at": t} for k, c, t in rows]

def search_memories_list(keyword, limit=20):
    conn = _connect()
    rows = conn.execute(
        "SELECT kind, content, created_at FROM memories WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
        (f"%{keyword}%", limit),
    ).fetchall()
    conn.close()
    return [{"kind": k, "content": c, "created_at": t} for k, c, t in rows]

def get_recent_memories(limit=5):
    rows = get_memories_list(limit)
    if not rows:
        return "（暂无记忆）"
    tag = {"user": "你", "ai": "AI", "task": "定时任务"}
    return "\n".join(f"- [{r['created_at']} {tag.get(r['kind'], r['kind'])}] {r['content'][:200]}" for r in reversed(rows))

# ==================== 1.5 v2.2 新增：语义搜索（sqlite-vec 思路） ====================
# 做法：记忆的向量以 float32 BLOB 存在 memory_vectors 表；嵌入用 Ollama 的 BGE-M3（可选）。
# 检索时优先用 sqlite-vec 的 vec0 虚拟表做 KNN（装了 sqlite-vec 才能用）；
# 没装 sqlite-vec 时用 Python 算余弦；嵌入模型不可用时退回关键词搜索，绝不崩。
# 注意：sqlite-vec 只是可选加速，不是必需——没装也能语义搜索（Python 算余弦），只是慢一点；
# 连嵌入模型都没有时才退回关键词搜索。向量表和关键词搜索互不干扰。

_vec_state = {"tried": False, "ok": False}

def _vec_extension_ok():
    """试着加载 sqlite-vec 扩展（只试一次，记下来）。返回 True/False，失败不抛错。"""
    if _vec_state["tried"]:
        return _vec_state["ok"]
    _vec_state["tried"] = True
    try:
        import sqlite_vec
        conn = _connect()
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.execute("SELECT vec_version()")
            _vec_state["ok"] = True
        finally:
            conn.close()
    except Exception as e:
        print(f"[语义搜索] sqlite-vec 扩展不可用（将用 Python 算余弦）：{e}", flush=True)
        _vec_state["ok"] = False
    return _vec_state["ok"]

def _ensure_vec_index():
    """建 vec0 虚拟表（只在 sqlite-vec 可用时）。返回 True 表示建好了。"""
    if not _vec_extension_ok():
        return False
    try:
        import sqlite_vec
        conn = _connect()
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec_index USING vec0(embedding float[%d])" % EMBED_DIM
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception:
        return False

def embed_text(text):
    """给一段文字算嵌入向量（Ollama 的 BGE-M3）。失败返回 None，绝不抛错。"""
    if not text or not str(text).strip():
        return None
    try:
        resp = ollama.embeddings(model=EMBED_MODEL, prompt=str(text).strip())
        vec = None
        if isinstance(resp, dict):
            vec = resp.get("embedding")
        elif resp is not None:
            vec = getattr(resp, "embedding", None)
        if not vec:
            return None
        return [float(x) for x in vec]
    except Exception as e:
        print(f"[语义搜索] 嵌入失败（{EMBED_MODEL}）：{e}。请确认 Ollama 已启动且已拉模型：ollama pull {EMBED_MODEL}", flush=True)
        return None

def _vec_to_blob(vec):
    return struct.pack("<%df" % len(vec), *vec)

def _blob_to_vec(blob):
    n = len(blob) // 4
    return list(struct.unpack("<%df" % n, blob))

def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)

def store_memory_vector(memory_id, text):
    """给一条记忆算向量并存进 memory_vectors（+ vec0 索引，如果可用）。失败返回 False。"""
    vec = embed_text(text)
    if not vec:
        return False
    dim = len(vec)
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO memory_vectors (memory_id, dim, vec) VALUES (?, ?, ?)",
            (memory_id, dim, _vec_to_blob(vec)),
        )
        conn.commit()
    finally:
        conn.close()
    # vec0 索引（可选加速；失败也不影响，BLOB 表里有数）
    if dim == EMBED_DIM and _ensure_vec_index():
        try:
            import sqlite_vec
            conn = _connect()
            try:
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO memory_vec_index (rowid, embedding) VALUES (?, ?)",
                    (memory_id, json.dumps(vec)),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass
    return True

def semantic_status():
    """语义搜索的状态：嵌入模型、已索引几条、sqlite-vec 能不能用。"""
    conn = _connect()
    n_vec = conn.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()[0]
    n_mem = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    conn.close()
    return {
        "embed_model": EMBED_MODEL,
        "indexed": n_vec,
        "memories": n_mem,
        "vec_extension": _vec_extension_ok(),
    }

def reindex_memory_vectors(limit=500):
    """给还没有向量的记忆补向量。返回 (成功数, 失败数)。失败不抛错。"""
    conn = _connect()
    rows = conn.execute(
        "SELECT m.id, m.content FROM memories m LEFT JOIN memory_vectors v ON v.memory_id = m.id WHERE v.memory_id IS NULL ORDER BY m.id LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    ok = 0
    fail = 0
    for mid, content in rows:
        try:
            if store_memory_vector(mid, content):
                ok += 1
            else:
                fail += 1
        except Exception:
            fail += 1
    return ok, fail

def _rows_by_ids(ids, scores):
    """按 id 列表读 memories，拼成带 score 的结果（保持 ids 的顺序）。"""
    if not ids:
        return []
    conn = _connect()
    try:
        ph = ",".join("?" * len(ids))
        mems = conn.execute(
            "SELECT id, kind, content, created_at FROM memories WHERE id IN (%s)" % ph,
            ids,
        ).fetchall()
    finally:
        conn.close()
    by_id = {m[0]: m for m in mems}
    out = []
    for mid in ids:
        m = by_id.get(mid)
        if m:
            out.append({"kind": m[1], "content": m[2], "created_at": m[3], "score": round(scores.get(mid, 0.0), 4)})
    return out

def search_memories_semantic(query, limit=20):
    """语义搜索记忆。返回 (rows, mode)，mode 是 'semantic' 或 'keyword_fallback'。
    rows 是 [{'kind','content','created_at','score'}]，score 是余弦相似度（0-1，越大越像）。"""
    vec = embed_text(query)
    if not vec:
        rows = search_memories_list(query, limit)
        return rows, "keyword_fallback"
    dim = len(vec)
    # 1) 优先用 vec0（sqlite-vec）做 KNN
    if dim == EMBED_DIM and _vec_extension_ok() and _ensure_vec_index():
        try:
            import sqlite_vec
            conn = _connect()
            try:
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                q = json.dumps(vec)
                hits = conn.execute(
                    "SELECT rowid, vec_distance_cosine(embedding, ?) AS d FROM memory_vec_index ORDER BY d LIMIT ?",
                    (q, limit),
                ).fetchall()
            finally:
                conn.close()
            if hits:
                ids = [h[0] for h in hits]
                scores = {h[0]: max(0.0, 1.0 - h[1]) for h in hits}
                return _rows_by_ids(ids, scores), "semantic"
        except Exception:
            pass
    # 2) 没走通 vec0 时，用 Python 算余弦（读 BLOB 表）
    conn = _connect()
    try:
        vecs = conn.execute("SELECT memory_id, dim, vec FROM memory_vectors").fetchall()
    finally:
        conn.close()
    scored = []
    for mid, d, blob in vecs:
        if d != dim:
            continue
        s = _cosine(vec, _blob_to_vec(blob))
        scored.append((s, mid))
    scored.sort(key=lambda t: t[0], reverse=True)
    top = scored[:limit]
    if not top:
        # 没有任何向量（还没索引过）：退回关键词
        return search_memories_list(query, limit), "keyword_fallback"
    ids = [mid for _, mid in top]
    scores = {mid: s for s, mid in top}
    return _rows_by_ids(ids, scores), "semantic"

# ==================== 1.6 v2.2 新增：技能包（skills/，格式学 anthropics/skills） ====================
# 技能 = "触发条件 + 操作手册 + 验收标准"。每个技能是一个目录里的 SKILL.md（frontmatter 写 name/description，
# 正文写何时使用 / 怎么做 / 验收标准）。内容是我们自己的（按低配版 Muse 的实际函数和接口写的），
# 只学了官方技能的格式，没抄它的文字。任务匹配到技能时，只加载那一个技能的步骤，不全塞进提示词。

_SKILL_KEYWORDS = [
    ("document-handling", ("pdf", "word", "excel", "ppt", "文档", "导入", "转成", "读一下")),
    ("scheduled-tasks", ("定时", "每天", "每周", "提醒", "到点")),
    ("self-check", ("检查", "审一遍", "质检", "对不对", "有没有错", "自我检查")),
    ("planning", ("计划", "任务", "目标", "步骤", "拆", "帮我做", "写一份", "完成")),
    ("file-approval", ("写文件", "保存成", "存成", "批准", "审批")),
]

def _parse_skill_frontmatter(text):
    """解析 SKILL.md 开头的 --- frontmatter，返回 {name, description}。没有就返回空。"""
    meta = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta

def list_skills():
    """列出 skills/ 下所有技能：[{name, description}]。目录不存在时返回空列表。"""
    out = []
    if not os.path.isdir(SKILLS_DIR):
        return out
    for name in sorted(os.listdir(SKILLS_DIR)):
        path = os.path.join(SKILLS_DIR, name, "SKILL.md")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            continue
        meta = _parse_skill_frontmatter(text)
        out.append({
            "name": meta.get("name") or name,
            "description": meta.get("description") or "",
        })
    return out

def get_skill(name):
    """读一个技能的 SKILL.md 全文。name 只允许小写字母、数字、横线，防路径穿越。"""
    if not name or not re.fullmatch(r"[a-z0-9-]+", str(name)):
        return None
    path = os.path.join(SKILLS_DIR, name, "SKILL.md")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return None
    meta = _parse_skill_frontmatter(text)
    return {"name": meta.get("name") or name, "description": meta.get("description") or "", "content": text}

def match_skill(text):
    """按关键词给一段任务匹配技能。返回技能名；没有具体技能匹配时返回 'chat'。"""
    if not text:
        return "chat"
    low = str(text).lower()
    for name, kws in _SKILL_KEYWORDS:
        if any(k in low for k in kws):
            return name
    return "chat"

def skill_prompt_block(name):
    """把一个技能的"怎么做"部分拼成提示词块（供 run_ai_agent 引用）。找不到返回 ''。"""
    sk = get_skill(name)
    if not sk:
        return ""
    text = sk["content"]
    m = re.search(r"## 怎么做\s*\n(.*?)(?=\n## |\Z)", text, re.S)
    steps = m.group(1).strip() if m else ""
    if not steps:
        return ""
    return f"\n【技能：{name}】{sk['description']}\n{steps}\n"

# ==================== 2. AI 推理：API（免费大模型）+ 本地（Ollama 小模型），自由选 ====================
def _pick_model(user_prompt, local_main, local_code):
    code_words = ("代码", "脚本", "写个", "函数", "修", "bug", "报错", "python", "py", "sql")
    if any(w in user_prompt.lower() for w in code_words):
        return local_code
    return local_main

def _chat_once_local(model, system_prompt, user_prompt):
    return ollama.chat(
        model=model,
        messages=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt}
        ],
        options={'num_thread': NUM_THREAD, 'num_ctx': NUM_CTX},
    )

def _api_chat_once(provider, api_key, model, system_prompt, user_prompt):
    info = API_PROVIDERS[provider]
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + api_key,
    }
    if provider == "openrouter":
        headers["HTTP-Referer"] = "http://127.0.0.1:8080"
        headers["X-Title"] = "LowSpec Muse"
    req = urllib.request.Request(info["url"], data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"API 返回 {e.code}：{body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"连不上 {info['label']}：{e.reason}（检查网络）")
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"API 返回格式不对：{str(data)[:300]}")

def call_model(system_prompt, user_prompt):
    """按当前模式调一次模型，返回 (text, model, source)。对话和任务共用。
    注意：规划、自我检查这类需要较强推理的调用，本地小模型效果有限，API 模式（70B）更合适。"""
    cfg = load_config()
    mode = cfg.get("mode", "auto")
    provider = cfg.get("provider", "groq")
    if provider not in API_PROVIDERS:
        provider = "groq"
    api_key = (cfg.get("api_key") or "").strip()
    api_model = (cfg.get("api_model") or "").strip() or API_PROVIDERS[provider]["default_model"]
    local_main = cfg.get("local_main") or LOCAL_MAIN
    local_code = cfg.get("local_code") or LOCAL_CODE

    def _local_reply():
        model = _pick_model(user_prompt, local_main, local_code)
        try:
            response = _chat_once_local(model, system_prompt, user_prompt)
        except Exception as e:
            if model != local_code:
                try:
                    response = _chat_once_local(local_code, system_prompt, user_prompt)
                    model = local_code
                except Exception as e2:
                    raise RuntimeError(f"请确认本地 Ollama 已启动（ollama serve）且已加载 {local_code} 模型。错误：{e2}")
            else:
                raise RuntimeError(f"请确认本地 Ollama 已启动（ollama serve）且已加载 {model} 模型。错误：{e}")
        return response['message']['content'], model, "本地"

    if mode == "local":
        return _local_reply()
    if mode == "api":
        if not api_key:
            raise RuntimeError("API 模式需要先在「设置」里填免费 API key（Groq 或 OpenRouter :free）。")
        return _api_chat_once(provider, api_key, api_model, system_prompt, user_prompt), api_model, "API"
    # auto：有 key 先试 API，API 不行自动切本地；没 key 直接本地
    if api_key:
        try:
            return _api_chat_once(provider, api_key, api_model, system_prompt, user_prompt), api_model, "API"
        except Exception as e:
            try:
                text, model, source = _local_reply()
                return text, model, "本地（API 失败后自动切换）"
            except Exception:
                raise RuntimeError(f"API 失败（{e}），本地也失败。请检查网络 / API key / Ollama。")
    return _local_reply()

def save_code_blocks(text):
    blocks = re.findall(r"```(?:python|py)?\n(.*?)```", text, re.S)
    if not blocks:
        return []
    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    saved = []
    for i, code in enumerate(blocks, 1):
        name = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{i}.py"
        path = os.path.join(SCRIPTS_DIR, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(code.strip() + "\n")
        saved.append(name)
    return saved

def run_ai_agent(user_prompt):
    history = get_recent_memories()
    # v2.2：匹配到具体技能时，把它的步骤附在提示词里（只附一个，不全塞）。
    skill = match_skill(user_prompt)
    skill_block = skill_prompt_block(skill) if skill and skill != "chat" else ""
    system_prompt = f"""你是低配版 Muse，一个跑在 2核8G 电脑上的个人 AI 助手，目标是让人人都能免费 AI 办公。
【历史记忆】
{history}
{skill_block}
请严格按以下格式输出你的响应：
思考: [分析用户的办公需求与边界]
推理: [拆解为低配电脑可执行的步骤]
决策/代码: [输出具体的Python脚本、分析结论或定时任务设置]
"""
    result, model, source = call_model(system_prompt, user_prompt)
    save_memory(result[:500], kind="ai")
    saved = save_code_blocks(result)
    return result, model, source, saved, skill

# ==================== 3. v2 新增：规划循环 ====================
def _parse_steps(text):
    """把模型的拆解结果解析成步骤列表。先试 JSON，失败则按行拆。"""
    try:
        data = json.loads(text.strip())
        if isinstance(data, list) and data:
            return [str(x).strip() for x in data if str(x).strip()][:8]
    except Exception:
        pass
    lines = []
    for line in text.splitlines():
        line = line.strip().lstrip("0123456789.-、）) ").strip()
        if line and len(line) > 2:
            lines.append(line)
    return lines[:8] or ["直接完成目标"]

def create_plan(goal):
    """把目标拆成 3-6 步，存成计划，返回 plan_id。"""
    system_prompt = "你是一个任务规划助手。用户给你一个办公目标，你把它拆成 3-6 个具体、可执行的步骤。只输出 JSON 数组，例如 [\"步骤一\", \"步骤二\"]，不要输出其他任何文字。"
    text, model, source = call_model(system_prompt, f"目标：{goal}")
    steps = _parse_steps(text)
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO plans (goal, status, created_at) VALUES (?, 'draft', ?)",
        (goal, datetime.now().strftime("%Y-%m-%d %H:%M")),
    )
    plan_id = cur.lastrowid
    for i, s in enumerate(steps):
        conn.execute(
            "INSERT INTO plan_steps (plan_id, idx, text, status) VALUES (?, ?, ?, 'pending')",
            (plan_id, i, s),
        )
    conn.commit()
    conn.close()
    save_memory(f"新建任务「{goal}」，拆成 {len(steps)} 步", kind="task")
    return plan_id

def get_plan(plan_id):
    conn = _connect()
    row = conn.execute("SELECT id, goal, status, created_at FROM plans WHERE id=?", (plan_id,)).fetchone()
    if not row:
        conn.close()
        return None
    steps = conn.execute(
        "SELECT idx, text, status, result FROM plan_steps WHERE plan_id=? ORDER BY idx", (plan_id,)
    ).fetchall()
    conn.close()
    return {
        "id": row[0], "goal": row[1], "status": row[2], "created_at": row[3],
        "steps": [{"idx": s[0], "text": s[1], "status": s[2], "result": s[3]} for s in steps],
    }

def list_plans(limit=10):
    conn = _connect()
    rows = conn.execute(
        "SELECT id, goal, status, created_at FROM plans ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [{"id": r[0], "goal": r[1], "status": r[2], "created_at": r[3]} for r in rows]

def run_plan_step(plan_id):
    """执行计划的下一个待执行步骤，返回 (plan, info)。info 含本步提案的文件。"""
    plan = get_plan(plan_id)
    if not plan:
        raise RuntimeError("任务不存在")
    pending = [s for s in plan["steps"] if s["status"] == "pending"]
    if not pending:
        return plan, None
    step = pending[0]
    done = [s for s in plan["steps"] if s["status"] == "done"]
    ctx = "\n".join(f"【已完成步骤 {s['idx']+1}】{s['text']}\n结果：{s['result'][:800]}" for s in done)
    history = get_recent_memories()
    system_prompt = f"""你是低配版 Muse，正在帮用户完成一个任务。
任务目标：{plan['goal']}
{ctx if ctx else '（这是第一步）'}
【历史记忆】
{history}

现在执行这一步：{step['text']}
直接给出这一步的结果。如果这一步需要保存成文件，用下面的格式输出（文件会先等用户批准才真正写入）：
[写文件: 文件名.txt]
文件内容
[/写文件]
文件名只能是英文、数字、下划线、横线加后缀（txt/md/csv/json/html/py），不要带路径。
"""
    text, model, source = call_model(system_prompt, f"执行步骤：{step['text']}")
    proposals = extract_file_proposals(text)
    conn = _connect()
    conn.execute(
        "UPDATE plan_steps SET status='done', result=? WHERE plan_id=? AND idx=?",
        (text[:4000], plan_id, step["idx"]),
    )
    for p in proposals:
        conn.execute(
            "INSERT INTO pending_files (plan_id, step_idx, filename, content, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
            (plan_id, step["idx"], p["filename"], p["content"], datetime.now().strftime("%Y-%m-%d %H:%M")),
        )
    remaining = conn.execute(
        "SELECT COUNT(*) FROM plan_steps WHERE plan_id=? AND status='pending'", (plan_id,)
    ).fetchone()[0]
    if remaining == 0:
        conn.execute("UPDATE plans SET status='done' WHERE id=?", (plan_id,))
    else:
        conn.execute("UPDATE plans SET status='running' WHERE id=?", (plan_id,))
    conn.commit()
    conn.close()
    save_memory(f"任务「{plan['goal']}」步骤 {step['idx']+1} 完成：{text[:200]}", kind="task")
    return get_plan(plan_id), {"step_idx": step["idx"], "proposals": proposals, "model": model, "source": source}

def reflect_plan(plan_id):
    """自我检查：审一遍任务全部结果，只审一次。返回 (text, model, source)。"""
    plan = get_plan(plan_id)
    if not plan:
        raise RuntimeError("任务不存在")
    all_results = "\n\n".join(
        f"步骤 {s['idx']+1}（{s['text']}）：\n{s['result'][:1500]}" for s in plan["steps"]
    )
    system_prompt = "你是一个质检员。检查下面这个任务的全部结果：有没有错漏、前后矛盾、没做完的地方。只检查一遍，给出结论：通过，或者列出要改的地方（每条一句话）。"
    text, model, source = call_model(system_prompt, f"任务目标：{plan['goal']}\n\n{all_results}")
    save_memory(f"任务「{plan['goal']}」自我检查：{text[:300]}", kind="task")
    return text, model, source

# ==================== 4. v2 新增：文件审批 ====================
def safe_workspace_name(name):
    """只允许 workspace/ 下的直接文件，防路径穿越。后缀限 txt/md/csv/json/html/py。"""
    if not name:
        return None
    name = name.strip().replace("\\", "/")
    if "/" in name or name in (".", "..") or name.startswith("."):
        return None
    if "." not in name:
        return None
    ext = name.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTS:
        return None
    return name

def extract_file_proposals(text):
    """从模型输出里提取 [写文件: 文件名] ... [/写文件] 块。"""
    out = []
    for m in re.finditer(r"\[写文件:\s*([^\]]+)\]\s*\n(.*?)\[/写文件\]", text, re.S):
        fname = m.group(1).strip()
        content = m.group(2).strip("\n")
        safe = safe_workspace_name(fname)
        if safe and content:
            out.append({"filename": safe, "content": content})
    return out

def list_pending_files():
    conn = _connect()
    rows = conn.execute(
        "SELECT id, plan_id, step_idx, filename, content, status, created_at FROM pending_files WHERE status='pending' ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [{"id": r[0], "plan_id": r[1], "step_idx": r[2], "filename": r[3], "content": r[4], "status": r[5], "created_at": r[6]} for r in rows]

def approve_pending(pid):
    conn = _connect()
    row = conn.execute("SELECT filename, content FROM pending_files WHERE id=? AND status='pending'", (pid,)).fetchone()
    if not row:
        conn.close()
        return False, "这条审批不存在或已处理"
    fname, content = row
    safe = safe_workspace_name(fname)
    if not safe:
        conn.close()
        return False, "文件名不合法"
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        conn.close()
        return False, "文件超过 1MB，不写"
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    path = os.path.join(WORKSPACE_DIR, safe)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    conn.execute("UPDATE pending_files SET status='approved' WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return True, f"已写入 workspace/{safe}"

def reject_pending(pid):
    conn = _connect()
    cur = conn.execute("UPDATE pending_files SET status='rejected' WHERE id=? AND status='pending'", (pid,))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return (True, "已拒绝") if changed else (False, "这条审批不存在或已处理")

def list_workspace_files():
    if not os.path.isdir(WORKSPACE_DIR):
        return []
    out = []
    for name in sorted(os.listdir(WORKSPACE_DIR), reverse=True):
        path = os.path.join(WORKSPACE_DIR, name)
        if os.path.isfile(path):
            out.append({"name": name, "size": os.path.getsize(path)})
    return out

def read_workspace_file(name):
    safe = safe_workspace_name(name)
    if not safe:
        return None
    path = os.path.join(WORKSPACE_DIR, safe)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

# ==================== 4.5 v2.2 新增：文档导入（转 Markdown，先审批再进 workspace/） ====================
# 做法：用户把文档放进 inbox/，调 /api/ingest 转成 Markdown；转换结果先存进待审批（pending_files），
# 批准后才写进 workspace/——和模型写文件走同一条审批，没有例外。
# 转换优先用 markitdown（装了 markitdown[docx,xlsx,pdf] 才能读 docx/xlsx/pdf）；
# 没装 markitdown 时用内置转换器，只读 txt/md/csv/json/html，读不了的格式明确报错。

def _html_text(s):
    import html as _html
    s = re.sub(r"(?i)<[^>]+>", "", s)
    return _html.unescape(s).strip()

def _builtin_to_markdown(path, ext):
    """内置转换器（不依赖任何第三方包）。返回 Markdown 文本；不支持的格式返回 None。"""
    if ext in ("txt", "md"):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    if ext == "csv":
        import csv as _csv
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(_csv.reader(f))
        if not rows:
            return ""
        out = []
        for i, row in enumerate(rows):
            out.append("| " + " | ".join(c.strip() for c in row) + " |")
            if i == 0:
                out.append("| " + " | ".join("---" for _ in row) + " |")
        return "\n".join(out) + "\n"
    if ext == "json":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return "```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```\n"
    if ext in ("html", "htm"):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            html = f.read()
        # 极简 HTML→Markdown：标题、段落、列表、加粗/斜体；其余标签去掉。
        html = re.sub(r"(?i)<h1[^>]*>(.*?)</h1>", lambda m: "\n# " + _html_text(m.group(1)) + "\n", html, flags=re.S)
        html = re.sub(r"(?i)<h2[^>]*>(.*?)</h2>", lambda m: "\n## " + _html_text(m.group(1)) + "\n", html, flags=re.S)
        html = re.sub(r"(?i)<h3[^>]*>(.*?)</h3>", lambda m: "\n### " + _html_text(m.group(1)) + "\n", html, flags=re.S)
        html = re.sub(r"(?i)<li[^>]*>(.*?)</li>", lambda m: "\n- " + _html_text(m.group(1)), html, flags=re.S)
        html = re.sub(r"(?i)<p[^>]*>(.*?)</p>", lambda m: "\n" + _html_text(m.group(1)) + "\n", html, flags=re.S)
        html = re.sub(r"(?i)<(strong|b)[^>]*>(.*?)</\1>", lambda m: "**" + _html_text(m.group(2)) + "**", html, flags=re.S)
        html = re.sub(r"(?i)<(em|i)[^>]*>(.*?)</\1>", lambda m: "*" + _html_text(m.group(2)) + "*", html, flags=re.S)
        html = re.sub(r"(?i)<br\s*/?>", "\n", html)
        text = _html_text(html)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text + "\n" if text else ""
    return None

def convert_to_markdown(path):
    """把一个文档转成 Markdown。返回 (text, converter, error)。
    converter 是 'markitdown' 或 'builtin'；失败时 text 为 None，error 是原因。"""
    if not os.path.isfile(path):
        return None, None, "文件不存在"
    ext = path.rsplit(".", 1)[-1].lower() if "." in os.path.basename(path) else ""
    if ext not in INGEST_EXTS:
        return None, None, f"不支持的格式 .{ext}（支持：{', '.join(sorted(INGEST_EXTS))}）"
    # 1) 优先用 markitdown
    md_available = False
    try:
        from markitdown import MarkItDown
        md_available = True
        result = MarkItDown().convert(path)
        text = (result.text_content or "").strip()
        if text:
            return text + "\n", "markitdown", None
    except Exception:
        pass
    # 2) markitdown 没装或没读出内容时，用内置转换器
    try:
        text = _builtin_to_markdown(path, ext)
    except Exception as e:
        return None, None, f"转换失败：{e}"
    if text is None:
        if md_available:
            return None, None, "转换后内容为空（markitdown 已装但没读出内容，可能是空文档）"
        return None, None, "这种格式需要装 markitdown 才能读：pip install \"markitdown[docx,xlsx,pdf]\""
    if not text.strip():
        return None, None, "转换后内容为空"
    return text, "builtin", None

def _safe_ingest_name(filename):
    """收件箱文件名：只允许直接文件名（不带路径），后缀在 INGEST_EXTS 里。"""
    if not filename:
        return None
    name = str(filename).strip().replace("\\", "/")
    if "/" in name or name in (".", "..") or name.startswith("."):
        return None
    if "." not in name:
        return None
    ext = name.rsplit(".", 1)[-1].lower()
    if ext not in INGEST_EXTS:
        return None
    return name

def list_inbox_files():
    """列出 inbox/ 里可导入的文档。"""
    if not os.path.isdir(INBOX_DIR):
        return []
    out = []
    for name in sorted(os.listdir(INBOX_DIR), reverse=True):
        path = os.path.join(INBOX_DIR, name)
        if not os.path.isfile(path):
            continue
        if _safe_ingest_name(name) is None:
            continue
        out.append({"name": name, "size": os.path.getsize(path)})
    return out

def ingest_document(filename):
    """把 inbox/ 里的一个文档转成 Markdown，存进待审批。返回 (ok, info)。
    ok 时 info 是 {'proposal_id','out_name','converter'}；失败时 info 是错误原因字符串。
    注意：这里只建待审批，不写 workspace/——批准后才写（和模型写文件同一条审批）。"""
    safe = _safe_ingest_name(filename)
    if not safe:
        return False, "文件名不合法或格式不支持"
    path = os.path.join(INBOX_DIR, safe)
    if not os.path.isfile(path):
        return False, "收件箱里没有这个文件"
    text, converter, error = convert_to_markdown(path)
    if error:
        return False, error
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        return False, "转换后超过 1MB，不导入（批准也写不进去）"
    stem = safe.rsplit(".", 1)[0]
    out_name = safe_workspace_name(stem + ".md")
    if not out_name:
        return False, "输出文件名不合法"
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO pending_files (plan_id, step_idx, filename, content, status, created_at) VALUES (0, -1, ?, ?, 'pending', ?)",
        (out_name, text, datetime.now().strftime("%Y-%m-%d %H:%M")),
    )
    pid = cur.lastrowid
    conn.commit()
    conn.close()
    return True, {"proposal_id": pid, "out_name": out_name, "converter": converter}

# ==================== 5. 定时任务 (和 v1 同一套) ====================
def run_scheduled_task(name, prompt):
    print(f"\n[定时任务「{name}」开始执行]", flush=True)
    try:
        result, model, source, saved, _skill = run_ai_agent(prompt)
    except Exception as e:
        result = f"定时任务「{name}」执行失败：{e}"
    save_memory(f"定时任务「{name}」结果：{result[:300]}", kind="task")
    conn = _connect()
    conn.execute("UPDATE tasks SET last_run = ? WHERE name = ?",
                 (datetime.now().strftime("%Y-%m-%d %H:%M"), name))
    conn.commit()
    conn.close()
    print(f"[定时任务「{name}」执行完毕]", flush=True)

def add_task(name, time_str, prompt):
    try:
        time.strptime(time_str, "%H:%M")
    except ValueError:
        return False, "时间格式不对，用 HH:MM，例如 21:30。"
    conn = _connect()
    conn.execute("INSERT INTO tasks (name, time_str, prompt) VALUES (?, ?, ?)", (name, time_str, prompt))
    conn.commit()
    conn.close()
    schedule.every().day.at(time_str).do(run_scheduled_task, name, prompt)
    return True, f"已加定时任务「{name}」，每天 {time_str} 执行。"

def load_tasks():
    conn = _connect()
    rows = conn.execute("SELECT name, time_str, prompt FROM tasks WHERE enabled = 1").fetchall()
    conn.close()
    for name, time_str, prompt in rows:
        schedule.every().day.at(time_str).do(run_scheduled_task, name, prompt)
    return len(rows)

def list_tasks_list():
    conn = _connect()
    rows = conn.execute("SELECT name, time_str, prompt, last_run FROM tasks WHERE enabled = 1").fetchall()
    conn.close()
    return [{"name": n, "time": t, "prompt": p, "last_run": r or ""} for n, t, p, r in rows]

def list_scripts():
    if not os.path.isdir(SCRIPTS_DIR):
        return []
    out = []
    for name in sorted(os.listdir(SCRIPTS_DIR), reverse=True):
        if name.endswith(".py"):
            path = os.path.join(SCRIPTS_DIR, name)
            out.append({"name": name, "size": os.path.getsize(path)})
    return out

def background_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

# ==================== 6. 网页服务 ====================
def _v1_status_payload():
    """给 GET /v1/status 用的状态（和 /api/status 同一份数据）。"""
    cfg = load_config()
    conn = _connect()
    n_mem = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    n_task = conn.execute("SELECT COUNT(*) FROM tasks WHERE enabled=1").fetchone()[0]
    n_plan = conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
    n_pend = conn.execute("SELECT COUNT(*) FROM pending_files WHERE status='pending'").fetchone()[0]
    conn.close()
    try:
        sem = semantic_status()
    except Exception:
        sem = {"embed_model": EMBED_MODEL, "indexed": 0, "memories": n_mem, "vec_extension": False}
    return {
        "ok": True, "app": "低配版 Muse",
        "memories": n_mem, "tasks": n_task, "plans": n_plan, "pending": n_pend,
        "mode": cfg.get("mode", "auto"),
        "provider": cfg.get("provider", "groq"),
        "api_configured": bool((cfg.get("api_key") or "").strip()),
        "semantic": sem,
    }


def _v1_models_list():
    """已配置的模型列表（API 默认模型 + 两个本地模型，去重）。"""
    cfg = load_config()
    provider = cfg.get("provider", "groq")
    if provider not in API_PROVIDERS:
        provider = "groq"
    api_model = (cfg.get("api_model") or "").strip() or API_PROVIDERS[provider]["default_model"]
    ids = []
    for m in (api_model, (cfg.get("local_main") or "").strip() or LOCAL_MAIN,
              (cfg.get("local_code") or "").strip() or LOCAL_CODE):
        if m and m not in ids:
            ids.append(m)
    now = int(time.time())
    return [{"id": m, "object": "model", "created": now, "owned_by": "lowspec"} for m in ids]


def _v1_estimate_tokens(text):
    """粗略估算 token 数（约 4 个字符 1 个 token，仅供 usage 字段参考）。"""
    return max(1, len(text or "") // 4)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, body, ctype):
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(BASE_DIR, "index.txt"), "r", encoding="utf-8") as f:
                    self._send_text(f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
        elif path == "/api/status":
            cfg = load_config()
            conn = _connect()
            n_mem = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            n_task = conn.execute("SELECT COUNT(*) FROM tasks WHERE enabled=1").fetchone()[0]
            n_plan = conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
            n_pend = conn.execute("SELECT COUNT(*) FROM pending_files WHERE status='pending'").fetchone()[0]
            conn.close()
            try:
                sem = semantic_status()
            except Exception:
                sem = {"embed_model": EMBED_MODEL, "indexed": 0, "memories": n_mem, "vec_extension": False}
            self._send_json({
                "ok": True, "app": "低配版 Muse",
                "memories": n_mem, "tasks": n_task, "plans": n_plan, "pending": n_pend,
                "mode": cfg.get("mode", "auto"),
                "provider": cfg.get("provider", "groq"),
                "api_configured": bool((cfg.get("api_key") or "").strip()),
                "semantic": sem,
            })
        elif path == "/api/config":
            cfg = load_config()
            self._send_json({
                "ok": True, "config": cfg,
                "providers": {k: {"label": v["label"], "default_model": v["default_model"], "signup": v["signup"]} for k, v in API_PROVIDERS.items()},
            })
        elif path == "/api/memories":
            self._send_json({"ok": True, "memories": get_memories_list(20)})
        elif path == "/api/search":
            q = (qs.get("q") or [""])[0].strip()
            mode = (qs.get("mode") or ["keyword"])[0].strip().lower()
            if not q:
                self._send_json({"ok": True, "memories": []})
            elif mode == "semantic":
                rows, used = search_memories_semantic(q, 20)
                self._send_json({"ok": True, "memories": rows, "mode": used, "semantic_available": used == "semantic"})
            elif mode == "hybrid":
                kw = search_memories_list(q, 20)
                sem, used = search_memories_semantic(q, 20)
                seen = set()
                merged = []
                for m in sem + kw:
                    key = (m["kind"], m["content"][:120])
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(m)
                self._send_json({"ok": True, "memories": merged[:20], "mode": "hybrid" if used == "semantic" else "keyword_fallback", "semantic_available": used == "semantic"})
            else:
                self._send_json({"ok": True, "memories": search_memories_list(q, 20)})
        elif path == "/api/tasks":
            self._send_json({"ok": True, "tasks": list_tasks_list()})
        elif path == "/api/scripts":
            self._send_json({"ok": True, "scripts": list_scripts()})
        elif path == "/api/script":
            name = (qs.get("name") or [""])[0]
            safe = os.path.basename(name)
            fpath = os.path.join(SCRIPTS_DIR, safe)
            if not safe.endswith(".py") or not os.path.isfile(fpath):
                self._send_json({"ok": False, "error": "文件不存在"}, status=404)
            else:
                with open(fpath, "r", encoding="utf-8") as f:
                    self._send_json({"ok": True, "name": safe, "content": f.read()})
        elif path == "/api/plans":
            self._send_json({"ok": True, "plans": list_plans(10)})
        elif path == "/api/plan":
            pid = (qs.get("id") or [""])[0]
            try:
                plan = get_plan(int(pid))
            except ValueError:
                plan = None
            if not plan:
                self._send_json({"ok": False, "error": "任务不存在"}, status=404)
            else:
                self._send_json({"ok": True, "plan": plan})
        elif path == "/api/pending":
            self._send_json({"ok": True, "pending": list_pending_files()})
        elif path == "/api/files":
            self._send_json({"ok": True, "files": list_workspace_files()})
        elif path == "/api/file":
            name = (qs.get("name") or [""])[0]
            content = read_workspace_file(name)
            if content is None:
                self._send_json({"ok": False, "error": "文件不存在或不允许读"}, status=404)
            else:
                self._send_json({"ok": True, "name": safe_workspace_name(name), "content": content})
        elif path == "/api/skills":
            self._send_json({"ok": True, "skills": list_skills()})
        elif path == "/api/skill":
            name = (qs.get("name") or [""])[0].strip()
            sk = get_skill(name)
            if not sk:
                self._send_json({"ok": False, "error": "技能不存在"}, status=404)
            else:
                self._send_json({"ok": True, "skill": sk})
        elif path == "/api/skill/match":
            q = (qs.get("q") or [""])[0].strip()
            self._send_json({"ok": True, "skill": match_skill(q)})
        elif path == "/api/inbox":
            self._send_json({"ok": True, "files": list_inbox_files()})
        elif path == "/v1/models":
            self._send_json({"object": "list", "data": _v1_models_list()})
        elif path == "/v1/status":
            self._send_json(_v1_status_payload())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/chat":
            data = self._read_json()
            prompt = (data.get("prompt") or "").strip()
            if not prompt:
                self._send_json({"ok": False, "error": "请输入内容"}, status=400)
                return
            save_memory(prompt, kind="user")
            try:
                reply, model, source, saved, skill = run_ai_agent(prompt)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
                return
            self._send_json({"ok": True, "reply": reply, "model": model, "source": source, "saved_scripts": saved, "skill": skill})
        elif path == "/api/task":
            data = self._read_json()
            time_str = (data.get("time") or "").strip()
            prompt = (data.get("prompt") or "").strip()
            if not time_str or not prompt:
                self._send_json({"ok": False, "error": "时间和内容都要填"}, status=400)
                return
            name = prompt[:20]
            ok, msg = add_task(name, time_str, prompt)
            self._send_json({"ok": ok, "message": msg}, status=200 if ok else 400)
        elif path == "/api/config":
            data = self._read_json()
            cfg = load_config()
            if "mode" in data and data["mode"] in ("auto", "api", "local"):
                cfg["mode"] = data["mode"]
            if "provider" in data and data["provider"] in API_PROVIDERS:
                cfg["provider"] = data["provider"]
            if "api_key" in data:
                cfg["api_key"] = str(data["api_key"] or "").strip()
            if "api_model" in data:
                cfg["api_model"] = str(data["api_model"] or "").strip()
            if "local_main" in data and str(data["local_main"]).strip():
                cfg["local_main"] = str(data["local_main"]).strip()
            if "local_code" in data and str(data["local_code"]).strip():
                cfg["local_code"] = str(data["local_code"]).strip()
            save_config(cfg)
            self._send_json({"ok": True, "message": "设置已保存", "config": cfg})
        elif path == "/api/plan":
            data = self._read_json()
            goal = (data.get("goal") or "").strip()
            if not goal:
                self._send_json({"ok": False, "error": "请先写目标"}, status=400)
                return
            try:
                pid = create_plan(goal)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
                return
            self._send_json({"ok": True, "plan_id": pid, "plan": get_plan(pid), "skill": "planning"})
        elif path == "/api/plan/step":
            data = self._read_json()
            try:
                pid = int(data.get("plan_id") or 0)
            except ValueError:
                pid = 0
            if not pid:
                self._send_json({"ok": False, "error": "任务不存在"}, status=400)
                return
            try:
                plan, info = run_plan_step(pid)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
                return
            self._send_json({"ok": True, "plan": plan, "step": info})
        elif path == "/api/plan/reflect":
            data = self._read_json()
            try:
                pid = int(data.get("plan_id") or 0)
            except ValueError:
                pid = 0
            if not pid:
                self._send_json({"ok": False, "error": "任务不存在"}, status=400)
                return
            try:
                text, model, source = reflect_plan(pid)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
                return
            self._send_json({"ok": True, "reflection": text, "model": model, "source": source})
        elif path == "/api/pending/approve":
            data = self._read_json()
            try:
                pid = int(data.get("id") or 0)
            except ValueError:
                pid = 0
            ok, msg = approve_pending(pid)
            self._send_json({"ok": ok, "message": msg}, status=200 if ok else 400)
        elif path == "/api/pending/reject":
            data = self._read_json()
            try:
                pid = int(data.get("id") or 0)
            except ValueError:
                pid = 0
            ok, msg = reject_pending(pid)
            self._send_json({"ok": ok, "message": msg}, status=200 if ok else 400)
        elif path == "/api/memories/reindex":
            try:
                ok_n, fail_n = reindex_memory_vectors()
                self._send_json({"ok": True, "indexed": ok_n, "failed": fail_n, "message": f"已补 {ok_n} 条向量，失败 {fail_n} 条（嵌入模型没装时会全失败，属正常）"})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
        elif path == "/api/ingest":
            data = self._read_json()
            fname = (data.get("filename") or "").strip()
            if not fname:
                self._send_json({"ok": False, "error": "请先选一个收件箱里的文档"}, status=400)
                return
            ok, info = ingest_document(fname)
            if ok:
                self._send_json({"ok": True, "proposal_id": info["proposal_id"], "out_name": info["out_name"], "converter": info["converter"], "message": f"已转成 {info['out_name']}，请去待审批里批准"})
            else:
                self._send_json({"ok": False, "error": info}, status=400)
        elif path == "/v1/chat/completions":
            # 标准对话接口（OpenAI 兼容，本机回环；做法学 PolyStrata，只学接口）。
            # 和网页 /api/chat 走同一条路：run_ai_agent（记忆、技能、规划格式、代码块保存、文件审批全都不变），
            # 不另开执行路径。请求里的 model 字段只作参考，实际用的永远是本机配置好的模型。
            data = self._read_json()
            messages = data.get("messages")
            if not isinstance(messages, list) or not messages:
                self._send_json({"error": {"message": "messages 必须是非空数组", "type": "invalid_request_error", "code": "messages_required"}}, status=400)
                return
            if data.get("stream"):
                self._send_json({"error": {"message": "暂不支持流式返回（stream），请用非流式请求", "type": "invalid_request_error", "code": "stream_not_supported"}}, status=400)
                return
            user_texts = [str(m.get("content", "")) for m in messages
                          if isinstance(m, dict) and m.get("role") == "user" and str(m.get("content", "")).strip()]
            if not user_texts:
                self._send_json({"error": {"message": "messages 里需要至少一条 user 消息", "type": "invalid_request_error", "code": "user_message_required"}}, status=400)
                return
            prompt = user_texts[-1]
            save_memory(prompt, kind="user")
            try:
                reply, model, source, saved, skill = run_ai_agent(prompt)
            except Exception as e:
                self._send_json({"error": {"message": str(e), "type": "server_error", "code": "model_error"}}, status=500)
                return
            now = int(time.time())
            self._send_json({
                "id": "chatcmpl-lowspec-" + datetime.now().strftime("%Y%m%d%H%M%S"),
                "object": "chat.completion",
                "created": now,
                "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": _v1_estimate_tokens(prompt),
                    "completion_tokens": _v1_estimate_tokens(reply),
                    "total_tokens": _v1_estimate_tokens(prompt) + _v1_estimate_tokens(reply),
                },
                "lowspec": {"source": source, "skill": skill, "saved_scripts": saved},
            })
        else:
            self.send_response(404)
            self.end_headers()

# ==================== 7. 启动 ====================
if __name__ == "__main__":
    init_db()
    n = load_tasks()
    t = threading.Thread(target=background_scheduler, daemon=True)
    t.start()
    cfg = load_config()
    print("==================================================")
    print("  低配版 Muse v2.2 已启动（人人都能免费 AI 办公）")
    print("  v2.2 新增：记忆语义搜索 + 技能包 + 文档转 Markdown 导入 + 标准对话接口 /v1/chat/completions")
    print("  标准对话接口：POST /v1/chat/completions（本机 127.0.0.1:8080；另有 GET /v1/models、/v1/status）")
    print(f"  模型模式：{cfg.get('mode', 'auto')}；API 服务商：{cfg.get('provider', 'groq')}；API key：{'已填' if (cfg.get('api_key') or '').strip() else '未填（用本地）'}")
    print(f"  已恢复 {n} 个定时任务；记忆库 {DB_NAME}")
    print(f"  浏览器打开：http://{HOST}:{PORT}")
    print("  按 Ctrl+C 退出")
    print("==================================================")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
