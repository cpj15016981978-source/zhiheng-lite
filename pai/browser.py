# -*- coding: utf-8 -*-
"""浏览器能力 v1：隔离进程里用 Playwright + Edge 执行少量动作；必须人工确认。"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

from pai import offline as OFF
from pai import exec_guard as EG
from pai import network_policy as NP

CAPABILITY = {
    "name": "浏览器能力 v1",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": True,
    "degrade": "没有 confirm=true 一律拒绝；Playwright/Edge 缺失=返回错误；动作限制 10 步",
    "approval": "ADR-046：本地单用户浏览器能力；每次任务必须人工确认",
}

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHOTS = ROOT / "data" / "browser_shots"
WORKER = ROOT / "browser_worker.py"


def start_login(url="", confirm=False):
    """Open a persistent Edge window for one-time login; returns immediately."""
    OFF.assert_allowed("browser", RuntimeError, "浏览器")
    if confirm is not True:
        return {"ok": False, "needs_approval": True, "reason": "打开登录窗口必须人工确认"}
    EG.gate("mcp_call", "browser-login:" + str(url or ""), approved=True, detail="browser.start_login")
    if url and not NP.is_host_allowed(url):
        return {"ok": False, "needs_allow": True, "reason": "出网白名单未允许：%s" % url}
    worker = ROOT / "browser_login.py"
    if not worker.exists():
        return {"ok": False, "reason": "缺少 browser_login.py"}
    profile = ROOT / "data" / "browser_profile"
    profile.mkdir(parents=True, exist_ok=True)
    try:
        proc = EG.guarded_popen(
            [sys.executable, str(worker), str(profile), str(url or "https://www.douyin.com/")],
            cwd=str(ROOT), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "%s: %s" % (type(e).__name__, e)}
    return {"ok": True, "pid": proc.pid,
            "note": "登录窗口已打开；登录完直接关闭窗口，登录态会留在 data/browser_profile"}


def task(url="", actions=None, confirm=False, headless=False):
    OFF.assert_allowed("browser", RuntimeError, "浏览器")
    if confirm is not True:
        return {"ok": False, "needs_approval": True,
                "reason": "浏览器任务必须人工确认"}
    EG.gate("mcp_call", "browser:" + str(url or ""), approved=True, detail="browser.task")
    if url and not NP.is_host_allowed(url):
        return {"ok": False, "needs_allow": True, "reason": "出网白名单未允许：%s" % url}
    for a in actions or []:
        u = str(a.get("url") or "") if isinstance(a, dict) else ""
        if u and not NP.is_host_allowed(u):
            return {"ok": False, "needs_allow": True, "reason": "出网白名单未允许：%s" % u}
    actions = actions or []
    if not isinstance(actions, list) or len(actions) > 10:
        return {"ok": False, "reason": "actions 必须是最多 10 步的数组"}
    if not WORKER.exists():
        return {"ok": False, "reason": "缺少 browser_worker.py"}
    SHOTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="luwu-browser-") as d:
        req = pathlib.Path(d) / "req.json"
        res = pathlib.Path(d) / "res.json"
        req.write_text(json.dumps({"url": url, "actions": actions,
                                   "headless": bool(headless),
                                   "profile": str(ROOT / "data" / "browser_profile"),
                                   "shots": str(SHOTS)}, ensure_ascii=False),
                       encoding="utf-8")
        try:
            proc = EG.guarded_popen([sys.executable, str(WORKER), str(req), str(res)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding="utf-8", errors="replace",
                                    cwd=str(ROOT))
            out, err = proc.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            if 'proc' in locals():
                proc.kill()
            return {"ok": False, "reason": "浏览器任务超时（120s）"}
        if not res.exists():
            return {"ok": False, "reason": "浏览器 worker 无结果：%s" %
                    ((err or out or "")[-500:])}
        return json.loads(res.read_text(encoding="utf-8"))
