# LowSpec Muse v2.2 桩测试（stub tests）
# 跑法（两种环境都要跑）：
#   1) 带 markitdown + sqlite-vec 的 Python（例如 /tmp/v22-venv/bin/python tests/test_v22.py），
#      且要先 unset 全部代理变量（否则 import ollama 会崩，这是本机代理环境的问题，Windows 上没有）。
#   2) 不带 markitdown / sqlite-vec 的 Python（例如本机 ml-venv），验证降级路径。
# 原理：只 stub 掉网络（ollama.embeddings 用假向量、call_model 不测），其余全是真实代码；
# 数据库、workspace、inbox 都用临时目录，不碰真实文件。
# 注意：本文件不依赖 pytest，直接跑 python tests/test_v22.py 即可。

import os
import sys
import json
import sqlite3
import tempfile
import shutil
import zipfile

# --- 先清掉代理变量（否则 import ollama 会崩；本机代理环境的问题） ---
for _v in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY", "no_proxy", "NO_PROXY"):
    os.environ.pop(_v, None)

# --- ollama / schedule 没装时给桩（只影响 import，测试里不调真模型） ---
try:
    import ollama  # noqa: F401
    import schedule  # noqa: F401
    _HAS_OLLAMA_PKG = True
except ImportError:
    _HAS_OLLAMA_PKG = False
    import types

    _ollama_stub = types.ModuleType("ollama")

    def _no_ollama(*a, **k):
        raise RuntimeError("ollama not installed (stub)")

    _ollama_stub.chat = _no_ollama
    _ollama_stub.embeddings = _no_ollama
    _ollama_stub.embed = _no_ollama
    sys.modules["ollama"] = _ollama_stub

    _sched_stub = types.ModuleType("schedule")

    class _Every:
        @property
        def day(self):
            return self

        def at(self, t):
            return self

        def do(self, *a, **k):
            return None

    _sched_stub.every = lambda: _Every()
    _sched_stub.run_pending = lambda: None
    sys.modules["schedule"] = _sched_stub

try:
    import sqlite_vec  # noqa: F401
    HAS_VEC = True
except ImportError:
    HAS_VEC = False

try:
    import markitdown  # noqa: F401
    HAS_MD = True
except ImportError:
    HAS_MD = False

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import app as L  # noqa: E402

# ============ 测试框架 ============
PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  PASS {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name} {detail}")


def _fake_embed(text):
    """假嵌入：1024 维，按关键词在固定维度上放信号（猫/狗/日报/周报/会议）。"""
    v = [0.0] * 1024
    t = (text or "").lower()
    if "猫" in t or "cat" in t:
        v[0] = 1.0
    if "狗" in t or "dog" in t:
        v[1] = 1.0
    if "日报" in t or "报告" in t:
        v[2] = 1.0
    if "周报" in t:
        v[2] = 0.9
        v[3] = 0.5
    if "会议" in t:
        v[4] = 1.0
    v[10] = 0.1
    return v


def _make_docx(path):
    ct = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    doc = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>季度总结</w:t></w:r></w:p><w:p><w:r><w:t>本季度完成了三个项目，收入增长 20%。</w:t></w:r></w:p></w:body></w:document>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)


# ============ 准备临时环境 ============
TMP = tempfile.mkdtemp(prefix="lowspec-v22-test-")
L.DB_NAME = os.path.join(TMP, "test.db")
L.WORKSPACE_DIR = os.path.join(TMP, "workspace")
L.INBOX_DIR = os.path.join(TMP, "inbox")
L.SCRIPTS_DIR = os.path.join(TMP, "scripts")
L.CONFIG_PATH = os.path.join(TMP, "muse_config.json")
os.makedirs(L.INBOX_DIR, exist_ok=True)
# SKILLS_DIR 保持指向真实的 v2.2 skills/ 目录（测真实 SKILL.md 文件）
print(f"TMP={TMP}")
print(f"HAS_VEC(sqlite-vec)={HAS_VEC}  HAS_MD(markitdown)={HAS_MD}  HAS_OLLAMA_PKG={_HAS_OLLAMA_PKG}")

_real_embed_text = L.embed_text
_real_vec_ok = L._vec_extension_ok
L.embed_text = _fake_embed

L.init_db()

