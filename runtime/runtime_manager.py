# -*- coding: utf-8 -*-
"""知衡 Runtime Manager：一个入口启动 Ollama/后端/桌面，并负责健康与重启。"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
SPINE = ROOT / "spine"
PY = ROOT / "python" / "python.exe"
PYW = ROOT / "python" / "pythonw.exe"
OLLAMA = ROOT / "ollama" / "ollama.exe"
WEBVIEW = ROOT / "webview_shell" / "app.py"
PORT = int(os.environ.get("ZH_PORT") or 8753)
URL = "http://127.0.0.1:%d" % PORT
DATA = pathlib.Path(os.environ.get("PAI_DATA_DIR") or (
    pathlib.Path(os.environ.get("LOCALAPPDATA") or (pathlib.Path.home() / "AppData" / "Local"))
    / "Zhiheng" / "data"))
LOG = ROOT / "logs"


def log(msg):
    LOG.mkdir(parents=True, exist_ok=True)
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg)
    with (LOG / "runtime.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def _json(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def health():
    try:
        return _json(URL + "/api/health")
    except Exception:
        return None


def ollama_ready():
    try:
        _json("http://127.0.0.1:11434/api/tags", timeout=2)
        return True
    except Exception:
        return False


def ensure_ollama():
    if ollama_ready():
        return True
    if not OLLAMA.exists():
        log("ollama 不存在：%s" % OLLAMA)
        return False
    log("启动 Ollama")
    subprocess.Popen([str(OLLAMA), "serve"], cwd=str(ROOT), stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(20):
        time.sleep(0.5)
        if ollama_ready():
            return True
    log("Ollama 启动超时")
    return False


def ensure_model():
    try:
        d = _json("http://127.0.0.1:11434/api/tags", timeout=3)
        names = [x.get("name") for x in d.get("models", [])]
        if not any(n and n.startswith("qwen") for n in names):
            log("没有 qwen 模型；请在模型管理里拉取 qwen2.5:1.5b")
            return False
        return True
    except Exception as e:
        log("读取模型失败：%s" % e)
        return False


def start_backend():
    DATA.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PAI_DATA_DIR=str(DATA), PAI_SPINE=str(SPINE))
    cmd = [str(PY), str(SPINE / "webapp.py"), "--no-browser",
           "--db", str(DATA / "memory.db"), "--allow-real-db"]
    log("启动后端 %s" % " ".join(cmd))
    return subprocess.Popen(cmd, cwd=str(SPINE), env=env,
                            stdout=open(LOG / "backend.log", "ab"),
                            stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def wait_health(seconds=90):
    for _ in range(seconds * 2):
        if health():
            return True
        time.sleep(0.5)
    return False


def start_desktop():
    if not WEBVIEW.exists():
        log("桌面壳缺失：%s" % WEBVIEW)
        return None
    log("启动桌面")
    return subprocess.Popen([str(PYW), str(WEBVIEW), URL + "/chat"], cwd=str(ROOT),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    LOG.mkdir(parents=True, exist_ok=True)
    log("Runtime 启动，端口 %d" % PORT)
    ensure_ollama()
    ensure_model()
    if health():
        log("已有后端在运行，直接打开桌面")
        start_desktop()
        return 0
    backend = start_backend()
    if not wait_health():
        log("后端健康检查失败，查看 logs/backend.log")
        return 1
    h = health() or {}
    log("后端就绪 version=%s model=%s" % (h.get("version", ""), h.get("model", "")))
    desktop = start_desktop()
    if desktop is None:
        return 1
    try:
        while True:
            if backend.poll() is not None:
                log("后端退出，重启")
                backend = start_backend()
                wait_health()
            if desktop.poll() is not None:
                log("桌面关闭，退出 Runtime")
                break
            time.sleep(2)
    except KeyboardInterrupt:
        log("收到中断")
    finally:
        if backend.poll() is None:
            backend.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
