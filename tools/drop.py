# -*- coding: utf-8 -*-
"""投递流程 CLI —— 把 claim / handoff / review / land 接到 git 原语上。

放在 tools/ 下运行：  python tools/drop.py <子命令> ...

设计约束（对应评审报告 ⑧ 第 8 节四条风险）：
  R1 中文路径 × MinGit × GBK —— slug 与 ref 名强制 ASCII；git 输出统一按 utf-8 解码；
     core.quotepath 由 doctor 检查并可自动修正；文件名判断一律在 Python 里做，
     绝不在 sh 钩子里对中文路径做 grep/case。
  R2 mktemp 未验证 —— 生成的服务端钩子不使用 mktemp，改用 $(git rev-parse --git-dir)/gate-tmp。
  R3 R1 退役早于 origin —— 任何会动 ref 的子命令都先 require_origin()，缺则拒绝。
  R4 update 钩子过紧 —— 默认 --advisory（只警告不拒），稳定后用 --harden 转硬；
     doctor 会报当前处于哪一档。

退出码：0 成功 / 1 业务失败 / 2 用法或环境前置不满足。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_ORIGIN = os.environ.get("PAI_BARE_ORIGIN") or str(Path.home() / "pai-origin.git")
ACTORS = ("Q", "DS", "Boss")
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}$")
TRAILERS = ("Approved-by:", "Reviewed-by:", "P-Number:")
NULL_SHA = "0" * 40
DRY = False


# ---------------------------------------------------------------- 基础设施
class Fail(Exception):
    """业务失败：命令本身没错，但判据不通过。"""


def _utf8():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def run(cmd, mutating=False, check=True, capture=True):
    """统一命令出口：dry-run 时跳过所有 mutating 调用并原样打印。"""
    shown = " ".join('"%s"' % c if (" " in c or not c) else c for c in cmd)
    if mutating and DRY:
        print("  [dry] " + shown)
        return ""
    print("  $ " + shown)
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=capture, text=True,
                       encoding="utf-8", errors="replace")
    if check and getattr(p, "returncode", 0) != 0:
        raise Fail("`%s` 失败（rc=%s）：%s" % (shown, p.returncode,
                                              ((getattr(p, "stderr", "") or "") or
                                               (getattr(p, "stdout", "") or "")).strip()[:400]))
    if capture:
        return (p.stdout or "").strip()
    return ""


def git(*args, **kw):
    return run(["git"] + list(args), **kw)


def need_git():
    if not shutil.which("git"):
        raise Fail("PATH 里找不到 git。本机 MinGit 在 D:\\Tools\\MinGit，"
                   "请把它的 cmd 目录加入 PATH，或设 PAI_GIT=<git.exe 绝对路径>。")


def q(msg):
    print(msg)


# ---------------------------------------------------------------- 前置校验
def require_ascii_slug(slug):
    """ref 名必须 ASCII：中文分支名在 MinGit + GBK 控制台下会被转义成八进制，
    钩子里的字符串比对随即失真。中文只允许出现在 commit message 与 tag annotation 里。"""
    if not SLUG_RE.match(slug):
        raise Fail(
            "slug 不合法：%r\n"
            "  要求：字母或数字开头，仅含 a-z A-Z 0-9 . _ -，长度 1-61。\n"
            "  原因：分支名/ref 名必须 ASCII，否则 MinGit 与 GBK 控制台下会转义成八进制，\n"
            "        钩子里的字符串比对随即失真。中文请写进 commit message 或 tag 说明。" % slug)


def require_actor(actor):
    if actor not in ACTORS:
        raise Fail("--by 必须是 %s 之一；别的值会让归属校验失去意义。" % " / ".join(ACTORS))


def origin_url():
    p = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    u = (p.stdout or "").strip()
    return u if p.returncode == 0 and u else ""


def require_origin():
    u = origin_url()
    if not u:
        raise Fail("还没有 origin。R1（镜像逐字节比对）退役后，origin 就是唯一的对账手段，\n"
                   "     缺了它，落板不可回退、复评不可信。先跑：\n"
                   "       python tools/drop.py init-origin --advisory")
    return u


def require_gates():
    if not (ROOT / "gates.json").exists() or not (ROOT / "tools" / "gates.py").exists():
        raise Fail("缺 gates.json 或 tools/gates.py —— 投递流程的门禁会空转。先落 ① 的工具包。")


def gate(tier):
    require_gates()
    rc = subprocess.run([sys.executable, "tools/gates.py", "--tier", tier], cwd=str(ROOT)).returncode
    if rc != 0:
        raise Fail("gates --tier %s 未通过（rc=%s）。%s" % (
            tier, rc,
            "1=有步骤红 2=档位哑了/名单失效 3=全绿但超预算。" ))


def clean_tree():
    dirty = git("status", "--porcelain")
    if dirty:
        raise Fail("工作区不干净，先提交或还原：\n" + dirty[:800])


def quotepath():
    p = subprocess.run(["git", "config", "--get", "core.quotepath"], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "").strip() or "(未设置)"


def hooks_path():
    p = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "").strip()


# ---------------------------------------------------------------- 服务端钩子
PRE_RECEIVE = """#!/bin/sh
# 由 tools/drop.py init-origin 生成。服务端门禁：--no-verify 只骗得过本地钩子，骗不过这里。
#
# 四条实测出来的坑，写给下一个换机器的人（本机 git 2.56.1.windows.1 + MinGit）：
#  1) pre-receive 跑在 ref 更新【之前】。此时 bare 仓里可能连 master 都还不存在，
#     所以绝不能 checkout 分支名，必须用 stdin 给的 newrev（对象已随推送到位）。
#  2) 钩子里 git-dir 常是相对路径 "."，一旦 cd 进临时目录它就失效 —— 取绝对 git-dir。
#  3) bare 仓的 core.bare=true 与 --work-tree 冲突：`checkout -f` 与
#     `-c core.bare=false checkout -f` 实测都失败；`git archive | tar` 在 MinGit 的
#     sh 里也失败（PATH 无 tar）。唯一可行的是 read-tree -u --reset + -c core.bare=false。
#  4) read-tree 会写 index。用 GIT_INDEX_FILE 指到临时目录里，否则并发推送抢 index.lock，
#     且会在 bare 仓根留下一个不该有的 index 文件。
# 另：不使用系统临时目录工具，临时工作树放在 bare 仓自己的 gate-tmp 下。
unset PAI_GATE_SKIP PAI_GATE_SKIP_REASON
gd=$(git rev-parse --absolute-git-dir 2>/dev/null) || gd=$(pwd)
tmp="$gd/gate-tmp"
zero=0000000000000000000000000000000000000000
fail=0
while read oldrev newrev refname; do
  [ "$newrev" = "$zero" ] && continue
  rm -rf "$tmp"
  mkdir -p "$tmp" || { echo "pre-receive: 建不出临时工作树 $tmp，拒。" >&2; exit 1; }
  if ! GIT_INDEX_FILE="$tmp/.gate-index" git --git-dir="$gd" --work-tree="$tmp" \\
        -c core.bare=false read-tree -u --reset "$newrev" >/dev/null 2>&1 </dev/null; then
    echo "pre-receive: 导不出 $refname 的工作树，拒。" >&2; exit 1
  fi
  if ! ( cd "$tmp" && python tools/gates.py --tier push ) </dev/null; then
    echo "pre-receive: gates[push] 红，$refname 推送被拒。" >&2; fail=1
  fi
