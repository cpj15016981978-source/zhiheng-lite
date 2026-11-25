"""验证 Phase3 主动推送骨架：M6 调度器 + orchestrator 主动发起 + outbox 落库。
运行： python -m unittest discover -s tests （项目根目录）
"""
import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai.memory import Memory
from pai.orchestrator import Orchestrator
from pai import scheduler as M6


class TestScheduler(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "s.db")
        self.mem = Memory(self.db)

    def tearDown(self):
        self.mem.close()

    def test_justify_reason_required(self):
        """反骚扰护栏：没有正当理由，禁止登记主动推送。"""
        with self.assertRaises(ValueError):
            M6.register_trigger(self.mem, "manual", "该睡了", "")   # 空理由→拒
        # 给了理由就能登记
        tid = M6.register_trigger(self.mem, "manual", "该睡了", "你答应我早睡的")
        self.assertIsInstance(tid, int)

    def test_manual_fires_once_then_deactivates(self):
        M6.register_trigger(self.mem, "manual", "喝水提醒", "久坐该动了")
        now = datetime.datetime(2026, 10, 20, 15, 0, 0)
        fired1 = M6.on_tick(self.mem, now)
        fired2 = M6.on_tick(self.mem, now)      # 再次滴答不应重复触发
        self.assertEqual(len(fired1), 1)
        self.assertEqual(len(fired2), 0, "manual 是一次性，触发后即失活")

    def test_date_fires_only_after_target(self):
        M6.register_trigger(self.mem, "date", "给爷爷上炷香",
                            "今天是忌日", spec="2026-10-21 09:00")
        before = M6.on_tick(self.mem, datetime.datetime(2026, 10, 20, 23, 0))
        self.assertEqual(len(before), 0, "未到点不触发")
        after = M6.on_tick(self.mem, datetime.datetime(2026, 10, 21, 9, 30))
        self.assertEqual(len(after), 1, "过了 spec 时间应触发")

    def test_daily_fires_once_per_calendar_day(self):
        M6.register_trigger(self.mem, "condition", "早安",
                            "每天一句问候", spec="daily")
        d1 = M6.on_tick(self.mem, datetime.datetime(2026, 10, 20, 8, 0))
        same_day = M6.on_tick(self.mem, datetime.datetime(2026, 10, 20, 22, 0))
        next_day = M6.on_tick(self.mem, datetime.datetime(2026, 10, 21, 7, 0))
        self.assertEqual(len(d1), 1)
        self.assertEqual(len(same_day), 0, "同一天不重复打扰")
        self.assertEqual(len(next_day), 1, "跨天可再次触发")

    def test_proactive_writes_outbox_and_grounds_message(self):
        M6.register_trigger(self.mem, "manual", "别太累", "你最近提过熬夜")
        orch = Orchestrator(self.mem)
        orch.handle("我叫阿明")          # 先建立“姓名”事实，让主动消息能带上称呼
        results = orch.tick(datetime.datetime(2026, 10, 20, 15, 0))
        self.assertEqual(len(results), 1)
        msg = results[0]["message"]
        self.assertIn("阿明", msg, "主动消息应 grounded 到称呼")
        self.assertIn("别太累", msg)
        inbox = self.mem.read_outbox()
        self.assertEqual(len(inbox), 1, "主动推送应落进 outbox")

    def test_outbox_survives_reopen(self):
        """跨进程：主动推送过的内容，重开仍在（“关掉也在”的另一半证明）。"""
        M6.register_trigger(self.mem, "manual", "记得吃饭", "你胃不好")
        orch = Orchestrator(self.mem)
        orch.tick(datetime.datetime(2026, 10, 20, 12, 0))
        n = len(self.mem.read_outbox())
        self.mem.close()

        m2 = Memory(self.db)   # 全新连接 = 全新进程
        self.assertEqual(len(m2.read_outbox()), n)
        m2.close()

    def test_bad_date_spec_rejected(self):
        with self.assertRaises(ValueError):
            M6.register_trigger(self.mem, "date", "x", "y", spec="不是时间")

    def test_unknown_condition_kind_rejected(self):
        with self.assertRaises(ValueError):
            M6.register_trigger(self.mem, "condition", "x", "y", spec={"kind": "teleport"})

    # ---------- Phase3.5：结构化 spec / 周期型 ----------
    def test_annual_fires_on_matching_day_once(self):
        """忌日/生日型：目标日月匹配且当刻>=time 触发一次，当天再 tick 不重复。"""
        M6.register_trigger(self.mem, "date", "给爷爷上炷香", "今天是忌日",
                            spec={"kind": "annual", "month": 4, "day": 5, "time": "09:00"})
        before = M6.on_tick(self.mem, datetime.datetime(2026, 4, 4, 23, 0))
        self.assertEqual(len(before), 0, "未到目标日不触发")
        fire = M6.on_tick(self.mem, datetime.datetime(2026, 4, 5, 9, 1))
        self.assertEqual(len(fire), 1, "目标日过点后触发")
        again = M6.on_tick(self.mem, datetime.datetime(2026, 4, 5, 22, 0))
        self.assertEqual(len(again), 0, "同一天只发一次（循环保留但当日去重）")
        # 循环触发器应仍处于 active（不是一次性失活）
        self.assertTrue(self.mem.get_trigger(fire[0]["id"])["active"])

    def test_weekly_fires_on_matching_weekday(self):
        M6.register_trigger(self.mem, "date", "周复盘", "每周一回顾",
                            spec={"kind": "weekly", "weekday": 0, "time": "08:30"})
        # 2026-10-26 是周一
        mon = M6.on_tick(self.mem, datetime.datetime(2026, 10, 26, 9, 0))
        tue = M6.on_tick(self.mem, datetime.datetime(2026, 10, 27, 9, 0))
        self.assertEqual(len(mon), 1)
        self.assertEqual(len(tue), 0, "非目标星期不触发")

    def test_structured_oneoff_deactivates_after_firing(self):
        M6.register_trigger(self.mem, "date", "取体检报告", "报告今天出",
                            spec={"kind": "oneoff", "at": "2026-11-01T09:00:00+08:00"})
        fired = M6.on_tick(self.mem, datetime.datetime(2026, 11, 1, 9, 30))
        self.assertEqual(len(fired), 1)
        self.assertFalse(self.mem.get_trigger(fired[0]["id"])["active"],
                         "oneoff 触发后应失活")

    # ---------- Phase3.5：时区感知 / catch_up ----------
    def test_timezone_aware_iso_comparable(self):
        """带 +08:00 偏移的 now 与裸时间 spec 应能正确比较，不抛 naive/aware 混比错。"""
        M6.register_trigger(self.mem, "date", "提醒", "理由",
                            spec={"kind": "oneoff", "at": "2026-12-01 09:00"})
        aware_now = datetime.datetime(2026, 12, 1, 9, 30, tzinfo=M6.LOCAL_TZ)
        fired = M6.on_tick(self.mem, aware_now)
        self.assertEqual(len(fired), 1)

    def test_catch_up_default_off_does_not_resurrect_past_oneoff(self):
        """一次性目标日已过、从未触发 → catch_up=False 默认不补发（防昨天的生日变今天骚扰）。"""
        M6.register_trigger(self.mem, "date", "生日提醒", "妈妈生日",
                            spec={"kind": "oneoff", "at": "2026-10-18T09:00:00+08:00"})
        missed = M6.on_tick(self.mem, datetime.datetime(2026, 10, 20, 9, 0))
        self.assertEqual(len(missed), 0, "默认不补发错过的历史一次性")
        # 显式 catch_up=True 才补
        caught = M6.on_tick(self.mem, datetime.datetime(2026, 10, 20, 9, 0), catch_up=True)
        self.assertEqual(len(caught), 1, "catch_up=True 才补发")

    # ---------- Phase3.5：条件型依赖 ctx（fail-safe）----------
    def test_condition_needs_ctx_none_no_fire(self):
        """ctx=None 时依赖上下文的条件一律不触发（宁可不醒，不可乱醒）。"""
        M6.register_trigger(self.mem, "condition", "好久没聊了", "你三天没说话了",
                            spec={"kind": "silence_days", "days": 3})
        no_ctx = M6.on_tick(self.mem, datetime.datetime(2026, 10, 20, 9, 0), ctx=None)
        self.assertEqual(len(no_ctx), 0)
        with_ctx = M6.on_tick(self.mem, datetime.datetime(2026, 10, 20, 9, 0),
                              ctx={"last_user_ts": "2026-10-15T09:00:00+08:00"})
        self.assertEqual(len(with_ctx), 1, "ctx 满足静默天数才触发")

    def test_silence_days_below_threshold_no_fire(self):
        M6.register_trigger(self.mem, "condition", "问候", "你两天没来了",
                            spec={"kind": "silence_days", "days": 3})
        near = M6.on_tick(self.mem, datetime.datetime(2026, 10, 20, 9, 0),
                          ctx={"last_user_ts": "2026-10-19T09:00:00+08:00"})
        self.assertEqual(len(near), 0, "未达静默天数不触发")

    def test_fact_exists_milestone_fires_once(self):
        M6.register_trigger(self.mem, "condition", "记得给妈妈过生日", "登记过妈妈生日",
                            spec={"kind": "fact_exists", "subject": "母亲", "predicate": "生日"})
        ctx = {"facts": [{"subject": "母亲", "predicate": "生日", "object": "4月5日"}]}
        first = M6.on_tick(self.mem, datetime.datetime(2026, 10, 20, 9, 0), ctx=ctx)
        self.assertEqual(len(first), 1)
        self.assertFalse(self.mem.get_trigger(first[0]["id"])["active"],
                         "fact_exists 是一次性里程碑")
        second = M6.on_tick(self.mem, datetime.datetime(2026, 10, 20, 10, 0), ctx=ctx)
        self.assertEqual(len(second), 0)

    def test_fact_exists_absent_no_fire(self):
        M6.register_trigger(self.mem, "condition", "x", "y",
                            spec={"kind": "fact_exists", "subject": "母亲", "predicate": "生日"})
        empty = M6.on_tick(self.mem, datetime.datetime(2026, 10, 20, 9, 0), ctx={"facts": []})
        self.assertEqual(len(empty), 0)


if __name__ == "__main__":
    unittest.main()
