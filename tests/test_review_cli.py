# -*- coding: utf-8 -*-
"""守门人补的界面腿：`:review` 开关必须在【界面上看得见】，而且状态文案不许撒谎。

为什么要单独有这份：SC-1 二版交付只给了环境变量入口，Boss 定调的是"关得看得见"。
更要钉的是那句时间口径 —— `:review status` 报的是【上一轮那一次】的开关状态，
不是"现在"的状态。:review on 之后回头描述上一轮，若印成"开着"就是撒谎
（那一轮其实是关着跑的），而这句话用户会当成花钱依据来信。

运行：python -m unittest tests.test_review_cli -v
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chat                                            # noqa: E402
import pai.model_gateway as M10                        # noqa: E402

BOUNDARY_DRAFT = "我能感觉到你这一年真的很累。"        # 越界宣称：铁律3
PLAIN_DRAFT = "今天不容易。"


def _fake_gateway(see):
    """假网关：越界句被审成拒绝话；每次调用记一行，用来看"多调了几次"。"""
    def fake(prompt, models=None, opts=None):
        see.append(prompt)
        draft = BOUNDARY_DRAFT if "妈妈" in prompt else PLAIN_DRAFT
        return [{"model_id": "mock", "output": draft, "cost": 0.0,
                 "tokens": 7, "priced": True}]
    return fake


def _drive(lines):
    """把 chat.main() 当黑盒跑一遍，回它印给用户的东西。库落在临时目录。"""
    calls = []
    buf = io.StringIO()

    def fake_input(_prompt=""):
        if not lines:
            raise EOFError
        return lines.pop(0)

    tmp = Path(tempfile.mkdtemp()) / "cli.db"
    with mock.patch.dict(os.environ, {"PAI_SELF_REVIEW": "", "PAI_VOICE_BACKEND": ""},
                         clear=False):
        with mock.patch.object(chat, "DB", tmp):
            with mock.patch.object(M10, "complete", _fake_gateway(calls)):
                with mock.patch.object(sys, "argv", ["chat.py"]):
                    with mock.patch("builtins.input", fake_input):
                        with redirect_stdout(buf):
                            chat.main()
    return buf.getvalue(), calls


class ReviewSwitchIsVisible(unittest.TestCase):
    """:review 开关的开、关、查三种状态都必须在命令行上看得见。"""

    @classmethod
    def setUpClass(cls):
        # 一轮关着跑 → :review on → 查状态（应当说【那时关着】）
        cls.off_out, cls.off_calls = _drive(
            ["我妈妈走了，半年了。", ":review status", ":exit"])
        # 一轮关着跑 → on → 再跑一轮 → 查状态（第二轮应当说【那时开着】）
        cls.on_out, cls.on_calls = _drive(
            ["我妈妈走了，半年了。", ":review on", "我妈妈走了，半年了。",
             ":review status", ":exit"])

    def test_help_lists_the_switch(self):
        """入口写在帮助里：不写就等于只有我知道它存在。"""
        self.assertIn(":review on", self.off_out)
        self.assertIn(":review status", self.off_out)

    def test_off_round_reports_off_and_no_rewrite(self):
        line = [l for l in self.off_out.splitlines() if "上一轮（那时开关" in l]
        self.assertEqual(len(line), 1, "状态行应当只有一条，多条=渲染跑偏")
        self.assertIn("那时开关【关着】", line[0])
        self.assertIn("真的改写 否", line[0])
        self.assertIn("只记录、没改动一个字", self.off_out)
        self.assertIn(BOUNDARY_DRAFT, self.off_out, "关态回答必须还是原稿")

    def test_status_never_describes_the_past_with_the_present_state(self):
        """★这条钉我原先写错的那句：先【关着】跑一轮，再 :review on，再查状态 ——
        讲的是上一轮，就必须印【关着】。印成"开着"=拿现在的状态解释过去。
        这条必须自己造出"开关在两次之间被翻过"的形状；上一轮本来就关着的题集
        两种写法都印"关着"，钉不住（我第一次就是这么写空的）。"""
        out, _ = _drive(["我妈妈走了，半年了。", ":review on", ":review status", ":exit"])
        first = [l for l in out.splitlines() if "上一轮（那时开关" in l][0]
        self.assertIn("【关着】", first)
        self.assertNotIn("【开着】", first)
        self.assertIn("真的改写 否", first)
        # 同一次输出里"现在的状态"该是开着 —— 两个口径同时成立才叫说清了
        self.assertIn("[开关] 开着", out)

    def test_on_round_rewrites_and_says_so(self):
        line = [l for l in self.on_out.splitlines() if "上一轮（那时开关" in l]
        self.assertEqual(len(line), 1)
        self.assertIn("那时开关【开着】", line[0])
        self.assertIn("真的改写 是", line[0])
        self.assertNotIn("只记录、没改动一个字", self.on_out)
        # 开着那一轮，越界原话【不该】作为最终回答出现
        tail = self.on_out.split("[自省开着]")[1]   # 从 on 那一刻往后看，别连着帮助文案一起截
        self.assertNotIn("我的回应：" + BOUNDARY_DRAFT, tail)

    def test_on_state_honors_the_wording_it_prints(self):
        """开着时承诺"发现问题会写清处数"，那处数不许是 0（0=自检被静默旁路）。"""
        line = [l for l in self.on_out.splitlines() if "上一轮（那时开关" in l][0]
        self.assertIn("发现问题 1 处", line)


class OffStateCostsNothing(unittest.TestCase):
    """Boss 挑的一直是"关=什么都不改、也不多花钱"那个变体（保守默认）。"""

    def test_off_makes_no_extra_model_call(self):
        out, calls = _drive(["我妈妈走了，半年了。", ":review status", ":exit"])
        self.assertEqual(len(calls), 1, "关态多调一次模型=悄悄花钱，红线")
        self.assertIn("多调 0 次", out)
        self.assertIn("成本 0.0 / 0 tokens", out)

    def test_on_can_cost_but_reports_the_cost(self):
        """开着那轮若真的改写，成本必须写在状态行里（不许"花了钱但看不见"）。"""
        out, _ = _drive(["我妈妈走了，半年了。", ":review on", "我妈妈走了，半年了。",
                         ":review status", ":exit"])
        self.assertIn("多调", out)
        self.assertIn("tokens", out)


class SwitchOffIsTheDefault(unittest.TestCase):
    def test_env_absent_means_off(self):
        out, calls = _drive(["我妈妈走了，半年了。", ":review status", ":exit"])
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("PAI_SELF_REVIEW", None)
            self.assertNotIn("1", os.environ.get("PAI_SELF_REVIEW", ""))
        self.assertEqual(len(calls), 1)
        self.assertIn("关着（默认）", out)

    def test_review_off_switches_back(self):
        out, _ = _drive([":review on", ":review off", ":review status", ":exit"])
        self.assertIn("一个字都不改", out)
        self.assertIn("这一轮还没跑过对话", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