done
exit $fail
"""


def _uses_temp_tool(text):
    """检查钩子是否真的调用了系统临时目录工具（注释里提到不算）。"""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#") or not s:
            continue
        if re.search(r"(^|[^A-Za-z_])mktemp(\\s|$)", s):
            return True
    return False


def update_hook(advisory):
    """advisory=True 时只警告不拒（迁移前两周用），False 时转硬。"""
    tail = ('    echo "update: [advisory] master 提交缺 \'$t\' trailer —— 本次放行，请尽快补。" >&2'
            if advisory else
            '    echo "update: master 上的提交缺 \'$t\' trailer，拒。落板请走 tools/drop.py land" >&2\n    exit 1')
    return """#!/bin/sh
# 由 tools/drop.py init-origin 生成。模式：%s
# update 钩子的入参顺序是 $1=refname $2=oldrev $3=newrev —— 别写错，
# 取 $2 会去校验【上一个】提交的 trailer，表现是「补了 trailer 仍被拒」。
refname="$1"; oldrev="$2"; newrev="$3"
[ "$refname" = "refs/heads/master" ] || exit 0
[ "$newrev" != "%s" ] || exit 0
for t in "Approved-by:" "P-Number:"; do
  git log -1 --format=%%B "$newrev" | grep -q "^$t" || {
%s
  }
