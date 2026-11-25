# -*- coding: utf-8 -*-
"""自我模型单测：身份连续性、原则修订（走 M2）、日志留存、自查、报告、零依赖。

两个运行位置都能跑：
  ① 独立（D:\\DSH）：用内置的最小 M2 替身（实现 get/set_principles 的语义）；
  ② 脊柱内：自动改用真实 Memory，验证“没有第二份实现、只走 M2 接口”。

运行：
    python -m unittest discover -s tests -p "test_self_model.py"
"""
import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pai import self_model as SM  # noqa: E402

try:
    from pai.memory import Memory
except Exception:  # noqa: BLE001
    Memory = None

# 只有已经并入原则生命周期的 M2 才跑“真实库”测试；旧副本自动跳过。
HAS_SET_PRINCIPLES = bool(Memory is not None and hasattr(Memory, "set_principles"))


class _FakeM2:
    """按脊柱 v0.17 的语义实现的最小 M2 替身：meta + principles + set_principles。

    刻意不带 conn：如果 self_model 偷偷绕过 M2 直接写 SQL，这些测试会失败。
    history 也按 M2 的写法记账——改口的账属于唯一写入口，不属于调用方。
    """

    def __init__(self):
        self.meta = {}
        self.principles = []
        self.history = []
        self._next = 1

    # meta
    def get_meta(self, key, default=None):
        return self.meta.get(key, default)

    def set_meta(self, key, value):
        self.meta[key] = str(value)

    # principles
    def add_principle(self, text):
        self.principles.append({"id": self._next, "text": text, "created_ts": "t0",
                                "revised_ts": None, "revision_reason": None,
                                "active": 1})
        self._next += 1
        self._log(self.principles[-1]["id"], "add", None, text, "新增")
        return self.principles[-1]["id"], True

    def get_principles(self, active_only=True):
        return [dict(p) for p in self.principles if p["active"] or not active_only]

    def set_principles(self, changes, reason):
        if not (reason or "").strip():
            raise ValueError("修改自写原则必须给出 reason")
        out = {"added": 0, "revised": 0, "retired": 0}
        for ch in changes:
            target = next((p for p in self.principles
                           if p["id"] == ch.get("id")), None)
            if ch.get("op") == "add":
                self.add_principle(ch["text"])
                out["added"] += 1
            elif ch.get("op") == "revise" and target is not None:
                old_text = target["text"]
                target.update(text=ch["text"], revised_ts="t1",
                              revision_reason=reason, active=1)
                if (old_text or "") != (ch["text"] or ""):   # 文本没变不造假账
                    self._log(target["id"], "revise", old_text, ch["text"], reason)
                out["revised"] += 1
            elif ch.get("op") == "retire" and target is not None:
                target.update(active=0, revised_ts="t1", revision_reason=reason)
                self._log(target["id"], "retire", target["text"], None, reason)
                out["retired"] += 1
        return out

    def _log(self, principle_id, op, old_text, new_text, reason):
        self.history.append({"id": len(self.history) + 1, "principle_id": principle_id,
                             "op": op, "old_text": old_text, "new_text": new_text,
                             "reason": reason, "ts": "t%d" % (len(self.history) + 1)})

    def principle_history(self, principle_id=None, limit=20):
        rows = [h for h in self.history
                if principle_id is None or h["principle_id"] == principle_id]
        return [dict(h) for h in reversed(rows[-int(limit):])]


def _mem():
    return _FakeM2()


class IdentityTest(unittest.TestCase):
    def test_absent_before_ensure(self):
        self.assertIsNone(SM.identity(_mem()))

    def test_ensure_is_idempotent(self):
        m = _mem()
        a = SM.ensure_identity(m, now="2026-10-20T21:00:00")
        b = SM.ensure_identity(m, now="2026-10-21T09:00:00")
        self.assertEqual(a["created_ts"], b["created_ts"], "第二次调用不得改写身份")
        self.assertEqual(a["role"], b["role"])
        self.assertEqual(SM.version(m), 0)

    def test_no_name_can_ever_be_stored(self):
        """X11 裁决（Boss 2026-10-22 选②）：名字不许进 meta。

        为什么单独钉这一条：身份 JSON 住在 meta.value，而 meta 不在 M2._TEXT_HOSTS
        的删除面里 —— :purge 删不到它。所以这里查的是【根本没有这个入口】，
        而不是"写了还能删"。签名里没有 name + 传了会 TypeError + 落库的名是空串。
        """
        import inspect
        self.assertNotIn("name", inspect.signature(SM.ensure_identity).parameters)
        m = _mem()
        SM.ensure_identity(m)
        self.assertEqual(SM.identity(m)["name"], "")
        with self.assertRaises(TypeError):
            SM.ensure_identity(m, name="阿伴")
        self.assertEqual(SM.identity(m)["name"], "", "报错的那次调用不许留下任何东西")

    def test_default_identity_is_behavioural(self):
        st = SM.ensure_identity(_mem())
        self.assertGreaterEqual(len(st["values"]), 3)
        self.assertIn("不宣称有意识或真实感受", st["boundaries"])

    def test_prompt_block_without_identity_is_honest(self):
        block = SM.prompt_block(_mem())
        self.assertIn("尚未建立身份记录", block)
        self.assertIn("不要假装记得", block)

    def test_prompt_block_with_identity(self):
        m = _mem()
        SM.ensure_identity(m)
        block = SM.prompt_block(m)
        self.assertIn("稳定的自我描述", block)
        self.assertIn("不是宣称有意识", block)
        self.assertIn("版本：v0", block)
        self.assertIn("改主意要能说出理由", block)

    def test_describe(self):
        m = _mem()
        SM.ensure_identity(m)
        text = SM.describe(m)
        self.assertIn("一个陪着你的人", text)     # X11：无名时的回退，不许显示空白或假名
        self.assertIn("当前版本 v0", text)
        self.assertIn("不是“意识”或“真实感受”的声明", text)


