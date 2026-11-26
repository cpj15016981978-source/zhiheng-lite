#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""真树提交署名与可追溯守卫（Q-单-1）。

两种用法：
  python tools/check_commits.py --message-file .git/COMMIT_EDITMSG --staged   # commit-msg 钩子用
  python tools/check_commits.py --audit                                        # 审历史（也进全量套）

判据三条，都能当场跑出红，不靠人自觉：
  ① 首行必须以「Q: 」或「DS: 」开头 —— 谁下的手写在信息里。
  ② 改动命中地基面（pai/ webapp.py chat.py agent_layer/ tools/check_* .githooks/ requirements.txt）
     => 信息里必须引板格号 P<数字> 或工单号（Q-单-N／DS-单-N／A 单／B 单／C 单）。
     为什么要这一刀：2026-11-15 fd51bbf 那笔把 Q 未提交的工作区改动卷进 DS 的署名，
     事后只能靠 git show 逐行比；引了 P 号，板上那一格就写着「这几行是谁落的」。
  ③ 信息里署的前缀必须与提交者身份对得上（2026-11-17 Boss 点头的 B 那刀）。
     起因：0e2c6f8「Q: P174 吹雪代码评审修复（5项批量）」author 串是本机默认「DSH local
     <dsh@local>」，不是 Q 落的笔，而它把一个 GBK 违规字符带进主干、红了全量 1230 条那一遍。
     前缀自己证不了言——它只是信息文本，谁都能写「Q: 」。①② 拦得住没署名，拦不住冒署名。
     这一刀不假装是鉴权：真鉴权要 GPG 签名或按机器分身份，那一档还没点头。它只做一件事：
     把「DSH local 这个两边都可能是、因此认不出人的串」从署名面赶出去——顶 Q: 提交者就得是
     Qoder/qoder@local（或 q/q@local），顶 DS: 就得是 DS/ds@local（或 deepseek/deepseek@local）。
     代价说清：DS 现成的提交本来就带 -c user.name=DS（现读 8a02257 等 8 笔 an=DS ds@local），零摩擦；
     往后谁顶着本机默认身份提 Q:/DS:，钩子直接拒。Q 侧写法：
     git -c user.name=Qoder -c user.email=qoder@local commit -m "Q: P… "
     已经落进主干的那两笔顶 DSH local 的（0e2c6f8 与我的 868226d）不回填、也不替它们松判据：
     ③ 的锚点＝本件落地那一刻，之前的照旧只数不判；锚点可用 PAI_COMMIT_IDENTITY_SINCE 覆盖。

cutoff 之前的提交只数不判（grandfather）：默认 SINCE_ISO，可用 PAI_COMMIT_GUARD_SINCE 覆盖。
本仓 1.2.0-dev 公开前那批旧前缀提交 grandfather；署名规则从公开修复起生效。
0 出网、0 模型调用、0 写库；只读 git 与一个消息文件。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import pathlib
import re
import subprocess
import sys

# 钩子里跑的时候没人设 PYTHONUTF8，中文拒信会被控制台代码页糊成一堆问号
# （2026-11-15 真踩过：拦下提交那几行显示成「?????????...」）。守卫的话必须读得懂。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# 本仓公开修复的落地时刻。1.2.0-dev 那批 conventional 前缀提交是公开前的旧历史，
# 按 grandfather 只数不判；从这一刻起新提交必须带署名与 P 号。
# 想在别的仓/别的时点提前启用，设 PAI_COMMIT_GUARD_SINCE 覆盖。
SINCE_ISO = os.environ.get("PAI_COMMIT_GUARD_SINCE") or "2026-11-26T11:00:00+08:00"
PREFIX_RE = re.compile(r"^(Q|DS):\s+\S")
TICKET_RE = re.compile(r"P\d{1,4}|Q-单-\d+|DS-单-\d+|[ABC]\s?单")
SPINE_RE = re.compile(r"^(pai/[^/]+\.py|webapp\.py|chat\.py|agent_layer/[^/]+\.py"
                      r"|tools/check_[^/]+\.py|\.githooks/|requirements\.txt$)")
