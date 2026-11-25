# -*- coding: utf-8 -*-
"""代码/工程能力 v1：读文件、搜索、带确认写文件、跑测试。

边界：只允许本机指定项目根；写文件必须 confirm=true 且校验旧哈希；测试命令只允许 python -m unittest/pytest。
"""
from __future__ import annotations

from pai import paths

import difflib
import hashlib
import os
import pathlib

from pai import exec_guard as EG
import re
import shlex
import subprocess
import sys

CAPABILITY = {
    "name": "代码/工程能力 v1",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "根目录越界/哈希不符/未确认=拒绝；测试只允许 unittest/pytest，超时返回失败，不伪造通过",
    "approval": "ADR-046：没有开关的既成常开登记，不发明新开关",
}

DEFAULT_ROOTS = (str(paths.DATA_DIR / "workspace"), str(paths.APP_ROOT))
MAX_READ = 200_000


class CodeError(ValueError):
    pass


def allowed_roots():
    raw = os.environ.get("PAI_CODE_ROOTS") or ";".join(DEFAULT_ROOTS)
    return [pathlib.Path(x).resolve() for x in raw.split(";") if x.strip()]


def _root(root):
    p = pathlib.Path(root or "").resolve()
    for base in allowed_roots():
        if p == base or base in p.parents:
            return p
    raise CodeError("项目根不在允许范围：%s" % p)


def _file(root, rel):
    p = (_root(root) / str(rel or "")).resolve()
    if _root(root) not in p.parents and p.parent != _root(root):
        raise CodeError("路径越界：%s" % rel)
    if not p.is_file():
        raise CodeError("文件不存在：%s" % rel)
    return p


def read_file(root, rel):
    p = _file(root, rel)
    text = p.read_text(encoding="utf-8", errors="replace")
    return {"root": str(_root(root)), "path": str(p.relative_to(_root(root))),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "content": text[:MAX_READ]}


def search(root, query, limit=20):
    q = str(query or "").strip().lower()
    if not q:
        return []
    base = _root(root)
    out = []
    for p in base.rglob("*"):
        if len(out) >= limit:
            break
        if not p.is_file() or p.suffix.lower() not in (".py", ".md", ".txt", ".json", ".html", ".js", ".vue", ".wxml"):
            continue
        try:
            if p.stat().st_size > 1_000_000:
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if q in line.lower():
                    out.append({"path": str(p.relative_to(base)), "line": i, "text": line[:300]})
                    break
        except OSError:
            continue
    return out[:limit]


def apply_file(root, rel, content, expected_sha="", confirm=False):
    try:
        EG.gate("overwrite", str(rel), approved=(confirm is True), detail="code_agent.apply_file")
    except EG.GuardError as e:
        raise CodeError(str(e)) from None
    if confirm is not True:
        raise CodeError("写文件必须先确认（confirm=true）")
    if EG.is_executable(rel):
        raise CodeError("拒绝写可执行体：%s" % rel)
    p = _file(root, rel)
    old = p.read_bytes()
    sha = hashlib.sha256(old).hexdigest()
    if expected_sha and expected_sha != sha:
        raise CodeError("文件已变化，旧哈希不匹配，拒绝写入")
    new = str(content if content is not None else "").encode("utf-8")
    if old == new:
        return {"changed": False, "sha256": sha, "path": str(p.relative_to(_root(root)))}
    backup = p.with_name(p.name + ".bak")
    backup.write_bytes(old)
    p.write_bytes(new)
    diff = "".join(difflib.unified_diff(
        old.decode("utf-8", errors="replace").splitlines(True),
        new.decode("utf-8", errors="replace").splitlines(True),
        fromfile=str(rel) + " (old)", tofile=str(rel) + " (new)"))[:20000]
    return {"changed": True, "sha256": hashlib.sha256(new).hexdigest(),
            "backup": str(backup), "path": str(p.relative_to(_root(root))), "diff": diff}


def rollback_file(root, rel):
    EG.gate("overwrite", str(rel), approved=True, detail="code_agent.rollback")
    p = _file(root, rel)
    backup = p.with_name(p.name + ".bak")
    if not backup.is_file():
        raise CodeError("没有可回滚备份：%s" % rel)
    old = p.read_bytes()
    new = backup.read_bytes()
    p.write_bytes(new)
    backup.write_bytes(old)
    return {"ok": True, "path": str(p.relative_to(_root(root))),
            "sha256": hashlib.sha256(new).hexdigest()}