done
exit 0
""" % ("ADVISORY（只警告）" if advisory else "HARD（拒绝）", NULL_SHA, tail)


def write_hook(hooks_dir, name, text):
    d = pathlib.Path(hooks_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(text.replace("\r\n", "\n"), encoding="utf-8", newline="\n")
    try:
        p.chmod(0o755)
    except Exception:
        pass
    q("  写入 %s" % p)
    return p


# ---------------------------------------------------------------- 子命令
def cmd_doctor(fix=False):
    need_git()
    findings = []

    def chk(ok, label, hint=""):
        findings.append((ok, label, hint))
        q("%-4s %s%s" % ("PASS" if ok else "FAIL", label, ("  ← " + hint) if (hint and not ok) else ""))

    top = ""
    try:
        top = git("rev-parse", "--show-toplevel")
    except Exception:
        pass
    chk(bool(top), "在 git 仓库内", "当前目录不是仓库根")
    if not top:
        return 2
    chk(pathlib.Path(top).resolve() == ROOT.resolve(), "drop.py 位于仓库根的 tools/ 下",
        "期望 %s，实际仓库根 %s" % (ROOT, top))
    chk((ROOT / "gates.json").exists() and (ROOT / "tools" / "gates.py").exists(),
        "门禁工具包就位", "缺 gates.json / tools/gates.py")
    qp = quotepath()
    chk(qp == "false", "core.quotepath=false", "当前=%s；中文路径会被转义成八进制，钩子里的比对会失真" % qp)
    if fix and qp != "false":
        git("config", "core.quotepath", "false", mutating=True)
        q("     已设为 false")

    u = origin_url()
    chk(bool(u), "origin 已配置", "未配置 → python tools/drop.py init-origin --advisory")
    chk(hooks_path() == ".githooks", "core.hooksPath=.githooks",
        "当前=%s；pre-commit/pre-push 不会生效" % (hooks_path() or "(未设置)"))
    if fix and hooks_path() != ".githooks":
        git("config", "core.hooksPath", ".githooks", mutating=True)
        q("     已设置")

    if u:
        gd = pathlib.Path(u)
        if gd.exists():
            for h, why in (("pre-receive", "服务端门禁"), ("update", "master trailer 校验")):
                p = gd / "hooks" / h
                chk(p.exists(), "服务端钩子 %s 存在（%s）" % (h, why), "重跑 init-origin")
                if p.exists():
                    body = p.read_text(encoding="utf-8", errors="replace")
                    chk(not _uses_temp_tool(body), "%s 未调用系统临时目录工具（风险 R2 已处置）" % h,
                        "改用 $(git rev-parse --absolute-git-dir)/gate-tmp")
                    mode = "ADVISORY" if "ADVISORY" in body else "HARD"
                    q("     %s 模式：%s%s" % (h, mode, "（稳定后请跑 init-origin --harden 转硬）"
                                             if (h == "update" and mode == "ADVISORY") else ""))
    n = len([x for x in git("for-each-ref", "--format=%(refname:short)", "refs/heads/").splitlines() if x])
    q("     本地分支 %d 个；未合入 master 的投递分支见 `drop.py status`" % n)
    bad = [f for f in findings if not f[0]]
    if fix and bad:
        q("\n==== 已应用 --fix，重新核验一遍 ====")
        return cmd_doctor(fix=False)
    q("\n==== doctor: %d/%d 通过 ====" % (len(findings) - len(bad), len(findings)))
    return 2 if bad else 0


def cmd_init_origin(path, advisory, harden, fix=False):
    need_git()
    if advisory and harden:
        raise Fail("--advisory 与 --harden 互斥")
    cur = origin_url()
    if cur and str(path).lower() != cur.lower() and not harden:
        raise Fail("origin 已指向 %s，与请求的 %s 不一致。确认要改请显式 --harden。" % (cur, path))
    target = cur or str(path)
    gd = pathlib.Path(target)
    if not gd.exists():
        run(["git", "init", "--bare", str(gd)], mutating=True, capture=False)
    if not cur:
        git("remote", "add", "origin", str(gd), mutating=True)
    mode = "advisory" if advisory else "hard"
    if harden:
        mode = "hard"
    hooks = gd / "hooks"
    write_hook(hooks, "pre-receive", PRE_RECEIVE)
    write_hook(hooks, "update", update_hook(advisory=mode == "advisory"))
    q("\norigin=%s  钩子模式=%s" % (gd, mode))
    q("下一步：python tools/drop.py doctor --fix   （会顺手设 core.quotepath 与 core.hooksPath）")
    q("提醒：服务端钩子是文件写入，dry-run 不会跳过它 —— 本命令不接受 --dry-run。")
    return 0


def cmd_install_hooks():
    need_git()
    src = ROOT / ".githooks"
    if not src.exists():
        raise Fail("仓库里没有 .githooks 目录")
    git("config", "core.hooksPath", ".githooks", mutating=True)
    git("config", "core.quotepath", "false", mutating=True)
    for h in sorted(p.name for p in src.iterdir() if not p.suffix):
        q("  已激活 %s" % h)
    return 0


def cmd_claim(slug, by):
    need_git()
    require_ascii_slug(slug)
    require_actor(by)
    require_origin()
    br = "%s/%s" % (by.lower(), slug)
    existing = git("branch", "--list", br).strip()
    if existing:
        raise Fail("分支 %s 已存在 —— 这就是撞车（替代旧 R4 的字符串比对）。"
                   "要接着干就 `git switch %s`，别再开第二个。" % (br, br))
    clean_tree()
    git("switch", "-c", br, "master", mutating=True)
    q("\n已认领：%s" % br)
    q("建议在黑板 Backlog 对应行写 [%s@%s] —— 现在它只是给人看的看板，"
      "机器判据来自分支存在性与 origin 上的 ref。" % (by, br))
    return 0


def cmd_handoff(note, tier):
    need_git()
    require_origin()
    br = git("rev-parse", "--abbrev-ref", "HEAD")
    if br == "master":
        raise Fail("不能从 master 直接交接。先 `python tools/drop.py claim <slug> --by <谁>`。")
    clean_tree()
    gate(tier)
    files = [x for x in git("diff", "--name-status", "master...HEAD").splitlines() if x.strip()]
    body = ["handoff: %s" % br, "改动 %d 个文件：" % len(files)]
    body += ["  " + f for f in files[:40]]
    if len(files) > 40:
        body.append("  ...+%d" % (len(files) - 40))
    body += ["", "gates[%s] GREEN at %s" % (tier, time.strftime("%Y-%m-%dT%H:%M:%S"))]
    if note:
        body += ["", note]
    text = "\n".join(body)
    q("\n" + text)
    out = ROOT / ".git" / "DROP_HANDOFF"
    if DRY:
        q("  [dry] 不写 %s，不 push" % out.name)
        return 0
    out.write_text(text, encoding="utf-8")
    q("\n交接正文已写入 .git/DROP_HANDOFF；可 `git commit --amend -F .git/DROP_HANDOFF` 并入提交信息。")
    git("push", "-u", "origin", br, mutating=True)
    return 0


def cmd_review(branch, by, tier):
    need_git()
    require_actor(by)
    require_origin()
    wt = (ROOT.parent / ("review-%s" % branch.replace("/", "-"))).resolve()
    if wt.exists():
        raise Fail("临时评审目录已存在，先人工确认再删：%s\n"
                   "     （不自动删：里面可能有别人正在看的东西）" % wt)
    try:
        git("fetch", "origin", branch, mutating=not DRY)
        if DRY:
            q("  [dry] 将在 %s 建 detached worktree 并跑 gates --tier %s" % (wt, tier))
            return 0
        git("worktree", "add", "--detach", str(wt), "FETCH_HEAD", mutating=True)
        rc = subprocess.run([sys.executable, "tools/gates.py", "--tier", tier],
                            cwd=str(wt)).returncode
        verdict = "pass" if rc == 0 else "reject"
        q("\n复评结论 %s：%s（在干净 worktree 上跑，不复用投递方环境）" % (verdict, branch))
        q("落板 merge commit 请带 trailer：\n  Reviewed-by: %s\n  Verdict: %s\n  Tested-on: clean-worktree"
          % (by, verdict))
        return rc
    finally:
        if not DRY and wt.exists():
            git("worktree", "remove", "--force", str(wt), check=False, mutating=True)


def cmd_land(pnum, branch, tier):
    need_git()
    require_origin()
    require_gates()
    if not re.match(r"^[A-Za-z0-9._-]+$", pnum):
        raise Fail("P 号必须是 ASCII（它会进 ref 名 board/%s）" % pnum)
    tag = "board/%s" % pnum
    # 先查 tag 再找分支：落板成功后投递分支已被删，若先解析分支会报「找不到候选分支」，
    # 把「这个 P 号已经落过板」这个真正的原因掩盖掉。
    if git("tag", "--list", tag).strip():
        raise Fail("tag %s 已存在 —— 这个 P 号已经落过板了，别重复。" % tag)
    if not branch:
        cands = [x for x in git("for-each-ref", "--format=%(refname:short)",
                                "refs/heads/").splitlines() if x.split("/")[-1].startswith(pnum + "-")]
        if len(cands) != 1:
            raise Fail("按 P 号 %s 找到 %d 个候选分支%s；请用 --branch 显式指定。"
                       % (pnum, len(cands),
                          ("：" + ", ".join(cands)) if cands else ""))
        branch = cands[0]
    msg = "merge %s: %s\n\nApproved-by: Boss\nReviewed-by: Q\nP-Number: %s\n" % (pnum, branch, pnum)
    missing = [t for t in TRAILERS if t not in msg]
    if missing:
        raise Fail("merge commit 缺 trailer：%s" % ", ".join(missing))
    clean_tree()
    gate(tier)
    if DRY:
        q("  [dry] 将执行：switch master → merge --no-ff %s → tag -a %s → push --follow-tags → branch -D"
          % (branch, tag))
        return 0
    git("switch", "master", mutating=True)
    git("merge", "--no-ff", branch, "-m", msg, mutating=True)
    git("tag", "-a", tag, "-m", "落板 %s（%s）" % (pnum, branch), mutating=True)
    git("push", "origin", "master", "--follow-tags", mutating=True)
    git("branch", "-D", branch, mutating=True)
    q("\n已落板 %s；tag=%s；分支 %s 已删（历史在 merge commit 与 tag 里）。" % (pnum, tag, branch))
    return 0


def cmd_status():
    need_git()
    rows = git("for-each-ref",
               "--format=%(refname:short)\t%(objectname:short)\t%(committerdate:short)\t%(authorname)",
               "refs/heads/").splitlines()
    merged = set()
    for line in git("log", "--merges", "--format=%s").splitlines():
        m = re.search(r"merge (\S+)", line)
        if m:
            merged.add(m.group(1))
    q("%-28s %-9s %-11s %s" % ("分支", "sha", "最后提交", "状态"))
    open_n = 0
    for line in rows:
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        name, sha, date, author = parts[0], parts[1], parts[2], parts[3]
        if name == "master":
            st = "主干"
        elif name.split("/")[-1] in merged:
            st = "已落板（分支未删）"
        else:
            st = "在飞"
            open_n += 1
        q("%-28s %-9s %-11s %s  [%s]" % (name, sha, date, st, author))
    q("\n在飞分支 %d 个。origin 上的对账：git ls-remote origin" % open_n)
    return 0


# ---------------------------------------------------------------- 入口
def main(argv=None):
    global DRY
    _utf8()
    ap = argparse.ArgumentParser(description="投递流程 CLI（claim / handoff / review / land）")
    ap.add_argument("--dry-run", action="store_true", help="所有会动 ref 或写文件的操作只打印不执行")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true", help="只打印不执行")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", parents=[common], help="环境前置自检")
    d.add_argument("--fix", action="store_true", help="顺手设 core.quotepath 与 core.hooksPath")

    i = sub.add_parser("init-origin", parents=[common], help="建 bare 仓 + 装服务端钩子")
    i.add_argument("--path", default=DEFAULT_ORIGIN)
    g = i.add_mutually_exclusive_group()
    g.add_argument("--advisory", action="store_true", help="update 钩子只警告（迁移前两周）")
    g.add_argument("--harden", action="store_true", help="update 钩子拒绝无 trailer 的 master 提交")

    sub.add_parser("install-hooks", parents=[common], help="设 core.hooksPath 并激活 .githooks")

    c = sub.add_parser("claim", parents=[common], help="认领一条投递 = 开分支")
    c.add_argument("slug")
    c.add_argument("--by", required=True)

    h = sub.add_parser("handoff", parents=[common], help="自证 + 推分支 + 生成交接正文")
    h.add_argument("--note", default="")
    h.add_argument("--tier", default="push")

    r = sub.add_parser("review", parents=[common], help="在干净 worktree 上复评")
    r.add_argument("branch")
    r.add_argument("--by", required=True)
    r.add_argument("--tier", default="push")

    l = sub.add_parser("land", parents=[common], help="落板 = merge --no-ff + tag + push")
    l.add_argument("pnum")
    l.add_argument("--branch")
    l.add_argument("--tier", default="push")

    sub.add_parser("status", parents=[common], help="列出在飞分支与已落板未删的分支")

    ns = ap.parse_args(sys.argv[1:] if argv is None else argv)
    DRY = bool(getattr(ns, "dry_run", False))
    if DRY:
        q("[dry-run] 只读命令照常执行，动 ref / 写文件的一律只打印。")
    try:
        if ns.cmd == "doctor":        return cmd_doctor(fix=ns.fix)
        if ns.cmd == "init-origin":   return cmd_init_origin(ns.path, ns.advisory, ns.harden)
        if ns.cmd == "install-hooks": return cmd_install_hooks()
        if ns.cmd == "claim":         return cmd_claim(ns.slug, ns.by)
        if ns.cmd == "handoff":       return cmd_handoff(ns.note, ns.tier)
        if ns.cmd == "review":        return cmd_review(ns.branch, ns.by, ns.tier)
        if ns.cmd == "land":          return cmd_land(ns.pnum, ns.branch, ns.tier)
        if ns.cmd == "status":        return cmd_status()
    except Fail as exc:
        print("FAIL %s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
