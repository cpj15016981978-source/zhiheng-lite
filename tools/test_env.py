# -*- coding: utf-8 -*-
"""测试环境自检 + 解释器探测 + 版本锁校验（两仓共用）。

背景（2026-11-16 评审第 3 条）：WorkBuddy 默认解释器缺 tkinter / zhconv，
跑测试会在收集期大面积假 FAIL。换带全套依赖的解释器后实测全绿（后端 Quick 69、
桌面 92/92）。本脚本把"用哪个解释器、什么版本"从记忆变成机制 + 锁。

三层保障：
  1) 探测候选解释器能否 import 必需模块（能跑）
  2) 读 python.lock.json 校验 Python 版本与关键包版本是否与锁一致（可复现）
  3) 优先选"完全符合锁"的解释器；只有"能跑但版本不符"的降级项时给出警告

用法：
  python tools/test_env.py                          # backend 档
  python tools/test_env.py --profile desktop        # desktop 档
  python tools/test_env.py --json                   # 机器可读（run_tests.ps1 用）
  python tools/test_env.py --strict                 # 严格模式：版本不符也算失败

候选解释器（优先级）：env:PAI_TEST_PYTHON > lock 里的 authority.python > 当前解释器
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent

# 档位 -> 需要能 import 的模块（import 名）
PROFILES = {
    "backend": ["zhconv", "pypdf", "docx", "openpyxl"],
    "desktop": ["zhconv", "PIL", "webview", "tkinter"],
}

# 探测脚本：一次性回传 Python 版本 + lock 里登记的所有包版本。
# 用 importlib.metadata 比 import __version__ 更可靠（有些包不暴露 __version__）。
PROBE_CODE = r'''
import json, sys
from importlib import metadata as md
# 注意：版本字段名必须叫 py_version，不能叫 python——后者是候选解释器路径的键，
# 若同名会在 {"python": exe, **r} 展开时把路径覆盖成版本号，导致 ps1 拿版本号当路径执行。
out = {"py_version": "%d.%d.%d" % sys.version_info[:3], "packages": {}}
for name in __PKGS__:
    try:
        out["packages"][name.lower()] = md.version(name)
    except Exception:
        out["packages"][name.lower()] = None
print(json.dumps(out))
'''


def _load_lock() -> dict:
    p = HERE / "python.lock.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _probe(exe: str, packages: list[str]) -> dict:
    """子进程探测解释器版本 + 包版本。"""
    code = PROBE_CODE.replace("__PKGS__", repr(packages))
    try:
        p = subprocess.run([exe, "-c", code], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25)
        if p.returncode != 0:
            err = (p.stderr or "").strip().splitlines()
            return {"reachable": False, "error": err[-1] if err else "probe failed"}
        return {"reachable": True, **json.loads(p.stdout.strip().splitlines()[-1])}
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "error": "%s: %s" % (type(e).__name__, e)}


def _norm(v) -> str:
    return str(v or "").strip()


def check(profile: str, strict: bool = False) -> dict:
    lock = _load_lock()
    locked_ver = _norm(lock.get("authority", {}).get("python_version"))
    locked_pkgs = {k.lower(): _norm(v) for k, v in lock.get("pinned_packages", {}).items()}
    # 本档关心的包 = 该档 import 模块映射到 lock 包名（简化：直接用 lock 全表校验）
    modules = PROFILES[profile]

    candidates = []
    seen = set()
    sources = [
        ("env:PAI_TEST_PYTHON", os.environ.get("PAI_TEST_PYTHON", "")),
        ("lock:authority", lock.get("authority", {}).get("python", "")),
        ("current", sys.executable),
    ]
    for label, exe in sources:
        exe = (exe or "").strip()
        if not exe:
            continue
        exe = str(pathlib.Path(exe))
        if exe.lower() in seen:
            continue
        seen.add(exe.lower())
        if not pathlib.Path(exe).is_file():
            candidates.append({"label": label, "python": exe, "reachable": False,
                               "error": "解释器文件不存在"})
            continue
        r = _probe(exe, list(locked_pkgs.keys()))
        c = {"label": label, "python": exe, **r}
        if r.get("reachable"):
            # import 校验：档位必需模块
            ic = subprocess.run([exe, "-c", "import " + ", ".join(modules)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25)
            c["imports_ok"] = ic.returncode == 0
            if ic.returncode != 0:
                err = (ic.stderr or "").strip().splitlines()
                c["import_error"] = err[-1] if err else "import failed"
            # 版本锁校验
            mism = []
            if locked_ver and _norm(r.get("py_version")) != locked_ver:
                mism.append("Python %s≠锁%s" % (r.get("py_version"), locked_ver))
            for pkg, want in locked_pkgs.items():
                got = _norm(r.get("packages", {}).get(pkg))
                if want and got and got != want:
                    mism.append("%s %s≠锁%s" % (pkg, got, want))
                elif want and not got:
                    mism.append("%s 缺失(锁%s)" % (pkg, want))
            c["version_mismatch"] = mism
            c["lock_ok"] = not mism
            c["ok"] = c["imports_ok"] and (c["lock_ok"] or not strict)
        else:
            c["imports_ok"] = False
            c["lock_ok"] = False
            c["ok"] = False
        candidates.append(c)

    # 选：ok 且 lock_ok 优先；其次 ok（版本不符，警告）；都不行则失败
    perfect = [c for c in candidates if c.get("ok") and c.get("lock_ok")]
    runnable = [c for c in candidates if c.get("ok")]
    selected = (perfect or runnable)
    chosen = selected[0] if selected else None
    return {
        "profile": profile,
        "required_modules": modules,
        "locked_python": locked_ver,
        "strict": strict,
        "has_lock": bool(lock),
        "selected": chosen["python"] if chosen else "",
        "selected_lock_ok": chosen.get("lock_ok", False) if chosen else False,
        "candidates": candidates,
        "ok": bool(chosen),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=sorted(PROFILES), default="backend")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="版本与锁不符也判失败")
    ns = ap.parse_args(argv)

    r = check(ns.profile, ns.strict)
    if ns.json:
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r["ok"] else 1

    print("测试环境自检 · 档位=%s%s" % (r["profile"], " · 严格" if r["strict"] else ""))
    print("必需模块: %s" % ", ".join(r["required_modules"]))
    print("锁: Python %s%s" % (r["locked_python"] or "(无锁文件)", "  [已加载]" if r["has_lock"] else "  [警告:无锁文件]"))
    for c in r["candidates"]:
        if not c.get("reachable"):
            print("  [MISS] %-16s %s  (%s)" % (c["label"], c["python"], c.get("error", "")))
            continue
        imp = "OK" if c.get("imports_ok") else "NO"
        lk = "OK" if c.get("lock_ok") else "DIFF"
        print("  [import %s / lock %s] %-14s %s  Python %s" %
              (imp, lk, c["label"], c["python"], c.get("py_version", "?")))
        if c.get("import_error"):
            print("        └─ import: %s" % c["import_error"])
        for m in c.get("version_mismatch", []):
            print("        └─ 版本差异: %s" % m)
    if r["ok"]:
        print("选用解释器: %s%s" % (r["selected"],
              "" if r["selected_lock_ok"] else "  [!] 版本与锁不符，结果可能不可复现"))
        return 0
    print("没有合格解释器。Python/tkinter 须换解释器；三方包可 pip install -r requirements.txt。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
