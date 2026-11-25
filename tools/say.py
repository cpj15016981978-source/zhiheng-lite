# -*- coding: utf-8 -*-
"""本机嗓子自检（M14 语音层的验证入口，非产品逻辑）。

它只做一件事：把给你的这句话用【本机】Windows 语音念出来，让你确认
"能说"这条路真的通了 —— 不出门、不联网、不装任何东西。

运行（整块可粘贴，中文别加引号也行）：
    cd C:\\Users\\Administrator\\Documents\\Qoder\\2026-10-19\\14868c32\\personal-ai-system
    python tools\\say.py 在的，中文嗓子可以用

附带能做的：
    python tools\\say.py --list               列出本机装了哪些嗓子
    python tools\\say.py --voice "Microsoft Huihui Desktop" --rate 2 慢一点我听听
    python tools\\say.py --stop               掐掉正在念的那句

只支持 Windows（System.Speech）。退出码：0 真念了 / 1 没念成（原因会说）。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pai import voice as M14                          # noqa: E402


def list_voices():
    """列本机嗓子：设备动作住在 pai/voice.py 的 installed_voices()，这件只是壳（P32 / ADR-079）。"""
    got = M14.installed_voices()
    rows = got["rows"]
    if not rows:
        print("   " + (got["reason"] or "查不到嗓子"))
        return 1
    zh = [r for r in rows if "zh" in r.lower()]
    print("   本机可用嗓子 %d 条%s：" % (len(rows), "（中文优先看这些）" if zh else ""))
    for r in rows:
        print("     " + r)
    if not zh:
        print("   ★没找到中文嗓：中文会念不出或念成洋腔。到"
              "「设置 → 时间和语言 → 语音」里加一个中文语音包再试。")
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if "--stop" in args:
        print("   [已掐掉]" if M14.stop() else "   现在没在念任何东西。")
        return 0
    if "--list" in args:
        return list_voices()

    voice = None
    rate = None
    if "--voice" in args:
        i = args.index("--voice")
        if i + 1 >= len(args):
            print("   --voice 后面要跟嗓子名（先跑 --list 看有哪些）")
            return 1
        voice = args[i + 1]
        del args[i:i + 2]
    if "--rate" in args:
        i = args.index("--rate")
        if i + 1 >= len(args):
            print("   --rate 要一个 -10 到 10 的数（0 正常，负数慢，正数快）")
            return 1
        rate = args[i + 1]
        del args[i:i + 2]

    text = " ".join(a for a in args if not a.startswith("--")).strip()
    if not text:
        text = "在的，我在。这条是本机念出来的，不是屏幕上的字。"

    os.environ[M14.ENV_BACKEND] = M14.BACKEND_WINDOWS     # 本工具就是来试嗓子的，只改本进程
    t = M14.speak(text, rate=rate, voice=voice, wait=True)
    if t["spoken"]:
        print("   [已念出] %d 字 · 后端 %s · 语速 %s%s" % (
            t["chars"], t["backend"], t["rate"],
            "（太长，按整句截过）" if t["truncated"] else ""))
        print("   听不到声音通常是输出设备问题，不是代码问题：先跑 --list 看嗓子。")
        return 0
    print("   [没念成] %s" % t["reason"])
    print("   这条路要求：Windows + PowerShell + 至少一个语音引擎（--list 查）。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
