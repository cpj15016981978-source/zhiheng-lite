# -*- coding: utf-8 -*-
"""Keep a persistent Edge window open so the user can log in once."""
import pathlib
import sys
import time

profile = pathlib.Path(sys.argv[1])
url = sys.argv[2] if len(sys.argv) > 2 else "https://www.douyin.com/"
profile.mkdir(parents=True, exist_ok=True)
from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(profile), channel="msedge", headless=False,
        viewport={"width": 1280, "height": 900})
    page = context.pages[0] if context.pages else context.new_page()
    if url:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    # 用户自己登录/关闭窗口；最多留 30 分钟。
    end = time.time() + 1800
    while time.time() < end:
        try:
            if not context.pages:
                break
        except Exception:
            break
        time.sleep(2)
    try:
        context.close()
    except Exception:
        pass
