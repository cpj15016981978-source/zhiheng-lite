# -*- coding: utf-8 -*-
"""G1·游戏本体（棋盘逻辑，零依赖）。见黑板 ADR-055 与 Backlog「G1 AI 自己开游戏机玩」。

只做棋盘：状态、走一步、合法方向、贪心策略、从"读屏认出来的格子"重建状态。
不开窗口、不读屏、不按键、不落盘、不出网、不起线程。

★尺寸与颜色【唯一住址在这里】：画面 tools/pai_game.py 按它画，读屏 pai/hands.py 按它认。
谁也不许抄第二份（ADR-030 同一口径）—— 抄了就会出现"画的是绿、认的是蓝"这种永远对不上的现场。

棋盘是 12x12、一格 24 像素、画面里没有第三种颜色：读屏那边只可能看到 BG/SNAKE/HEAD/FOOD
四种，认不出来的格子标成 "other"（遮住了、别的窗口压上来了），不许当成背景猜过去。
"""

COLS = 12
ROWS = 12
CELL_PX = 24
CANVAS_PX = COLS * CELL_PX          # 画面只有这一块，客户区就等于它 —— 读屏按这个对齐

# 颜色一律 (r, g, b)。四色互相离得远，是为了让"最近色分类"在抗锯齿边缘也不会认错。
COLOR_BG = (255, 255, 255)
COLOR_SNAKE = (46, 139, 87)
COLOR_HEAD = (0, 0, 205)
COLOR_FOOD = (220, 20, 60)
COLOR_TOLERANCE = 90                # 离四个色都超过这个距离 = 这不是自家棋盘，标 other

KIND_BG = "bg"
KIND_SNAKE = "snake"
KIND_HEAD = "head"
KIND_FOOD = "food"
KIND_OTHER = "other"

DIR_UP = "up"
DIR_DOWN = "down"
DIR_LEFT = "left"
DIR_RIGHT = "right"
DIRS = (DIR_UP, DIR_RIGHT, DIR_DOWN, DIR_LEFT)      # 平票时按这个次序取，保证可复现
DELTA = {DIR_UP: (-1, 0), DIR_DOWN: (1, 0), DIR_LEFT: (0, -1), DIR_RIGHT: (0, 1)}
OPPOSITE = {DIR_UP: DIR_DOWN, DIR_DOWN: DIR_UP, DIR_LEFT: DIR_RIGHT, DIR_RIGHT: DIR_LEFT}

SNAKE_START_LEN = 3

MOVED = "moved"
ATE = "ate"
DEAD = "dead"

# 开关名唯一住址在 pai/hands.py:ENV_SWITCH。这里必须写字面量（契约登记要能被 ast 解析出来，
# 而 game 反过来 import hands 会成环），所以由 tests/test_game.py 钉住两者不许漂移。
CAPABILITY = {
    "name": "G1 棋盘本体（纯逻辑，不开窗口不读屏）",
    "switch_env": "PAI_HANDS",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "没有外部依赖，也就没有降级面：输入不合法（越界/撞身子/掉头）一律如实返回 "
               "dead 或原样退回，不猜、不抛",
    "probe_cost": "纯函数，不读环境变量、不开文件、不起进程、不联网",
    "approval": "棋盘自己没有开关，随 pai/hands.py 的 PAI_HANDS 一起关着；Boss 2026-10-22 "
                "选「它自己开游戏玩、我在旁边看」这个方向，留底见黑板 ADR-055",
}


def in_bounds(pos):
    r, c = pos
    return 0 <= r < ROWS and 0 <= c < COLS


