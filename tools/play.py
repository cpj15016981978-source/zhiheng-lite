# -*- coding: utf-8 -*-
"""G1·一手：开自家窗口 → 读屏 → 计划 → 按一个键 → 对照现场。一次调用只走一步。

这条就是 Boss 要的那个现场：「它自己开游戏玩、我在旁边看」。他看的是屏幕上那个窗口，
本工具打印的是它这一步看见了什么、决定按哪个键、以及【按完之后窗口自己认的现场】
和【我们从像素推算出来的下一步】对不对得上。

★为什么不循环：Backlog 上写死「一次只玩一手」。要连着玩就把这条命令再跑一遍
  （python tools/play.py 一次一步），或者让 Boss 自己在旁边按。这里没有 while，
  也不起常驻进程 —— 游戏窗口空闲 --idle-ttl 秒会自己关掉。
★两个现场是【两条独立渠道】：像素（hands.capture_grid）和窗口标题（GetWindowText）。
  对不上就是有问题，不许只信其中一个 —— ADR-062 探针第三问在这条线上的落点。

用法（在工作区根目录跑）：
    set PAI_HANDS=on
    python tools/play.py --start      只开窗口，不动
    python tools/play.py --look       只读屏，不按键
    python tools/play.py              开窗口（如果还没有）→ 读屏 → 按一手
    python tools/play.py --stop       给自家窗口按 q，关掉不留进程
"""
import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pai import game, hands                                   # noqa: E402

TITLE_RE = re.compile(r"分(\d+) · 头(\d+),(\d+) · 向(\w+) · (活|死)")
WAIT_DEADLINE_S = 3.0
POLL_S = 0.05


def parse_title(title):
    hit = TITLE_RE.search(title or "")
    if not hit:
        return None
    score, r, c, direction, alive = hit.groups()
    return {"score": int(score), "head": (int(r), int(c)), "direction": direction,
            "alive": alive == "活", "title": title}


def find_game():
    """返回 (hwnd, reason)。多个自家窗口 = 判不准是哪个，如实报回来不猜。"""
    found = hands.own_windows()
    if isinstance(found, dict):
        return None, found["reason"]
    if not found:
        return None, "还没有自家窗口（先跑 --start，或让它自己开）"
    if len(found) > 1:
        return None, "自家窗口有 %d 个，判不准该动哪个：%s" % (
            len(found), "、".join(w["title"] for w in found))
    return found[0]["hwnd"], ""


def start_game(idle_ttl=60):
    """起一个自家游戏窗口就撒手：不等它、不接管它的控制台，空闲它自己退。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen([sys.executable, str(ROOT / "tools" / "pai_game.py"),
                             "--idle-ttl", str(idle_ttl)],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.pid


def wait_for_game(timeout_s=8.0):
    """轮询到窗口出现为止 —— 只看标题，一个键也不发。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        hwnd, reason = find_game()
        if hwnd:
            return hwnd, ""
        time.sleep(POLL_S)
    return None, "等了 %.0f 秒还没看到自家窗口（%s）" % (timeout_s, reason_or(reason))


def reason_or(reason):
    return reason or "没有匹配的标题"


def wait_until_title_changes(hwnd, before, deadline_s=WAIT_DEADLINE_S):
    """按完键等窗口自己改标题：等不到就如实回报【没变】，不许拿旧标题当新现场。"""
    deadline = time.time() + deadline_s
    last = before
    while time.time() < deadline:
        last = hands.window_title(hwnd)
        if last != before:
            return last, "ok"
        time.sleep(POLL_S)
    return last, "按下去之后 %.1f 秒标题没变：键没进事件循环，或被别的程序抢了焦点" % deadline_s


def grid_report(counts):
    return "身%d 头%d 食物%d 认不出的格子%d" % (
        counts.get(game.KIND_SNAKE, 0), counts.get(game.KIND_HEAD, 0),
        counts.get(game.KIND_FOOD, 0), counts.get(game.KIND_OTHER, 0))


def do_start(args):
    hwnd, reason = find_game()
    if hwnd:
        print("已经有自家窗口了：%s（句柄 %s）" % (hands.window_title(hwnd), hwnd))
        print("START=existing")
        return 0
    pid = start_game(args.idle_ttl)
    hwnd, reason = wait_for_game()
    if not hwnd:
        print("开不出来：%s（我起的进程 pid=%s，它可能自己退了）" % (reason, pid))
        print("START=fail")
        return 1
    print("窗口开好了：%s（句柄 %s，pid=%s）" % (hands.window_title(hwnd), hwnd, pid))
    print("它空闲 %d 秒会自己关掉，不用你去杀进程。" % args.idle_ttl)
    print("START=ok")
    return 0


