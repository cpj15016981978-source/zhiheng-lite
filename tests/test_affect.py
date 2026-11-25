# -*- coding: utf-8 -*-
"""M5 情感层单测：识别 / 衰减 / 混合 / 落库 / prompt 块 / 输出护栏。

两个运行位置都能跑绿：
  ① 脊柱内： python -m unittest discover -s tests       （用真实 Memory）
  ② 独立投放：python -m unittest tests.test_affect       （用自带的最小 sqlite 容器）

为什么测试要自带一个 _Mem：M5 只能在 D:\\DSH 独立交付，这里还没有完整脊柱。
用同样的 affective_state DDL 起一个最小库，M5 的落库逻辑就能被真实验证；
另有 TestRealSpineMemory 在脊柱内用【真实 Memory】再验一次结构兼容。
"""
import ast
import datetime
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai import affect as M5  # noqa: E402

# 与 pai/schema.sql 中 affective_state 的定义逐字一致（改一处必须改另一处）。
_AFFECT_DDL = """
CREATE TABLE IF NOT EXISTS affective_state (
  id         INTEGER PRIMARY KEY CHECK (id = 1),
  mood       TEXT,
  valence    REAL,
  arousal    REAL,
  updated_ts TEXT,
  note       TEXT
);
"""


class _Mem:
    """最小 Memory 替身：只提供 .conn（与 M2 的现状一致）。"""

    def __init__(self, path=":memory:", with_table=True):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        if with_table:
            self.conn.executescript(_AFFECT_DDL)
            self.conn.commit()

    def close(self):
        self.conn.close()


class _AccessorMem:
    """模拟“M2 已并入 get_affect/set_affect”之后的形状：没有 conn 也能工作。"""

    def __init__(self):
        self.row = None
        self.calls = []

    def get_affect(self):
        return self.row

    def set_affect(self, mood, valence, arousal, note):
        self.calls.append((mood, valence, arousal, note))
        self.row = {"mood": mood, "valence": valence, "arousal": arousal,
                    "updated_ts": "2026-10-20T21:00:00", "note": note}


def _t(hour=21, minute=0):
    return datetime.datetime(2026, 10, 20, hour, minute, 0)


class TestDetect(unittest.TestCase):
    """识别是纯函数：不读库、不调模型、可离线复现。"""

    def test_grief_detected(self):
        sig = M5.detect_affect("我爷爷去世了，我一直走不出来")
        self.assertEqual(sig["mood"], "丧痛")
        self.assertLess(sig["valence"], -0.5)
        self.assertTrue(sig["heavy"])
        self.assertIn("去世", sig["cues"])

    def test_grief_words_without_person_are_not_grief(self):
        """没有人物、也没有明确丧事词，不能凭一个“死”字判丧痛（宁可漏，不可误伤）。"""
        sig = M5.detect_affect("这游戏太难了，主角死了十几次")
        self.assertNotEqual(sig["mood"], "丧痛")

    def test_grief_outranks_other_cues(self):
        """生死话题不能被“生气/烦”盖过去——同分时丧痛优先。"""
        sig = M5.detect_affect("爷爷刚去世，家里人还在吵遗产，我很生气")
        self.assertEqual(sig["mood"], "丧痛")

    def test_kinship_euphemism_is_grief(self):
        """“奶奶走了”是讳称，应识别为丧痛（弱丧事词 + 亲属称谓）。"""
        sig = M5.detect_affect("奶奶走了三年了，我还是想她")
        self.assertEqual(sig["mood"], "丧痛")

    def test_pronoun_departure_is_not_grief(self):
        """“他走了”多半只是离开，不能当丧亲——弱词必须搭配亲属称谓。"""
        sig = M5.detect_affect("他走了，今天只剩我一个人吃饭")
        self.assertNotEqual(sig["mood"], "丧痛")

    def test_anxiety(self):
        sig = M5.detect_affect("明天就要交东西了，我睡不着，心里发慌")
        self.assertEqual(sig["mood"], "焦虑")
        self.assertGreater(sig["arousal"], 0.6)

    def test_joy(self):
        sig = M5.detect_affect("今天特别开心，事情都挺顺利")
        self.assertEqual(sig["mood"], "轻快")
        self.assertGreater(sig["valence"], 0.2)

    def test_polite_thanks_alone_is_not_joy(self):
        """一句“谢谢”是礼貌，不是心情好——单独出现不应改基调。"""
        self.assertEqual(M5.detect_affect("谢谢")["mood"], "平静")

    def test_negated_joy_is_not_joy(self):
        sig = M5.detect_affect("我不开心")
        self.assertNotEqual(sig["mood"], "轻快")
        self.assertLess(sig["valence"], 0)

    def test_neutral_text_stays_calm_with_low_confidence(self):
        sig = M5.detect_affect("今天天气不错")
        self.assertEqual(sig["mood"], "平静")
        self.assertLessEqual(sig["confidence"], 0.25)
        self.assertEqual(sig["cues"], [])

    def test_empty_text_is_calm(self):
        for t in ("", "   ", None):
            self.assertEqual(M5.detect_affect(t)["mood"], "平静")

    def test_signal_shape_and_ranges(self):
        sig = M5.detect_affect("我很难受，想哭")
        self.assertEqual(set(sig), {"mood", "valence", "arousal", "intensity",
                                    "confidence", "cues", "heavy"})
        self.assertTrue(-1.0 <= sig["valence"] <= 1.0)
        self.assertTrue(0.0 <= sig["arousal"] <= 1.0)
        self.assertTrue(0.0 <= sig["intensity"] <= 1.0)
        self.assertTrue(0.0 <= sig["confidence"] <= 1.0)
        self.assertIsInstance(sig["cues"], list)

    def test_same_text_same_signal(self):
        a = M5.detect_affect("亲人离世后我一直很难受")
        b = M5.detect_affect("亲人离世后我一直很难受")
        self.assertEqual(a, b)


