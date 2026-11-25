# -*- coding: utf-8 -*-
"""M1.tick() 端到端集成测试 —— 补 2026-10-20 合并事故暴露的缺口。

那次事故：pai/scheduler.py 被换成了不兼容的实现，orchestrator.py:157 的调用
会直接崩，但当时 40 个单测全绿——因为没有任何测试覆盖 “tick() → proactive()
→ outbox” 这条跨模块通路。

本文件的每条用例都必须打在“模块之间的接缝”上，而不是重复测单模块内部逻辑：
    M6.on_tick 选触发器  →  M1.proactive 组织消息  →  M2.push_outbox 落库
任何一环换实现、签名漂移，这里必须变红。

运行：
    python -m unittest discover -s tests
    python -m unittest tests.test_tick_integration -v
"""

import datetime
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.memory import Memory
from pai.orchestrator import Orchestrator
from pai import scheduler as M6

TZ = datetime.timezone(datetime.timedelta(hours=8))


def dt(y, m, d, hh=0, mm=0):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=TZ)


class TickPipelineTest(unittest.TestCase):
    """整条主动推送链路的接缝测试。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, "tick.db")
        self.mem = Memory(self.db)
        self.orch = Orchestrator(self.mem)

    def tearDown(self):
        self.mem.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---------------------------------------------------------- 工具
    def add_annual(self, month=4, day=5, hhmm="09:00",
                   template="今天是母亲生日，要不要打个电话？",
                   reason="生日是用户明确在意的时间点"):
        return M6.register_trigger(
            self.mem, "date", template, reason,
            {"kind": "annual", "month": month, "day": day, "time": hhmm},
        )

    # ---------------------------------------------------------- 主链路
    def test_tick_writes_outbox(self):
        """M6 选出 → M1 组织 → M2 落库：一条都不能断。"""
        self.add_annual()
        fired = self.orch.tick(dt(2026, 4, 5, 9, 30))

        self.assertEqual(len(fired), 1, "到期触发器应被 tick 抓到")
        self.assertIn("outbox_id", fired[0])

        outbox = self.mem.read_outbox()
        self.assertEqual(len(outbox), 1, "proactive 必须真的写进 outbox")
        self.assertIn("母亲生日", outbox[0]["message"], "消息内容应来自 message_template")

    def test_message_carries_justify_reason(self):
        """主动推送必须带“为什么此刻开口”——这是 M11 护栏的落地要求。"""
        self.add_annual(reason="生日是用户明确在意的时间点")
        self.orch.tick(dt(2026, 4, 5, 9, 30))
        msg = self.mem.read_outbox()[0]["message"]
        self.assertIn("生日是用户明确在意的时间点", msg)

    def test_second_tick_same_day_is_idempotent(self):
        """同日重复 tick 不得重复推送——否则用户一天收十条生日祝福。"""
        self.add_annual()
        self.assertEqual(len(self.orch.tick(dt(2026, 4, 5, 9, 30))), 1)
        self.assertEqual(len(self.orch.tick(dt(2026, 4, 5, 18, 0))), 0)
        self.assertEqual(len(self.mem.read_outbox()), 1)

    def test_not_due_yet_pushes_nothing(self):
        """未到目标日：tick 不能有任何副作用（空 outbox 是硬要求）。"""
        self.add_annual()
        self.assertEqual(len(self.orch.tick(dt(2026, 4, 4, 9, 30))), 0)
        self.assertEqual(self.mem.read_outbox(), [])

    def test_two_triggers_push_independently(self):
        """多条触发器各自推送，不能因为第一条出错就吞掉第二条。"""
        self.add_annual(template="母亲生日")
        self.add_annual(template="父亲的忌日", reason="忌日是用户明确在意的时间点")
        fired = self.orch.tick(dt(2026, 4, 5, 9, 30))
        self.assertEqual(len(fired), 2)
        self.assertEqual(len(self.mem.read_outbox()), 2)

    def test_missed_oneoff_not_caught_up_by_default(self):
        """错过的一过性提醒默认不补发——昨天的生日不该今天补。"""
        M6.register_trigger(
            self.mem, "date", "该办那件事了", "用户登记的一次性提醒",
            {"kind": "oneoff", "at": dt(2026, 4, 1, 9, 0).isoformat()},
        )
        self.assertEqual(len(self.orch.tick(dt(2026, 4, 5, 9, 30))), 0)
        self.assertEqual(self.mem.read_outbox(), [])

    def test_handle_and_tick_coexist(self):
        """被动问答与主动推送共用同一条脊柱，互不污染。"""
        res = self.orch.handle("最近有点累")
        self.assertIn("response", res)
        self.assertIn("trace", res)

        self.add_annual()
        self.assertEqual(len(self.orch.tick(dt(2026, 4, 5, 9, 30))), 1)
        self.assertEqual(len(self.mem.read_outbox()), 1)

    # ---------------------------------------------------------- 失败回滚
    def test_send_failure_should_not_mark_trigger_fired(self):
        """发送失败必须回滚“已触发”标记，让下一次 tick 能重试。

        历史：2026-10-20 之前 on_tick 先写 last_fired_ts，proactive 随后才发送；
        发送一失败，这条消息永久丢失且不会重试（评审报告 P1）。
        修复后：tick 在发送前做快照，失败即回滚 last_fired_ts 与 active，
        并把错误汇总抛给调用方。
        """
        self.add_annual()

        def boom(trigger, now=None):
            raise RuntimeError("模拟 outbox 写入失败")

        original = self.orch.proactive
        self.orch.proactive = boom
        try:
            with self.assertRaises(RuntimeError):
                self.orch.tick(dt(2026, 4, 5, 9, 30))
        finally:
            self.orch.proactive = original

        # 关键断言：失败后重试仍应能推送出来
        retry = self.orch.tick(dt(2026, 4, 5, 10, 0))
        self.assertEqual(len(retry), 1, "发送失败后不应被标记为已触发")
        self.assertEqual(len(self.mem.read_outbox()), 1)

    def test_one_shot_failure_restores_active_flag(self):
        """一次性触发器发送失败后必须重新激活，否则它就此消失。"""
        M6.register_trigger(
            self.mem, "date", "该办那件事了", "用户登记的一次性提醒",
            {"kind": "oneoff", "at": dt(2026, 4, 5, 9, 0).isoformat()},
        )

        def boom(trigger, now=None):
            raise RuntimeError("模拟发送失败")

        original = self.orch.proactive
        self.orch.proactive = boom
        try:
            with self.assertRaises(RuntimeError):
                self.orch.tick(dt(2026, 4, 5, 9, 30))
        finally:
            self.orch.proactive = original

        self.assertEqual(len(self.mem.list_triggers(active_only=True)), 1,
                         "发送失败的一次性触发器不应被停用")
        self.assertEqual(len(self.orch.tick(dt(2026, 4, 5, 10, 0))), 1,
                         "回滚后应能重试成功")


if __name__ == "__main__":
    unittest.main(verbosity=2)