def apply_batch(root, changes, confirm=False):
    if confirm is not True:
        raise CodeError("批量写文件必须先确认（confirm=true）")
    if not isinstance(changes, list) or not changes:
        raise CodeError("changes 必须是非空列表")
    done = []
    try:
        for item in changes:
            out = apply_file(root, item.get("path"), item.get("content"),
                             str(item.get("expected_sha") or ""), True)
            done.append(out)
        return {"ok": True, "count": len(done), "items": done}
    except Exception:
        for item in reversed(done):
            if item.get("changed"):
                try:
                    rollback_file(root, item["path"])
                except Exception:
                    pass
        raise


def isolation_status():
    return {"tests": (os.environ.get("PAI_CODE_ISOLATE", "on").strip().lower()
                      not in ("0", "off", "false", "no")),
            "snippet": os.environ.get("PAI_CODE_EXEC", "").strip().lower() in ("1", "on", "true", "yes")}


def run_snippet(code, timeout=30):
    """任意 Python 片段：默认拒绝；PAI_CODE_EXEC=on 才跑，只写临时目录。"""
    if os.environ.get("PAI_CODE_EXEC", "").strip().lower() not in ("1", "on", "true", "yes"):
        raise CodeError("代码片段执行默认关闭（PAI_CODE_EXEC=on 才开）")
    code = str(code or "")
    if not code.strip():
        raise CodeError("代码为空")
    try:
        EG.gate("exec", "snippet", approved=True, detail="code_agent.run_snippet")
    except EG.GuardError as e:
        raise CodeError(str(e)) from None
    import tempfile, shutil
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="zhiheng-snippet-"))
    try:
        f = tmp / "snippet.py"
        f.write_text(code, encoding="utf-8")
        env = {"PATH": os.environ.get("PATH", ""), "PAI_OFFLINE": "1",
               "PYTHONIOENCODING": "utf-8"}
        try:
            proc = subprocess.run([sys.executable, "-I", str(f)], cwd=str(tmp),
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return {"ok": False, "code": -1, "output": "片段超时（%ss）" % timeout, "isolated": True}
        return {"ok": proc.returncode == 0, "code": proc.returncode,
                "output": ((proc.stdout or "") + (proc.stderr or ""))[-10000:],
                "isolated": True}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_tests(root, command="python -m unittest discover -s tests -p test_*.py"):
    base = _root(root)
    command = str(command or "")
    try:
        EG.assert_test_command(command)   # P0-2：只允许发现式，禁 pytest/点名 .py
    except EG.GuardError as e:
        raise CodeError(str(e)) from None
    argv = shlex.split(command)
    if len(argv) < 3 or pathlib.Path(argv[0]).name.lower() not in ("python", "python.exe", "py"):
        raise CodeError("只允许 python -m unittest / python -m pytest")
    argv[0] = sys.executable
    if argv[1] != "-m" or argv[2] not in ("unittest", "pytest"):
        raise CodeError("只允许 unittest / pytest")
    try:
        EG.gate("exec", command[:200], approved=True, detail="code_agent.run_tests")
    except EG.GuardError as e:
        raise CodeError(str(e)) from None
    import tempfile, shutil
    isolated = (os.environ.get("PAI_CODE_ISOLATE", "on").strip().lower()
                not in ("0", "off", "false", "no"))
    run_root = base
    tmp = None
    try:
        if isolated:
            tmp = pathlib.Path(tempfile.mkdtemp(prefix="zhiheng-test-"))
            shutil.copytree(base, tmp, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("data", ".git", "__pycache__", "backups", "browser_profile"))
            run_root = tmp
        try:
            proc = subprocess.run(argv, cwd=str(run_root), capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=300, env=dict(os.environ, PAI_OFFLINE="1"))
        except subprocess.TimeoutExpired:
            return {"passed": False, "code": -1, "output": "测试超时（300s）", "command": argv,
                    "isolated": isolated}
        output = ((proc.stdout or "") + (proc.stderr or ""))[-20000:]
        return {"passed": proc.returncode == 0, "code": proc.returncode,
                "output": output, "command": argv, "isolated": isolated}
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def check_deps(root):
    """只读检查 requirements.txt：缺哪些包，不自动安装。"""
    import importlib.metadata as md
    base = _root(root)
    req = base / "requirements.txt"
    if not req.exists():
        return {"ok": False, "reason": "没有 requirements.txt", "root": str(base)}
    missing, present = [], []
    for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = re.split(r"[=<>!~\[]", line, 1)[0].strip()
        if not name:
            continue
        try:
            v = md.version(name)
            present.append({"name": name, "version": v})
        except Exception:
            missing.append(line)
    return {"ok": True, "root": str(base), "missing": missing,
            "present": present, "count": len(present) + len(missing)}
