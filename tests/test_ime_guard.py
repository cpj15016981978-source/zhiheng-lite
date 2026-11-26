# LowSpec Muse v2.2 输入法守卫静态测试（Issue #1 回归钉）
# 跑法：python tests/test_ime_guard.py（只用标准库，离线可跑）
# 原理：读 index.txt 原文，检查三处 Enter 处理是否都有 Shift+Enter 不发送的守卫，
# 以及对话输入框是否保留组字守卫（composing / isComposing / keyCode 229）。
# 行为级验证（抽真实处理函数原文跑 11 项用例）用 Node 脚本做过，见提交说明，不进仓库。

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASSED = []
FAILED = []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(detail)) if detail and not cond else ""))


def handler_block(src, marker, window=400):
    i = src.find(marker)
    if i < 0:
        return ""
    return src[i:i + window]


def main():
    path = os.path.join(ROOT, "index.txt")
    src = open(path, encoding="utf-8").read()

    chat = handler_block(src, "input.addEventListener('keydown', function(e){")
    search = handler_block(src, "$('#search-input').addEventListener('keydown', function(e){")
    plan = handler_block(src, "$('#plan-goal').addEventListener('keydown', function(e){")

    check("1 三处 keydown 处理都在 index.txt 里", bool(chat and search and plan))
    check("2 对话框 Shift+Enter 直接返回不发送",
          "e.key === 'Enter' && e.shiftKey" in chat, chat[:200])
    check("3 对话框保留组字守卫（composing/isComposing/229）",
          "composing" in chat and "e.isComposing" in chat and "e.keyCode === 229" in chat)
    check("4 搜索框 Enter 带 !shiftKey",
          "e.key === 'Enter' && !e.shiftKey" in search, search[:200])
    check("5 任务目标框 Enter 带 !shiftKey",
          "e.key === 'Enter' && !e.shiftKey" in plan, plan[:200])

    print(f"\n==== 结果：{len(PASSED)} 通过，{len(FAILED)} 失败 ====")
    if FAILED:
        print("失败项：")
        for f in FAILED:
            print("  -", f)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