class RevisionTest(unittest.TestCase):
    def setUp(self):
        self.m = _mem()
        self.pid = self.m.add_principle("回应前先分清查到的与推断的")[0]

    def test_revise_updates_through_m2(self):
        res = SM.revise_principle(
            self.m, self.pid, "回应前先分清事实与推断，并标出依据",
            "原句没要求标出依据，容易被当成空话", now="2026-10-20T22:00:00")
        row = SM._principle(self.m, self.pid)
        self.assertEqual(row["text"], "回应前先分清事实与推断，并标出依据")
        self.assertEqual(row["active"], 1)
        self.assertIn("标出依据", row["revision_reason"])
        self.assertEqual(res["version"], 1)

    def test_revision_log_keeps_old_text(self):
        SM.revise_principle(self.m, self.pid, "新的说法更具体一点",
                            "因为旧说法太笼统，无法执行")
        log = SM.revision_log(self.m)
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["kind"], "revise")
        self.assertEqual(log[0]["id"], self.pid)
        self.assertIn("分清", log[0]["old_text"], "旧文本必须留在日志里（M2 会覆盖它）")
        self.assertIn("具体", log[0]["new_text"])

    def test_revise_requires_reason(self):
        for bad in ("", "   ", "随便"):
            with self.assertRaises(ValueError):
                SM.revise_principle(self.m, self.pid, "新文本", bad)

    def test_revise_rejects_unknown(self):
        with self.assertRaises(ValueError):
            SM.revise_principle(self.m, 999, "新文本", "这条原则根本不存在")

    def test_revise_rejects_identical_text(self):
        with self.assertRaises(ValueError):
            SM.revise_principle(self.m, self.pid, "回应前先分清查到的与推断的。",
                                "换个标点不算修订")

    def test_revise_rejects_empty_or_too_long(self):
        with self.assertRaises(ValueError):
            SM.revise_principle(self.m, self.pid, "   ", "说明为什么改是必要的")
        with self.assertRaises(ValueError):
            SM.revise_principle(self.m, self.pid, "长" * (SM.MAX_PRINCIPLE_CHARS + 1),
                                "太长的原则不是原则")

    def test_no_second_ledger_in_meta(self):
        """自我模型不许在 meta 里另记一本账（两份真相的直接钉）。"""
        SM.revise_principle(self.m, self.pid, "新一点的表述方式", "旧表述不好执行")
        SM.retire_principle(self.m, self.pid, "这条已经用不上了")
        self.assertNotIn("self.revisions", self.m.meta)
        self.assertLessEqual({k for k in self.m.meta if k.startswith("self.")},
                             {"self.identity", "self.version"})

    def test_revise_bumps_version(self):
        SM.revise_principle(self.m, self.pid, "第一版新说法", "先改一次看看")
        SM.revise_principle(self.m, self.pid, "第二版新说法", "再改一次看看")
        self.assertEqual(SM.version(self.m), 2)
        self.assertEqual(len(SM.revision_log(self.m)), 2)

    def test_retire(self):
        SM.retire_principle(self.m, self.pid, "这条原则对当前阶段不适用")
        self.assertEqual(SM._principle(self.m, self.pid)["active"], 0)
        self.assertEqual(SM.version(self.m), 1)

    def test_double_retire_rejected(self):
        SM.retire_principle(self.m, self.pid, "这条原则对当前阶段不适用")
        with self.assertRaises(ValueError):
            SM.retire_principle(self.m, self.pid, "重复停用必须报错而不是静默")

    def test_revision_log_capped(self):
        """封顶是【读取窗口】，不是截断存储。

        改口的流水在 M2 的 principle_history 里——那本账是审计用的，
        不该因为“自我模型只想看最近 200 条”就把旧账删掉。
        """
        for i in range(5):
            SM.revise_principle(self.m, self.pid, "第 %d 版原则文本" % i,
                                "连续修订用于验证读取封顶")
        self.assertEqual(len(SM.revision_log(self.m)), 5, "旧账不得被读窗口抹掉")
        with mock.patch.object(SM, "MAX_REVISIONS", 3):
            log = SM.revision_log(self.m)
        self.assertEqual(len(log), 3)
        self.assertEqual(log[-1]["new_text"], "第 4 版原则文本", "窗口取最近 3 条")


