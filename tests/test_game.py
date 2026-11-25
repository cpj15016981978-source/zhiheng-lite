# -*- coding: utf-8 -*-
"""G1 棋盘本体（pai/game.py）的行为测试。

钉的是四件事：
  1 规则不许含糊：撞墙/撞身子=死，掉头不算一步，吃了才长、才加分
  2 尾格那半：不吃时尾格会走掉（不算障碍），但【读屏重建的状态整条身子都算障碍】
  3 plan() 合法、确定、可复现：给的必须是 legal_moves 里的，同一状态两次给同一个答案
  4 四色是读屏的前提：颜色两两离得够远，且只有这一个住址 —— 谁把颜色调淡了，
    画面照样好看、读屏却从此认错格子，这种错只在现场炸，所以在这里钉住
"""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pai import game, hands

ROOT = Path(__file__).resolve().parent.parent


def kinds_of(state):
    """把一个状态画成"读屏会认出来的那种标签格"（不走像素，专测重建那一半）。"""
    grid = [game.KIND_BG] * (game.ROWS * game.COLS)

    def put(pos, kind):
        r, c = pos
        grid[r * game.COLS + c] = kind

    for index, pos in enumerate(state["snake"]):
        put(pos, game.KIND_HEAD if index == 0 else game.KIND_SNAKE)
    if state["food"]:
        put(state["food"], game.KIND_FOOD)
    return grid