def new_state(rng=None, head=None):
    """开局：三节身子朝右，头在中间偏左。rng 只用来撒食物（传进来才好复现）。"""
    r, c = head if head is not None else (ROWS // 2, COLS // 4)
    snake = [(r, c), (r, c - 1), (r, c - 2)]
    state = {"snake": snake, "food": None, "score": 0, "alive": True,
             "direction": DIR_RIGHT, "steps": 0}
    state["food"] = spawn_food(snake, rng)
    return state


def spawn_food(snake, rng=None):
    """在空格里随机撒一个食物。没有 rng 就用全局的（界面手动玩时不讲究复现）。"""
    import random
    free = [(r, c) for r in range(ROWS) for c in range(COLS) if (r, c) not in set(snake)]
    if not free:
        return None
    return (rng or random).choice(free)


def body_without_tail(state):
    """撞身子要撞的是"留下来的那截"：不吃东西时尾格这步会走掉，所以不算障碍。

    ★读屏重建的状态例外：照片看不出身子顺序，也就不知道哪一格是尾 —— 那种状态带
    whole_body_blocks=True，整条身子都当障碍。宁可笨一点，不许把尾巴认成路。
    """
    snake = state["snake"]
    if state.get("whole_body_blocks"):
        return set(snake)
    return set(snake[:-1])


def target_of(state, direction):
    hr, hc = state["snake"][0]
    dr, dc = DELTA[direction]
    return (hr + dr, hc + dc)


def step(state, direction, rng=None):
    """走一步。返回 (新状态, 事件)，事件 = moved / ate / dead；死了的状态不再往前推。"""
    if not state["alive"]:
        return state, DEAD
    # 掉头不算一步：12x12 上"往回走"必然撞自己的第二节，与其让它死于物理不可能的动作，
    # 不如把这一步原样退回 —— 读屏那边看到的和棋盘算的是同一件事。
    if direction == OPPOSITE.get(state["direction"]):
        return state, MOVED
    nxt = target_of(state, direction)
    if not in_bounds(nxt) or nxt in body_without_tail(state):
        dead = dict(state)
        dead.update(alive=False, direction=direction)
        return dead, DEAD
    snake = [nxt] + list(state["snake"])
    score = state["score"]
    food = state["food"]
    event = MOVED
    if nxt == food:
        score += 1
        food = spawn_food(snake, rng)
        event = ATE
    else:
        snake.pop()
    new = dict(state)
    new.update(snake=snake, food=food, score=score, alive=True,
               direction=direction, steps=state["steps"] + 1)
    return new, event


def legal_moves(state):
    """不会立刻死的方向。平局/排序都由调用方按 DIRS 决定，这里只做筛选。"""
    if not state["alive"]:
        return []
    out = []
    for d in DIRS:
        if d == OPPOSITE.get(state["direction"]):
            continue
        nxt = target_of(state, d)
        if in_bounds(nxt) and nxt not in body_without_tail(state):
            out.append(d)
    return out


def plan(state):
    """贪心一步：在合法方向里挑【离食物曼哈顿距离最近】的那个，平票按 DIRS 次序。

    ★这一版故意只做一步：主线要验的是"它能不能自己开窗口、看现场、按一手"这条链路，
    不是下棋水平。没有 lookahead、不接模型、不花钱、不出网。
    """
    legal = legal_moves(state)
    if not legal or state["food"] is None:
        return None
    fr, fc = state["food"]

    def distance(direction):
        r, c = target_of(state, direction)
        return abs(r - fr) + abs(c - fc)

    return min(legal, key=lambda d: (distance(d), DIRS.index(d)))


def classify_pixel(rgb):
    """一个像素属于哪种格子 —— 只认这四个色，认不出来如实标 other，不当背景猜。"""
    best, best_d = KIND_OTHER, None
    for kind, color in ((KIND_HEAD, COLOR_HEAD), (KIND_FOOD, COLOR_FOOD),
                        (KIND_SNAKE, COLOR_SNAKE), (KIND_BG, COLOR_BG)):
        d = sum((a - b) ** 2 for a, b in zip(rgb, color)) ** 0.5
        if best_d is None or d < best_d:
            best, best_d = kind, d
    return best if best_d is not None and best_d <= COLOR_TOLERANCE else KIND_OTHER


def state_from_cells(kinds, direction=DIR_RIGHT):
    """把读屏认出来的 kinds（ROWS x COLS 的字符串）重建成本模块的状态。

    照片里没有身子顺序，所以 snake 只有头是真头、其余按位置列出；尾格"这步会走掉"这件事
    从画面看不出来，因此【保守地把整条身子都当障碍】—— 宁可笨一点，不许把墙认成路。
    """
    head = None
    body = []
    food = None
    other = 0
    for r in range(ROWS):
        row = kinds[r * COLS:(r + 1) * COLS] if isinstance(kinds, list) and len(kinds) == ROWS * COLS \
            else list(kinds[r])
        for c, kind in enumerate(row):
            if kind == KIND_HEAD:
                head = (r, c)
            elif kind == KIND_SNAKE:
                body.append((r, c))
            elif kind == KIND_FOOD:
                food = (r, c)
            elif kind == KIND_OTHER:
                other += 1
    if head is None:
        return None, other
    return ({"snake": [head] + body, "food": food, "score": 0, "alive": True,
             "direction": direction, "steps": 0, "whole_body_blocks": True}, other)