# ============ 1. 语义搜索 ============
print("\n[1] 语义搜索")
conn = sqlite3.connect(L.DB_NAME)
has_vec_table = conn.execute(
    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='memory_vectors'"
).fetchone()[0] == 1
conn.close()
check("1.1 init_db 建出 memory_vectors 表", has_vec_table)

# 存几条记忆（用假向量）
L.save_memory("我家养了一只白猫，叫雪球", kind="user")
L.save_memory("邻居家的狗很吵", kind="user")
L.save_memory("今天写了日报，汇报了三个项目", kind="user")
L.save_memory("下周要开会讨论周报", kind="user")
conn = sqlite3.connect(L.DB_NAME)
n_vec = conn.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()[0]
conn.close()
check("1.2 save_memory 后向量已存（4 条）", n_vec == 4, f"n_vec={n_vec}")

# 语义搜索：搜"猫"应先返回白猫那条
rows, mode = L.search_memories_semantic("猫", 5)
check("1.3 语义搜'猫'返回 semantic 模式", mode == "semantic", f"mode={mode}")
check("1.4 语义搜'猫'第一条是白猫", bool(rows) and "白猫" in rows[0]["content"], f"rows={rows[:1]}")
check("1.5 语义结果带 score（0-1）", bool(rows) and 0.0 <= rows[0].get("score", -1) <= 1.0, f"score={rows[0].get('score') if rows else None}")
# 语义搜"周报"应先返回"日报"或"周报"相关（向量相近），而不是"狗"
rows2, mode2 = L.search_memories_semantic("周报怎么写", 5)
check("1.6 语义搜'周报'先返回日报/周报相关", mode2 == "semantic" and bool(rows2) and ("日报" in rows2[0]["content"] or "周报" in rows2[0]["content"]), f"rows2={rows2[:1]}")

# vec0 索引（有 sqlite-vec 时）
if HAS_VEC:
    L._vec_state["tried"] = False
    L._vec_state["ok"] = False
    ok = L._vec_extension_ok()
    check("1.7 sqlite-vec 扩展能加载", ok)
    idx = L._ensure_vec_index()
    check("1.8 vec0 虚拟表能建", idx)
    conn = sqlite3.connect(L.DB_NAME)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    n_idx = conn.execute("SELECT COUNT(*) FROM memory_vec_index").fetchone()[0] if idx else -1
    conn.close()
    check("1.9 vec0 索引里的条数与向量表一致", n_idx == n_vec, f"n_idx={n_idx} n_vec={n_vec}")
    # 用 vec0 搜一次（走 KNN 路径）
    rows3, mode3 = L.search_memories_semantic("猫", 5)
    check("1.10 vec0 路径搜'猫'仍是白猫第一", mode3 == "semantic" and bool(rows3) and "白猫" in rows3[0]["content"], f"mode3={mode3} rows3={rows3[:1]}")
else:
    print("  SKIP 1.7-1.10（本环境没装 sqlite-vec，跳过 vec0 路径）")

# Python 余弦路径（模拟 sqlite-vec 不可用）
L._vec_extension_ok = lambda: False
rows4, mode4 = L.search_memories_semantic("猫", 5)
check("1.11 没 sqlite-vec 时 Python 余弦仍能语义搜", mode4 == "semantic" and bool(rows4) and "白猫" in rows4[0]["content"], f"mode4={mode4}")
L._vec_extension_ok = _real_vec_ok
L._vec_state["tried"] = False
L._vec_state["ok"] = False

# 嵌入不可用时退回关键词
L.embed_text = lambda text: None
rows5, mode5 = L.search_memories_semantic("日报", 5)
check("1.12 嵌入不可用时退回关键词搜索", mode5 == "keyword_fallback" and bool(rows5) and "日报" in rows5[0]["content"], f"mode5={mode5}")
L.embed_text = _fake_embed

# embed_text 本身：ollama 报错时返回 None（不抛错）
_orig_embeddings = L.ollama.embeddings


def _boom(*a, **k):
    raise RuntimeError("ollama down")


L.ollama.embeddings = _boom
# 注意：L.embed_text 已被换成假向量函数，这里要调保存下来的真实 embed_text
check("1.13 embed_text 在 ollama 报错时返回 None", _real_embed_text("test") is None)
L.ollama.embeddings = _orig_embeddings