class PaletteTest(unittest.TestCase):
    def test_four_colors_are_far_apart(self):
        """★距离必须 > 2 倍容差：不然两种颜色中间那个混色像素会两可，读屏就开始猜。"""
        colors = [game.COLOR_BG, game.COLOR_SNAKE, game.COLOR_HEAD, game.COLOR_FOOD]
        for i, a in enumerate(colors):
            for b in colors[i + 1:]:
                d = sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5
                self.assertGreater(d, 2 * game.COLOR_TOLERANCE,
                                   "%r 和 %r 太近（%0.f <= %d），读屏认不出来" % (
                                       a, b, d, 2 * game.COLOR_TOLERANCE))

    def test_each_color_classifies_to_itself(self):
        for kind, color in ((game.KIND_BG, game.COLOR_BG), (game.KIND_SNAKE, game.COLOR_SNAKE),
                            (game.KIND_HEAD, game.COLOR_HEAD), (game.KIND_FOOD, game.COLOR_FOOD)):
            self.assertEqual(game.classify_pixel(color), kind)

    def test_unrelated_color_is_other_not_guessed(self):
        """纯绿不在四色里：它离蛇身没那么近，宁可标 other 让上层拒读，也不许当成蛇。"""
        self.assertEqual(game.classify_pixel((0, 255, 0)), game.KIND_OTHER)

    def test_palette_has_one_address(self):
        """画面和读屏都不许抄第二份颜色（ADR-030）。"""
        for rel in ("pai/hands.py", "tools/pai_game.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            for color in (game.COLOR_SNAKE, game.COLOR_HEAD, game.COLOR_FOOD):
                self.assertNotIn(repr(color), src, "%s 里抄了第二份颜色 %r" % (rel, color))


class SwitchNameTest(unittest.TestCase):
    def test_game_registers_the_same_switch_as_hands(self):
        """game 的登记写的是字面量（ast 要能解析，反向 import 又成环）—— 那就不许漂移。"""
        self.assertEqual(game.CAPABILITY["switch_env"], hands.ENV_SWITCH)


class MoveTest(unittest.TestCase):
    def setUp(self):
        self.state = {"snake": [(5, 5), (5, 4), (5, 3)], "food": (5, 9),
                      "score": 0, "alive": True, "direction": game.DIR_RIGHT, "steps": 0}

    def test_plain_move_steps_one_cell_and_keeps_length(self):
        new, event = game.step(self.state, game.DIR_RIGHT)
        self.assertEqual(event, game.MOVED)
        self.assertEqual(new["snake"][0], (5, 6))
        self.assertEqual(len(new["snake"]), 3)
        self.assertEqual(new["score"], 0)
        self.assertEqual(new["steps"], 1)

    def test_reversal_is_not_a_move(self):
        """掉头在这条线上是物理不可能的动作：原样退回，别让它死得莫名其妙。"""
        new, event = game.step(self.state, game.DIR_LEFT)
        self.assertEqual(event, game.MOVED)
        self.assertEqual(new["snake"], self.state["snake"])
        self.assertEqual(new["steps"], 0)

    def test_wall_kills(self):
        state = dict(self.state, snake=[(0, 5), (0, 6), (0, 7)], direction=game.DIR_UP)
        new, event = game.step(state, game.DIR_UP)
        self.assertEqual(event, game.DEAD)
        self.assertFalse(new["alive"])

    def test_dead_state_does_not_keep_moving(self):
        state = dict(self.state, alive=False)
        new, event = game.step(state, game.DIR_RIGHT)
        self.assertEqual(event, game.DEAD)
        self.assertEqual(new["snake"], state["snake"])

    def test_body_kills_but_tail_is_free(self):
        # 头 (3,3)、往下去 (4,3)：这一格是身子第二节 = 撞死
        body_ahead = {"snake": [(3, 3), (4, 3), (4, 4), (5, 3)], "food": (0, 0), "score": 0,
                      "alive": True, "direction": game.DIR_RIGHT, "steps": 0}
        dead, event = game.step(body_ahead, game.DIR_DOWN)
        self.assertEqual(event, game.DEAD)
        self.assertFalse(dead["alive"])
        # 同样一步，(4,3) 换成尾巴 = 这步它要走的，不算障碍
        tail_ahead = {"snake": [(3, 3), (3, 4), (4, 4), (4, 3)], "food": (0, 0), "score": 0,
                      "alive": True, "direction": game.DIR_RIGHT, "steps": 0}
        moved, event = game.step(tail_ahead, game.DIR_DOWN)
        self.assertEqual(event, game.MOVED)
        self.assertTrue(moved["alive"])

    def test_eating_grows_scores_and_respawns_food_off_the_snake(self):
        state = dict(self.state, food=(5, 6))
        rng = random.Random(3)
        new, event = game.step(state, game.DIR_RIGHT, rng)
        self.assertEqual(event, game.ATE)
        self.assertEqual(new["score"], 1)
        self.assertEqual(len(new["snake"]), 4)
        self.assertNotIn(new["food"], new["snake"])


class LegalAndPlanTest(unittest.TestCase):
    def test_legal_moves_drop_walls_body_and_reversal(self):
        state = {"snake": [(0, 5), (0, 4), (0, 3)], "food": (9, 9), "score": 0,
                 "alive": True, "direction": game.DIR_RIGHT, "steps": 0}
        # 上=出界，左=掉头，下=(1,5) 空，右=(0,6) 空；DIRS 次序是 上右下左
        self.assertEqual(game.legal_moves(state), [game.DIR_RIGHT, game.DIR_DOWN])

    def test_plan_is_always_legal_and_deterministic(self):
        for seed in range(12):
            state = game.new_state(rng=random.Random(seed))
            d = game.plan(state)
            self.assertIn(d, game.legal_moves(state), "plan 给了个会立刻死的方向")
            self.assertEqual(d, game.plan(state), "同一现场两次给不同方向 = 不可复现")

    def test_plan_heads_for_the_food(self):
        state = {"snake": [(6, 6), (6, 5), (6, 4)], "food": (6, 9), "score": 0,
                 "alive": True, "direction": game.DIR_RIGHT, "steps": 0}
        self.assertEqual(game.plan(state), game.DIR_RIGHT)

    def test_plan_says_none_when_nothing_is_legal(self):
        # 头在角上：上/左出界，右和下都顶着【不是尾巴】的身子格
        state = {"snake": [(0, 0), (0, 1), (1, 1), (2, 1), (2, 0)], "food": (9, 9), "score": 0,
                 "alive": True, "direction": game.DIR_UP, "steps": 0}
        self.assertEqual(game.legal_moves(state), [])
        self.assertIsNone(game.plan(state))

    def test_plan_says_none_without_food(self):
        state = dict(game.new_state(rng=random.Random(2)), food=None)
        self.assertIsNone(game.plan(state))

    def test_new_state_is_sane(self):
        for seed in range(20):
            state = game.new_state(rng=random.Random(seed))
            self.assertEqual(len(state["snake"]), game.SNAKE_START_LEN)
            self.assertTrue(all(game.in_bounds(p) for p in state["snake"]))
            self.assertNotIn(state["food"], state["snake"])


class StateFromCellsTest(unittest.TestCase):
    def test_roundtrip_from_a_state(self):
        state = game.new_state(rng=random.Random(11))
        rebuilt, other = game.state_from_cells(kinds_of(state))
        self.assertEqual(other, 0)
        self.assertEqual(rebuilt["snake"][0], state["snake"][0])
        self.assertEqual(rebuilt["food"], state["food"])
        self.assertEqual(len(rebuilt["snake"]), len(state["snake"]))

    def test_accepts_rows_and_flat_alike(self):
        state = game.new_state(rng=random.Random(5))
        flat = kinds_of(state)
        rows = [flat[r * game.COLS:(r + 1) * game.COLS] for r in range(game.ROWS)]
        a, _ = game.state_from_cells(flat)
        b, _ = game.state_from_cells(rows)
        self.assertEqual(a["snake"], b["snake"])

    def test_no_head_means_no_guess(self):
        grid = [game.KIND_BG] * (game.ROWS * game.COLS)
        state, other = game.state_from_cells(grid)
        self.assertIsNone(state, "画面上没有蛇头，不许凭空造一个位置")

    def test_screen_state_treats_the_whole_body_as_obstacle(self):
        """★照片看不出身子顺序，也就不知道哪格是尾 —— 那种状态保守地把尾巴也算墙。"""
        state = {"snake": [(3, 3), (3, 4), (4, 4), (4, 3)], "food": (9, 9), "score": 0,
                 "alive": True, "direction": game.DIR_UP, "steps": 0}
        rebuilt, _ = game.state_from_cells(kinds_of(state))
        self.assertTrue(rebuilt["whole_body_blocks"])
        legal = game.legal_moves(rebuilt)
        self.assertNotIn(game.DIR_DOWN, legal, "尾格 (4,3) 在画面上是身子，不能当路")


if __name__ == "__main__":
    unittest.main(verbosity=2)