# 默认量自己所在的那棵仓库；单元测试把它指向临时仓库，才能在真 git 上端到端跑一遍「这刀会红」。
REPO = pathlib.Path(os.environ.get("PAI_COMMIT_REPO") or
                    pathlib.Path(__file__).resolve().parent.parent)

# 判据③ 的身份面：前缀 -> 允许的提交者身份（小写后比对）。
# 刻意不含本机默认串 DSH local / dsh@local——它两边都可能是，留着它就等于这条没写。
ROLE_IDENT = {
    "Q": {"names": {"q", "qoder"}, "emails": {"qoder@local", "q@local"}},
    "DS": {"names": {"ds", "deepseek"}, "emails": {"ds@local", "deepseek@local"}},
}
HINT = {
    "Q": 'git -c user.name=Qoder -c user.email=qoder@local commit -m "Q: P… "',
    "DS": 'git -c user.name=DS -c user.email=ds@local commit -m "DS: P… "',
}


def _own_mtime_iso():
    """本件自身的落地时刻＝③ 的锚点。不写死日期：写死就成了「今天之前」，跟「这把刀之前」不是一回事。"""
    return _dt.datetime.fromtimestamp(
        pathlib.Path(__file__).resolve().stat().st_mtime).isoformat(timespec="seconds")


IDENT_SINCE = os.environ.get("PAI_COMMIT_IDENTITY_SINCE") or _own_mtime_iso()


