# -*- coding: utf-8 -*-
"""个人AI系统 · 本机图形界面（M7 客户端第二形态）— 契约 PART B2 (M7)。

为什么现在做它：六大能力里“独立应用”一直只有命令行。用标准库 http.server 起一个
【只绑 127.0.0.1】的本地服务，双击批处理就能开——零第三方依赖、不联网、不部署。
按 ADR-022，云端常驻留到开发收口后转 Muse 服务器；本机界面与那一步不冲突。

运行：  双击 打开界面.bat          （自动起服务 + 自动开浏览器）
        python webapp.py            （同上，命令行起）
        python webapp.py --port 9000 --no-browser
        python webapp.py --live     （先悄悄输入密钥再开界面，同 chat.py）

接口（契约 M7）：
    POST /api/chat    {text} -> {response, trace}
    GET  /api/history        -> {episodes:[Episode]}
    GET  /api/state          -> {facts, principles, outbox, model, affect, self, self_revisions}
    POST /api/reflect        -> 反思一次（自我成长）
    POST /api/tick           -> 模拟时钟滴答，触发到期的主动推送（关掉也在）
    POST /api/forget         {id, reason?} -> 忘掉一条事实（理由可选，留 meta 审计）
    POST /api/retire         {id, reason} -> 作废一条自写原则（理由必填）
    GET  /api/voice          -> {enabled, backend}
    POST /api/voice          {enabled} -> 开/关本机语音（只改本进程，不落盘）

三条安全底线（不可放宽）：
    1. 只绑 127.0.0.1；请求的 Host 必须是本机，否则 403（挡 CSRF/DNS rebinding）。
    2. 页面渲染一律用 textContent，不拼 innerHTML —— 记忆里的内容不许被当 HTML 执行。
    3. 密钥通过 /api/providers 接收后写入 DPAPI 加密的 providers.json；
       接口只返回是否配置，不回显明文。误贴进来的密钥仍由 _looks_secret 护栏拦截。
"""
import argparse
import base64
import datetime
import getpass
import hashlib
import hmac
import json
import os
import sys
import tempfile
import threading
import time
import secrets
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pai.memory import Memory
from pai.orchestrator import Orchestrator, _looks_secret
from pai import atomic as AT
from pai import reflection as M4
from pai import model_gateway as M10
from pai import correction_rules as CR
from pai import voice as M14
from pai import affect as M5
from pai import self_model as SM
from pai import self_knowledge as SK
from pai import reasoning_gate as RG
from pai import trace_store as TS
from pai import c_score as CSCORE
from pai import world_write_gate as WG
from pai import daily_memory as DM
from pai import world_shadow as WSH
from pai import task_ledger as TL
from pai import profile as PF
from pai import skill_library as SL
from pai import file_extract as FE
from pai import conversation_store as CONV
from pai import asr as ASR
from pai import vision as VIS
from pai import web_search as WS
from pai import usage as USAGE
from pai import offline as OFF
from pai import image_ocr as OCR
from pai import local_search as LSEARCH
from pai import code_agent as CODE
from pai import tool_loop as TU
from pai import tools as TOOLS
from pai import writing as WR
from pai import doc_index as DI
from pai import writing_style as WST
from pai import backup as BK
from pai import mcp_client as MCPC
from pai import browser as BROW
from pai import desktop_control as DC
from pai import continuity as CT
from pai import semantic as SC
from pai import stream_asr as SASR
from pai import punctuate as PT
from pai import proactive as PA
from pai import meta as MT
from pai import jarvis as JV
from pai import model_usage as MU
from pai import mirrai_emotion as ME
from pai import mirrai_memory as MMEM
from pai import memory_candidates as MC
from pai import persona_distill as PD
from pai import relationship as REL
from pai import graduation as GR
from pai import job_runner as JR
from pai import job_worker as JW
from pai import approval_token as TOK
from pai import agent_registry as AR
from pai import workspaces as TWS
from pai import model_packs as MP
from pai import model_settings as MS
from pai import local_cascade as LC
from pai import daily_brief as BR
from pai import capability_router as CAPR
from pai import startup as SU
from pai import low_spec as LOWSPEC
from pai import model_mode as MMODE
from pai import network_policy as NP
from pai import onboarding as OB
from pai import providers as PR
from pai import model_admin as MA
from pai import model_registry as MR
from pai import custom_select as CUS
from pai import ai_provider as AIP
from pai import paths as PATHS
from pai import exec_guard as EG
from pai import job_health as JH
from pai import unified as UNI
from pai import memory_layers as ML
from pai import backup_task as BT
from pai import roi as ROI
from pai import ws_backfill as WB

DB = PATHS.DATA_DIR / "memory.db"
BUSY_FILE = Path(__file__).resolve().parent / "data" / "generation.active"
_GEN_LOCK = threading.Lock()
LOOPBACK = "127.0.0.1"
DEFAULT_PORT = 8753

