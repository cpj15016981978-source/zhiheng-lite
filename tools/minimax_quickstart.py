# -*- coding: utf-8 -*-
"""接 MiniMax 的启动器 —— 给「试了很多次都不行」的那条路用的。

为什么要它：cmd 的 .bat 是按【字节】切行的，中文（GBK 双字节）的第二字节可能正好是
管道符或反斜杠，于是整行被拆断，报 "xxx is not recognized as an internal or external
command"。我把中文提示写进 .bat 试过两次，两次都是这个断法。所以规矩定死：
 bat 只许 ASCII（tests/test_console_encoding.py 守着），所有中文话都在 Python 里说
 —— Python 直接按控制台编码写出去，GBK 窗口显示中文毫无问题。

密钥的走法（安全底线，见 ADR-017）：只进【本进程】环境变量，
不写文件、不进记忆库、不发到共享目录、不贴进对话；关掉窗口就消失。
这里【故意用看得见的 input()】而不是 getpass 盲输 —— 盲输看不见自己粘了什么，
正是这几轮失败的原因之一。长度当场报出来，粘漏没粘漏一眼能判。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import check_key                                  # 复用同一份自检，不抄第二份规则

PROVIDER = "openai"                                          # ★协议名，不是公司名（ADR-041）
BASE_URL = "https://api.minimaxi.com/v1"                     #   这台 2026-10-21 验通过的端点
MODEL_NAME = "MiniMax-M3.1-Flash"                            # Boss 2026-10-22 定：切 Flash（1.0/4.0）
                                                             # ★Flash 在这个端点上【还没验通】，
                                                             #   验通的是同端点的 MiniMax-M3


def read_key(prompt=""):
    """读一行粘贴的 Key。返回 None = 没读到（空、或根本没有终端可问）。"""
    try:
        raw = input(prompt)
    except (EOFError, KeyboardInterrupt):
        print("\n  没拿到输入（没有终端，或按了 Ctrl+C）。什么都不改，退出。")
        return None
    key = raw.strip()                                        # 首尾空格是复制粘贴最常见的坑
    if not key:
        return None
    if len(key) < 12:
        print(f"  ★这一行只有 {len(key)} 个字符，不像一把 Key。"
              f"通常是复制没选中、或粘到了别的东西。已退出，没联网、没花钱。")
        return None
    if " " in key or "\t" in key:
        print("  ★这行中间有空格，多半是把两行一起粘进来了。已退出，重来一次只粘一行。")
        return None
    return key


def apply_env(key):
    os.environ["PAI_MODEL_PROVIDER"] = PROVIDER
    os.environ[f"PAI_{PROVIDER.upper()}_BASE_URL"] = BASE_URL
    os.environ["PAI_MODEL_NAME"] = MODEL_NAME
    os.environ[f"{PROVIDER.upper()}_API_KEY"] = key            # 只进本进程，不落盘


def ask_mode():
    """返回 go_interface / go_chat / stop。问不出来按 stop 处理。"""
    try:
        ans = (input("  直接按回车 = 开界面；输入 c 回车 = 开命令行；输入 q 回车 = 就停在这里：")
               .strip().lower())
    except (EOFError, KeyboardInterrupt):
        print()
        return "stop"
    if ans == "c":
        return "chat"
    if ans in ("", "i"):
        return "interface"
    return "stop"


def launch(mode):
    if mode == "chat":
        import chat
        sys.argv = ["chat.py"]                                # 别让本启动器的参数串进去
        chat.main()
        return 0
    if mode == "interface":
        import webapp
        return webapp.main([])
    print("  就停在这里。密钥只在这个窗口的环境变量里，关掉窗口就没了。")
    return 0


def main(argv=None):
    print("=" * 52)
    print("  接 MiniMax：下一步让你把 Key 粘进来。")
    print("  会显示你粘的内容 —— 看得见才判得准，这几轮就是栽在看不见。")
    print("  两个别做：别把 Key 贴进聊天对话；")
    print("  别在程序的「你」提问后面打 Key，那会被当成你说的话存进记忆库。")
    print("  （变量名里的 OPENAI 指的是【协议】不是公司：MiniMax 讲 OpenAI 那套话，")
    print("   所以走 provider=openai + 把端点指到 MiniMax。已定不给网关加 minimax 别名，见 ADR-041。）")
    print("=" * 52)
    print()

    key = read_key("  粘贴 MiniMax Key，然后按回车：")
    if key is None:
        print("\n  已退出：没设任何变量、没联网、没花钱。")
        print("  拿 Key 的地方：MiniMax 开放平台控制台 - 账户 - API Key 管理 - 新建。")
        return 1

    print(f"  收到：{len(key)} 个字符（值不重复显示）。")
    apply_env(key)
    code = check_key.main([])                                # 本地自检，不联网
    print()
    print("  怎么读上面这份报告：")
    print("  密钥状态 已配置（长度 %d） + 结论「已就绪」 = 齐了，可以开聊。" % len(key))
    print("  结论「还没配好」 = 这一行 Key 没进去，重来一次。")
    print("  结论「配了但发现泄露迹象」（退出码 2） = 先按上面指出的位置清干净。")
    print("  ★报告里出现「会出门」= 这台机器的【持久层】里还留着一份旧配置，")
    print("    它跟本窗口的设置是两回事；清它要在注册表里删（README 接真实模型一节有那行命令）。")
    print()
    return launch(ask_mode())


if __name__ == "__main__":
    sys.exit(main())