def do_stop(_args):
    found = hands.own_windows()
    if isinstance(found, dict):
        print(found["reason"])
        return 1
    if not found:
        print("没有自家窗口要关。")
        print("STOP=none")
        return 0
    for item in found:
        trace = hands.press_key(item["hwnd"], "q")
        print("给 %s 按 q：%s" % (item["title"], "ok" if trace["ok"] else trace["reason"]))
    print("STOP=%d" % len(found))
    return 0


def do_move(args, press=True):
    if not hands.enabled():
        print("读屏/按键是关的：设 %s=on 才动（默认关是 Boss 给的限制之一）。" % hands.ENV_SWITCH)
        print("MOVE=off")
        return 2
    hwnd, reason = find_game()
    if not hwnd and press:
        pid = start_game(args.idle_ttl)
        hwnd, reason = wait_for_game()
        if not hwnd:
            print("开不出来：%s（pid=%s）" % (reason, pid))
            print("MOVE=fail")
            return 1
    if not hwnd:
        print("没有自家窗口，读不了屏（%s）" % reason)
        print("MOVE=no-window")
        return 1

    before_title = hands.window_title(hwnd)
    before = parse_title(before_title)
    shot = hands.capture_grid(hwnd)
    if not shot["ok"]:
        print("读屏没成：%s" % shot["reason"])
        print("MOVE=look-fail")
        return 1
    state, other = game.state_from_cells(shot["grid"],
                                         direction=(before or {}).get("direction", game.DIR_RIGHT))
    print("看见的（像素）：%s" % grid_report(shot["counts"]))
    if other:
        print("★有 %d 个格子认不出来（被遮住了？），这一帧不当完整现场用。" % other)
    if state is None:
        print("画面上找不到蛇头，不猜它在哪儿。")
        print("MOVE=no-head")
        return 1
    if before and before["head"] != state["snake"][0]:
        print("★两条渠道先就对不上：标题说头在 %s，像素说头在 %s。" % (
            "%d,%d" % before["head"], "%d,%d" % state["snake"][0]))
    if not press:
        print("LOOK=ok")
        return 0

    direction = game.plan(state)
    if direction is None:
        print("计划：合法方向一个都不剩（该死了），不按。")
        print("MOVE=stuck")
        return 0
    expected, event = game.step(state, direction)
    # ★分数像素看不出来（画面上只有格子），这一路只能从标题拿：格子归像素、分数归标题。
    before_score = (before or {}).get("score", 0)
    expected["score"] = before_score + (1 if event == game.ATE else 0)
    trace = hands.press_key(hwnd, direction)
    if not trace["ok"]:
        print("按键没成：%s" % trace["reason"])
        print("MOVE=press-fail")
        return 1
    print("计划的这一手：%s（事件 %s，预计头 %s 分 %d）" % (
        direction, event, "%d,%d" % expected["snake"][0], expected["score"]))
    after_title, why = wait_until_title_changes(hwnd, before_title)
    after = parse_title(after_title)
    if after is None:
        print("窗口自报的现场读不到：%s（标题 %r）" % (why, after_title))
        print("MATCH=no-title")
        return 1
    print("窗口自报的现场：%s" % ("分%d 头%s 向%s %s" % (
        after["score"], "%d,%d" % after["head"], after["direction"],
        "活" if after["alive"] else "死")))
    agree = (after["head"] == expected["snake"][0] and after["score"] == expected["score"]
             and after["alive"] == expected["alive"])
    print("一致：%s" % ("是（像素推的下一格 == 窗口自报的这一格）" if agree else
                    "否 —— 读屏那边看到的和它自己认的对不上，这一手不算验通"))
    print("MOVE=ok direction=%s posted=%d target=%s" % (direction, trace["posted"], trace["target"]))
    print("MATCH=%s" % ("yes" if agree else "no"))
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="G1 一手：开窗口 → 读屏 → 按一手 → 对照现场")
    p.add_argument("--start", action="store_true", help="只开窗口")
    p.add_argument("--look", action="store_true", help="只读屏，不按键")
    p.add_argument("--stop", action="store_true", help="给自家窗口按 q 关掉")
    p.add_argument("--idle-ttl", type=int, default=60, help="新开窗口空闲多少秒自关")
    return p.parse_args(argv)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    if args.stop:
        return do_stop(args)
    if args.start:
        return do_start(args)
    return do_move(args, press=not args.look)


if __name__ == "__main__":
    sys.exit(main())
