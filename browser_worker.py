# -*- coding: utf-8 -*-
"""Standalone Playwright worker: backend calls this in a subprocess."""
import json
import pathlib
import sys
import time


def main():
    req = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    url = req.get("url") or ""
    actions = req.get("actions") or []
    headless = bool(req.get("headless"))
    shots = pathlib.Path(req["shots"])
    shots.mkdir(parents=True, exist_ok=True)
    profile = pathlib.Path(req["profile"])
    profile.mkdir(parents=True, exist_ok=True)
    out = {"ok": False, "log": []}
    started = time.time()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(profile), channel="msedge",
                headless=headless, viewport={"width": 1280, "height": 900})
            page = context.pages[0] if context.pages else context.new_page()
            if url:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                out["log"].append({"step": "goto", "ok": True, "url": page.url})
            for i, a in enumerate(actions, 1):
                kind = str((a or {}).get("type") or "")
                sel = str((a or {}).get("selector") or "")
                if kind == "click":
                    page.locator(sel).click(timeout=10000)
                elif kind == "fill":
                    page.locator(sel).fill(str((a or {}).get("text") or ""), timeout=10000)
                elif kind == "wait":
                    page.wait_for_timeout(min(5000, int((a or {}).get("ms") or 500)))
                elif kind == "text":
                    page.locator(sel).inner_text(timeout=10000)
                else:
                    raise ValueError("未知动作：%s" % kind)
                out["log"].append({"step": i, "ok": True, "type": kind, "selector": sel})
            shot = shots / ("shot-%d.png" % int(time.time()))
            page.screenshot(path=str(shot), full_page=False)
            out.update(ok=True, url=page.url, title=page.title(),
                       text=page.locator("body").inner_text(timeout=10000)[:12000],
                       screenshot=str(shot))
            context.close()
    except Exception as e:  # noqa: BLE001
        out.update(ok=False, reason="%s: %s" % (type(e).__name__, e))
    out["elapsed_sec"] = round(time.time() - started, 2)
    pathlib.Path(sys.argv[2]).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