def _agentd_call(path, payload):
    if not AGENTD_TOKEN:
        return 503, {"ok": False, "error": "PAI_AGENTD_TOKEN_QODER 未配置"}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(AGENTD_URL + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Agent-Token": AGENTD_TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {"ok": False, "error": "agentd HTTP %s" % e.code}
        return e.code, body
    except Exception as e:
        return 502, {"ok": False, "error": "agentd 不可用：%s" % type(e).__name__}

AGENTD_URL = os.environ.get("PAI_AGENTD_URL", "http://127.0.0.1:8766")


def _agentd_available():
    try:
        with urllib.request.urlopen(AGENTD_URL.rstrip("/") + "/health", timeout=0.5) as r:
            return r.status == 200
    except Exception:
        return False
GATED_PREFIXES = (
    "/api/approval", "/api/approvals", "/api/files/", "/api/plans/",
    "/api/inbox/", "/api/tools/", "/api/qwen_agent/", "/api/providers",
    "/api/custom_models", "/api/models/", "/api/network/", "/api/desktop/",
    "/api/browser/", "/api/mcp/", "/api/code/", "/api/persona/",
    "/api/agents/", "/api/jobs/", "/api/workspaces/", "/api/memory_candidates/",
)
UI_NONCE_FIXED = (os.environ.get("PAI_UI_NONCE") or "").strip()
UI_NONCE = UI_NONCE_FIXED or secrets.token_urlsafe(32)


def _native_confirm(title, detail):
    """桌面壳内高风险动作的系统级确认；非 Windows/未开启时直接放行。"""
    if os.name != "nt" or os.environ.get("PAI_NATIVE_CONFIRM", "").strip() not in ("1", "on", "true", "yes"):
        return True
    try:
        import ctypes
        text = str(detail)[:500]
        return ctypes.windll.user32.MessageBoxW(0, text, "知衡 · " + str(title), 0x4 | 0x40 | 0x10000) == 6
    except Exception:
        return False


def _local_api_key():
    """本地 /v1 的默认 key；首次生成，之后复用。"""
    env = (os.environ.get("PAI_LOCAL_API_KEY") or "").strip()
    if env:
        return env
    p = PATHS.DATA_DIR / "local_api_key"
    try:
        if p.is_file():
            v = p.read_text(encoding="utf-8").strip()
            if v:
                return v
        v = secrets.token_urlsafe(32)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(v + "\n", encoding="utf-8")
        return v
    except Exception:
        return ""
STARTUP_REPORT = {}


def _startup_step(name, fn, critical=False):
    try:
        fn()
        STARTUP_REPORT[name] = {"ok": True}
        return True
    except Exception as e:
        STARTUP_REPORT[name] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        if critical:
            raise
        return False
AGENTD_TOKEN = os.environ.get("PAI_AGENTD_TOKEN_QODER", "")
M1_DIR = Path(__file__).resolve().parent / "web"
PAGES = {
    "/chat": "chat.html", "/approvals": "approvals.html",
    "/templates": "templates.html", "/graph": "graph.html",
    "/mcp": "mcp.html", "/plans": "plans.html", "/files": "files.html",
    "/security": "security.html", "/usage": "usage.html",
    "/audit": "audit.html", "/memory": "memory.html",
}
MAX_BODY = 64 * 1024          # 本机单人用；超限直接拒，免得一个烂请求吃掉内存

mem = None                    # 单进程共享；模型生成由 _GEN_LOCK 串行
orch = None
tasks = None
skills = None
convs = None


def _local_host(value):
    """只认本机的 Host/绝对 URL。"""
    v = (value or "").strip().lower()
    if not v:
        return False
    origin = v.split("//", 1)[-1].split("/", 1)[0]
    host = origin.split(":")[0]
    allowed = {x.strip().lower() for x in (os.environ.get("PAI_ALLOWED_HOSTS") or "").split(",") if x.strip()}
    return host in ("127.0.0.1", "localhost", "[::1]", "::1") or host in allowed


def _relationship_rule():
    try:
        return REL.prompt_block()
    except Exception:
        return ""


def _qwen_agent_installed():
    import importlib.util
    return importlib.util.find_spec("qwen_agent") is not None


def _apply_agent_engine():
    try:
        p = PATHS.DATA_DIR / "agent_engine.json"
        engine = str(json.loads(p.read_text(encoding="utf-8")).get("engine") or
                     ("qwen_agent" if _qwen_agent_installed() else "builtin"))
    except Exception:
        engine = "qwen_agent" if _qwen_agent_installed() else "builtin"
    os.environ["PAI_AGENT_ENGINE"] = "qwen_agent" if engine == "qwen_agent" else "builtin"


def _apply_fallbacks():
    if os.environ.get("PAI_MODEL_FALLBACKS", "").strip():
        return
    try:
        from pai import model_admin as MA
        names = [x.get("name") for x in MA.installed() if x.get("name")]
        local = next((x for x in names if "4b" in str(x).lower()), names[0] if names else "")
        if local:
            os.environ["PAI_MODEL_FALLBACKS"] = "ollama:" + str(local)
    except Exception:
        pass


def _persona_rule():
    try:
        return PD.prompt_block_active()
    except Exception:
        return ""


def _memory_rule(text):
    try:
        return MMEM.prompt_block(text)
    except Exception:
        return ""


def _emotion_rule(text):
    try:
        return ME.prompt_block(text)
    except Exception:
        return ""


def _skill_rule(text):
    try:
        hits = SL.SkillLibrary(PF.skills_dir()).find(text, 1)
        if not hits:
            return ""
        sk = hits[0]
        body = str(sk.get("_body") or "").strip()
        if body.startswith("---") and body.count("---") >= 2:
            body = body.split("---", 2)[2].lstrip()
        # 只注入命中的一个技能，截断，避免把技能库整本塞进提示词。
        body = body[:1800]
        return "[本轮技能：%s]\n%s" % (sk.get("name") or "skill", body)
    except Exception:
        return ""


def _jarvis_rule():
    try:
        return JV.prompt_block(PF.current(), tasks)
    except Exception:
        return ""


PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>个人AI系统 · 本机</title>
<style>
:root{--bg:#14161a;--fg:#e8e6e1;--mut:#8b8f98;--line:#262a31;--acc:#c8a15a;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.7 "Segoe UI","Microsoft YaHei",system-ui,sans-serif}
.wrap{display:flex;height:100vh;max-width:1180px;margin:0 auto}
main{flex:1;display:flex;flex-direction:column;min-width:0}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;
justify-content:space-between;align-items:center;gap:12px}
h1{font-size:15px;margin:0;font-weight:600;letter-spacing:.02em}
.tag{font-size:12px;color:var(--mut)}
#log{flex:1;overflow:auto;padding:18px 20px}
.turn{margin-bottom:18px}
.you{color:var(--mut);font-size:13px;margin-bottom:4px}
.ai{white-space:pre-wrap}
.trace{margin-top:6px;font-size:12px;color:var(--mut);border-left:2px solid var(--line);
padding-left:10px}
.trace b{color:var(--acc);font-weight:600}
form{display:flex;gap:8px;padding:14px 20px;border-top:1px solid var(--line)}
textarea{flex:1;background:#0f1114;color:var(--fg);border:1px solid var(--line);
border-radius:8px;padding:10px;font:inherit;resize:none;height:52px}
button{background:var(--acc);color:#161616;border:0;border-radius:8px;
padding:0 18px;font:inherit;font-weight:600;cursor:pointer}
.vbtn{background:#0f1114;color:var(--fg);border:1px solid var(--line);
font-weight:400;font-size:12px;padding:4px 10px;border-radius:6px}
aside{width:320px;border-left:1px solid var(--line);overflow:auto;padding:16px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.tabs button{background:#0f1114;color:var(--mut);border:1px solid var(--line);
font-weight:400;font-size:12px;padding:5px 10px}
.tabs button.on{color:var(--fg);border-color:var(--acc)}
.row{font-size:13px;padding:8px 0;border-bottom:1px solid var(--line)}
.row .x{color:var(--mut);font-size:11px}
.empty{color:var(--mut);font-size:13px}
.acts{margin-top:14px;display:flex;flex-direction:column;gap:6px}
.acts button{background:#0f1114;color:var(--fg);border:1px solid var(--line);
font-weight:400;font-size:12px;padding:8px;text-align:left}
.note{font-size:11px;color:var(--mut);margin-top:10px;line-height:1.6}
@media (max-width:760px){
/* 窗口拉窄（或分屏一半）时：两栏会挤成一列文字一列压住输入框，发送键点不到。
   窄屏就上下叠起来，主区至少留一半高度。 */
.wrap{flex-direction:column;height:auto}
main{min-height:70vh}
aside{width:auto;border-left:0;border-top:1px solid var(--line)}
}
</style></head><body><div class="wrap">
<main>
<header><h1>个人AI系统 · 本机</h1><span class="tag"><button id="voice" class="vbtn" type="button">语音：关</button> <a href="/dashboard" style="color:var(--acc)">仪表盘</a> · <span id="model"></span></span></header>
<div id="log"></div>
<form id="f"><textarea id="t" placeholder="直接说——它在听，并且会记住"></textarea>
<button type="submit">发送</button></form>
</main>
<aside>
<div class="tabs" id="tabs"></div>
<div id="panel"></div>
<div class="acts">
<button data-act="reflect">触发一次反思（它会重读对话，蒸馏事实与原则）</button>
<button data-act="tick">模拟时钟滴答（看它主动找你）</button>
</div>
<p class="note">关掉重开还记得：记忆存在 data/memory.db。<br>
密钥只走环境变量，别贴进对话——贴进来会被拦下、不落库。</p>
</aside></div>
<script>
var panel="facts";
function el(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;
if(text!==undefined&&text!==null)e.textContent=String(text);return e;}
function api(path,body){var opt={method:body?"POST":"GET",headers:{'Content-Type':'application/json'}};
if(body)opt.body=JSON.stringify(body);
return fetch(path,opt).then(function(r){return r.json();});}
function say(text,cls){var d=el('div','turn');d.appendChild(el('div',cls?'you':'ai',text));
document.getElementById('log').appendChild(d);
document.getElementById('log').scrollTop=1e9;return d;}
function traceBox(tr){var d=el('div','trace');
if(tr.blocked_secret){d.appendChild(el('div',null,'已拦截：没查记忆、没推断、没联网，原句也没落库'));
return d;}
var rec=tr.recalled||[],rea=tr.reason||'';
d.appendChild(el('div',null,'查到的 '+rec.length+' 条 · 我推断的：'+
String(rea).slice(0,120)));
if(tr.culture_refs&&tr.culture_refs.length){
tr.culture_refs.forEach(function(c){d.appendChild(el('div',null,'· 文化参照 '+c.ref));});}
if(tr.affect){d.appendChild(el('div',null,'· 心境估计：'+tr.affect.mood+'（强度 '+
tr.affect.intensity+'）——只调语气轻重，不改事实'));}
if(tr['self']){d.appendChild(el('div',null,'· 身份：v'+tr['self'].version+
' · 在守原则 '+tr['self'].principles_active+' 条'));}
if(tr.divergence&&tr.divergence.flagged){d.appendChild(el('div',null,
'· 两模型分歧较大（相似 '+tr.divergence.score+'）——两边原文都在，判断权在你'));}
return d;}
function submit(text){say(text,'you');
api('/api/chat',{text:text}).then(function(o){
var d=say(o.response);if(o.trace)d.appendChild(traceBox(o.trace));
refresh();});}
document.getElementById('f').addEventListener('submit',function(e){
e.preventDefault();var t=document.getElementById('t');
var v=t.value.trim();if(!v)return;t.value='';submit(v);});
document.getElementById('t').addEventListener('keydown',function(e){
if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();
document.getElementById('f').dispatchEvent(new Event('submit'));}});
var TABS=[["facts","事实"],["principles","原则"],["self","我和心境"],["inbox","主动"],["history","对话"]];
function renderTabs(){var w=document.getElementById('tabs');w.textContent='';
TABS.forEach(function(t){var b=el('button',panel===t[0]?'on':'',t[1]);
b.addEventListener('click',function(){panel=t[0];refresh();});w.appendChild(b);});}
function row(text,sub,act){var d=el('div','row');d.appendChild(el('div',null,text));
if(sub)d.appendChild(el('div','x',sub));
if(act){var b=el('button','x',act.label);b.style.cssText='background:none;border:none;color:var(--acc);cursor:pointer;font-size:11px;padding:0;margin-top:2px;display:block';
b.addEventListener('click',function(){var body={},k;for(k in act.body)body[k]=act.body[k];
if(act.ask){var why=window.prompt(act.ask,'');if(!why)return;body.reason=why;}
api(act.path,body).then(refresh);});d.appendChild(b);}
return d;}
function refresh(){api('/api/state').then(function(s){
document.getElementById('model').textContent='模型：'+s.model;
var p=document.getElementById('panel');p.textContent='';
if(panel==='facts'){if(!s.facts.length){p.appendChild(el('div','empty','还没记住事实。试试「我叫阿明」或「我爷爷1959年在挨饿」'));}
s.facts.forEach(function(f){p.appendChild(row(f.subject+' · '+f.predicate+' = '+f.object,
'#'+f.id+' 置信 '+f.confidence,{label:'忘掉这条',path:'/api/forget',body:{id:f.id}}));});}
else if(panel==='principles'){if(!s.principles.length){p.appendChild(el('div','empty','还没有原则。点下面“触发一次反思”'));}
s.principles.forEach(function(x){p.appendChild(row(x.text,'#'+x.id+(x.revision_reason?' 修订：'+x.revision_reason:''),
{label:'作废这条',path:'/api/retire',body:{id:x.id},ask:'为什么要作废这条？（会作为理由留痕）'}));});}
else if(panel==='inbox'){if(!s.outbox.length){p.appendChild(el('div','empty','它还没主动找过你。（提醒目前只能在命令行设：python chat.py 里 :remind；这里先只负责看）'));}
s.outbox.forEach(function(o){p.appendChild(row(o.message,o.ts+' · '+(o.is_read?'已读':'未读')));});}
else if(panel==='self'){var sm=s['self']||{},id=sm.identity||{},af=s.affect||{};
p.appendChild(row('心境（系统内部估计）：'+(af.mood||'平静'),
 '强度 '+((af.intensity||0))+' · 线索 '+((af.cues||[]).join('、')||'无')+
 '　※这是估计不是诊断，也不代表它有感；你说自己怎么样，以你说的为准'));
p.appendChild(row(id.role||'身份：还没建立（先聊几句，它才会写下第一份记录）',
 id.role?('版本 v'+sm.version+' · '+(id.created_ts||'—')+' 起 · 改口 '+sm.revisions+' 次'):''));
(id.boundaries||[]).forEach(function(b){p.appendChild(row('它在守：'+b,''));});
(s.self_revisions||[]).slice(0,8).forEach(function(r){
p.appendChild(row('改口：'+(r.old_text||'')+' → '+(r.new_text||'（作废）'),
 r.ts+' · '+r.kind+' 理由：'+r.reason));});
if((sm.warnings||[]).length){p.appendChild(row('自查提醒 '+sm.warnings.length+' 处：'+
sm.warnings.map(function(w){return w.kind;}).join('、'),
'近重复 / 越界宣称（把自己说成有意识）/ 过长——它自己说不清，只能列给你判断'));}
else p.appendChild(row('自查：没发现近重复、越界宣称或过长的原则',''));}
else{api('/api/history').then(function(h){h.episodes.slice().reverse().forEach(function(e){
p.appendChild(row(e.role+'：'+e.content.slice(0,60),e.ts));});});}});
renderTabs();}
function refreshVoice(){api('/api/voice').then(function(v){var b=document.getElementById('voice');if(!b)return;
b.dataset.on=v.enabled?'1':'0';b.textContent='语音：'+(v.enabled?'开':'关');});}
document.getElementById('voice').addEventListener('click',function(){
var on=this.dataset.on!=='1';api('/api/voice',{enabled:on}).then(refreshVoice);});
document.querySelectorAll('.acts button').forEach(function(b){
b.addEventListener('click',function(){api('/api/'+b.dataset.act,{}).then(refresh);});});
refresh();
refreshVoice();
</script></body></html>"""


DASH_PATH = Path(__file__).with_name("dashboard.html")


def _busy_mark():
    try:
        BUSY_FILE.parent.mkdir(parents=True, exist_ok=True)
        AT.write_text(BUSY_FILE, "%.3f %s" % (time.time(), os.getpid()), encoding="utf-8")
    except OSError:
        pass


def _version():
    try:
        p = Path(__file__).resolve().parent.parent / "version.json"
        return json.loads(p.read_text(encoding="utf-8")).get("version", "")
    except Exception:
        return ""


def _busy_clear():
    try:
        BUSY_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _busy_start():
    _busy_mark()
    stop = threading.Event()

    def beat():
        while not stop.wait(5):
            _busy_mark()

    threading.Thread(target=beat, daemon=True, name="pai-busy-heartbeat").start()
    return stop


def _approval_ok(payload, kind):
    if not TOK.required():
        return True
    return TOK.consume(str(payload.get("approval_token") or ""), kind,
                       bind=str(payload.get("approval_bind") or ""))


class Handler(BaseHTTPRequestHandler):
    try:
        _vp = Path(__file__).resolve().parent.parent / "version.json"
        server_version = "PAI/" + json.loads(_vp.read_text(encoding="utf-8")).get("version", "1.1.0")
    except Exception:
        server_version = "PAI/1.1.0"

    def log_message(self, *a):
        pass                      # 不打印请求体，避免把用户说的话刷进终端历史

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(
            body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def _blocked_host(self):
        if _local_host(self.headers.get("Host")):
            return False
        self.close_connection = True     # 拒了但不读体：残留字节会污染同连接的下一个请求
        self._send(403, {"error": "只允许本机访问"})
        return True

    def _blocked_origin(self):
        """POST 带 Origin/Referer 时，只允许本机页面。

        不带头的 curl/本地脚本放行；带上外站来源一律 403。
        Host 校验挡 DNS rebinding，这一层挡跨站表单/脚本。
        """
        raw = (self.headers.get("Origin") or self.headers.get("Referer") or "").strip()
        if not raw:
            return False
        if _local_host(raw):
            return False
        self.close_connection = True
        self._send(403, {"error": "只允许本机页面调用"})
        return True

    def _ui_gate(self):
        raw = (self.headers.get("Origin") or self.headers.get("Referer") or "").strip()
        if not raw or not _local_host(raw):
            self._send(403, {"ok": False, "error": "审批必须由本机页面发起"})
            return False
        if str(self.headers.get("X-DSH-Nonce") or "") != UI_NONCE:
            self._send(403, {"ok": False, "error": "缺少或错误的页面 nonce"})
            return False
        return True

    def _html(self, body):
        try:
            script = (
                '<script>(function(){var f=window.fetch;window.fetch=function(i,o){o=o||{};'
                'var u=typeof i==="string"?i:(i&&i.url)||"";var m=(o.method||"GET").toUpperCase();'
                'if(m!=="GET"&&m!=="HEAD"&&u.indexOf("/api/")===0){o.headers=o.headers||{};'
                'if(window.__DSH_NONCE__)o.headers["X-DSH-Nonce"]=window.__DSH_NONCE__;}'
                'return f(i,o);};})();</script>'
            )
            if not UI_NONCE_FIXED:
                script = '<script>window.__DSH_NONCE__="%s";</script>' % UI_NONCE + script
            return body.replace(b"</head>", (script + "</head>").encode("utf-8"))
        except Exception:
            return body

    def do_GET(self):
        try:
            self._do_GET()
        except Exception as e:
            try:
                self._send(500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
            except Exception:
                pass

    def _do_GET(self):
        if self._blocked_host():
            return
        if self._blocked_origin():
            return
        if self.path in ("/", "/m1", "/m1/"):
            self.send_response(302)
            self.send_header("Location", "/chat")
            self.end_headers()
        elif self.path == "/dashboard":
            try:
                body = DASH_PATH.read_text(encoding="utf-8").encode("utf-8")
            except OSError:
                self._send(500, {"error": "dashboard.html 缺失"})
            else:
                self._send(200, self._html(body), "text/html; charset=utf-8")
        elif self.path.split("?", 1)[0] in PAGES:
            name = PAGES[self.path.split("?", 1)[0]]
            try:
                body = (M1_DIR / name).read_bytes()
            except OSError:
                self._send(500, {"error": name + " 缺失"})
            else:
                self._send(200, self._html(body), "text/html; charset=utf-8")
        elif self.path == "/api/local_api_key":
            if not self._ui_gate():
                return
            self._send(200, {"key": _local_api_key()})
        elif self.path == "/v1/models":
            self._send(200, {"object": "list", "data": [{"id": M10.active_provider(), "object": "model", "owned_by": "zhiheng"}]})
        elif self.path == "/v1/status":
            self._send(200, {"ok": True, "model": M10.active_provider(), "approval_mode": os.environ.get("PAI_APPROVAL_MODE", "ledger")})
        elif self.path == "/api/plans":
            out = []
            for p in JR.list_plans(20):
                jobs = JR.plan_jobs(p["id"])
                files = [x for x in JR.list_file_proposals("pending") if x.get("plan_id") == p["id"]]
                out.append({"plan": p, "jobs": jobs, "files": files})
            self._send(200, {"items": out})
        elif self.path == "/api/files/pending":
            self._send(200, {"items": JR.list_file_proposals("pending")})
        elif self.path == "/api/inbox":
            try:
                self._send(200, {"items": FE.list_inbox()})
            except Exception as e:
                self._send(500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
        elif self.path == "/api/credentials":
            try:
                self._send(200, {"providers": PR.status().get("providers", []),
                                 "custom": MR.key_status(),
                                 "network": NP.status()})
            except Exception as e:
                self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})
        elif self.path.startswith("/m1/"):
            name = self.path[len("/m1/"):]
            if not name or "/" in name or chr(92) in name or name.startswith("."):
                self._send(404, {"error": "没有这个文件"})
                return
            fp = M1_DIR / name
            ctype = {"js": "application/javascript; charset=utf-8",
                     "css": "text/css; charset=utf-8",
                     "json": "application/json; charset=utf-8",
                     "png": "image/png"}.get(fp.suffix.lstrip("."), "application/octet-stream")
            try:
                body = fp.read_bytes()
            except OSError:
                self._send(404, {"error": "没有这个文件"})
            else:
                self._send(200, body, ctype)
        elif self.path.startswith("/api/events"):
            self._send(405, {"error": "流式接口只接受 POST"})
        elif self.path == "/api/models":
            opts = [{"id": "", "label": "当前模型", "spec": ""}]
            if not OFF.enabled():
                opts += [
                    {"id": "deepseek-chat", "label": "DeepSeek Chat",
                     "spec": "deepseek:deepseek-chat"},
                    {"id": "deepseek-reasoner", "label": "DeepSeek Reasoner（深度思考）",
                     "spec": "deepseek:deepseek-reasoner"},
                    {"id": "claude", "label": "Claude（在线，需 ANTHROPIC_API_KEY）",
                     "spec": "anthropic:" + (os.environ.get("PAI_CLAUDE_MODEL")
                                             or "claude-3-5-sonnet-latest")},
                ]
            agg = [x.strip() for x in (os.environ.get("PAI_AGGREGATOR_MODELS") or "").split(",") if x.strip()]
            if not OFF.enabled():
                for name in agg:
                    opts.append({"id": name, "label": "聚合 · " + name,
                                 "spec": "aggregator:" + name})
            local_names = []
            try:
                with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1) as r:
                    tags = json.loads(r.read().decode("utf-8"))
                for item in tags.get("models", [])[:20]:
                    name = str(item.get("name") or "").strip()
                    caps = item.get("capabilities") or []
                    if "embedding" in caps and "completion" not in caps:
                        continue
                    if name.lower().startswith("bge-"):
                        continue
                    if caps and "completion" not in caps:
                        continue
                    if name:
                        local_names.append(name)
                        opts.append({"id": name, "label": "本地 · " + name,
                                     "spec": "ollama:" + name})
            except Exception:
                pass
            if len(local_names) >= 2:
                opts.append({"id": "local_duo", "label": "知衡 · 双脑（每句都审）",
                             "spec": "local_duo"})
                opts.append({"id": "local_smart", "label": "知衡 · 智能双脑（推荐）",
                             "spec": "local_smart"})
            self._send(200, {"current": M10.active_provider(), "options": opts})
        elif self.path == "/api/health":
            db_path = getattr(mem, "db_path", DB)
            self._send(200, {"ok": True, "model": M10.active_provider(),
                             "version": _version(),
                             "db": str(db_path), "db_exists": Path(db_path).exists(),
                             "agentd": AGENTD_URL,
                             "agentd_available": _agentd_available(),
                             "approval_mode": (os.environ.get("PAI_APPROVAL_MODE") or "ledger"),
                             "startup": dict(STARTUP_REPORT)})
        elif self.path == "/api/reminders/pending":
            rows = mem.read_outbox(unread_only=True) if mem is not None else []
            self._send(200, {"items": rows})
        elif self.path == "/api/approvals/local":
            self._send(200, {"items": TOK.pending()})
        elif self.path == "/api/approvals":
            if not AGENTD_TOKEN:
                self._send(200, {"ok": True, "available": False,
                                 "reason": "PAI_AGENTD_TOKEN_QODER 未配置",
                                 "items": []})
                return
            try:
                req = urllib.request.Request(
                    AGENTD_URL + "/tasks",
                    headers={"X-Agent-Token": AGENTD_TOKEN})
                with urllib.request.urlopen(req, timeout=3) as r:
                    data = json.loads(r.read().decode("utf-8"))
                items = []
                for t in data.get("tasks", []):
                    if int(t.get("confirm") or 0) and int(t.get("approved") or 0) == 0 \
                            and t.get("status") in ("queued", "running"):
                        items.append(t)
                self._send(200, {"ok": True, "available": True, "items": items})
            except Exception as e:
                self._send(200, {"ok": True, "available": False,
                                 "reason": "agentd 不可用：%s" % type(e).__name__,
                                 "items": []})
        elif self.path == "/api/approvals/history":
            if not AGENTD_TOKEN:
                self._send(200, {"ok": True, "available": False,
                                 "reason": "PAI_AGENTD_TOKEN_QODER 未配置",
                                 "items": []})
                return
            try:
                req = urllib.request.Request(
                    AGENTD_URL + "/tasks",
                    headers={"X-Agent-Token": AGENTD_TOKEN})
                with urllib.request.urlopen(req, timeout=3) as r:
                    data = json.loads(r.read().decode("utf-8"))
                items = []
                for t in data.get("tasks", []):
                    status = str(t.get("status") or "")
                    approved = int(t.get("approved") or 0)
                    if approved or status in ("done", "failed", "cancelled"):
                        t["history_kind"] = (
                            "approved" if approved else
                            ("executed" if status == "done" else status))
                        items.append(t)
                items.sort(key=lambda x: str(x.get("created") or ""), reverse=True)
                self._send(200, {"ok": True, "available": True,
                                 "items": items[:50]})
            except Exception as e:
                self._send(200, {"ok": True, "available": False,
                                 "reason": "agentd 不可用：%s" % type(e).__name__,
                                 "items": []})
        elif self.path == "/api/world/approvals":
            d = WG._dir()
            items = []
            for fp in sorted(d.glob("*.request.json")):
                try:
                    req = json.loads(fp.read_text(encoding="utf-8"))
                except Exception:
                    continue
                ap = fp.with_suffix(".approval.json")
                used = False
                if ap.exists():
                    try:
                        used = bool(json.loads(ap.read_text(encoding="utf-8")).get("used"))
                    except Exception:
                        pass
                items.append({"id": req.get("id"), "kind": req.get("kind"),
                              "candidate_id": req.get("candidate_id"),
                              "name": req.get("name") or req.get("value") or "",
                              "db": req.get("db"), "effects": req.get("effects") or [],
                              "request": fp.name, "approval": ap.name if ap.exists() else None,
                              "approved": ap.exists(), "used": used})
            self._send(200, {"items": items[-100:]})
        elif self.path == "/api/world/candidates":
            if not WSH.enabled():
                self._send(403, {"ok": False, "error": "影子未开（PAI_WORLD_SHADOW=on）"})
                return
            d = Path(os.environ.get("PAI_WORLD_SHADOW_DIR") or (Path(__file__).resolve().parent / "data" / "world_shadow"))
            items = []
            reviewed = set()
            rejected = set()
            rp = d / "reviewed.jsonl"
            if rp.exists():
                for line in rp.read_text(encoding="utf-8").splitlines():
                    try:
                        reviewed.add(json.loads(line).get("candidate_id"))
                    except Exception:
                        pass
            rj = d / "rejected.jsonl"
            if rj.exists():
                for line in rj.read_text(encoding="utf-8").splitlines():
                    try:
                        rejected.add(json.loads(line).get("candidate_id"))
                    except Exception:
                        pass
            if d.exists():
                for fp in sorted(d.glob("shadow-*.jsonl")):
                    for line in fp.read_text(encoding="utf-8").splitlines():
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        for c in rec.get("candidates") or []:
                            cid = hashlib.sha256(json.dumps(c, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
                            kind = c.get("kind")
                            if kind == "relation":
                                text = "%s -%s-> %s" % (c.get("subject"), c.get("predicate"), c.get("object"))
                            elif kind == "belief":
                                text = "%s -%s-> %s" % (c.get("holder"), c.get("predicate"), c.get("value"))
                            else:
                                text = "%s %s" % (c.get("type") or "", c.get("name") or "")
                            items.append({"id": cid, "kind": kind, "text": text,
                                          "evidence": c.get("evidence") or "",
                                          "status": "已拒" if cid in rejected else ("已审" if cid in reviewed else "待审")})
            self._send(200, {"items": items[:200]})
        elif self.path == "/api/world":
            # B0 世界模型：只读列表，不自动抽取、不写库。
            try:
                def rows(sql):
                    return [dict(r) for r in mem.conn.execute(sql).fetchall()]
                self._send(200, {
                    "entities": rows(
                        "SELECT id,type,name,aliases,created_at,status"
                        " FROM world_entity WHERE status='active'"
                        " ORDER BY created_at DESC LIMIT 100"),
                    "relations": rows(
                        "SELECT r.id,r.subject_id,r.predicate,r.object_id,"
                        "r.object_value,r.confidence,r.observed_at,"
                        "s.name AS subject_name,o.name AS object_name"
                        " FROM world_relation r"
                        " LEFT JOIN world_entity s ON s.id=r.subject_id"
                        " LEFT JOIN world_entity o ON o.id=r.object_id"
                        " WHERE r.status='active' ORDER BY r.observed_at DESC LIMIT 100"),
                    "events": rows(
                        "SELECT id,at,kind,summary,participants,confidence"
                        " FROM world_event WHERE status='active'"
                        " ORDER BY at DESC LIMIT 100"),
                })
            except Exception as e:
                self._send(200, {"entities": [], "relations": [], "events": [],
                                 "error": type(e).__name__})
        elif self.path == "/api/voice":
            self._send(200, {"enabled": M14.speaking_enabled(), "backend": M14.backend()})
        elif self.path == "/api/corrections":
            self._send(200, {"ok": True, "enabled": CR.enabled(), "items": CR.list_all()})
        elif self.path == "/api/memory/cards":
            try:
                items = DM.cards(mem, 20)
            except Exception as e:
                self._send(500, {"ok": False, "error": str(e)})
                return
            self._send(200, {"ok": True, "items": items})
        elif self.path == "/api/state":
            self._send(200, {
                "model": M10.active_provider(),
                "facts": mem.all_facts(),
                "principles": mem.get_principles(),
                "outbox": mem.read_outbox(),
                # M5/M2：心境是【估计】、自我模型是【可审计记录】，都只读展示。
                "affect": M5.read_affect(mem),
                "self": SM.self_report(mem),
                "self_revisions": SM.revision_log(mem),
            })
        elif self.path == "/api/reminders":
            self._send(200, {"triggers": mem.list_triggers()})
        elif self.path == "/api/self-report":
            # 只读展示：临时打开开关读一次，读完恢复，不改变进程默认
            from pai import self_facts as SF
            from pai import manifest as MF
            from pai import self_model as SMR
            from tools import c_self_probe as CP
            sk_on = SK.backend() == SK.BACKEND_ON
            sf_old = os.environ.get("PAI_SELF_FACTS")
            mf_old = os.environ.get("PAI_MANIFEST")
            SK.set_enabled(True)
            os.environ["PAI_SELF_FACTS"] = "on"
            os.environ["PAI_MANIFEST"] = "on"
            try:
                data = SK.report(mem)
                rows, _ = CP.run(mem)
                sm = SMR.self_report(mem)
                data["checked_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                data["c_score"] = CSCORE.score(mem)
                data["c_metrics"] = {
                    "c0_passed": sum(1 for r in rows if r["ok"]),
                    "c0_total": len(rows),
                    "identity_version": sm.get("version"),
                    "principles_active": (sm.get("principles") or {}).get("active"),
                    "revisions": sm.get("revisions"),
                    "healthy": sm.get("healthy"),
                }
            finally:
                SK.set_enabled(sk_on)
                if sf_old is None:
                    os.environ.pop("PAI_SELF_FACTS", None)
                else:
                    os.environ["PAI_SELF_FACTS"] = sf_old
                if mf_old is None:
                    os.environ.pop("PAI_MANIFEST", None)
                else:
                    os.environ["PAI_MANIFEST"] = mf_old
            self._send(200, data)
        elif self.path == "/api/backup/list":
            d = Path(__file__).resolve().parent / "backups"
            files = []
            if d.exists():
                for p in sorted(d.glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True)[:10]:
                    files.append({"name": p.name, "bytes": p.stat().st_size,
                                  "mtime": p.stat().st_mtime})
            self._send(200, {"backups": files})
        elif self.path == "/api/writing/style":
            self._send(200, {"style": WST.load(PF.current()),
                             "block": WST.block(PF.current())})
        elif self.path == "/api/mcp/servers":
            self._send(200, {"servers": MCPC.servers()})
        elif self.path == "/api/desktop/windows":
            self._send(200, DC.windows())
        elif self.path == "/api/continuity":
            self._send(200, {"threads": CT.all_threads(PF.current()),
                             "due": CT.due(PF.current())})
        elif self.path == "/api/proactive":
            self._send(200, {"proposals": PA.proposals(PF.current()),
                             "ledger": PA.ledger(30)})
        elif self.path == "/api/model":
            self._send(200, {"provider": M10.active_provider(),
                             "model": os.environ.get("PAI_MODEL_NAME") or "",
                             "offline": NP.is_offline(),
                             "fallback": os.environ.get("PAI_LOCAL_FALLBACK") == "on",
                             "cascade": LC.status(),
                             "usage": MU.summary()})
        elif self.path == "/api/relationship":
            d = REL.ensure()
            self._send(200, {"days": REL.days(), "events": (d.get("events") or [])[-10:],
                             "moods": (d.get("moods") or [])[-10:]})
        elif self.path == "/api/onboarding":
            self._send(200, {"ok": True, **OB.status()})
        elif self.path == "/api/providers":
            self._send(200, {"ok": True, **PR.status()})
        elif self.path == "/api/ai_provider":
            pvd = AIP.selected()
            self._send(200, {"ok": True, "health": pvd.health_check(),
                             "info": pvd.model_info()})
        elif self.path == "/api/settings/custom_model":
            self._send(200, {"ok": True, **CUS.status()})
        elif self.path == "/api/custom_models":
            self._send(200, {"ok": True, "items": MR.list_models()})
        elif self.path == "/api/models/installed":
            self._send(200, {"ok": True, "items": MA.installed()})
        elif self.path == "/api/jobs/health":
            self._send(200, {"ok": True, **JH.summary()})
        elif self.path == "/api/settings/network":
            self._send(200, {"ok": True, **NP.status()})
        elif self.path == "/api/settings/model_mode":
            self._send(200, {"ok": True, **MMODE.status()})
        elif self.path == "/api/low_spec/status":
            self._send(200, {"ok": True, **LOWSPEC.status()})
        elif self.path == "/api/settings/low_spec":
            self._send(200, {"ok": True, **LOWSPEC.status()})
        elif self.path == "/api/memory/layers":
            self._send(200, {"ok": True, "items": [
                {"layer": n, "text": ML.read(n)} for n in ML.LAYERS]})
        elif self.path == "/api/settings/autostart":
            self._send(200, {"ok": True, **SU.status()})
        elif self.path == "/api/backup/status":
            self._send(200, {"ok": True, **BT.latest()})
        elif self.path == "/api/brief/latest":
            out = sorted(BR.OUT.glob("brief-*.md"))[-1:] if BR.OUT.is_dir() else []
            if out:
                self._send(200, {"ok": True, "path": str(out[0]), "text": out[0].read_text(encoding="utf-8", errors="replace")})
            else:
                self._send(404, {"ok": False, "error": "还没有简报"})
        elif self.path == "/api/roi/dashboard":
            self._send(200, ROI.dashboard())
        elif self.path.startswith("/api/roi/summary"):
            from urllib.parse import urlparse, parse_qs
            qid = parse_qs(urlparse(self.path).query).get("workspace_id", [""])[0]
            self._send(200, ROI.summary(qid))
        elif self.path == "/api/model_packs":
            self._send(200, {"items": MP.installed()})
        elif self.path == "/api/workspaces":
            self._send(200, {"items": TWS.list_workspaces()})
        elif self.path.startswith("/api/workspaces/"):
            wid = self.path.rsplit("/", 1)[-1]
            out = TWS.get(wid)
            if out:
                out["roi"] = ROI.summary(wid)
            self._send(200 if out else 404, out or {"ok": False, "error": "not found"})
        elif self.path == "/api/agents":
            self._send(200, {"items": AR.list_agents()})
        elif self.path == "/api/jobs":
            self._send(200, {"enabled": JR.enabled(), "items": JR.list_jobs(),
                             "worker": JW.status()})
        elif self.path == "/api/jobs/worker/status":
            self._send(200, JW.status())
        elif self.path.startswith("/api/jobs/"):
            parts = self.path.split("/")
            jid = parts[3] if len(parts) > 3 else ""
            if len(parts) > 4 and parts[4] == "events":
                self._send(200, {"events": JR.events(jid)})
            else:
                row = JR.get(jid)
                self._send(200 if row else 404, row or {"error": "not found"})
        elif self.path == "/api/persona":
            self._send(200, {"personas": PD.list_personas(), "active": PD.active_slug()})
        elif self.path == "/api/memory_candidates":
            self._send(200, {"items": MC.pending()})
        elif self.path == "/api/mirrai_memory":
            self._send(200, MMEM.all())
        elif self.path.startswith("/api/emotion"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            t = str((q.get("text") or [""])[0])
            self._send(200, {"state": ME.compute_state(t),
                             "desc": ME.DESC[ME.compute_state(t)]})
        elif self.path == "/api/jarvis":
            self._send(200, {"snapshot": JV.snapshot(PF.current(), tasks),
                             "brief": JV.brief(PF.current(), tasks),
                             "prompt": JV.prompt_block(PF.current(), tasks)})
        elif self.path.startswith("/api/browser/shot"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            name = os.path.basename(str((q.get("name") or [""])[0]))
            p = Path(__file__).resolve().parent / "data" / "browser_shots" / name
            if p.is_file():
                self._send(200, p.read_bytes(), "image/png")
            else:
                self._send(404, {"error": "截图不存在"})
        elif self.path == "/api/qwen_agent":
            self._send(200, {"ok": True, "installed": _qwen_agent_installed(),
                             "enabled": (os.environ.get("PAI_AGENT_ENGINE") or "").lower() == "qwen_agent",
                             "model": os.environ.get("PAI_QWEN_AGENT_MODEL") or os.environ.get("PAI_MODEL_NAME") or "qwen3-4b-instruct:latest"})
        elif self.path.startswith("/api/audit"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            limit = int((q.get("limit") or ["100"])[0])
            self._send(200, {"items": EG.audit_recent(limit)})
        elif self.path == "/api/tools":
            self._send(200, {"enabled": TU.enabled(), "tools_enabled": TOOLS.backend() == TOOLS.BACKEND_ON,
                             "tools": TOOLS.list_unified()})
        elif self.path == "/api/usage/panel":
            self._send(200, {"today": USAGE.today(), "summary": MU.summary()})
        elif self.path == "/api/usage":
            self._send(200, USAGE.today())
        elif self.path == "/api/settings":
            self._send(200, {
                "profile": PF.current(),
                "offline": OFF.enabled(),
                "version": _version(),
                "model": M10.active_provider(),
                "vision": {"configured": VIS.configured(),
                           "model": os.environ.get("PAI_VISION_MODEL") or "Qwen/Qwen3-VL-32B-Instruct",
                           "offline_ocr": OFF.enabled() and os.name == "nt"},
                "search": {"configured": WS.configured(), "provider": "tavily"},
                "asr": {"backend": ASR.backend(), "available": ASR.available()},
                "context": int(os.environ.get("PAI_OLLAMA_NUM_CTX", "16384")),
                "conversations": len((convs.load_from_messages(PF.current())
                                      .get("sessions") or [])),
            })
        elif self.path == "/api/conversations":
            self._send(200, convs.load_from_messages(PF.current()))
        elif self.path == "/api/office/config":
            self._send(200, {"ok": True, "tasks": orch.office_tasks()})
        elif self.path == "/api/memory/health":
            try:
                self._send(200, mem.health(convs, PF.current()))
            except Exception as e:
                self._send(500, {"ok": False, "error": str(e)})
        elif self.path == "/api/memory/graph":
            try:
                self._send(200, {"ok": True, **mem.graph()})
            except Exception as e:
                self._send(500, {"ok": False, "error": str(e)})
        elif self.path.startswith("/api/memory/search"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            query = str((q.get("q") or [""])[0]).strip()
            mode = str((q.get("mode") or ["hybrid"])[0]).strip().lower()
            from pai import embedding as EMB
            if mode == "semantic":
                rows = mem.recall_items_semantic(query, 10) if query else []
                self._send(200, {"ok": True, "mode": "semantic", "query": query,
                                 "backend": EMB.backend(), "items": [
                    {"kind": "semantic", "text": x.get("text") or x.get("summary") or "",
                     "source": "semantic", "score": x.get("relevance", 0),
                     "tags": x.get("tags")} for x in rows]})
            else:
                data = mem.hybrid_search(query, convs, PF.current(), 10,
                                         include_semantic=(mode != "keyword"))
                data["backend"] = EMB.backend()
                self._send(200, {"ok": True, **data})
        elif self.path == "/api/tasks":
            prof = PF.current()
            self._send(200, {"profile": prof,
                             "counts": tasks.counts(prof),
                             "tasks": tasks.list(prof, limit=100)})
        elif self.path == "/api/profile":
            prof = PF.current()
            self._send(200, {"profile": prof,
                             "db": str(PF.db_path(prof)),
                             "skills_dir": str(PF.skills_dir(prof)),
                             "task_counts": tasks.counts(prof),
                             "profiles": list(PF.PROFILES)})
        elif self.path == "/api/skills":
            self._send(200, {"skills": [
                {"name": s.get("name"), "version": s.get("version"),
                 "status": s.get("status"), "approved_by": s.get("approved_by"),
                 "description": s.get("description")}
                for s in skills.list(include_drafts=True)]})
        elif self.path == "/api/history":
            self._send(200, {"episodes": mem.recent_episodes(30)})
        else:
            self._send(404, {"error": "没有这个接口"})

    def _sse(self, event, data):
        raw = "event: %s\ndata: %s\n\n" % (event, json.dumps(data, ensure_ascii=False))
        lock = getattr(self, "_sse_lock", None)
        if lock is None:
            self.wfile.write(raw.encode("utf-8"))
            self.wfile.flush()
            return
        with lock:
            self.wfile.write(raw.encode("utf-8"))
            self.wfile.flush()

    def _sse_chat(self, text, model=None):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        cmd = UNI.handle(text, mem=mem)
        if cmd is not None:
            self._sse("delta", {"text": cmd})
            self._sse("done", {"trace": {"command": text.split(" ", 1)[0]}})
            return
        if MT.is_project_question(text):
            self._sse("delta", {"text": MT.project_answer()})
            self._sse("done", {"trace": {"meta": "project"}})
            return
        if MT.is_briefing_question(text):
            self._sse("delta", {"text": MT.briefing_answer()})
            self._sse("done", {"trace": {"meta": "briefing"}})
            return
        if MT.is_question(text):
            self._sse("delta", {"text": MT.answer()})
            self._sse("done", {"trace": {"meta": "capability"}})
            return
        self._sse_lock = threading.Lock()
        busy_stop = None
        ping_stop = None
        try:
            busy_stop = _busy_start()
            ping_stop = threading.Event()

            def ping():
                while not ping_stop.wait(10):
                    try:
                        self._sse("ping", {})
                    except Exception:
                        return

            threading.Thread(target=ping, daemon=True, name="pai-sse-ping").start()
            self._sse("start", {"text": text, "model": model or ""})
            if OFF.enabled() or (os.environ.get("PAI_MODEL_PROVIDER") or "").lower() == "ollama":
                self._sse("status", {"text": "本地模型准备中；冷启动可能 30–90 秒，之后会快很多"})
            chunks = []
            def emit(delta):
                if delta:
                    chunks.append(delta)
                    self._sse("delta", {"text": delta})
            def emit_reasoning(delta):
                if delta:
                    self._sse("reasoning", {"text": delta})
            with _GEN_LOCK:
                extra = "\n\n".join(x for x in [
                    CR.prompt_block() if CR.enabled() else "",
                    _jarvis_rule(), _emotion_rule(text),
                    _memory_rule(text), _persona_rule(),
                    _relationship_rule(), _skill_rule(text)] if x)
                out = orch.handle(text, channel="web", on_delta=emit,
                                  extra_rule=extra or None,
                                  model=model or None, on_reasoning=emit_reasoning)
            if not chunks:
                self._sse("delta", {"text": out.get("response") or ""})
            saved = UNI.save_code_blocks(out.get("response") or "")
            if saved:
                out.setdefault("trace", {})["code_saved"] = saved
            TS.append(text, out.get("trace") or {}, "sse")
            WSH.observe(text, source="sse")
            CT.auto_from_chat(text, out.get("response") or "", PF.current())
            MC.extract(text)
            SC.auto_semantic(text, out.get("response") or "", PF.current())
            self._sse("done", {"trace": out.get("trace") or {}})
        except Exception as e:
            self._sse("error", {"error": type(e).__name__,
                                "detail": str(e)[:300]})
        finally:
            if ping_stop is not None:
                ping_stop.set()
            if busy_stop is not None:
                busy_stop.set()
            _busy_clear()
            self.close_connection = True

    def _body(self, limit=None):
        """读请求体。返回 (payload, 状态码, 错误说明)；成功时后两者为 (obj, None, None)。"""
        limit = MAX_BODY if limit is None else limit
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, 400, "请求长度非法"
        if n < 0:
            # read(-5) 会抛 ValueError，而且抛在 dispatch 的 try 之外=连接直接断、
            # 页面只看到一个没有响应的请求。长度不合法就该在这里说明白。
            return None, 400, "请求长度非法"
        if n > limit:
            # 不收下这批字节就回 413：剩下的还堵在 socket 里，同连接的下一个请求会读串。
            self.close_connection = True
            return None, 413, f"内容过大（上限 {limit // 1024}KB）"
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}"), None, None
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, 400, "请求体不是合法 JSON"

    def do_POST(self):
        if self._blocked_host():
            return
        if self._blocked_origin():
            return
        payload, code, err = self._body(
            limit=16 * 1024 * 1024
            if self.path in ("/api/extract", "/api/asr", "/api/vision",
                             "/api/doc/index") else None)
        if code:
            self._send(code, {"error": err})
            return
        if any(self.path.split("?", 1)[0].startswith(x) for x in GATED_PREFIXES):
            if not self._ui_gate():
                return
        try:
            if self.path == "/v1/chat/completions":
                key = _local_api_key()
                require = os.environ.get("PAI_LOCAL_API_REQUIRE_KEY", "1").strip().lower() not in ("0", "off", "false", "no")
                if require and key and self.headers.get("Authorization", "") != ("Bearer " + key):
                    self._send(401, {"error": {"message": "invalid api key", "type": "authentication_error"}})
                    return
                msgs = payload.get("messages")
                if not isinstance(msgs, list) or not msgs:
                    self._send(400, {"error": {"message": "messages must be a non-empty list", "type": "invalid_request_error"}})
                    return
                if payload.get("stream") is True:
                    self._send(400, {"error": {"message": "stream is not supported", "type": "invalid_request_error"}})
                    return
                try:
                    temperature = float(payload.get("temperature", 0.3))
                    max_tokens = int(payload.get("max_tokens") or 900)
                except (TypeError, ValueError):
                    self._send(400, {"error": {"message": "temperature/max_tokens must be numeric", "type": "invalid_request_error"}})
                    return
                parts = []
                for m in msgs:
                    role = str((m or {}).get("role") or "user")
                    content = (m or {}).get("content") or ""
                    if isinstance(content, list):
                        content = "".join(str(x.get("text") or "") if isinstance(x, dict) else str(x) for x in content)
                    parts.append("%s: %s" % (role, content))
                prompt = "\n".join(parts).strip()
                if not prompt:
                    self._send(400, {"error": {"message": "messages is empty", "type": "invalid_request_error"}})
                    return
                try:
                    out = M10.complete(prompt, opts={"temperature": temperature,
                                                     "max_tokens": max_tokens})
                    text = str((out[0] or {}).get("output") or "") if out else ""
                except Exception as e:
                    self._send(500, {"error": {"message": "%s: %s" % (type(e).__name__, e), "type": "server_error"}})
                    return
                model = str(payload.get("model") or M10.active_provider())
                self._send(200, {"id": "chatcmpl-%s" % uuid.uuid4().hex[:16], "object": "chat.completion",
                                 "created": int(time.time()), "model": model,
                                 "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                              "finish_reason": "stop"}],
                                 "usage": {"prompt_tokens": max(1, len(prompt) // 2),
                                           "completion_tokens": max(1, len(text) // 2),
                                           "total_tokens": max(1, (len(prompt) + len(text)) // 2)}})
                return
            if self.path == "/api/events":
                text = str(payload.get("text") or "").strip()
                if not text:
                    self._send(400, {"error": "没内容"})
                    return
                self._sse_chat(text[:4000], model=payload.get("model") or None)
                return
            if self.path == "/api/memory/auto":
                try:
                    self._send(200, {"ok": True, **mem.auto_scan(convs, PF.current())})
                except Exception as e:
                    self._send(400, {"ok": False, "error": str(e)})
                return
            if self.path == "/api/memory/classify":
                n = convs.backfill_classify(PF.current()) if convs is not None else 0
                self._send(200, {"ok": True, "updated": n})
                return
            if self.path == "/api/office/batch":
                import base64
                try:
                    if payload.get("texts"):
                        out = orch.office_batch_texts(payload.get("texts") or [],
                                                      str(payload.get("task") or "minutes"),
                                                      str(payload.get("title") or ""))
                    else:
                        items = []
                        for x in (payload.get("files") or []):
                            items.append({"name": str(x.get("name") or "file"),
                                          "data": base64.b64decode(str(x.get("data_b64") or ""), validate=True)})
                        out = orch.office_batch(items, str(payload.get("task") or "minutes"),
                                                str(payload.get("title") or ""))
                    self._send(200, out)
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/office/run":
                try:
                    name = str(payload.get("filename") or "input.txt")
                    raw = base64.b64decode(payload.get("data_b64") or "", validate=True) if payload.get("data_b64") else b""
                    if raw:
                        text, kind = orch.office_extract(name, raw)
                    else:
                        text, kind = str(payload.get("text") or ""), "text"
                    out = orch.office_run(payload.get("task"), text, str(payload.get("title") or ""))
                    self._send(200, {"ok": True, "kind": kind, **out})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/office/export":
                fmt = str(payload.get("format") or "md").lower()
                content = str(payload.get("content") or "")
                if fmt == "docx":
                    body, ctype = orch.office_export("docx", content), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                elif fmt == "xlsx":
                    body, ctype = orch.office_export("xlsx", content), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                else:
                    body, ctype = content.encode("utf-8"), "text/markdown; charset=utf-8"
                self._send(200, body, ctype)
                return
            if self.path == "/api/reminders/read":
                for rid in payload.get("ids") or []:
                    try:
                        mem.mark_outbox_read(int(rid))
                    except Exception:
                        pass
                self._send(200, {"ok": True})
                return
            if self.path == "/api/approvals/execute":
                if not self._ui_gate():
                    return
                if not _native_confirm("审批执行", "确认执行待审批动作？"):
                    self._send(403, {"ok": False, "error": "用户取消了系统确认"})
                    return
                rec = TOK.take(str(payload.get("id") or ""))
                if not rec:
                    self._send(404, {"ok": False, "error": "没有待审批记录"})
                    return
                act = rec.get("action") or {}
                try:
                    if act.get("type") == "desktop":
                        if act.get("kind") == "click":
                            out = DC.click_control(act.get("title") or "", act.get("control") or "", confirm=True)
                        elif act.get("kind") == "type_control":
                            out = DC.type_control(act.get("title") or "", act.get("control") or "",
                                                  act.get("text") or "", confirm=True)
                        else:
                            out = DC.action(act.get("kind") or "focus", act.get("title") or "",
                                            act.get("text") or "", confirm=True)
                    elif act.get("type") == "browser":
                        out = BROW.task(act.get("url") or "", act.get("actions") or [],
                                        confirm=True, headless=bool(act.get("headless")))
                    elif act.get("type") == "mcp":
                        out = MCPC.call(act.get("server") or "", act.get("tool") or "",
                                        act.get("args") or {}, confirm=True)
                    else:
                        self._send(200, {"ok": True, "executed": False,
                                         "reason": "这条审批只发 token，未绑定可执行动作"})
                        return
                    self._send(200, {"ok": bool(out.get("ok")), "executed": True, "result": out})
                except Exception as e:
                    self._send(500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/approvals/approve":
                if not self._ui_gate():
                    return
                # 不再把 token 回给页面；执行请走 /api/approvals/execute。
                ok = bool(TOK.approve_id(payload.get("id")))
                self._send(200, {"ok": ok})
                return
            if self.path == "/api/approvals/deny":
                if not self._ui_gate():
                    return
                self._send(200, {"ok": TOK.deny_id(payload.get("id"))})
                return
            if self.path == "/api/qwen_agent/toggle":
                on = bool(payload.get("enabled"))
                os.environ["PAI_AGENT_ENGINE"] = "qwen_agent" if on else "builtin"
                try:
                    p = PATHS.DATA_DIR / "agent_engine.json"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(json.dumps({"engine": "qwen_agent" if on else "builtin"}), encoding="utf-8")
                except Exception:
                    pass
                self._send(200, {"ok": True, "enabled": on})
                return
            if self.path == "/api/tools/toggle":
                on = bool(payload.get("enabled"))
                TOOLS.set_enabled(on); TU.set_enabled(on); TOOLS.persist(on)
                self._send(200, {"ok": True, "enabled": on, "tools": TOOLS.list_tools()})
                return
            if self.path == "/api/approval/request":
                if not self._ui_gate():
                    return
                kind = str(payload.get("kind") or "").strip()
                if kind not in ("browser", "desktop", "mcp", "code", "login", "persona", "memory", "agent", "workspace"):
                    self._send(400, {"ok": False, "error": "未知审批类型"})
                    return
                self._send(200, {"ok": True, **TOK.issue(kind, str(payload.get("detail") or ""),
                                                       bind=str(payload.get("bind") or ""))})
                return
            if self.path == "/api/simulate":
                tid = str(payload.get("id") or "").strip()
                if not tid:
                    self._send(400, {"ok": False, "error": "缺 id"})
                    return
                code, out = _agentd_call("/simulate", {"id": tid})
                self._send(code, out)
                return
            if self.path == "/api/world/approval/prepare":
                cid = str(payload.get("candidate_id") or "").strip()
                try:
                    p, req = WG.prepare(cid, str(getattr(mem, "db_path", DB)))
                except ValueError as e:
                    self._send(403, {"ok": False, "error": str(e)})
                    return
                self._send(200, {"ok": True, "request": p.name, "id": req["id"],
                                 "kind": req["kind"]})
                return
            if self.path in ("/api/world/approval/approve", "/api/world/approval/execute"):
                name = str(payload.get("request") or "").strip()
                d = WG._dir().resolve()
                rp = (d / name).resolve()
                if rp.parent != d or not rp.name.endswith(".request.json"):
                    self._send(400, {"ok": False, "error": "request 名字不合法"})
                    return
                try:
                    if self.path.endswith("approve"):
                        by = str(payload.get("by") or "").strip()
                        token = str(payload.get("token") or "")
                        expected = os.environ.get("PAI_WORLD_APPROVE_TOKEN") or ""
                        if not expected:
                            self._send(403, {"ok": False, "error": "未配置审批凭据（PAI_WORLD_APPROVE_TOKEN）"})
                            return
                        if by != "Boss" or not hmac.compare_digest(token, expected):
                            self._send(403, {"ok": False, "error": "审批凭据不对"})
                            return
                        ap, appr = WG.approve(str(rp), by)
                        self._send(200, {"ok": True, "approval": ap.name, "used": appr["used"]})
                    else:
                        an = str(payload.get("approval") or "").strip()
                        ap = (d / an).resolve()
                        if ap.parent != d or not ap.name.endswith(".approval.json"):
                            self._send(400, {"ok": False, "error": "approval 名字不合法"})
                            return
                        oid, log = WG.execute(str(rp), str(ap))
                        self._send(200, {"ok": True, "object_id": oid, "log": log.name})
                except ValueError as e:
                    self._send(403, {"ok": False, "error": str(e)})
                return
            if self.path == "/api/memory/card":
                if (os.environ.get("PAI_MEMORY_WRITE") or "").strip().lower() != "on":
                    self._send(403, {"ok": False, "error": "今日一记未开（PAI_MEMORY_WRITE=on）"})
                    return
                text = " ".join(str(payload.get(k) or "") for k in ("what", "why", "next_step"))
                if _looks_secret(text):
                    self._send(400, {"ok": False, "error": "内容像密钥，未保存"})
                    return
                try:
                    out = DM.save(mem, payload.get("kind"), payload.get("person"),
                                  payload.get("what"), payload.get("why"),
                                  payload.get("next_step"), payload.get("at"))
                except ValueError as e:
                    self._send(400, {"ok": False, "error": str(e)})
                    return
                self._send(200, {"ok": True, **out})
                return
            if self.path == "/api/world/add":
                if (os.environ.get("PAI_WORLD_WRITE") or "").strip().lower() != "on":
                    self._send(403, {"ok": False, "error": "手工写世界未开（PAI_WORLD_WRITE=on）"})
                    return
                try:
                    db_path = Path(getattr(mem, "db_path", DB)).resolve()
                except OSError:
                    db_path = Path(DB).resolve()
                if db_path == Path(DB).resolve():
                    self._send(403, {"ok": False, "error": "真库禁止手工写世界；请用临时库"})
                    return
                if str(payload.get("kind") or "entity") != "entity":
                    self._send(400, {"ok": False, "error": "v0 只支持 kind=entity"})
                    return
                typ = str(payload.get("type") or "person").strip()
                name = str(payload.get("name") or "").strip()
                aliases = payload.get("aliases") or []
                if isinstance(aliases, str):
                    aliases = [x.strip() for x in aliases.split(",") if x.strip()]
                row = mem.conn.execute(
                    "SELECT id FROM episodes ORDER BY id DESC LIMIT 1").fetchone()
                src = int(row["id"]) if row else 0
                from pai.world import World
                try:
                    eid = World(mem).upsert_entity(
                        typ, name, aliases=aliases, source_episode=src)
                except ValueError as e:
                    self._send(400, {"ok": False, "error": str(e)})
                    return
                self._send(200, {"ok": True, "id": eid, "source_episode": src})
                return
            if self.path in ("/api/approve", "/api/reject"):
                tid = str(payload.get("id") or "").strip()
                if not tid:
                    self._send(400, {"ok": False, "error": "缺 id"})
                    return
                if self.path == "/api/approve":
                    code, out = _agentd_call("/approve", {"id": tid})
                else:
                    code, out = _agentd_call("/cancel", {"id": tid,
                                                          "reason": str(payload.get("reason") or "前端拒绝")})
                self._send(code, out)
                return
            if self.path == "/api/corrections/approve":
                cid = str(payload.get("id") or "")
                if not cid:
                    self._send(400, {"ok": False, "error": "缺 id"})
                    return
                try:
                    self._send(200, {"ok": True, "item": CR.approve(cid)})
                except KeyError:
                    self._send(404, {"ok": False, "error": "没有这条纠正"})
                return
            if self.path == "/api/corrections/disable":
                cid = str(payload.get("id") or "")
                if not cid:
                    self._send(400, {"ok": False, "error": "缺 id"})
                    return
                try:
                    self._send(200, {"ok": True, "item": CR.disable(cid)})
                except KeyError:
                    self._send(404, {"ok": False, "error": "没有这条纠正"})
                return
            if self.path.startswith("/api/conversations/") and self.path.endswith("/meta"):
                cid = self.path[len("/api/conversations/"):-len("/meta")].strip("/")
                try:
                    out = convs.update_meta(PF.current(), cid,
                                            payload.get("title"),
                                            payload.get("pinned"))
                except ValueError as exc:
                    self._send(400, {"ok": False, "error": str(exc)})
                    return
                self._send(200, out)
                return
            if self.path == "/api/conversations/delete":
                try:
                    out = convs.delete_conversation(PF.current(),
                                                    str(payload.get("id") or ""))
                except ValueError as exc:
                    self._send(400, {"ok": False, "error": str(exc)})
                    return
                self._send(200, out)
                return
            if self.path.startswith("/api/conversations/") and self.path.endswith("/replace"):
                cid = self.path[len("/api/conversations/"):-len("/replace")].strip("/")
                try:
                    out = convs.replace_messages(
                        PF.current(), cid, payload.get("title"),
                        payload.get("messages") or [])
                except ValueError as exc:
                    self._send(400, {"ok": False, "error": str(exc)})
                    return
                self._send(200, out)
                return
            if self.path.startswith("/api/conversations/") and self.path.endswith("/messages"):
                cid = self.path[len("/api/conversations/"):-len("/messages")].strip("/")
                try:
                    out = convs.append_message(
                        PF.current(), cid, payload.get("title"),
                        payload.get("message") or {})
                except ValueError as exc:
                    self._send(400, {"ok": False, "error": str(exc)})
                    return
                self._send(200, out)
                return
            if self.path == "/api/conversations":
                try:
                    out = convs.save(PF.current(), payload)
                except ValueError as exc:
                    self._send(400, {"ok": False, "error": str(exc)})
                    return
                self._send(200, out)
                return
            if self.path == "/api/tasks":
                title = str(payload.get("title") or "").strip()
                goal = str(payload.get("goal") or "").strip()
                if not title or not goal:
                    self._send(400, {"ok": False, "error": "缺 title/goal"})
                    return
                row = tasks.create(PF.current(), title, goal,
                                   owner=str(payload.get("owner") or "boss"),
                                   idempotency_key=str(payload.get("idempotency_key") or ""))
                self._send(200, {"ok": True, "task": row})
                return
            if self.path in ("/api/tasks/start", "/api/tasks/retry",
                             "/api/tasks/takeover", "/api/tasks/transition",
                             "/api/tasks/cancel"):
                tid = str(payload.get("id") or "").strip()
                if not tid:
                    self._send(400, {"ok": False, "error": "缺 id"})
                    return
                try:
                    if self.path.endswith("/start"):
                        row = tasks.start(tid, approver=payload.get("approver"),
                                          token=payload.get("approval_token"))
                    elif self.path.endswith("/retry"):
                        row = tasks.retry(tid)
                    elif self.path.endswith("/takeover"):
                        row = tasks.take_over(tid, str(payload.get("owner") or "boss"))
                    elif self.path.endswith("/cancel"):
                        # 退单也要留痕：状态、原因、人
                        row = tasks.cancel(tid, str(payload.get("reason") or ""),
                                           owner=payload.get("owner"))
                    else:
                        row = tasks.transition(tid, str(payload.get("state") or ""),
                                               error=payload.get("error"),
                                               result=payload.get("result"),
                                               owner=payload.get("owner"),
                                               approver=payload.get("approver"),
                                               token=payload.get("approval_token"))
                except ValueError as exc:
                    self._send(400, {"ok": False, "error": str(exc)})
                    return
                self._send(200, {"ok": True, "task": row})
                return
            if self.path in ("/api/skills/approve", "/api/skills/disable",
                             "/api/skills/rollback"):
                name = str(payload.get("name") or "").strip()
                if not name:
                    self._send(400, {"ok": False, "error": "缺 name"})
                    return
                if self.path.endswith("/approve"):
                    item = skills.approve(name, str(payload.get("by") or "boss"))
                elif self.path.endswith("/disable"):
                    item = skills.disable(name)
                else:
                    item = skills.rollback(name)
                self._send(200, {"ok": True, "skill": {
                    "name": item.get("name"), "version": item.get("version"),
                    "status": item.get("status"), "approved_by": item.get("approved_by")}})
                return
            if self.path == "/api/vision":
                raw = str(payload.get("data_b64") or "")
                if not raw:
                    self._send(400, {"ok": False, "error": "缺 data_b64"})
                    return
                try:
                    data = base64.b64decode(raw, validate=True)
                    if OFF.enabled():
                        text = OCR.extract(data)
                        self._send(200, {"ok": True, "text": text,
                                         "backend": "windows-ocr", "offline": True})
                        return
                    text = VIS.describe(
                        data,
                        str(payload.get("mime") or "image/png"),
                        str(payload.get("question") or "请描述图片内容，并提取其中可见文字。"))
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                    return
                self._send(200, {"ok": True, "text": text, "configured": VIS.configured()})
                return
            if self.path == "/api/doc/index":
                try:
                    out = DI.index(PF.current(), payload.get("name"), payload.get("text"))
                except ValueError as exc:
                    self._send(400, {"ok": False, "error": str(exc)})
                    return
                self._send(200, dict(out, ok=True))
                return
            if self.path == "/api/doc/search":
                try:
                    out = DI.search(PF.current(), payload.get("doc_id"),
                                    payload.get("q"), int(payload.get("k") or 5))
                except Exception as exc:
                    self._send(400, {"ok": False, "error": str(exc)})
                    return
                self._send(200, {"ok": True, "chunks": out})
                return
            if self.path in ("/api/proactive/approve", "/api/proactive/reject"):
                pid = str(payload.get("id") or "")
                try:
                    out = (PA.approve(pid, PF.current()) if self.path.endswith("approve")
                           else PA.reject(pid))
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                    return
                self._send(200, dict(out, ok=out.get("ok", False)))
                return
            if self.path == "/api/continuity/save":
                try:
                    out = CT.save(PF.current(), payload.get("title"), payload.get("last_step"),
                                  payload.get("next_step"), payload.get("evidence"),
                                  payload.get("due"), str(payload.get("id") or ""))
                except ValueError as exc:
                    self._send(400, {"ok": False, "error": str(exc)})
                    return
                self._send(200, {"ok": True, "thread": out})
                return
            if self.path == "/api/continuity/close":
                try:
                    out = CT.close(PF.current(), str(payload.get("id") or ""))
                except ValueError as exc:
                    self._send(400, {"ok": False, "error": str(exc)})
                    return
                self._send(200, {"ok": True, "thread": out})
                return
            if self.path == "/api/browser/login":
                if not _approval_ok(payload, "login"):
                    self._send(403, {"ok": False, "error": "缺少/过期/重复的审批 token"})
                    return
                out = BROW.start_login(str(payload.get("url") or ""), payload.get("confirm") is True)
                self._send(200, out)
                return
            if self.path == "/api/browser/task":
                if not _approval_ok(payload, "browser"):
                    self._send(403, {"ok": False, "error": "缺少/过期/重复的审批 token"})
                    return
                out = BROW.task(str(payload.get("url") or ""), payload.get("actions") or [],
                              payload.get("confirm") is True,
                              bool(payload.get("headless")))
                self._send(200, out)
                return
            if self.path == "/api/desktop/action":
                if not _approval_ok(payload, "desktop"):
                    self._send(403, {"ok": False, "error": "缺少/过期/重复的审批 token"})
                    return
                out = DC.action(str(payload.get("kind") or ""), str(payload.get("title") or ""),
                                str(payload.get("text") or ""), payload.get("confirm") is True)
                self._send(200, out)
                return
            if self.path == "/api/mcp/servers":
                try:
                    action = str(payload.get("action") or "add")
                    if action == "remove":
                        out = MCPC.remove_server(payload.get("name"))
                    else:
                        args = payload.get("args") or []
                        if isinstance(args, str):
                            args = json.loads(args or "[]")
                        out = MCPC.add_server(payload.get("name"), payload.get("command"),
                                              args, payload.get("cwd") or "")
                    self._send(200, {"ok": True, "servers": out if isinstance(out, list) else out.get("servers", [])})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/mcp/tools":
                try:
                    out = MCPC.list_tools(str(payload.get("server") or ""))
                except Exception as exc:
                    self._send(500, {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})
                    return
                self._send(200, {"ok": True, "tools": out})
                return
            if self.path == "/api/mcp/call":
                if not _approval_ok(payload, "mcp"):
                    self._send(403, {"ok": False, "error": "缺少/过期/重复的审批 token"})
                    return
                out = MCPC.call(str(payload.get("server") or ""), str(payload.get("tool") or ""),
                              payload.get("args") or {}, payload.get("confirm") is True)
                self._send(200 if (out.get("ok") or out.get("needs_approval")) else 400, out)
                return
            if self.path == "/api/backup/create":
                try:
                    out = BK.create()
                except Exception as exc:
                    self._send(500, {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})
                    return
                self._send(200, out)
                return
            if self.path in ("/api/writing/style/save",
                             "/api/writing/style/sample",
                             "/api/writing/style/clear"):
                try:
                    if self.path.endswith("/save"):
                        out = WST.save(PF.current(), payload.get("name"),
                                       payload.get("tone"), payload.get("notes"))
                    elif self.path.endswith("/sample"):
                        out = WST.add_sample(PF.current(), payload.get("text"))
                    else:
                        out = WST.clear(PF.current())
                except ValueError as exc:
                    self._send(400, {"ok": False, "error": str(exc)})
                    return
                self._send(200, {"ok": True, "style": out})
                return
            if self.path == "/api/writing":
                try:
                    prompt = WR.build_prompt(
                        payload.get("task"), payload.get("topic"),
                        payload.get("audience"), payload.get("tone"),
                        payload.get("length"), payload.get("material"),
                        style_block=WST.block(PF.current()))
                    out = orch.handle(prompt, channel="web-writing", ephemeral=True)
                except ValueError as exc:
                    self._send(400, {"ok": False, "error": str(exc)})
                    return
                except Exception as exc:
                    self._send(500, {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})
                    return
                self._send(200, {"ok": True, "response": out.get("response") or "",
                                 "model": out.get("models") or []})
                return
            if self.path.startswith("/api/code/"):
                try:
                    action = self.path.rsplit("/", 1)[-1]
                    root = str(payload.get("root") or "")
                    if action == "read":
                        out = CODE.read_file(root, payload.get("path"))
                    elif action == "search":
                        out = {"results": CODE.search(root, payload.get("q"), 30)}
                    elif action == "apply":
                        if not _approval_ok(payload, "code"):
                            self._send(403, {"ok": False, "error": "缺少/过期/重复的审批 token"})
                            return
                        out = CODE.apply_file(
                            root, payload.get("path"), payload.get("content"),
                            str(payload.get("expected_sha") or ""),
                            payload.get("confirm") is True)
                    elif action == "apply_batch":
                        if not _approval_ok(payload, "code"):
                            self._send(403, {"ok": False, "error": "缺少/过期/重复的审批 token"})
                            return
                        out = CODE.apply_batch(root, payload.get("changes") or [],
                                               payload.get("confirm") is True)
                    elif action == "rollback":
                        if not _approval_ok(payload, "code"):
                            self._send(403, {"ok": False, "error": "缺少/过期/重复的审批 token"})
                            return
                        out = CODE.rollback_file(root, payload.get("path"))
                    elif action == "test":
                        if not _approval_ok(payload, "code"):
                            self._send(403, {"ok": False, "error": "缺少/过期/重复的审批 token"})
                            return
                        out = CODE.run_tests(root, payload.get("command")
                                             or "python -m unittest discover -s tests -p test_*.py")
                    else:
                        raise CODE.CodeError("未知代码动作")
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                    return
                self._send(200, dict(out, ok=True))
                return
            if self.path == "/api/local_search":
                q = str(payload.get("q") or "").strip()
                if not q:
                    self._send(400, {"ok": False, "error": "缺 q"})
                    return
                self._send(200, {"ok": True, "results": LSEARCH.search(mem, q, 5)})
                return
            if self.path == "/api/search":
                q = str(payload.get("q") or "").strip()
                if not q:
                    self._send(400, {"ok": False, "error": "缺 q"})
                    return
                try:
                    out = WS.search(q, int(payload.get("limit") or 5))
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                    return
                self._send(200, dict(out, ok=True, configured=WS.configured()))
                return
            if self.path == "/api/meeting/start":
                try:
                    SASR.cleanup()
                    sid = SASR.meeting_start()
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                    return
                self._send(200, {"ok": True, "sid": sid})
                return
            if self.path == "/api/meeting/chunk":
                sid = str(payload.get("sid") or "")
                try:
                    pcm = base64.b64decode(str(payload.get("data_b64") or ""), validate=True)
                    out = SASR.meeting_feed(sid, pcm)
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                    return
                self._send(200, dict(out, ok=True))
                return
            if self.path == "/api/meeting/stop":
                try:
                    out = SASR.meeting_stop(str(payload.get("sid") or ""))
                    self._send(200, out)
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/asr/stream/start":
                try:
                    SASR.cleanup()
                    sid = SASR.start()
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                    return
                self._send(200, {"ok": True, "sid": sid})
                return
            if self.path == "/api/asr/stream/chunk":
                sid = str(payload.get("sid") or "")
                try:
                    pcm = base64.b64decode(str(payload.get("data_b64") or ""), validate=True)
                    out = SASR.feed(sid, pcm)
                    if not out.get("punctuated"):
                        out["punctuated"] = PT.punctuate(out.get("text") or "", final=False)
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                    return
                self._send(200, dict(out, ok=True))
                return
            if self.path == "/api/asr/stream/stop":
                sid = str(payload.get("sid") or "")
                try:
                    text = SASR.stop(sid)
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                    return
                self._send(200, {"ok": True, "text": text, "punctuated": PT.punctuate(text), "raw": text})
                return
            if self.path == "/api/asr":
                raw = str(payload.get("data_b64") or "")
                if not raw:
                    self._send(400, {"ok": False, "error": "缺 data_b64"})
                    return
                try:
                    data = base64.b64decode(raw, validate=True)
                except Exception:
                    self._send(400, {"ok": False, "error": "base64 不合法"})
                    return
                tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                try:
                    tmp.write(data)
                    tmp.close()
                    text = ASR.transcribe(tmp.name)
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                    return
                finally:
                    try:
                        os.unlink(tmp.name)
                    except OSError:
                        pass
                self._send(200, {"ok": True, "text": PT.punctuate(text), "raw": text, "backend": ASR.backend()})
                return
            if self.path == "/api/extract":
                name = str(payload.get("name") or "").strip()
                raw = str(payload.get("data_b64") or "")
                if not name or not raw:
                    self._send(400, {"ok": False, "error": "缺 name/data_b64"})
                    return
                try:
                    data = base64.b64decode(raw, validate=True)
                    text = FE.extract(name, data)
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                    return
                self._send(200, {"ok": True, "name": name, "text": text})
                return
            if self.path == "/api/chat":
                text = str(payload.get("text") or "").strip()
                if not text:
                    self._send(400, {"error": "没内容"})
                    return
                cmd = UNI.handle(text, mem=mem)
                if cmd is not None:
                    self._send(200, {"response": cmd, "trace": {"command": text.split(" ", 1)[0]}})
                    return
                if MT.is_project_question(text):
                    self._send(200, {"response": MT.project_answer(),
                                     "trace": {"meta": "project"}})
                    return
                if MT.is_briefing_question(text):
                    self._send(200, {"response": MT.briefing_answer(),
                                     "trace": {"meta": "briefing"}})
                    return
                if MT.is_question(text):
                    self._send(200, {"response": MT.answer(),
                                     "trace": {"meta": "capability"}})
                    return
                if not _looks_secret(text):
                    blocked = RG.gate(text)
                else:
                    blocked = None
                if blocked is not None:
                    TS.append(text, blocked.get("trace") or {}, "gate")
                    self._send(200, blocked)
                    return
                ephemeral = bool(payload.get("ephemeral")) or payload.get("remember") is False
                model = str(payload.get("model") or "").strip() or None
                extra = "\n\n".join(x for x in [
                    CR.prompt_block() if CR.enabled() else "",
                    _jarvis_rule(), _emotion_rule(text),
                    _memory_rule(text), _persona_rule(),
                    _relationship_rule(), _skill_rule(text)] if x)
                out = orch.handle(text, channel="web", extra_rule=extra or None,
                                  ephemeral=ephemeral, model=model)
                saved = UNI.save_code_blocks(out.get("response") or "")
                if saved:
                    out.setdefault("trace", {})["code_saved"] = saved
                out.setdefault("trace", {})["r0_intent"] = RG.classify(text)
                if CR.enabled():
                    prop = CR.propose(text)
                    if prop:
                        out["trace"]["correction_proposal"] = prop
                    out["trace"].update(CR.trace_fields())
                TS.append(text, out["trace"], "chat")
                WSH.observe(text, source="chat")
                CT.auto_from_chat(text, out.get("response") or "", PF.current())
                MC.extract(text)
                SC.auto_semantic(text, out.get("response") or "", PF.current())
                if M14.speaking_enabled():
                    M14.speak(out["response"])
                self._send(200, {"response": out["response"], "trace": out["trace"]})
            if self.path == "/api/jobs/worker/dry-run":
                try:
                    self._send(200, JW.run_once(dry_run=True))
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/jobs/worker/run":
                if not bool(payload.get("confirm")):
                    self._send(400, {"ok": False, "error": "真实执行必须 confirm=true"})
                    return
                try:
                    self._send(200, JW.run_once(dry_run=False))
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/onboarding/complete":
                self._send(200, {"ok": True, **OB.complete()})
                return
            if self.path == "/api/providers":
                try:
                    action = str(payload.get("action") or "set")
                    if action == "delete":
                        out = PR.delete(payload.get("name"))
                    else:
                        out = PR.set_key(payload.get("name"), payload.get("key"),
                                         payload.get("base_url"))
                        PR.apply()
                    self._send(200, {"ok": True, **out})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/settings/custom_model":
                try:
                    self._send(200, {"ok": True, **CUS.select(str(payload.get("name") or ""))})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/ai_provider/chat":
                try:
                    msgs = payload.get("messages") or []
                    if not isinstance(msgs, list) or not msgs:
                        raise ValueError("messages 不能为空")
                    out = AIP.selected().chat(msgs)
                    self._send(200, {"ok": True, **out})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/custom_models":
                try:
                    action = str(payload.get("action") or "save")
                    if action == "test":
                        out = MR.test(payload)
                    elif action == "delete":
                        out = MR.delete(payload.get("name"))
                    else:
                        out = MR.save(payload)
                    self._send(200, {"ok": True, **out})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/models/pull":
                try:
                    self._send(200, {"ok": True, **MA.pull(payload.get("name"))})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/plans/reflect":
                if not self._ui_gate():
                    return
                try:
                    pid = str(payload.get("id") or "")
                    plan = JR.get_plan(pid)
                    if not plan:
                        raise ValueError("规划不存在")
                    jobs = JR.plan_jobs(pid)
                    body = "\n".join("步骤 %d：%s\n结果：%s" % (
                        j.get("step_index", 0) + 1, j.get("title"), (j.get("result") or "")[:2000])
                        for j in jobs)
                    prompt = ("你是独立质检员。检查下面计划的执行结果，只指出错漏/未完成/前后矛盾，"
                              "结论必须明确：通过，或列出要改的地方。\n目标：%s\n%s" % (plan.get("goal"), body))
                    outs = M10.complete(prompt, opts={"temperature": 0.2})
                    text = str((outs[0] if outs else {}).get("output") or "")
                    JR.set_plan_reflection(pid, text)
                    EG.audit("plan_reflect", "medium", "allow", pid, text[:500])
                    self._send(200, {"ok": True, "reflection": text, "plan": JR.get_plan(pid)})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/plans/file":
                if not self._ui_gate():
                    return
                try:
                    out = JR.create_file_proposal(payload.get("filename"), payload.get("content"),
                                                  payload.get("id") or "", int(payload.get("step_index") or 0))
                    self._send(200, out)
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/plans/step":
                if not self._ui_gate():
                    return
                if not _native_confirm("规划执行", "确认执行下一步？"):
                    self._send(403, {"ok": False, "error": "用户取消了系统确认"})
                    return
                try:
                    pid = str(payload.get("id") or "")
                    jobs = [x for x in JR.plan_jobs(pid) if x.get("status") == "pending"]
                    if not jobs:
                        raise ValueError("没有待执行步骤")
                    j = jobs[0]
                    dep = j.get("depends_on") or ""
                    if dep and (JR.get(dep) or {}).get("status") != "done":
                        raise ValueError("上一步未完成")
                    JR.approve(j["id"], "Boss")
                    out = JW.run_once(dry_run=False)
                    self._send(200, {"ok": True, "job": JR.get(j["id"]), "result": out})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/inbox/ingest":
                if not self._ui_gate():
                    return
                if not _native_confirm("文档导入", "确认把收件箱文档转成待审批？"):
                    self._send(403, {"ok": False, "error": "用户取消了系统确认"})
                    return
                try:
                    name, content, chars = FE.ingest_inbox(str(payload.get("name") or ""))
                    out = JR.create_file_proposal(name, content)
                    self._send(200, {"ok": True, "chars": chars, "proposal": out})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/files/approve":
                if not self._ui_gate():
                    return
                if not _native_confirm("文件写入", "确认把文件写入 workspace？"):
                    self._send(403, {"ok": False, "error": "用户取消了系统确认"})
                    return
                try:
                    self._send(200, JR.approve_file_proposal(payload.get("id")))
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/files/reject":
                if not self._ui_gate():
                    return
                self._send(200, JR.reject_file_proposal(payload.get("id")))
                return
            if self.path == "/api/network/allow":
                try:
                    host = str(payload.get("host") or "").strip()
                    if not host:
                        raise ValueError("host 为空")
                    action = str(payload.get("action") or "add")
                    out = NP.remove_host(host) if action == "remove" else NP.allow_host(host)
                    EG.audit("network_allow", "low", "allow", host, action)
                    self._send(200, {"ok": True, **out})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/settings/network":
                try:
                    self._send(200, {"ok": True, **NP.set_online(bool(payload.get("online")))})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/settings/model_mode":
                try:
                    self._send(200, {"ok": True, **MMODE.apply(str(payload.get("mode") or ""))})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/low_spec/unload":
                if not self._ui_gate():
                    return
                try:
                    self._send(200, {"ok": True, **LOWSPEC.unload_models()})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/settings/low_spec":
                try:
                    out = LOWSPEC.enable() if bool(payload.get("enabled")) else LOWSPEC.disable()
                    self._send(200, {"ok": True, **out})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/settings/autostart":
                try:
                    enabled = bool(payload.get("enabled"))
                    out = SU.enable() if enabled else SU.disable()
                    self._send(200, {"ok": True, **out})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/backup/run":
                try:
                    self._send(200, {"ok": True, **BT.run()})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/capability/run":
                try:
                    name = str(payload.get("name") or "").strip()
                    q = str(payload.get("query") or "").strip()
                    text = {"memory": "查记忆 " + q, "book": "查书 " + q,
                            "brief": "今日简报"}.get(name)
                    if not text:
                        raise ValueError("只读能力只允许 memory/book/brief")
                    out = CAPR.dispatch(text, mem=mem)
                    if out is None:
                        raise ValueError("能力未命中")
                    self._send(200, {"ok": True, "capability": out["trace"].get("capability"),
                                     "confidence": out["trace"].get("capability_confidence"),
                                     "text": out["response"]})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/backfill/workspace":
                if not _approval_ok(payload, "workspace"):
                    self._send(403, {"ok": False, "error": "缺少/过期/重复的审批 token"})
                    return
                try:
                    self._send(200, WB.backfill(str(payload.get("workspace_id") or ""),
                                               str(payload.get("title") or "历史作业"),
                                               int(payload.get("limit") or 0)))
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/model_packs/route":
                try:
                    out = MP.route(str(payload.get("text") or ""), str(payload.get("pack_id") or "qwen3-0.6b"))
                    self._send(200, {"ok": True, **out})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/workspaces":
                try:
                    self._send(200, {"ok": True, "workspace": TWS.create(
                        str(payload.get("title") or ""), str(payload.get("owner") or "Boss"))})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path.startswith("/api/workspaces/"):
                parts = self.path.strip("/").split("/")
                wid = parts[2] if len(parts) > 2 else ""
                act = parts[3] if len(parts) > 3 else ""
                try:
                    if act == "items":
                        out = TWS.add_item(wid, str(payload.get("type") or "note"),
                                          str(payload.get("ref_id") or ""),
                                          str(payload.get("title") or ""),
                                          str(payload.get("body") or ""))
                        self._send(200, {"ok": True, "item": out})
                    elif act == "status":
                        self._send(200, {"ok": True, "workspace": TWS.set_status(wid, str(payload.get("status") or ""))})
                    else:
                        self._send(404, {"ok": False, "error": "未知工作区动作"})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/agents":
                if not _approval_ok(payload, "agent"):
                    self._send(403, {"ok": False, "error": "缺少/过期/重复的审批 token"})
                    return
                try:
                    self._send(200, {"ok": True, "agent": AR.register(
                        str(payload.get("name") or ""), str(payload.get("owner") or "Boss"),
                        payload.get("scopes") or None)})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path.startswith("/api/agents/"):
                if not _approval_ok(payload, "agent"):
                    self._send(403, {"ok": False, "error": "缺少/过期/重复的审批 token"})
                    return
                parts = self.path.split("/")
                aid = parts[3] if len(parts) > 3 else ""
                act = parts[4] if len(parts) > 4 else ""
                try:
                    out = AR.revoke(aid) if act == "revoke" else AR.enable(aid)
                    self._send(200, {"ok": True, "agent": out})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/jobs":
                if not JR.enabled():
                    self._send(400, {"ok": False, "error": "PAI_JOB_RUNNER 未开启"})
                    return
                try:
                    aid = str(payload.get("agent_id") or "")
                    if aid and not AR.allowed(aid, "job:create"):
                        raise ValueError("agent 无 job:create 权限或已停用：%s" % aid)
                    job = JR.create(str(payload.get("title") or ""),
                                    str(payload.get("prompt") or ""),
                                    int(payload.get("ttl_hours") or 24),
                                    str(payload.get("agent") or ""),
                                    str(payload.get("cwd") or ""),
                                    str(payload.get("access") or "read"),
                                    str(payload.get("action_type") or "noop"),
                                    payload.get("action_payload") or {},
                                    aid, str(payload.get("workspace_id") or ""),
                                    float(payload.get("roi_estimate_minutes") or 0))
                    self._send(200, {"ok": True, "job": job})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path.startswith("/api/jobs/"):
                parts = self.path.split("/")
                jid = parts[3] if len(parts) > 3 else ""
                action = parts[4] if len(parts) > 4 else ""
                try:
                    if action == "approve":
                        job = JR.approve(jid, str(payload.get("by") or "Boss"))
                    elif action == "reject":
                        job = JR.reject(jid, str(payload.get("by") or "Boss"))
                    elif action == "start":
                        job = JR.start(jid)
                    elif action == "wait":
                        job = JR.wait(jid, str(payload.get("reason") or ""))
                    elif action == "resume":
                        job = JR.resume(jid, str(payload.get("answer") or ""))
                    elif action == "finish":
                        job = JR.finish(jid, str(payload.get("result") or ""))
                    elif action == "fail":
                        job = JR.fail(jid, str(payload.get("error") or ""))
                    else:
                        self._send(400, {"ok": False, "error": "未知动作"})
                        return
                    self._send(200, {"ok": True, "job": job})
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/relationship/event":
                try:
                    self._send(200, REL.add_event(str(payload.get("kind") or "事"),
                                                  str(payload.get("text") or "")))
                except Exception as e:
                    self._send(400, {"ok": False, "error": str(e)})
                return
            if self.path == "/api/relationship/mood":
                try:
                    self._send(200, REL.mood(int(payload.get("score") or 0),
                                             str(payload.get("note") or "")))
                except Exception as e:
                    self._send(400, {"ok": False, "error": str(e)})
                return
            if self.path == "/api/graduation":
                if not _approval_ok(payload, "persona"):
                    self._send(403, {"ok": False, "error": "缺少/过期/重复的审批 token"})
                    return
                try:
                    self._send(200, GR.farewell(str(payload.get("slug") or "") or None))
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/persona/import":
                if not _approval_ok(payload, "persona"):
                    self._send(403, {"ok": False, "error": "缺少/过期/重复的审批 token"})
                    return
                try:
                    res = PD.import_text(str(payload.get("text") or ""),
                                         str(payload.get("name") or "未命名"),
                                         bool(payload.get("use_llm", True)),
                                         str(payload.get("slug") or "") or None)
                    self._send(200, res)
                except Exception as e:
                    self._send(400, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                return
            if self.path == "/api/persona/activate":
                if not _approval_ok(payload, "persona"):
                    self._send(403, {"ok": False, "error": "缺少/过期/重复的审批 token"})
                    return
                try:
                    self._send(200, PD.save_active(str(payload.get("slug") or "")))
                except Exception as e:
                    self._send(400, {"ok": False, "error": str(e)})
                return
            if self.path == "/api/memory_candidates/approve":
                if not _approval_ok(payload, "memory"):
                    self._send(403, {"ok": False, "error": "缺少/过期/重复的审批 token"})
                    return
                try:
                    self._send(200, MC.approve(str(payload.get("id") or ""),
                                                str(payload.get("category") or "memory"),
                                                bool(payload.get("anchor"))))
                except Exception as e:
                    self._send(400, {"ok": False, "error": str(e)})
                return
            if self.path == "/api/memory_candidates/reject":
                if not _approval_ok(payload, "memory"):
                    self._send(403, {"ok": False, "error": "缺少/过期/重复的审批 token"})
                    return
                try:
                    self._send(200, MC.reject(str(payload.get("id") or "")))
                except Exception as e:
                    self._send(400, {"ok": False, "error": str(e)})
                return
            if self.path == "/api/mirrai_memory":
                try:
                    out = MMEM.add(str(payload.get("title") or ""),
                                 str(payload.get("body") or ""),
                                 str(payload.get("category") or "memory"),
                                 bool(payload.get("anchor")))
                    self._send(200, out)
                except Exception as e:
                    self._send(400, {"ok": False, "error": str(e)})
                return
            if self.path == "/api/model":
                provider = str(payload.get("provider") or "").strip().lower()
                model = str(payload.get("model") or "").strip()
                if provider not in ("ollama", "deepseek", "openai", "anthropic", "mock"):
                    self._send(400, {"ok": False, "error": "不支持的 provider"})
                    return
                key_name = {"deepseek": "DEEPSEEK_API_KEY", "openai": "OPENAI_API_KEY",
                            "anthropic": "ANTHROPIC_API_KEY"}.get(provider)
                if key_name and not os.environ.get(key_name):
                    self._send(400, {"ok": False, "error": "缺少 %s，先填 cloud.env" % key_name})
                    return
                os.environ["PAI_MODEL_PROVIDER"] = provider
                if model:
                    os.environ["PAI_MODEL_NAME"] = model
                MS.save(provider, model)
                offline = NP.is_offline()
                self._send(200, {"ok": True, "provider": M10.active_provider(),
                                 "model": os.environ.get("PAI_MODEL_NAME") or "",
                                 "offline": offline})
                return
            elif self.path == "/api/voice":
                on = bool(payload.get("enabled"))
                M14.set_enabled(on)
                self._send(200, {"enabled": M14.speaking_enabled(), "backend": M14.backend()})
            elif self.path == "/api/reflect":
                self._send(200, M4.reflect(mem))
            elif self.path == "/api/tick":
                self._send(200, {"fired": orch.tick(datetime.datetime.now())})
            elif self.path == "/api/forget":
                self._send(200, {"forgotten": mem.forget_fact(
                    int(payload["id"]), str(payload.get("reason") or ""))})
            elif self.path == "/api/retire":
                r = mem.set_principles(
                    [{"op": "retire", "id": int(payload["id"])}],
                    str(payload.get("reason") or ""))
                self._send(200, r)
            else:
                self._send(404, {"error": "没有这个接口"})
        except (KeyError, ValueError, TypeError) as e:
            traceback.print_exc()
            self._send(400, {"error": f"参数不对：{type(e).__name__}"})
        except Exception:
            # 兜底：绝不把异常文本（可能含路径/上下文）回给页面。
            # 但"请看终端"不能是空话——栈只打在终端，否则本机出错了无处可查。
            traceback.print_exc()
            self._send(500, {"error": "本机服务出错，请看终端"})


def _enter_live_key():
    provider = (os.environ.get("PAI_MODEL_PROVIDER") or "").strip().lower() or "openai"
    os.environ["PAI_MODEL_PROVIDER"] = provider
    key_var = f"{provider.upper()}_API_KEY"
    if (os.environ.get(key_var) or os.environ.get("PAI_MODEL_API_KEY") or "").strip():
        return
    secret = getpass.getpass(f"{key_var} > ").strip()      # 不回显、不落盘
    if not secret:
        os.environ["PAI_MODEL_PROVIDER"] = "mock"
        return
    os.environ[key_var] = secret                           # 只进当前进程


def start(port=DEFAULT_PORT, db_path=None, quiet=False, ready_timeout=10):
    """装配并后台跑起来，返回 (httpd, thread)。测试与 serve() 共用这一条装配线。

    连接必须在【服务线程】里建：sqlite 对象跨线程使用直接报 ProgrammingError，
    而处理请求的正是后台线程。所以 mem/orch 的装配放进线程内部，主线程只等
    ready 标志、绝不碰数据库。连接也同一个线程自己关（见 run 的 finally）。

    用 ThreadingHTTPServer：health 可以在长模型生成时并发回；真正的生成仍由
    _GEN_LOCK 串行，避免两个模型请求同时压同一个 backend。
    """
    httpd = ThreadingHTTPServer((LOOPBACK, port), Handler)
    httpd.daemon_threads = True
    ready = threading.Event()
    failed = []
    path = str(db_path or DB)

    def run():
        global mem, orch, tasks, skills, convs
        try:
            mem = Memory(path)
            orch = Orchestrator(mem)
            tasks = TL.TaskLedger(Path(path).parent / "tasks.db")
            skills = SL.SkillLibrary(PF.skills_dir())
            convs = CONV.ConversationStore(Path(path).parent / "conversations.db")
            STARTUP_REPORT["core"] = {"ok": True}
        except Exception as e:
            STARTUP_REPORT["core"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            failed.append(e)
            ready.set()
            return
        ready.set()
        try:
            httpd.serve_forever()
        finally:
            httpd.server_close()
            mem.close()
            if tasks is not None:
                tasks.close()
            if convs is not None:
                convs.close()

    thread = threading.Thread(target=run, daemon=True, name="pai-webapp")
    thread.start()
    if not ready.wait(ready_timeout):
        raise RuntimeError("本机界面启动超时（记忆库可能被别的进程占着）")
    if failed:
        raise failed[0]
    _startup_step("jobs", lambda: JW.start_background(interval=60, dry_run=False))
    _startup_step("reminders", lambda: threading.Thread(
        target=UNI.run_reminder_loop, args=(mem,), daemon=True, name="pai-reminders").start())
    _startup_step("proactive", lambda: threading.Thread(
        target=PA.run_background, args=(None, 300, PF.current()), daemon=True,
        name="pai-proactive").start())
    def _memory_loop():
        import time
        while True:
            try:
                mem.dedupe()
            except Exception:
                pass
            time.sleep(3600)
    _startup_step("memory_loop", lambda: threading.Thread(
        target=_memory_loop, daemon=True, name="pai-memory").start())
    if not quiet:
        # flush：双击打开时这行"已启动"必须马上出现，否则用户以为没反应。
        # 输出被重定向时 stdout 是块缓冲，不 flush 就会一直卡在缓冲区里。
        print(f"个人AI系统 · 本机界面已启动： http://{LOOPBACK}:{httpd.server_port}/", flush=True)
        print(f"模型：{M10.active_provider()}   记忆库：{mem.db_path}", flush=True)
        print("只绑 127.0.0.1，别的地方打不开。Ctrl+C 退出。", flush=True)
    return httpd, thread


def stop(httpd, thread=None):
    """关停。连接由服务线程自己在 finally 里关——主线程去 close 就是跨线程用 sqlite。"""
    httpd.shutdown()
    if thread is not None:
        thread.join(5)


def serve(port=DEFAULT_PORT, db_path=None, quiet=False, open_browser=True):
    """起服务并阻塞，直到 Ctrl+C。返回 (httpd, thread)。

    open_browser：双击 .bat 的人不会自己去敲网址，所以绑定成功后自己开浏览器。
    放在 start() 之后——那时端口已经监听，不会出现"页面打开了但服务还没起来"。
    """
    httpd, thread = start(port=port, db_path=db_path, quiet=quiet)
    url = f"http://{LOOPBACK}:{httpd.server_port}/"
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            print(f"没能自动开浏览器，手动打开这个地址就行： {url}", flush=True)
    try:
        thread.join()
    except KeyboardInterrupt:
        stop(httpd, thread)
        raise
    return httpd, thread


def main(argv=None):
    os.environ.setdefault("PAI_TRACE", "on")
    os.environ.setdefault("PAI_A1_ACTIVITY", "on")
    ap = argparse.ArgumentParser(description="个人AI系统 · 本机图形界面（只绑 127.0.0.1）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--db", default=None, help="记忆库路径（默认 data/memory.db）")
    ap.add_argument("--allow-real-db", action="store_true",
                    help="显式允许 --db 指向项目 data/ 下的真库")
    ap.add_argument("--live", action="store_true", help="先悄悄输入模型密钥再开界面")
    ap.add_argument("--no-browser", action="store_true", help="只起服务，不自动开浏览器")
    ap.add_argument("--profile", default=None,
                    help="隔离 profile：work / side / study / game")
    ns = ap.parse_args(argv)
    TOOLS.apply_saved()
    _apply_agent_engine()
    _apply_fallbacks()
    os.environ.setdefault("PAI_APPROVAL_MODE", "ledger")
    os.environ.setdefault("PAI_JOB_RUNNER", "on")
    os.environ.setdefault("PAI_JOB_WORKER", "on")
    MS.apply()
    from pai import low_spec as LOWSPEC
    from pai import model_mode as MMODE
    _startup_step("low_spec", LOWSPEC.apply)
    _startup_step("model_mode", MMODE.startup_apply)
    _startup_step("custom_select", CUS.apply)
    _startup_step("network_policy", NP.apply)
    _startup_step("providers", PR.apply)
    if ns.profile:
        os.environ["PAI_PROFILE"] = PF.normalize(ns.profile)
    db = Path(ns.db) if ns.db else PF.db_path(ns.profile)
    data_root = PATHS.DATA_DIR
    legacy_data = PATHS.APP_ROOT / "data"
    try:
        dbr = db.resolve()
        inside = any(r.resolve() == dbr or r.resolve() in dbr.parents
                     for r in (data_root, legacy_data))
    except OSError:
        inside = False
    if inside and not ns.allow_real_db:
        print("拒绝启动：--db 指向用户/项目 data 下的库；确需真库请加 --allow-real-db")
        return 2
    if ns.live:
        _enter_live_key()
    try:
        serve(port=ns.port, db_path=ns.db, open_browser=not ns.no_browser)
    except KeyboardInterrupt:
        print("\n已退出。记忆已存进：", DB)
    return 0


if __name__ == "__main__":
    sys.exit(main())
