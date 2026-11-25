# -*- coding: utf-8 -*-
"""开机自启开关：只管理 Startup 快捷方式，不写注册表。"""
from __future__ import annotations

from pai import paths

import os
import pathlib
import subprocess
import tempfile

CAPABILITY = {
    "name": "开机自启开关",
    "switch_env": "PAI_AUTOSTART",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "非 Windows/创建失败=明确报错；状态以快捷方式是否存在为准",
    "approval": "ADR-046：本地单用户启动项，默认关，用户显式开启",
}

ROOT = pathlib.Path(__file__).resolve().parent.parent
STARTUP = pathlib.Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
LNK = STARTUP / "知衡.lnk"
_candidates = (ROOT / "知衡.cmd", ROOT.parent / "知衡.cmd",
               paths.APP_ROOT / "知衡.cmd")
TARGET = next((p for p in _candidates if p.exists()), _candidates[0])
PS = r'''$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:ZH_LNK)
$s.TargetPath = $env:ZH_TARGET
$s.WorkingDirectory = $env:ZH_ROOT
$s.IconLocation = "$env:ZH_ROOT\luwu.ico,0"
$s.Description = "Zhiheng autostart"
$s.Save()
'''


def status():
    return {"enabled": LNK.exists() and TARGET.exists(), "shortcut": str(LNK),
            "target": str(TARGET), "target_exists": TARGET.exists()}


def enable():
    if os.name != "nt":
        raise RuntimeError("只支持 Windows")
    if not TARGET.exists():
        raise RuntimeError("缺少启动入口：%s" % TARGET)
    STARTUP.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as f:
        f.write(PS)
        script = f.name
    try:
        env = dict(os.environ, ZH_LNK=str(LNK), ZH_TARGET=str(TARGET), ZH_ROOT=str(ROOT))
        p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
                           env=env, capture_output=True, timeout=30,
                           text=True, encoding="utf-8", errors="replace")
        if p.returncode != 0 or not LNK.exists():
            raise RuntimeError("创建快捷方式失败：%s" % ((p.stderr or p.stdout or "")[:300]))
    finally:
        try:
            os.unlink(script)
        except OSError:
            pass
    return status()


def disable():
    try:
        LNK.unlink()
    except FileNotFoundError:
        pass
    return status()
