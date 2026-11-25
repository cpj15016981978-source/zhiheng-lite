# 低配版 Muse v2 — 在 v1 基础上加三个能力：规划循环、文件审批、自我检查
# 目标不变：人人都能免费 AI 办公。2核8G电脑能跑；联网用免费大模型（API）是主线，断网用本地小模型（Ollama）是备选。
# v2 新增：
#   1. 规划循环（任务页）：给一个目标，模型先拆成 3-6 步，展示计划，一步步执行，每步结果都记下来。
#   2. 文件操作（加审批）：模型想写文件时，先放进“待审批”，你点了批准才真正写进 workspace/ 文件夹；不批准就不写。
#      只允许 workspace/ 下的直接文件，后缀限 txt/md/csv/json/html/py，单个文件不超过 1MB。
#   3. 自我检查：任务做完后，点一下“自我检查”，模型自己审一遍结果，只审一次，给出“通过”或要改的地方。
# 不做的：MCP（太重）、记忆分层（现有 SQLite 记忆够用）。保持轻、免费、老电脑能跑。
# 依赖：pip install ollama schedule；Ollama 须启动（本地模式用）；API 模式需要免费 API key（Groq / OpenRouter :free），填在设置里，存在本机 muse_config.json，不上传。
# 只有 Python 标准库做 Web 服务；API 模式才联网（只连你选的模型服务），其余流量只在你自己电脑里。
# 前端页面存在 index.txt 里（内容是 HTML），由 app.py 读出来当网页发给浏览器。

import os
import re
import json
import sqlite3
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

LOCAL_CODE = "qwen2.5-coder:3b"
LOCAL_MAIN = os.environ.get("OFFICE_MAIN_MODEL", "qwen3:4b")
NUM_THREAD = 2
NUM_CTX = 4096
HOST = "127.0.0.1"
PORT = 8080

# 文件审批：只允许这些后缀，单个文件不超过 1MB
ALLOWED_EXTS = {"txt", "md", "csv", "json", "html", "py"}
MAX_FILE_BYTES = 1024 * 1024

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
    conn.commit()
    conn.close()

def save_memory(info, kind="user"):
    if not info or not str(info).strip():
        return
    conn = _connect()
    conn.execute(
        "INSERT INTO memories (kind, content, created_at) VALUES (?, ?, ?)",
        (kind, str(info).strip(), datetime.now().strftime("%Y-%m-%d %H:%M")),
    )
    conn.commit()
    conn.close()

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
    system_prompt = f"""你是低配版 Muse，一个跑在 2核8G 电脑上的个人 AI 助手，目标是让人人都能免费 AI 办公。
【历史记忆】
{history}

请严格按以下格式输出你的响应：
思考: [分析用户的办公需求与边界]
推理: [拆解为低配电脑可执行的步骤]
决策/代码: [输出具体的Python脚本、分析结论或定时任务设置]
"""
    result, model, source = call_model(system_prompt, user_prompt)
    save_memory(result[:500], kind="ai")
    saved = save_code_blocks(result)
    return result, model, source, saved

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

# ==================== 5. 定时任务 (和 v1 同一套) ====================
def run_scheduled_task(name, prompt):
    print(f"\n[定时任务「{name}」开始执行]", flush=True)
    try:
        result, model, source, saved = run_ai_agent(prompt)
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
            self._send_json({
                "ok": True, "app": "低配版 Muse",
                "memories": n_mem, "tasks": n_task, "plans": n_plan, "pending": n_pend,
                "mode": cfg.get("mode", "auto"),
                "provider": cfg.get("provider", "groq"),
                "api_configured": bool((cfg.get("api_key") or "").strip()),
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
            self._send_json({"ok": True, "memories": search_memories_list(q, 20) if q else []})
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
                reply, model, source, saved = run_ai_agent(prompt)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
                return
            self._send_json({"ok": True, "reply": reply, "model": model, "source": source, "saved_scripts": saved})
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
            self._send_json({"ok": True, "plan_id": pid, "plan": get_plan(pid)})
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
    print("  低配版 Muse v2 已启动（人人都能免费 AI 办公）")
    print("  v2 新增：规划循环（任务页）+ 文件审批 + 自我检查")
    print(f"  模型模式：{cfg.get('mode', 'auto')}；API 服务商：{cfg.get('provider', 'groq')}；API key：{'已填' if (cfg.get('api_key') or '').strip() else '未填（用本地）'}")
    print(f"  已恢复 {n} 个定时任务；记忆库 {DB_NAME}")
    print(f"  浏览器打开：http://{HOST}:{PORT}")
    print("  按 Ctrl+C 退出")
    print("==================================================")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