class SelfCheckTest(unittest.TestCase):
    def test_near_duplicates_flagged(self):
        m = _mem()
        m.add_principle("先分清事实，再下判断")
        m.add_principle("先分清事实再下判断。")
        kinds = [i["kind"] for i in SM.consistency(m)]
        self.assertIn("near_duplicate", kinds)

    def test_distinct_principles_not_flagged(self):
        m = _mem()
        m.add_principle("先分清事实再下判断")
        m.add_principle("主动找用户必须有正当理由")
        self.assertEqual(SM.consistency(m), [])

    def test_overclaim_flagged(self):
        m = _mem()
        m.add_principle("我能感受到你的痛苦，所以我会陪着你")
        self.assertTrue(any(i["kind"] == "overclaim" for i in SM.consistency(m)))

    def test_too_long_flagged(self):
        m = _mem()
        m.add_principle("长" * (SM.MAX_PRINCIPLE_CHARS + 1))
        self.assertTrue(any(i["kind"] == "too_long" for i in SM.consistency(m)))

    def test_report_counts(self):
        m = _mem()
        SM.ensure_identity(m)
        pid = m.add_principle("回应前先分清查到的与推断的")[0]
        SM.revise_principle(m, pid, "回应前先分清事实与推断", "因为需要更清楚的边界")
        m.add_principle("主动要有正当理由")
        rep = SM.self_report(m)
        self.assertIsNone(rep["name"], "X11：身份里不该有名字可报")
        self.assertEqual(rep["version"], 1)
        self.assertEqual(rep["principles"]["active"], 2)
        self.assertEqual(rep["principles"]["retired"], 0)
        self.assertEqual(rep["revisions"], 1)
        self.assertEqual(rep["last_revision"]["kind"], "revise")
        self.assertTrue(rep["healthy"])

    def test_report_surfaces_warnings(self):
        m = _mem()
        m.add_principle("我有意识地选择这样说")
        rep = SM.self_report(m)
        self.assertFalse(rep["healthy"])
        self.assertTrue(any(w["kind"] == "overclaim" for w in rep["warnings"]))


@unittest.skipUnless(HAS_SET_PRINCIPLES, "当前 M2 还没有 set_principles，跳过真实库测试")
class RealMemoryTest(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "s.db")
        self.mem = Memory(self.db)

    def tearDown(self):
        self.mem.close()

    def test_identity_and_revision_with_real_memory(self):
        SM.ensure_identity(self.mem)
        pid, _ = self.mem.add_principle("先分清事实再下判断")
        SM.revise_principle(self.mem, pid, "先分清事实，并标出依据", "旧句没有要求标出来源")
        row = dict(self.mem.conn.execute(
            "SELECT * FROM principles WHERE id=?", (pid,)).fetchone())
        self.assertEqual(row["active"], 1)
        self.assertIn("标出依据", row["text"])
        self.assertEqual(SM.version(self.mem), 1)

    def test_state_survives_reopen(self):
        SM.ensure_identity(self.mem)
        pid, _ = self.mem.add_principle("先分清事实再下判断")
        SM.revise_principle(self.mem, pid, "先分清事实与推断", "需要更清楚的表述")
        self.mem.close()
        self.mem = Memory(self.db)
        self.assertEqual(SM.version(self.mem), 1)
        self.assertEqual(len(SM.revision_log(self.mem)), 1)
        self.assertEqual(SM.identity(self.mem)["name"], "", "重开之后也不该凭空长出名字")

    def test_ledger_covers_writes_that_skip_self_model(self):
        """★守门人评审补的钉：CLI/界面是直接调 M2 改原则的，不经过自我模型。

        账若记在 self_model 里，这条路改的口就查不到（实测 revision_log 停在 1）。
        现在记在 M2 的 principle_history，谁来写都有痕。
        """
        pid, _ = self.mem.add_principle("陪伴就是顺着对方说")
        SM.revise_principle(self.mem, pid, "陪伴不等于没有立场", "先经自我模型改一次")
        self.mem.set_principles(                               # 界面那条路：绕过 SM
            [{"op": "revise", "id": pid, "text": "陪伴要敢在关键处说一句不同意见"}],
            "界面里用户自己改的理由")
        log = SM.revision_log(self.mem)
        self.assertEqual(len(log), 2, "绕过自我模型的改口也必须进账")
        self.assertEqual(log[-1]["old_text"], "陪伴不等于没有立场")
        self.assertIn("界面", log[-1]["reason"])

    def test_retire_and_no_fake_entry(self):
        pid, _ = self.mem.add_principle("先分清事实再下判断")
        self.mem.set_principles([{"op": "revise", "id": pid,
                                 "text": "先分清事实再下判断"}], "文本没变，不该记账")
        self.assertEqual(SM.revision_log(self.mem), [], "文本未变不得造假账")
        self.mem.set_principles([{"op": "retire", "id": pid}], "这条在当前阶段用不上")
        log = SM.revision_log(self.mem)
        self.assertEqual([r["kind"] for r in log], ["retire"])
        self.assertIsNone(log[0]["new_text"])

    def test_add_is_not_a_revision(self):
        """反思会自动加原则；把“新增”算进改口流水会把账冲爆。"""
        self.mem.add_principle("主动要有正当理由")
        self.assertEqual(SM.revision_log(self.mem), [])