def _git(*args):
    return subprocess.run(["git", "-C", str(REPO)] + list(args),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace").stdout


def spine_hits(paths):
    return [p for p in paths if SPINE_RE.match(p)]


def _aware(d):
    return d if d.tzinfo else d.astimezone()


def _since_dt():
    txt = SINCE_ISO.strip()
    if len(txt) <= 10:
        txt += "T00:00:00"
    return _aware(_dt.datetime.fromisoformat(txt))


def _ident_since():
    txt = IDENT_SINCE.strip()
    if len(txt) <= 10:
        txt += "T00:00:00"
    return _aware(_dt.datetime.fromisoformat(txt))


def _ident_ok(role, ident):
    name, email = ident
    allow = ROLE_IDENT[role]
    return (str(name or "").strip().lower() in allow["names"]
            or str(email or "").strip().lower() in allow["emails"])


def _committer_ident():
    """从 git 要当次提交的真实身份，不信信息文本。GIT_COMMITTER_* 环境变量优先，正是 ③ 要的那一层。"""
    m = re.match(r"^(.*?)\s*<([^>]*)>", _git("var", "GIT_COMMITTER_IDENT").strip())
    return (m.group(1), m.group(2)) if m else ("", "")


def judge(msg, paths, where, when=None, idents=None):
    """返回违规行列表；空＝过。when（一个 datetime）在 cutoff 之前 => grandfather，只数不判。
    idents 为 [(name, email), …]；None＝③ 不判（老单测只喂 ①② 的路径）。"""
    if when is not None and _aware(when) < _since_dt():
        return []
    out = []
    lines = [ln for ln in str(msg or "").splitlines() if ln.strip()]
    if not lines:
        return ["%s：提交信息是空的" % where]
    m = PREFIX_RE.match(lines[0])
    if not m:
        out.append("%s：首行没写「Q: 」或「DS: 」，现在这行是「%s」" % (where, lines[0][:60]))
    hit = spine_hits(paths)
    if hit and not TICKET_RE.search(str(msg or "")):
        out.append("%s：动了地基面（%s）却没引 P 号或工单号"
                   % (where, "、".join(hit[:4]) + ("…" if len(hit) > 4 else "")))
    if m and idents and when is not None and _aware(when) >= _ident_since():
        role = m.group(1)
        if not any(_ident_ok(role, it) for it in idents):
            out.append("%s：首行署「%s:」但提交者身份是 %s —— 这个串认不出是谁下的手（本机默认两边共用）。"
                       "按 %s 重提；不要为了过闸把 %s 加进白名单"
                       % (where, role, "、".join("%s <%s>" % it for it in idents),
                          HINT[role], "DSH local"))
    return out


def _commit_paths(sha):
    return [p for p in _git("show", "--name-only", "--format=", sha).splitlines() if p.strip()]


def _commit_idents(sha):
    row = _git("log", "-1", "--format=%an\t%ae\t%cn\t%ce", sha).rstrip("\n").split("\t")
    return [(row[0], row[1]), (row[2], row[3])] if len(row) == 4 else []


def audit():
    shas = [s for s in _git("log", "--format=%H").splitlines() if s.strip()]
    bad, skipped, checked = [], 0, 0
    for sha in shas:
        stamp = _git("log", "-1", "--format=%cI", sha).strip()
        try:
            when = _aware(_dt.datetime.fromisoformat(stamp))
        except ValueError:
            skipped += 1
            continue
        if when < _since_dt():
            skipped += 1
            continue
        checked += 1
        msg = _git("log", "-1", "--format=%B", sha)
        bad += judge(msg, _commit_paths(sha), sha[:7], when, _commit_idents(sha))
    print("=== 提交署名与可追溯自检 · cutoff %s ===" % SINCE_ISO)
    print("判据③（前缀与身份对应）锚点：%s｜之前的顶默认身份的提交 grandfather 不判" % IDENT_SINCE)
    if not shas:
        print("这个仓库里一笔提交都没有 => 这条守卫【没测到东西】，别把它当绿。")
        return 1
    print("cutoff 之后判 %d 笔、grandfather 跳过 %d 笔（只数不判）。" % (checked, skipped))
    if bad:
        print("发现 %d 处违规：" % len(bad))
        for line in bad[:40]:
            print("  [G1] " + line)
        print("结论：署名面有缺口 —— 补信息或按规矩重提，别改检查器来将就。")
        return 1
    if checked == 0:
        print("cutoff 之后 0 笔 => 结论只能是【没东西可判】，不是【全都合规】。")
        return 0
    print("结论：cutoff 之后 %d 笔都带署名前缀，动地基面的都引了号，锚点之后的身份也都对得上。" % checked)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="真树提交署名与可追溯守卫（Q-单-1）")
    ap.add_argument("--message-file", help="commit-msg 钩子传进来的信息文件")
    ap.add_argument("--staged", action="store_true", help="按暂存区路径判地基面那一刀")
    ap.add_argument("--audit", action="store_true", help="审 cutoff 之后的历史提交")
    ap.add_argument("--msg", default=None, help="测试用：直接给信息文本")
    ap.add_argument("--paths", default=None, help="测试用：逗号分隔的改动路径")
    ap.add_argument("--ident", default=None, help="测试用：逗号分隔的提交者身份 Name,email（给了就判③）")
    ns = ap.parse_args(argv)
    paths, msg = [], ""
    if ns.paths is not None:
        paths = [p.strip() for p in ns.paths.split(",") if p.strip()]
    if ns.msg is not None:
        msg = ns.msg
    elif ns.message_file:
        p = pathlib.Path(ns.message_file)
        msg = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    if ns.staged:
        paths = [x for x in _git("diff", "--cached", "--name-only").splitlines() if x.strip()]
    idents = None
    if ns.ident is not None:
        part = ns.ident.split(",", 1)
        idents = [(part[0], part[1] if len(part) > 1 else "")]
    elif ns.message_file and not ns.audit:
        idents = [_committer_ident()]
    if not (ns.audit or ns.message_file or ns.paths is not None or ns.msg is not None):
        print("用法：--message-file PATH --staged｜--audit｜--msg TEXT --paths A,B")
        return 2
    if ns.audit:
        return audit()
    bad = judge(msg, paths, "本次提交", when=_dt.datetime.now().astimezone(), idents=idents)
    if bad:
        print("拒绝这次提交：")
        for line in bad:
            print("  [G1] " + line)
        print("（cutoff＝%s；身份锚点＝%s；地基面＝pai/ webapp.py chat.py agent_layer/ "
              "tools/check_* .githooks/ requirements.txt）" % (SINCE_ISO, IDENT_SINCE))
        return 1
    if _dt.datetime.now().astimezone() < _since_dt():
        print("署名与可追溯：本次【没判】—— 现在早于 cutoff %s（这一刀还没到点）。" % SINCE_ISO)
        return 0
    who = "" if not idents else "（提交者身份 %s <%s>）" % (idents[0][0], idents[0][1])
    print("署名与可追溯：过（首行前缀与 P 号/工单号都在%s）。" % who)
    return 0


if __name__ == "__main__":
    sys.exit(main())
