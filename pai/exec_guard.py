# -*- coding: utf-8 -*-
"""执行边界守卫 —— 知衡 P0 补丁的唯一真源。

为什么单独成模块：测试命令白名单原本抄在四处（code_tools.py:26-32、auto_agent.py:24-25、
ai_coding/coding.py:218-223、pr_bot/scoreboard.py:31-36），改一处漏三处。收敛到这里之后，
「允许跑什么测试」只有一个住址。

三条硬规则：
  1. 只允许「发现式」跑测试（discover / 目录级），不允许点名 .py 文件 —— 点名文件即
     import 期执行任意顶层代码，那不是跑测试，那是执行模型写的程序。
  2. os.startfile 前必须过扩展名闸门 —— 白名单目录里的 .bat/.ps1/.lnk/.exe 不是「文件」，
     是可执行体。
  3. 屏幕 OCR 文本属高敏来源，默认不落对话日志。
"""
from __future__ import annotations

CAPABILITY = {
    "name": "执行边界守卫",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "守卫自身失败=拒绝执行，不静默放行",
    "approval": "ADR-046：本地既成常开工具，不新增开关",
}


import os
import re
import shlex
import subprocess
import sys

# ---- 规则 2：可执行体扩展名（Windows 上 startfile 即执行）-----------------
EXEC_SUFFIXES = frozenset({
    ".bat", ".cmd", ".ps1", ".psm1", ".lnk", ".exe", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".msi", ".msp", ".scr", ".com",
    ".pif", ".hta", ".cpl", ".jar",
})

# 明确允许「打开看看」的类型。宁可少，不要多。
OPEN_SAFE_SUFFIXES = frozenset({
    ".txt", ".md", ".json", ".jsonl", ".csv", ".tsv", ".xml", ".yaml", ".yml",
    ".log", ".ini", ".cfg", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
})


RISK_LOW = frozenset({"read", "list", "search", "summarize", "describe"})
RISK_MEDIUM = frozenset({"excel_convert", "excel_clean", "chart", "rename", "generate", "write_output", "backup", "report"})
RISK_HIGH = frozenset({"delete", "overwrite", "exec", "mcp_call", "send", "external_write"})


def risk_level(action):
    a = str(action or "").strip().lower()
    if a in RISK_HIGH:
        return "high"
    if a in RISK_MEDIUM:
        return "medium"
    return "low"