# reindex：删掉一条向量再补
conn = sqlite3.connect(L.DB_NAME)
conn.execute("DELETE FROM memory_vectors WHERE memory_id = (SELECT MAX(id) FROM memories)")
conn.commit()
conn.close()
ok_n, fail_n = L.reindex_memory_vectors()
check("1.14 reindex 能补回缺失的向量", ok_n == 1 and fail_n == 0, f"ok={ok_n} fail={fail_n}")
st = L.semantic_status()
check("1.15 semantic_status 返回完整字段", st["indexed"] == 4 and st["memories"] == 4 and st["embed_model"] == "bge-m3", f"st={st}")

# ============ 2. 技能包 ============
print("\n[2] 技能包")
skills = L.list_skills()
names = sorted(s["name"] for s in skills)
check("2.1 list_skills 返回 6 个技能", names == ["chat", "document-handling", "file-approval", "planning", "scheduled-tasks", "self-check"], f"names={names}")
check("2.2 每个技能都有 description", all(s.get("description") for s in skills))

REQUIRED_SECTIONS = ["## 何时使用", "## 怎么做", "## 验收标准"]
all_ok = True
for s in skills:
    sk = L.get_skill(s["name"])
    if not sk:
        all_ok = False
        break
    meta = L._parse_skill_frontmatter(sk["content"])
    if not meta.get("name") or not meta.get("description"):
        all_ok = False
        break
    for sec in REQUIRED_SECTIONS:
        if sec not in sk["content"]:
            all_ok = False
            break
check("2.3 每份 SKILL.md 都有 frontmatter 和三个必需章节", all_ok)
check("2.4 get_skill 能读 planning 全文", bool(L.get_skill("planning")) and "规划循环" in L.get_skill("planning")["content"])
check("2.5 get_skill 挡路径穿越", L.get_skill("../etc") is None and L.get_skill("..") is None and L.get_skill("a/b") is None)
check("2.6 get_skill 不存在的技能返回 None", L.get_skill("no-such-skill") is None)

cases = [
    ("帮我写一份周报", "planning"),
    ("把这个保存成文件", "file-approval"),
    ("检查一下结果有没有错", "self-check"),
    ("每天 21:30 提醒我写日报", "scheduled-tasks"),
    ("读一下这个 PDF 文档", "document-handling"),
    ("你好，今天天气怎么样", "chat"),
]
match_ok = all(L.match_skill(q) == want for q, want in cases)
check("2.7 match_skill 对 6 类任务都匹配正确", match_ok, f"{[(q, L.match_skill(q)) for q, _ in cases]}")
blk = L.skill_prompt_block("planning")
check("2.8 skill_prompt_block 返回规划技能的步骤", "【技能：planning】" in blk and "POST /api/plan" in blk)
check("2.9 skill_prompt_block 不存在的技能返回空", L.skill_prompt_block("no-such-skill") == "")
_old_skills_dir = L.SKILLS_DIR
L.SKILLS_DIR = os.path.join(TMP, "no-such-dir")
_empty = L.list_skills() == []
L.SKILLS_DIR = _old_skills_dir
check("2.10 list_skills 在 skills 目录不存在时返回空", _empty)

# ============ 3. 文档导入（markitdown） ============
print("\n[3] 文档导入")
with open(os.path.join(L.INBOX_DIR, "sample.txt"), "w", encoding="utf-8") as f:
    f.write("周报\n本周完成了三件事：写日报、修输入法、整理记忆。\n下周计划：做月度总结。\n")
with open(os.path.join(L.INBOX_DIR, "sample.csv"), "w", encoding="utf-8") as f:
    f.write("name,age\nAlice,30\nBob,25\n")
with open(os.path.join(L.INBOX_DIR, "sample.html"), "w", encoding="utf-8") as f:
    f.write("<html><body><h1>会议纪要</h1><p>决定：下周三开评审会。</p><ul><li>张三负责前端</li><li>李四负责后端</li></ul></body></html>")
with open(os.path.join(L.INBOX_DIR, "sample.json"), "w", encoding="utf-8") as f:
    f.write('{"project": "知衡", "version": "1.1.0"}')
_make_docx(os.path.join(L.INBOX_DIR, "sample.docx"))

