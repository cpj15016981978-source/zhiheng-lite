# -*- coding: utf-8 -*-
"""代码请求分流：写代码/修脚本走 coder 模型，其余走主模型。"""
from __future__ import annotations

import os

CAPABILITY = {
    "name": "代码模型分流",
    "switch_env": "PAI_CODE_ROUTE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "coder 缺失=回主模型；不静默把代码请求交给 1.5B",
    "approval": "ADR-046：本地模型路由，不新增开关",
}

CODE_WORDS = ("代码", "脚本", "写个", "函数", "修", "bug", "报错", "python", "py", "sql",
              "class", "def ", "调试", "单元测试", "unittest", "接口")


def is_code(text):
    low = str(text or "").lower()
    return any(w in low for w in CODE_WORDS)


def model():
    return os.environ.get("PAI_CODE_MODEL", "qwen2.5-coder:3b").strip()


def spec():
    return "ollama:" + model()
