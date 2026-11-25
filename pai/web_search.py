# -*- coding: utf-8 -*-
"""联网搜索：Tavily Search API。

Key 只从环境变量读，绝不进前端。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from . import offline as OFF

DEFAULT_BASE = "https://api.tavily.com"
MAX_QUERY = 400

CAPABILITY = {
    "name": "联网搜索",
    "switch_env": "TAVILY_API_KEY",
    "default_on": False,
    "stores_user_text": False,
    "network": True,
    "degrade": "无 Key/401/403/超时=SearchError，不假装搜过；只把查询发用户配置的 Tavily 端点",
    "approval": "ADR-044 出网接入登记；ADR-046 出网/常开边界",
}


class SearchError(ValueError):
    pass


def _key():
    return (os.environ.get("TAVILY_API_KEY")
            or os.environ.get("PAI_SEARCH_API_KEY") or "").strip()


def configured():
    return bool(_key()) and OFF.is_allowed("web_search")


def search(query, limit=5):
    OFF.assert_allowed("web_search", SearchError)
    q = str(query or "").strip()
    if not q:
        raise SearchError("搜索词为空")
    key = _key()
    if not key:
        raise SearchError("未配置 TAVILY_API_KEY / PAI_SEARCH_API_KEY")
    base = (os.environ.get("PAI_SEARCH_BASE_URL") or DEFAULT_BASE).rstrip("/")
    body = {
        "api_key": key,
        "query": q[:MAX_QUERY],
        "search_depth": os.environ.get("PAI_SEARCH_DEPTH", "basic"),
        "max_results": max(1, min(10, int(limit))),
        "include_answer": True,
        "include_raw_content": False,
    }
    req = urllib.request.Request(
        base + "/search",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=int(os.environ.get("PAI_SEARCH_TIMEOUT", "30"))) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300] if hasattr(e, "read") else ""
        raise SearchError("搜索接口 HTTP %s：%s" % (e.code, detail)) from None
    except urllib.error.URLError as e:
        raise SearchError("连不上搜索接口：%s" % e.reason) from None
    results = []
    for item in (data.get("results") or [])[:10]:
        results.append({
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "content": str(item.get("content") or "")[:1200],
        })
    return {"query": q, "answer": str(data.get("answer") or ""), "results": results}