t, conv, err = L.convert_to_markdown(os.path.join(L.INBOX_DIR, "sample.txt"))
check("3.1 txt 转 Markdown（含原文）", t is not None and "周报" in t, f"err={err}")
t, conv, err = L.convert_to_markdown(os.path.join(L.INBOX_DIR, "sample.csv"))
check("3.2 csv 转 Markdown 表格", t is not None and "| name | age |" in t and "Alice" in t, f"t={t!r} err={err}")
t, conv, err = L.convert_to_markdown(os.path.join(L.INBOX_DIR, "sample.html"))
check("3.3 html 转 Markdown（标题+列表）", t is not None and "# 会议纪要" in t and "张三负责前端" in t, f"t={t!r} err={err}")
t, conv, err = L.convert_to_markdown(os.path.join(L.INBOX_DIR, "sample.json"))
check("3.4 json 转 Markdown", t is not None and "知衡" in t, f"err={err}")
if HAS_MD:
    t, conv, err = L.convert_to_markdown(os.path.join(L.INBOX_DIR, "sample.docx"))
    check("3.5 docx 用 markitdown 转 Markdown", t is not None and "季度总结" in t and conv == "markitdown", f"conv={conv} err={err} t={t!r}")
else:
    t, conv, err = L.convert_to_markdown(os.path.join(L.INBOX_DIR, "sample.docx"))
    check("3.5 没 markitdown 时 docx 明确报错（不是静默失败）", t is None and err is not None and "markitdown" in err, f"err={err}")
with open(os.path.join(L.INBOX_DIR, "bad.exe"), "w") as f:
    f.write("x")
t, conv, err = L.convert_to_markdown(os.path.join(L.INBOX_DIR, "bad.exe"))
check("3.6 不支持的格式明确报错", t is None and err is not None and "不支持" in err, f"err={err}")

# ingest：建待审批，不写 workspace
ok, info = L.ingest_document("sample.txt")
check("3.7 ingest sample.txt 成功", ok and info.get("out_name") == "sample.md", f"info={info}")
pend = L.list_pending_files()
check("3.8 ingest 后待审批里有这条", any(p["filename"] == "sample.md" and p["status"] == "pending" for p in pend))
ws = L.list_workspace_files()
check("3.9 ingest 后 workspace/ 里还没有（没批准就不写）", not any(f["name"] == "sample.md" for f in ws))
# 批准后才写
pid = next(p["id"] for p in pend if p["filename"] == "sample.md")
ok2, msg2 = L.approve_pending(pid)
content = L.read_workspace_file("sample.md") if ok2 else None
check("3.10 批准后 workspace/ 里有了，且内容是转换后的 Markdown", ok2 and content is not None and "周报" in content, f"msg2={msg2}")
# 拒绝路径
ok3, info3 = L.ingest_document("sample.csv")
pid3 = info3["proposal_id"] if ok3 else None
ok4, msg4 = L.reject_pending(pid3) if pid3 else (False, "")
check("3.11 拒绝后 workspace/ 里没有 sample.csv 对应的 md", ok4 and not any(f["name"] == "sample.csv.md" for f in L.list_workspace_files()))
# 非法输入
ok5, info5 = L.ingest_document("../sample.txt")
check("3.12 ingest 挡路径穿越", not ok5)
ok6, info6 = L.ingest_document("no-such-file.txt")
check("3.13 ingest 不存在的文件明确报错", not ok6 and "没有这个文件" in str(info6))
ok7, info7 = L.ingest_document("bad.exe")
check("3.14 ingest 不支持的格式明确报错", not ok7)
# 超大文件
with open(os.path.join(L.INBOX_DIR, "big.txt"), "w", encoding="utf-8") as f:
    f.write("x" * (1024 * 1024 + 100))
ok8, info8 = L.ingest_document("big.txt")
check("3.15 ingest 超过 1MB 的文档被拒", not ok8 and "1MB" in str(info8))
# inbox 列表
inbox = L.list_inbox_files()
check("3.16 list_inbox_files 只列可导入的文档", any(f["name"] == "sample.txt" for f in inbox) and not any(f["name"] == "bad.exe" for f in inbox), f"inbox={inbox}")