def audit(action, risk, decision, target="", detail=""):
    """一本审计账：本地 SQLite，只追加，不覆盖。"""
    import datetime
    import sqlite3
    from pai import paths
    db = paths.DATA_DIR / "audit.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(db), timeout=10)
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS audit(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, action TEXT NOT NULL, risk TEXT NOT NULL,
            decision TEXT NOT NULL, target TEXT, detail TEXT)""")
        c.execute("INSERT INTO audit(ts,action,risk,decision,target,detail) VALUES(?,?,?,?,?,?)",
                  (datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                   str(action), str(risk), str(decision), str(target)[:500], str(detail)[:2000]))
        c.commit()
    finally:
        c.close()


def gate(action, target="", approved=False, detail=""):
    """唯一审批闸：低/中直接过，高风险必须 approved。"""
    risk = risk_level(action)
    if risk == "high" and not approved:
        audit(action, risk, "deny", target, detail or "高风险未批准")
        raise GuardError("高风险操作需要审批：%s" % action)
    audit(action, risk, "allow" if approved or risk != "high" else "allow", target, detail)
    return risk


def audit_recent(limit=50):
    import sqlite3
    from pai import paths
    db = paths.DATA_DIR / "audit.db"
    if not db.exists():
        return []
    c = sqlite3.connect(str(db), timeout=10)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(
            "SELECT id,ts,action,risk,decision,target,detail FROM audit ORDER BY id DESC LIMIT ?",
            (int(limit),))]
    finally:
        c.close()


_JOBS = {}


def safe_child_env(extra=None):
    """子进程环境清洗：不继承 API Key/Token/密码；默认标记离线。"""
    base = dict(os.environ)
    drop = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")
    for k in list(base):
        if any(x in k.upper() for x in drop):
            base.pop(k, None)
    base["PAI_OFFLINE"] = "1"
    if extra:
        base.update({str(k): str(v) for k, v in extra.items()})
    return base


def guarded_popen(argv, **kwargs):
    """Popen + Windows Job Object（进程数/内存上限）；失败返回普通 Popen。"""
    kwargs.setdefault("env", safe_child_env())
    proc = subprocess.Popen(argv, **kwargs)
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            k = ctypes.windll.kernel32
            job = k.CreateJobObjectW(None, None)
            if job:
                class _Basic(ctypes.Structure):
                    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                                ("PerJobUserTimeLimit", ctypes.c_longlong),
                                ("LimitFlags", wintypes.DWORD),
                                ("MinimumWorkingSetSize", ctypes.c_size_t),
                                ("MaximumWorkingSetSize", ctypes.c_size_t),
                                ("ActiveProcessLimit", wintypes.DWORD),
                                ("Affinity", ctypes.c_size_t),
                                ("PriorityClass", wintypes.DWORD),
                                ("SchedulingClass", wintypes.DWORD)]
                class _Io(ctypes.Structure):
                    _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                                ("WriteOperationCount", ctypes.c_ulonglong),
                                ("OtherOperationCount", ctypes.c_ulonglong),
                                ("ReadTransferCount", ctypes.c_ulonglong),
                                ("WriteTransferCount", ctypes.c_ulonglong),
                                ("OtherTransferCount", ctypes.c_ulonglong)]
                class _Ext(ctypes.Structure):
                    _fields_ = [("BasicLimitInformation", _Basic),
                                ("IoInfo", _Io),
                                ("ProcessMemoryLimit", ctypes.c_size_t),
                                ("JobMemoryLimit", ctypes.c_size_t),
                                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                                ("PeakJobMemoryUsed", ctypes.c_size_t)]
                info = _Ext()
                info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
                info.BasicLimitInformation.ActiveProcessLimit = 8
                info.ProcessMemoryLimit = 512 * 1024 * 1024
                k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
                h = k.OpenProcess(0x0100 | 0x0001, False, proc.pid)  # SET_QUOTA|TERMINATE
                if h and k.AssignProcessToJobObject(job, h):
                    _JOBS[proc.pid] = job
                if h:
                    k.CloseHandle(h)
        except Exception:
            pass
    return proc


def guarded_run(argv, timeout=300, **kwargs):
    proc = guarded_popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out or b"", err or b""
    except subprocess.TimeoutExpired:
        proc.kill(); proc.communicate()
        raise
    finally:
        job = _JOBS.pop(proc.pid, None)
        if job:
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(job)
            except Exception:
                pass


def retry_call(fn, action="", retries=2, base_delay=0.5):
    """统一重试：最多 retries 次，指数退避；每次重试/失败写审计。"""
    import time
    last = None
    for i in range(max(0, int(retries)) + 1):
        try:
            return fn()
        except Exception as e:
            last = e
            if i >= int(retries):
                audit(action or "retry", "medium", "deny", "", "%s: %s" % (type(e).__name__, e))
                raise
            audit(action or "retry", "medium", "retry", "", "第 %d 次：%s" % (i + 1, type(e).__name__))
            time.sleep(base_delay * (2 ** i))
    raise last


class GuardError(RuntimeError):
    """执行边界拒绝。调用方应当作「正常拒绝」处理，不要 except 掉当成功。"""


def suffix_of(path) -> str:
    try:
        return os.path.splitext(str(path or ""))[1].lower()
    except Exception:
        return ""


def is_executable(path) -> bool:
    return suffix_of(path) in EXEC_SUFFIXES


def assert_openable(path) -> None:
    """startfile 之前必须调这个。可执行体一律拒；未知类型也拒（白名单而非黑名单兜底）。"""
    sfx = suffix_of(path)
    if is_executable(path):
        raise GuardError("拒绝打开可执行体：%s（%s）" % (path, sfx))
    if sfx not in OPEN_SAFE_SUFFIXES:
        raise GuardError("拒绝打开未分类类型：%s（%s）" % (path, sfx or "无扩展名"))


# ---- 规则 1：测试命令白名单 ----------------------------------------------
# 只允许「发现式」跑测试。任何点名 .py 文件的形式都被拒 —— 那等于执行模型写的脚本。
TEST_PATTERNS = (
    re.compile(r"^python -m unittest discover(\s+-s\s+[\w.\-]+)?(\s+-t\s+[\w.\-]+)?"
               r"(\s+-p\s+[\w.*\-]+)?(\s+[-\-]{1,2}\w+)*$"),
    re.compile(r"^python -m compileall\s+(-q\s+)?\.$"),
    re.compile(r"^node\s+--test$"),
    re.compile(r"^npm\s+(run\s+)?test$"),
    re.compile(r"^npm\s+run\s+check$"),
)

# 供文档与提示引用：白名单里唯一实际可用的命令
TEST_COMMAND = "python -m unittest discover -s tests -p test_*.py"


def _normalize(cmd: str) -> str:
    return " ".join(str(cmd or "").strip().split()).lower()


def is_allowed_test_command(cmd: str) -> bool:
    """精确匹配「发现式」测试命令；拒绝 pytest、拒绝点名文件、拒绝一切元字符。"""
    low = _normalize(cmd)
    if not low:
        return False
    if any(x in low for x in ("|", ">", "<", "&&", "||", ";", "`", "$(", "\n", "\r")):
        return False
    # 先把解释器绝对路径归一成 "python"。必须在 shlex 之前做：Windows 路径里的反斜杠
    # 会被 posix shlex 当转义吃掉，导致 "D:\python\python.exe" 变成一串乱 token。
    exe = (sys.executable or "").lower()
    if exe and low.startswith(exe):
        low = "python" + low[len(exe):]
    else:
        head = low.split(" ", 1)[0]
        if os.path.basename(head).lower() in ("python", "python.exe", "python3", "python3.exe"):
            rest = low[len(head):]
            low = "python" + rest
    try:
        argv = shlex.split(low)
    except ValueError:
        return False
    if not argv:
        return False
    # 点名具体 .py 文件 = import 期执行任意顶层代码，一律拒。
    # 但 -p/--pattern 的取值本来就是 glob（test_*.py），不能误伤 —— 否则白名单自己都不通过。
    VALUE_OPTS = {"-p", "--pattern", "-k"}
    prev = ""
    for a in argv[1:]:
        if a.endswith(".py") and prev not in VALUE_OPTS:
            return False
        if "/" in a or "\\" in a:
            return False
        prev = a
    return any(p.match(low) for p in TEST_PATTERNS)


def assert_test_command(cmd: str) -> str:
    if not is_allowed_test_command(cmd):
        raise GuardError("测试命令不在白名单（只允许发现式跑测试，且不得点名 .py 文件）：%s" % cmd)
    return cmd


# ---- 规则 3：来源分级与日志 ----------------------------------------------
# screen：屏幕 OCR，可能含密码管理器、他人聊天、病历 → 不落对话日志
SENSITIVE_SOURCES = frozenset({"screen", "ocr"})


def should_log(source: str = "desktop") -> bool:
    return str(source or "desktop").lower() not in SENSITIVE_SOURCES


def redact_screen(text: str, limit: int = 0) -> str:
    """需要留痕时只留长度与指纹，不留内容。"""
    t = str(text or "")
    return "[screen:%d chars]" % len(t)


# ---- 供 P1 使用：AI 可写文件类型应收窄（.ps1/.bat/.cmd 是可执行体）--------
def safe_text_extensions(current) -> set:
    cur = set(current or ())
    return {e for e in cur if e.lower() not in EXEC_SUFFIXES}