class TestDecayAndBlend(unittest.TestCase):
    def _grief(self, ts=None):
        return {
            "mood": "丧痛", "valence": -0.8, "arousal": 0.45,
            "intensity": 0.7, "confidence": 0.8, "cues": ["去世"],
            "heavy": True, "updated_ts": ts, "note": None,
        }

    def test_decay_returns_to_calm_after_days(self):
        st = M5.decay(self._grief(_t().isoformat()), elapsed_h=5 * 24)
        self.assertEqual(st["mood"], "平静")
        self.assertFalse(st["heavy"])
        self.assertEqual(st["valence"], 0.0)

    def test_decay_keeps_heavy_within_hours(self):
        st = M5.decay(self._grief(_t().isoformat()), elapsed_h=3)
        self.assertEqual(st["mood"], "丧痛")
        self.assertTrue(st["heavy"])

    def test_decay_does_not_move_timestamp(self):
        ts = _t().isoformat()
        st = M5.decay(self._grief(ts), elapsed_h=10)
        self.assertEqual(st["updated_ts"], ts, "衰减不是更新，不该推进时间戳")

    def test_anger_decays_faster_than_grief(self):
        anger = {"mood": "愤怒", "valence": -0.55, "arousal": 0.8,
                 "intensity": 0.7, "confidence": 0.7, "cues": ["生气"],
                 "heavy": False, "updated_ts": _t().isoformat(), "note": None}
        a = M5.decay(anger, 6.0)
        g = M5.decay(self._grief(_t().isoformat()), 6.0)
        self.assertLess(a["intensity"], g["intensity"])

    def test_blend_moves_valence_toward_signal(self):
        prev = M5.baseline()
        sig = M5.detect_affect("亲人去世了")
        st = M5.blend(prev, sig)
        self.assertEqual(st["mood"], "丧痛")
        self.assertLess(st["valence"], 0)
        self.assertGreater(st["intensity"], 0)

    def test_neutral_signal_does_not_erase_heavy_state(self):
        prev = self._grief(_t().isoformat())
        st = M5.blend(prev, M5.detect_affect("今天几号"), elapsed_h=0.0)
        self.assertEqual(st["mood"], "丧痛")
        self.assertTrue(st["heavy"])

    def test_weaker_new_signal_does_not_downgrade_grief(self):
        """丧亲后一句“心里堵得慌”不能把基调降级成“低落”——强度更高才改基调。"""
        prev = self._grief(_t().isoformat())
        sig = M5.detect_affect("明天还要去殡仪馆办手续，心里堵得慌")
        st = M5.blend(prev, sig, elapsed_h=6.0)
        self.assertEqual(st["mood"], "丧痛")
        self.assertTrue(st["heavy"])

    def test_strong_new_signal_can_change_grief(self):
        """但真正强烈的反向信号要允许翻篇，否则就成了刻板。"""
        prev = dict(self._grief(_t().isoformat()), intensity=0.40, valence=-0.45, arousal=0.4)
        st = M5.blend(prev, M5.detect_affect("今天特别开心，事情都顺利"), elapsed_h=12.0)
        self.assertEqual(st["mood"], "轻快")

    def test_blend_deterministic(self):
        prev = self._grief(_t().isoformat())
        sig = M5.detect_affect("还是很难受")
        self.assertEqual(M5.blend(prev, sig, 2.0), M5.blend(prev, sig, 2.0))


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.mem = _Mem()

    def tearDown(self):
        self.mem.close()

    def test_default_state_is_calm(self):
        st = M5.read_affect(self.mem)
        self.assertEqual(st["mood"], "平静")
        self.assertIsNone(st["updated_ts"])

    def test_update_persists_and_reads_back(self):
        st = M5.update_affect(self.mem, "亲人去世了，我很难受", now=_t())
        self.assertEqual(st["mood"], "丧痛")
        row = self.mem.conn.execute("SELECT * FROM affective_state WHERE id=1").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["mood"], "丧痛")
        again = M5.read_affect(self.mem, now=_t())
        self.assertEqual(again["mood"], "丧痛")

    def test_state_survives_reconnect(self):
        """关掉再打开仍记得——情感层必须跨进程/跨会话连续（铁律 3 的连续心境）。"""
        path = os.path.join(tempfile.mkdtemp(), "affect.db")
        m1 = _Mem(path)
        M5.update_affect(m1, "我爷爷去世了", now=_t())
        m1.close()
        m2 = _Mem(path)
        try:
            self.assertEqual(M5.read_affect(m2, now=_t())["mood"], "丧痛")
        finally:
            m2.close()

    def test_read_applies_decay_without_writing(self):
        M5.update_affect(self.mem, "亲人去世了", now=_t())
        later = _t().replace(day=25)
        st = M5.read_affect(self.mem, now=later)
        self.assertEqual(st["mood"], "平静")
        row = self.mem.conn.execute("SELECT mood FROM affective_state WHERE id=1").fetchone()
        self.assertEqual(row["mood"], "丧痛", "读操作不得改库（衰减是视图，不是写）")

    def test_corrupt_row_falls_back_to_baseline(self):
        self.mem.conn.execute(
            "INSERT INTO affective_state(id,mood,valence,arousal,updated_ts,note)"
            " VALUES(1,'???',NULL,NULL,'not-a-date','not json')")
        self.mem.conn.commit()
        st = M5.read_affect(self.mem)
        self.assertEqual(st["mood"], "平静")

    def test_update_touches_only_affective_state(self):
        self.mem.conn.execute(
            "CREATE TABLE episodes (id INTEGER PRIMARY KEY, ts TEXT, role TEXT, content TEXT)")
        self.mem.conn.execute("INSERT INTO episodes(ts,role,content) VALUES('t','user','x')")
        self.mem.conn.commit()
        before = self.mem.conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        M5.update_affect(self.mem, "我很焦虑，睡不着", now=_t())
        after = self.mem.conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        self.assertEqual(before, after, "M5 不得写 episodes")

    def test_missing_table_does_not_crash_read(self):
        bare = _Mem(with_table=False)
        try:
            self.assertEqual(M5.read_affect(bare)["mood"], "平静")
        finally:
            bare.close()

    def test_row_without_row_factory_is_supported(self):
        """连接没开 row_factory（返回普通元组）时也要能读——否则会静默退回平静。"""
        path = os.path.join(tempfile.mkdtemp(), "plain.db")
        conn = sqlite3.connect(path)
        conn.executescript(_AFFECT_DDL)
        conn.commit()

        class _PlainMem:
            def __init__(self, c):
                self.conn = c

        mem = _PlainMem(conn)
        try:
            M5.update_affect(mem, "亲人去世了", now=_t())
            self.assertEqual(M5.read_affect(mem, now=_t())["mood"], "丧痛")
        finally:
            conn.close()

    def test_prefers_accessor_when_memory_provides_it(self):
        """脊柱并入 get_affect/set_affect 后，M5 自动走接口，不再碰 conn。"""
        mem = _AccessorMem()
        st = M5.update_affect(mem, "我很难过", now=_t())
        self.assertEqual(st["mood"], "低落")
        self.assertTrue(mem.calls, "应调用 set_affect")
        self.assertEqual(M5.read_affect(mem, now=_t())["mood"], "低落")


