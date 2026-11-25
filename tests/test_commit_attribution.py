# -*- coding: utf-8 -*-
"""Q-单-1 的守卫：提交署名前缀 + 地基面须引 P 号/工单号 + 署的名要与提交者身份对得上（判据③）。

单元那三条判据跑纯函数；末尾两条在临时仓库里【真跑一次 git commit】——
因为「钩子装了但没人验过它会红」这句话，板上已经写过一次别人的账了。
0 模型调用、0 出网、0 写真库；git 只在临时目录里 init。
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

from tools import check_commits as CC

GIT = shutil.which("git")
CHECKER = str(pathlib.Path(__file__).resolve().parent.parent / "tools" / "check_commits.py")


def _git(args, cwd, env=None):
    e = dict(os.environ)
    # 临时仓库的缺省身份必须落在某一档被认可的角色身份上（DS/ds@local），否则判据③ 会先把
    # 这些用例自己的提交拦掉——那样测的就不是 ①② 了。要测 ③ 的红，调用处显式换成认不出人的串。
    e.update({"GIT_AUTHOR_NAME": "DS", "GIT_AUTHOR_EMAIL": "ds@local",
              "GIT_COMMITTER_NAME": "DS", "GIT_COMMITTER_EMAIL": "ds@local"})
    if env:
        e.update(env)
    return subprocess.run([GIT] + args, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=e)


NOBODY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@l",
          "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@l"}


class _Repo(object):
    """临时真仓库：写好一笔待提交改动，再把钩子装成 .git/hooks/commit-msg。

    hook="real" 装的是仓库里那份【出厂件】.githooks/commit-msg —— 装法不同（少一级路径）
    这件事本身在 2026-11-15 真踩过一次，所以那条路必须有测试，不能只测我自己生成的版本。
    """

    ROOT = pathlib.Path(__file__).resolve().parent.parent

    def __init__(self, spine=True, since="2000-01-01T00:00:00", hook="gen"):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.env = {"PAI_COMMIT_REPO": str(self.dir), "PAI_COMMIT_GUARD_SINCE": since}
        assert _git(["init", "-q", "--initial-branch=main"], self.dir).returncode == 0
        target = self.dir / ("pai/x.py" if spine else "docs/y.txt")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("hi\n", encoding="utf-8")
        assert _git(["add", "-A"], self.dir, self.env).returncode == 0
        hookfile = self.dir / ".git" / "hooks" / "commit-msg"
        if hook == "real":
            # 出厂件用 git rev-parse 要根目录，再去 <root>/tools/check_commits.py 找判据
            # => 临时仓库里得真放一支检查器，否则测的是「文件不存在」而不是守卫。
            (self.dir / "tools").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(self.ROOT / "tools" / "check_commits.py"),
                            str(self.dir / "tools" / "check_commits.py"))
            shutil.copyfile(str(self.ROOT / ".githooks" / "commit-msg"), str(hookfile))
        else:
            hookfile.write_text('#!/bin/sh\nexec "%s" "%s" --message-file "$1" --staged\n'
                                % (sys.executable.replace("\\", "/"), CHECKER.replace("\\", "/")),
                                encoding="utf-8")
        self.msg = self.dir / "MSG"

    def commit(self, message, env=None):
        self.msg.write_text(message, encoding="utf-8")
        e = dict(self.env)
        if env:
            e.update(env)
        r = _git(["commit", "-q", "-F", str(self.msg)], self.dir, e)
        n = _git(["rev-list", "--count", "HEAD"], self.dir, self.env).stdout.strip()
        return r, n

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class JudgeTest(unittest.TestCase):
    def test_missing_prefix_is_red(self):
        bad = CC.judge("feat: 双脑又改了一版", ["pai/local_duo.py"], "本次提交")
        self.assertEqual(len(bad), 2, bad)                    # 缺前缀 + 动地基面没引号
        self.assertIn("首行没写", bad[0])
        self.assertIn("没引 P 号", bad[1])

    def test_grandfather_before_cutoff_is_counted_not_judged(self):
        import datetime as _dt
        self.assertEqual(CC.judge("feat: 双脑又改了一版", ["pai/x.py"], "abc1234",
                                  when=_dt.datetime(2020, 1, 1)), [])

    def test_spine_change_without_ticket_is_red(self):
        self.assertTrue(CC.judge("DS: 台账加了一刀", ["pai/task_ledger.py"], "本次提交"))
        self.assertEqual(CC.judge("DS: 台账加了一刀（P164）", ["pai/task_ledger.py"], "本次提交"), [])
        self.assertEqual(CC.judge("Q: 落地开单 Q-单-1", ["tools/check_commits.py"], "本次提交"), [])

    def test_surface_change_only_needs_prefix(self):
        self.assertEqual(CC.judge("Q: 落板 P164", ["00_协作规程与接口契约.txt"], "本次提交"), [])
        self.assertTrue(CC.judge("落板一格", ["00_协作规程与接口契约.txt"], "本次提交"))

    def test_empty_message_is_red(self):
        self.assertTrue(CC.judge("", ["pai/x.py"], "本次提交")[0].startswith("本次提交：提交信息是空的"))

    def test_spine_surface_list_is_explicit(self):
        for p in ("pai/profile.py", "webapp.py", "chat.py", "agent_layer/agentd.py",
                  "tools/check_relay.py", ".githooks/commit-msg", "requirements.txt"):
            self.assertTrue(CC.SPINE_RE.match(p), p)
        for p in ("web/chat.html", "tests/test_bases.py", "wiki/a.md", "tools/preflight.py"):
            self.assertIsNone(CC.SPINE_RE.match(p), p)


@unittest.skipUnless(GIT, "没找到 git => 这两条端到端证明跑不了（不是绿，是没测）")
class HookEndToEndTest(unittest.TestCase):
    def setUp(self):
        self.repos = []

    def tearDown(self):
        for r in self.repos:
            r.close()

    def _repo(self, **kw):
        r = _Repo(**kw)
        self.repos.append(r)
        return r

    def test_shipped_hook_works_from_git_hooks_dir(self):
        """出厂件复制到 .git/hooks/ 后仍然有效（路径推导那一坑的真证明）。"""
        r = self._repo(spine=True, hook="real")
        res, count = r.commit("feat: 出厂件也拦得住")
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        text = res.stdout + res.stderr
        self.assertIn("首行没写", text)
        self.assertNotIn("找不到仓库根", text)
        self.assertEqual(count, "")
        ok, n2 = r.commit("DS: 出厂件放行这条（P164）")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertEqual(n2, "1")

    def test_hook_refuses_anonymous_commit(self):
        r = self._repo(spine=True)
        res, count = r.commit("feat: 改了台账一刀")
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        text = res.stdout + res.stderr        # 钩子的话被 git 转手，落点不算规矩，拒与否才算
        self.assertIn("首行没写", text)
        self.assertIn("没引 P 号", text)
        self.assertEqual(count, "", "钩子拒了还落进历史 => 那把刀是假的")

    def test_hook_lets_a_compliant_commit_through(self):
        r = self._repo(spine=True)
        res, count = r.commit("DS: 台账加一刀（P164）")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(count, "1")

    def test_prefix_claimed_by_an_unidentifiable_committer_is_refused(self):
        """判据③ 的红：信息署「DS:」而提交者是 t <t@l>（本机谁都能这么提）=> 拦下且不落历史。
        这一条是 2026-11-17 为 0e2c6f8 那笔补的：它顶「Q:」却带着两边共用的默认身份串，
        把一个 GBK 违规字符送进主干、红了全量——①② 当时全绿，因为冒署名不在判据里。"""
        r = self._repo(spine=True)
        res, count = r.commit("DS: 台账加一刀（P164）", env=NOBODY)
        text = res.stdout + res.stderr
        self.assertNotEqual(res.returncode, 0, text)
        self.assertIn("认不出是谁下的手", text)
        self.assertEqual(count, "", "③ 拒了还落进历史 => 那把刀是假的")

    def test_identity_gate_off_lets_the_same_commit_through(self):
        """反向证明：同一句信息、同一个认不出的身份，把③ 的锚点推到将来就该回绿——
        红来自这把刀，不是 git 自己拒的。"""
        r = self._repo(spine=True)
        res, count = r.commit("DS: 台账加一刀（P164）",
                              env=dict(NOBODY, PAI_COMMIT_IDENTITY_SINCE="2099-01-01T00:00:00"))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(count, "1")

    def test_red_disappears_only_when_the_gate_is_off(self):
        """反向证明：同一句坏信息，cutoff 设在将来就不判 —— 红来自这把刀，不是 git 自己。"""
        r = self._repo(spine=True, since="2099-01-01T00:00:00")
        res, count = r.commit("feat: 改了台账一刀")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(count, "1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