# markitdown 缺失时的内置降级
_saved_md = sys.modules.get("markitdown")
sys.modules["markitdown"] = None
try:
    t2, conv2, err2 = L.convert_to_markdown(os.path.join(L.INBOX_DIR, "sample.txt"))
    check("3.17 没 markitdown 时 txt 仍能用内置转换器", t2 is not None and "周报" in t2 and conv2 == "builtin", f"conv2={conv2} err2={err2}")
    t3, conv3, err3 = L.convert_to_markdown(os.path.join(L.INBOX_DIR, "sample.docx"))
    check("3.18 没 markitdown 时 docx 明确提示要装什么", t3 is None and err3 is not None and "markitdown" in err3, f"err3={err3}")
finally:
    if _saved_md is None:
        sys.modules.pop("markitdown", None)
    else:
        sys.modules["markitdown"] = _saved_md

# ============ 4. 回归（v2/v2.1 行为没变） ============
print("\n[4] 回归")
check("4.1 safe_workspace_name 仍挡 ../、.exe、无后缀、前导点",
      L.safe_workspace_name("../x.py") is None
      and L.safe_workspace_name("x.exe") is None
      and L.safe_workspace_name("noext") is None
      and L.safe_workspace_name(".hidden.md") is None
      and L.safe_workspace_name("a/b.md") is None
      and L.safe_workspace_name("ok.md") == "ok.md")
kw = L.search_memories_list("日报", 5)
check("4.2 关键词搜索仍可用", bool(kw) and "日报" in kw[0]["content"])
mems = L.get_memories_list(5)
check("4.3 get_memories_list 仍可用", len(mems) >= 4)
# 待审批 approve/reject（普通提案）
conn = sqlite3.connect(L.DB_NAME)
cur = conn.execute(
    "INSERT INTO pending_files (plan_id, step_idx, filename, content, status, created_at) VALUES (1, 0, 't1.md', 'hello', 'pending', '2026-11-25 00:00')"
)
pid_r = cur.lastrowid
conn.commit()
conn.close()
ok_r, _ = L.approve_pending(pid_r)
check("4.4 普通文件提案批准仍能写进 workspace/", ok_r and L.read_workspace_file("t1.md") == "hello")
ok_r2, _ = L.approve_pending(pid_r)
check("4.5 重复批准同一条不再重复写（第二次返回失败）", not ok_r2)
# 输入法守卫三处还在
idx_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "index.txt")
idx = open(idx_path, encoding="utf-8").read()
check("4.6 index.txt 输入法守卫三处都在（compositionstart/end + keyCode 229）",
      "compositionstart" in idx and "compositionend" in idx and idx.count("keyCode === 229") >= 3,
      f"count229={idx.count('keyCode === 229')}")
# 新路由字符串都在 app.py 里
app_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app.py"), encoding="utf-8").read()
for route in ["/api/skills", "/api/skill", "/api/inbox", "/api/ingest", "/api/memories/reindex"]:
    check(f"4.7 路由 {route} 在 app.py 里", f'"{route}"' in app_src)
# 配置默认值
cfg = L.load_config()
check("4.8 load_config 默认值不变（auto/groq）", cfg.get("mode") == "auto" and cfg.get("provider") == "groq")

# ============ 5. 标准对话接口 /v1/* ============
print("\n[5] 标准对话接口")
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

_v1_seen = {}


def _fake_run_ai_agent(prompt):
    _v1_seen["prompt"] = prompt
    reply = ("思考：这是 Issue #1 的修法，先定位输入框守卫再改。\n"
             "推理：只加 shiftKey 判断，不动组字逻辑。\n"
             "决策/代码：\n```python\n# 在 keydown 判断里加一行：Enter 且 shiftKey 时直接返回\nif e.shiftKey:\n    return\n```\n")
    return reply, "qwen3:4b", "本地", [], "chat"


_orig_run_ai_agent = L.run_ai_agent
L.run_ai_agent = _fake_run_ai_agent
_srv = ThreadingHTTPServer(("127.0.0.1", 0), L.Handler)
_port = _srv.server_address[1]
_thread = threading.Thread(target=_srv.serve_forever, daemon=True)
_thread.start()


def _v1_req(method, path, payload=None):
    url = f"http://127.0.0.1:{_port}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        return e.code, json.loads(raw) if raw.strip() else {}