class TestPromptAndModulate(unittest.TestCase):
    def setUp(self):
        st = M5.detect_affect("我爷爷去世了，走不出来")
        self.heavy = dict(M5.baseline(), **st, updated_ts=_t().isoformat())

    def test_prompt_block_has_boundary_and_guidance(self):
        block = M5.prompt_block(self.heavy)
        self.assertIn("丧痛", block)
        self.assertIn("不是诊断", block)
        self.assertIn("以对方说的为准", block)
        self.assertIn("节哀顺变", block)   # 明确列出“不要说”的话

    def test_prompt_block_for_calm(self):
        block = M5.prompt_block(M5.baseline())
        self.assertIn("平静", block)
        self.assertIn("不必刻意安慰", block)

    def test_modulate_drops_false_comfort_keeps_information(self):
        out = M5.modulate("我懂你的难过。想开点，日子还得过。明天我陪你去医院。", self.heavy)
        self.assertNotIn("想开点", out)
        self.assertIn("日子还得过", out)
        self.assertIn("明天我陪你去医院", out)

    def test_modulate_never_returns_empty(self):
        out = M5.modulate("想开点", self.heavy)
        self.assertEqual(out, "想开点", "删光时回退原文，不许返回空字符串")

    def test_modulate_noop_when_not_heavy(self):
        text = "想开点，会好起来的！"
        self.assertEqual(M5.modulate(text, M5.baseline()), text)

    def test_modulate_is_delete_only(self):
        """护栏只能删字：输出不得含有原文没有的字（句号收尾除外）。"""
        text = "节哀顺变，我明天来陪你。"
        out = M5.modulate(text, self.heavy)
        self.assertNotIn("节哀顺变", out)
        for ch in out:
            self.assertIn(ch, text + "。", "不得凭空添加内容：%r" % (ch,))

    def test_modulate_handles_changed_sentence_without_punctuation(self):
        out = M5.modulate("别难过，我陪着你", self.heavy)
        self.assertNotIn("别难过", out)
        self.assertIn("我陪着你", out)
        self.assertTrue(out.endswith("。"))