class StdlibOnlyTest(unittest.TestCase):
    def test_no_third_party_imports(self):
        src = Path(SM.__file__).read_text(encoding="utf-8")
        mods = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods.add(node.module.split(".")[0])
        allowed = {"__future__", "datetime", "json", "typing"}
        self.assertTrue(mods <= allowed, "引入了非标准库：%s" % (mods - allowed))


class CapabilityBlockTest(unittest.TestCase):
    """递归自我模型·能力半边：只转述登记，登记之外一个字都不许说。"""

    @staticmethod
    def _row(name, switch="PAI_X", default_on=False, network=False, approval=""):
        return {"name": name, "switch_env": switch, "default_on": default_on,
                "stores_user_text": False, "network": network,
                "degrade": "读不到就退回空自述", "approval": approval}

    def _line_with(self, text, key):
        for ln in text.split("\n"):
            if key in ln:
                return ln
        return ""

    def test_empty_registry_gives_no_fabricated_capability(self):
        out = SM.capability_block({}, {})
        self.assertIn("我会什么 / 不会什么", out)
        self.assertNotIn("语音", out)
        self.assertNotIn("看图", out)

    def test_default_off_lands_in_off_line(self):
        out = SM.capability_block({"voice.py": self._row("M14 语音", "PAI_VOICE")}, {})
        self.assertIn("M14 语音（PAI_VOICE）", self._line_with(out, "缺省关"))
        self.assertNotIn("M14 语音", self._line_with(out, "缺省开着："))

    def test_no_switch_means_always_on(self):
        out = SM.capability_block({"memory.py": self._row("M2 记忆体", "NONE", True)}, {})
        self.assertIn("M2 记忆体", self._line_with(out, "一直开着"))

    def test_network_flag_is_stated_where_it_belongs(self):
        out = SM.capability_block({"voice.py": self._row("M14 语音"),
                                   "model_gateway.py": self._row("M10 网关",
                                                                 "PAI_MODEL_BACKEND",
                                                                 network=True)}, {})
        self.assertIn("M10 网关", self._line_with(out, "会连外网的"))
        self.assertNotIn("M14 语音", self._line_with(out, "会连外网的"))

    def test_unapproved_shown_approved_not(self):
        out = SM.capability_block({
            "a.py": self._row("甲", approval="未获单独批准：默认关+只读+不联网"),
            "b.py": self._row("乙", approval="常开由 ADR-031 立项")}, {})
        self.assertIn("甲", self._line_with(out, "没拿到单独批准的"))
        self.assertNotIn("乙", self._line_with(out, "没拿到单独批准的"))

    def test_pending_listed_only_when_present(self):
        out = SM.capability_block({}, {"orchestrator.py": "接缝未空"})
        self.assertIn("orchestrator.py", self._line_with(out, "还没登记清楚"))
        self.assertNotIn("还没登记清楚", SM.capability_block({}, {}))

    def test_real_spine_voice_reads_as_default_off(self):
        """守卫：这条真会红——语音一旦缺省开、或登记里冒出没登记过的名字，就落错位。"""
        try:
            from tools.check_capability import scan
        except Exception:  # 独立运行（D:\DSH）没有登记器
            self.skipTest("登记器不在这个运行位置")
        reg, pend, _violations = scan()
        out = SM.capability_block(reg, pend)
        self.assertIn("M14 语音·说（PAI_VOICE_BACKEND）", self._line_with(out, "缺省关"))
        # P162 补登记后这两支是真能力，不再是“没登记过的名字”
        self.assertIn("图片视觉理解（PAI_VISION_API_KEY）", self._line_with(out, "缺省关"))
        self.assertIn("联网搜索（TAVILY_API_KEY）", self._line_with(out, "缺省关"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