# 5.1 标准请求返回 chat.completion 形状
st, body = _v1_req("POST", "/v1/chat/completions", {
    "model": "qwen3:4b",
    "messages": [{"role": "system", "content": "你是助手"}, {"role": "user", "content": "帮我看看这个任务"}],
})
check("5.1 标准请求返回 200 + chat.completion",
      st == 200 and body.get("object") == "chat.completion"
      and body.get("choices", [{}])[0].get("message", {}).get("role") == "assistant"
      and body["choices"][0]["message"]["content"].startswith("思考：")
      and body.get("model") == "qwen3:4b"
      and set(body.get("usage", {})) == {"prompt_tokens", "completion_tokens", "total_tokens"},
      f"st={st} body={str(body)[:200]}")
check("5.2 响应带 lowspec 附加信息（来源/技能）",
      body.get("lowspec", {}).get("source") == "本地" and body["lowspec"].get("skill") == "chat")

# Issue 文字直通过去（用真实 Issue #1 的标题+正文要点）
ISSUE1 = ("GitHub Issue #1：Shift+Enter 会直接发送（应不发送）\n"
          "对话框按 Shift+Enter 会直接把消息发出去。按 open-claude-ui ChatComposer 的做法，Shift+Enter 应留作换行、不发送。\n"
          "位置：index.txt 对话输入框守卫（约第 214–220 行）：只判断了 composing / isComposing / keyCode 229，没判断 shiftKey。\n"
          "建议修法：在 keydown 判断里加一行：Enter 且 shiftKey 时直接返回，不发。改完重跑 7 项，应为 7/7。")
st2, body2 = _v1_req("POST", "/v1/chat/completions", {
    "model": "qwen3:4b",
    "messages": [{"role": "user", "content": ISSUE1}],
})
check("5.3 Issue 文字经接口直接送达（不经人手复制）",
      st2 == 200 and _v1_seen.get("prompt") == ISSUE1, f"st={st2} seen={str(_v1_seen.get('prompt'))[:60]}")
_st4, _b4 = _v1_req("POST", "/v1/chat/completions", {
    "messages": [{"role": "user", "content": "第一条"}, {"role": "user", "content": "第二条"}],
})
check("5.4 多条 user 消息时取最后一条作任务",
      _st4 == 200 and _v1_seen.get("prompt") == "第二条", f"seen={_v1_seen.get('prompt')}")

# 校验与错误
st3, body3 = _v1_req("POST", "/v1/chat/completions", {"messages": []})
check("5.5 messages 为空返回 400 + error", st3 == 400 and "error" in body3, f"st={st3}")
st4, body4 = _v1_req("POST", "/v1/chat/completions", {"messages": [{"role": "system", "content": "x"}]})
check("5.6 没有 user 消息返回 400", st4 == 400 and "error" in body4, f"st={st4}")
st5, body5 = _v1_req("POST", "/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}], "stream": True})
check("5.7 stream=true 明确拒绝（暂不支持流式）", st5 == 400 and body5.get("error", {}).get("code") == "stream_not_supported", f"st={st5} body={body5}")
st6, body6 = _v1_req("GET", "/v1/models")
check("5.8 GET /v1/models 返回模型列表",
      st6 == 200 and isinstance(body6.get("data"), list) and len(body6["data"]) >= 2
      and all(d.get("object") == "model" and d.get("id") for d in body6["data"]), f"st={st6} body={body6}")
st7, body7 = _v1_req("GET", "/v1/status")
check("5.9 GET /v1/status 返回状态",
      st7 == 200 and body7.get("ok") is True and body7.get("mode") in ("auto", "api", "local"), f"st={st7} body={body7}")
st8, _b8 = _v1_req("GET", "/v1/load")
check("5.10 未实现的 /v1/load 返回 404（不装假接口）", st8 == 404, f"st={st8}")
check("5.11 服务只绑本机回环（HOST=127.0.0.1）", L.HOST == "127.0.0.1", f"HOST={L.HOST}")
# 新路由字符串都在 app.py 里
for route in ["/v1/chat/completions", "/v1/models", "/v1/status"]:
    check(f"5.12 路由 {route} 在 app.py 里", f'"{route}"' in app_src)
_srv.shutdown()
_srv.server_close()
L.run_ai_agent = _orig_run_ai_agent

# ============ 汇总 ============
print(f"\n==== 结果：{len(PASSED)} 通过，{len(FAILED)} 失败 ====")
if FAILED:
    print("失败项：")
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("全部通过。")
shutil.rmtree(TMP, ignore_errors=True)