class TestStdlibOnly(unittest.TestCase):
    def test_no_third_party_imports(self):
        src = Path(M5.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods.add(node.module.split(".")[0])
        allowed = {"__future__", "datetime", "json", "re", "typing"}
        self.assertTrue(mods <= allowed, "M5 引入了非标准库：%s" % (mods - allowed))


# 脊柱里才有真实 Memory；独立投放时自动跳过。
try:
    from pai.memory import Memory  # type: ignore
except Exception:  # noqa: BLE001
    Memory = None


@unittest.skipUnless(Memory is not None, "不在脊柱内，跳过真实 Memory 兼容测试")
class TestRealSpineMemory(unittest.TestCase):
    """结构兼容：M5 直接用脊柱的 Memory，不改它一行。"""

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "spine.db")
        self.mem = Memory(self.path)

    def tearDown(self):
        self.mem.close()

    def test_roundtrip_with_real_memory(self):
        before = self.mem.max_episode_id()
        st = M5.update_affect(self.mem, "我爸刚过世，我不知道怎么跟妈妈说", now=_t())
        self.assertEqual(st["mood"], "丧痛")
        self.assertEqual(self.mem.max_episode_id(), before, "M5 不得写 episodes")
        row = self.mem.conn.execute(
            "SELECT mood,valence,note FROM affective_state WHERE id=1").fetchone()
        self.assertEqual(row["mood"], "丧痛")
        self.assertEqual(M5.read_affect(self.mem, now=_t())["mood"], "丧痛")

    def test_two_turns_blend_in_real_memory(self):
        M5.update_affect(self.mem, "亲人去世了", now=_t())
        st = M5.update_affect(self.mem, "今天稍微好一点，但还是空落落的",
                              now=_t(22))
        self.assertIn(st["mood"], ("丧痛", "孤独", "低落"))
        rows = self.mem.conn.execute("SELECT COUNT(*) FROM affective_state").fetchone()[0]
        self.assertEqual(rows, 1, "心境只允许一行（id=1）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
