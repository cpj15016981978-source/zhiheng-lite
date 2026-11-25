# -*- coding: utf-8 -*-
"""G1·自家游戏窗口（Tkinter，stdlib）。棋盘逻辑一律 import pai.game，开关与标题前缀 import pai.hands。

为什么要这个文件：G1 要验的是【它自己开窗口 → 看现场 → 按一手】这条链路，所以画面必须
真的有一块屏 —— pai/game.py 算得再对也不构成"读屏"。

三条现场约束（都写在代码里，不靠使用者自觉）：
  · 标题 = pai.hands.WINDOW_TITLE_PREFIX 开头：读屏/按键那边只认这一串，画面换名字要先改常量。
  · 客户区严格等于 COLS*CELL_PX x ROWS*CELL_PX：hands.capture_grid() 拿尺寸对不上就拒读，
    所以这里不许出现滚动条、边距、状态条 —— 分数写在标题里，不占画面。
  · 空闲会自己关掉：--idle-ttl 秒没有键就退出（默认 60），再加一道 --max-ttl 硬上限。
    ★这条是给「不许变成常驻进程」兜底的：没人看也没人按，它自己就走。

移动是键驱动的：一格一步，一个键走一步 —— 对应 Backlog 上「一次只玩一手」。
没有内部计时器往前爬，所以调用方按一次它动一次，读屏看到的和按下去的能一一对上。
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai import game                                    # noqa: E402
from pai.hands import KEYS, WINDOW_TITLE_PREFIX          # noqa: E402

DIR_BY_KEY = {KEYS["up"]: game.DIR_UP, KEYS["down"]: game.DIR_DOWN,
              KEYS["left"]: game.DIR_LEFT, KEYS["right"]: game.DIR_RIGHT}


def hex_of(rgb):
    return "#%02x%02x%02x" % rgb


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="G1 自家游戏窗口（默认不常驻：空闲自关）")
    p.add_argument("--seed", type=int, default=7, help="撒食物的随机种子")
    p.add_argument("--head", default="", help="开局头位置 r,c，空则用默认（方便测试摆格子）")
    p.add_argument("--idle-ttl", type=int, default=60, help="多少秒没有键就自己退出")
    p.add_argument("--max-ttl", type=int, default=600, help="最长活多久，到点一定退")
    p.add_argument("--tick-ms", type=int, default=500, help="看门狗轮询间隔（不动棋盘，只管超时）")
    return p.parse_args(argv)


def title_of(state):
    hr, hc = state["snake"][0]
    return "%s贪吃蛇 · 分%d · 头%d,%d · 向%s · %s" % (
        WINDOW_TITLE_PREFIX, state["score"], hr, hc, state["direction"],
        "活" if state["alive"] else "死")


def report(state):
    """一行机器可读的现场（手动跑时给终端看；tools/play.py 不读它，读的是标题）。"""
    hr, hc = state["snake"][0]
    print("STATE score=%d head=%d,%d dir=%s alive=%d steps=%d"
          % (state["score"], hr, hc, state["direction"], 1 if state["alive"] else 0,
             state["steps"]))
    sys.stdout.flush()


class Shell(object):
    def __init__(self, root, canvas, state, rng):
        self.root, self.canvas, self.state, self.rng = root, canvas, state, rng
        self.last_key = time.time()
        self.started = time.time()

    def draw(self):
        cv, cell = self.canvas, game.CELL_PX
        cv.delete("all")
        kinds = [[game.KIND_BG for _ in range(game.COLS)] for _ in range(game.ROWS)]
        for index, (r, c) in enumerate(self.state["snake"]):
            kinds[r][c] = game.KIND_HEAD if index == 0 else game.KIND_SNAKE
        if self.state["food"]:
            fr, fc = self.state["food"]
            kinds[fr][fc] = game.KIND_FOOD
        fill = {game.KIND_BG: game.COLOR_BG, game.KIND_SNAKE: game.COLOR_SNAKE,
                game.KIND_HEAD: game.COLOR_HEAD, game.KIND_FOOD: game.COLOR_FOOD,
                game.KIND_OTHER: (0, 0, 0)}
        for r in range(game.ROWS):
            for c in range(game.COLS):
                x, y = c * cell, r * cell
                cv.create_rectangle(x, y, x + cell - 1, y + cell - 1,
                                    fill=hex_of(fill[kinds[r][c]]), width=0)
        self.root.title(title_of(self.state))

    def on_key(self, event):
        direction = DIR_BY_KEY.get(event.keycode)
        self.last_key = time.time()
        if direction is None:
            if event.char.lower() == "q":
                self.root.destroy()
            return
        if not self.state["alive"]:
            report(self.state)
            return
        self.state, _event = game.step(self.state, direction, self.rng)
        self.draw()
        report(self.state)


def build(args):
    import random
    import tkinter as tk
    root = tk.Tk()
    canvas = tk.Canvas(root, width=game.CANVAS_PX, height=game.CANVAS_PX,
                       background=hex_of(game.COLOR_BG), highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)
    root.resizable(False, False)
    # 读的是屏幕像素：窗口被别的程序压住就会读成别人的东西，所以置顶（不抢焦点、不最小化别人）。
    root.attributes("-topmost", True)
    head = None
    if args.head:
        r, c = args.head.split(",")
        head = (int(r), int(c))
    rng = random.Random(args.seed)
    shell = Shell(root, canvas, game.new_state(rng=rng, head=head), rng)
    shell.draw()
    root.bind("<Key>", shell.on_key)
    canvas.focus_set()
    root.focus_force()
    return root, shell, args


def watchdog(root, shell, args):
    gone_idle = time.time() - shell.last_key
    gone_total = time.time() - shell.started
    if gone_idle > args.idle_ttl or gone_total > args.max_ttl:
        print("EXIT %s idle=%ds total=%ds" % (
            "max-ttl" if gone_total > args.max_ttl else "idle-ttl", gone_idle, gone_total))
        sys.stdout.flush()
        root.destroy()
        return
    root.after(args.tick_ms, lambda: watchdog(root, shell, args))


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    try:
        root, shell, args = build(args)
    except Exception as exc:
        print("起不了窗口：%s: %s" % (exc.__class__.__name__, exc))
        return 1
    watchdog(root, shell, args)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
