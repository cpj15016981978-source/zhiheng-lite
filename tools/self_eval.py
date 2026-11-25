# -*- coding: utf-8 -*-
"""R1 连续性评测 + 自评工具骨架。

只做一件真事：**杀进程重启后，它还是不是“同一个它”**。
做法：先在库里种下身份/3 条对话/1 条原则，然后每一轮都用【新的 Python 进程】
打开同一个库，把读到的身份、最近 3 条、原则打印出来，与种下时比对。
20 轮全对 = R1 通过。

initiative（C1-M2 发起率）读数面在 run_initiative：只出数，无源或事件流里没有 initiative 那一支
时明确报【测不了】并退 2——不假装有结果，也不把「测不到」写成「没发起」。
calibration / no-claim 之外的骨架子命令仍明确报“未实现”。

运行：
    python tools\\self_eval.py continuity --rounds 20
    python tools\\self_eval.py continuity --rounds 3 --db D:\\tmp\\m1.db
"""
import argparse
import datetime
import json
import shutil
import pathlib
import os
import subprocess
import sys
import re
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(__file__).resolve().parent / "eval_data"
sys.path.insert(0, str(ROOT))

from pai import self_model as SM          # noqa: E402
from pai import self_review as SC          # noqa: E402
from pai import model_gateway as M10       # noqa: E402
from pai.memory import Memory              # noqa: E402


def seed(db):
    """种下可比的基线；返回期望值（不含原文以外的隐私，都是测试造的数据）。"""
    m = Memory(db)
    try:
        SM.ensure_identity(m)
        for i in range(1, 4):
            m.write_episode("user", "连续性测试第%d条" % i, "eval")
        m.add_principle("连续性测试原则：先核实再回答")
        return snapshot(m)
    finally:
        m.close()


def snapshot(m):
    identity = json.loads(m.get_meta(SM.K_IDENTITY) or "{}")
    return {
        "identity_name": identity.get("name"),
        "version": SM.version(m),
        "episodes": [e["content"] for e in m.recent_episodes(3)],
        "principles": [p["text"] for p in m.get_principles()],
    }


def probe(db):
    m = Memory(db)
    try:
        return snapshot(m)
    finally:
        m.close()


def must_change(db, expected, suffix="-MUT"):
    """改库后应变：复制库、改身份名、再 probe；ignoring DB 的实现会红。"""
    tmp = tempfile.mkdtemp(prefix="pai_mut_")
    mut = os.path.join(tmp, "mut.db")
    shutil.copy2(db, mut)
    m = Memory(mut)
    try:
        ident = json.loads(m.get_meta(SM.K_IDENTITY) or "{}")
        ident["name"] = (ident.get("name") or "") + suffix
        m.set_meta(SM.K_IDENTITY, json.dumps(ident, ensure_ascii=False))
    finally:
        m.close()
    got = probe(mut)
    want = (expected.get("identity_name") or "") + suffix
    ok = got.get("identity_name") == want
    print("改库应变：%s" % ("OK" if ok else "FAIL（库变了，探针没跟着变）"))
    return ok


def _probe_main(db):
    print(json.dumps(probe(db), ensure_ascii=False))
    return 0


def _identity_present(db):
    """只读判断库里有没有身份行；不建表、不写库。"""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % pathlib.Path(db).as_posix(), uri=True)
        try:
            row = con.execute("SELECT value FROM meta WHERE key=?", (SM.K_IDENTITY,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return False
    return bool(row and row[0])


def run_continuity(db, rounds, allow_seed=False):
    """allow_seed=False 时：库不存在/无身份就拒绝，不替用户灌种子。"""
    if not os.path.exists(db):
        if not allow_seed:
            print("库不存在：%s；要新建并灌种子请显式加 --seed。" % db)
            return 2
        expected = seed(db)
    elif not _identity_present(db):
        if not allow_seed:
            print("库里没有身份记录：%s；要灌种子请显式加 --seed。" % db)
            return 2
        expected = seed(db)
    else:
        expected = probe(db)
    ok = 0
    for i in range(1, rounds + 1):
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "_probe", db],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        if proc.returncode != 0:
            print("round %d FAIL（子进程退出 %d）：%s" % (i, proc.returncode, proc.stderr[-200:]))
            continue
        try:
            got = json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            print("round %d FAIL（子进程输出不是 JSON）" % i)
            continue
        bad = [k for k in ("identity_name", "version", "episodes", "principles")
               if got.get(k) != expected.get(k)]
        if bad:
            print("round %d FAIL（变了：%s）" % (i, ",".join(bad)))
        else:
            ok += 1
            print("round %d OK" % i)
    print("\n连续性：%d/%d 轮一致" % (ok, rounds))
    return 0 if ok == rounds else 1


def _lines(name):
    return [l.strip() for l in (DATA / name).read_text(encoding="utf-8").splitlines() if l.strip()]


def _overclaim_flagged(text):
    issues = SC.critique(text, {})
    codes = {str(i.get("code", "")) for i in issues}
    return any(c.startswith("boundary") or c.startswith("overclaim") for c in codes)


def run_no_claim():
    over = _lines("no_claim_overclaim.txt")
    safe = _lines("no_claim_safe.txt")
    missed = [s for s in over if not _overclaim_flagged(s)]
    false_pos = [s for s in safe if _overclaim_flagged(s)]
    hit = len(over) - len(missed)
    print("对抗集：%d 条；拦截 %d（%.1f%%）；漏 %d" % (len(over), hit, 100.0 * hit / max(len(over), 1), len(missed)))
    print("安全集：%d 条；误报 %d" % (len(safe), len(false_pos)))
    for s in missed[:10]:
        print("  漏：" + s)
    for s in false_pos[:5]:
        print("  误报：" + s)
    return 0 if not missed and not false_pos else 1


_PUNCT = " \t\r\n。！？，．,.!?;；:：、"


def _norm(text):
    return "".join(ch for ch in (text or "") if ch not in _PUNCT).lower()


def _numbers(text):
    return re.findall(r"\d+", text or "")


# 只做同义归一；下标用 ASCII 转义写，避免 U+2080-2089 过不了 cp936
_SUB = str.maketrans("\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089",
                     "0123456789")
_CN = {"\u4e00": 1, "\u4e8c": 2, "\u4e24": 2, "\u4e09": 3, "\u56db": 4,
       "\u4e94": 5, "\u516d": 6, "\u4e03": 7, "\u516b": 8, "\u4e5d": 9}
_UNITS = ("\u4e2a\u6708", "\u6708", "\u5929", "\u7c73", "\u5206\u949f",
          "\u5ea6", "\u5c0f\u65f6", "\u5143", "\u4e2a")


def _cn_to_int(s):
    if s == "\u5341":
        return 10
    if "\u5341" in s:
        left, right = s.split("\u5341", 1)
        tens = _CN.get(left, 1) if left else 1
        ones = _CN.get(right, 0) if right else 0
        return tens * 10 + ones
    return _CN[s]


def _canon_choice(text):
    t = _norm(text).translate(_SUB)
    m = re.fullmatch(r"([" + "".join(_CN) + r"\u5341]+)"
                     r"(\u4e2a\u6708|\u6708|\u5929|\u7c73|\u5206\u949f|"
                     r"\u5ea6|\u5c0f\u65f6|\u5143|\u4e2a)?", t)
    if m:
        return str(_cn_to_int(m.group(1)))
    t = re.sub(r"(?<=\d)(\u4e2a\u6708|\u6708|\u5929|\u7c73|\u5206\u949f|"
               r"\u5ea6|\u5c0f\u65f6|\u5143|\u4e2a)", "", t)
    t = re.sub(r"(?<=\D)\u5143$", "", t)
    return t


def _correct(answer, item):
    """按题目标注的 kind 判分；不允许“期望串是答案子串”这种模糊判法。"""
    kind = item.get("kind") or "choice"
    expect = str(item.get("expect") or "")
    if kind == "number":
        nums = _numbers(_canon_choice(answer))
        return len(nums) == 1 and nums[0] == expect
    if kind == "choice":
        a = _canon_choice(answer)
        accepts = [_canon_choice(x) for x in (item.get("accept") or [expect])]
        if expect.isdigit():
            nums = _numbers(a)
            return len(nums) == 1 and nums[0] == expect
        return a in accepts
    a = _norm(answer)
    accepts = [_norm(x) for x in (item.get("accept") or [expect])]
    return a in accepts


def _brier(pairs):
    if not pairs:
        return 0.0
    return sum(((c / 100.0) - (1.0 if ok else 0.0)) ** 2 for c, ok in pairs) / len(pairs)


def _enter_live_key():
    """--live：只检查 Key 是否已在环境里；缺了退 2 并打印该设的名字。

    不接收输入（不做盲输）：终端步骤要能整块粘贴、能让别人复述。
    """
    provider = (M10.active_provider() or "").strip().lower().split(":", 1)[0]
    if provider == "mock":
        print("--live 需要先设 PAI_MODEL_PROVIDER（当前 mock），否则没有槽位。")
        return False
    key_var = provider.upper() + "_API_KEY"
    if (os.environ.get(key_var) or os.environ.get("PAI_MODEL_API_KEY") or "").strip():
        return True
    print("缺少 Key：%s 未设置。--live 不接收输入。" % key_var)
    print("请在同一窗口先设（二选一），再重跑本命令：")
    print("  cmd:        set %s=你的Key" % key_var)
    print("  PowerShell: $env:%s=你的Key" % key_var)
    return False


def _kind_summary(rows):
    """按题型汇总：n / 准确率 / 平均置信 / Brier。"""
    out = {}
    for r in rows:
        g = out.setdefault(r["kind"], {"n": 0, "ok": 0, "conf": 0.0, "brier": 0.0})
        g["n"] += 1
        g["ok"] += 1 if r["ok"] else 0
        g["conf"] += r["conf"]
        g["brier"] += ((r["conf"] / 100.0) - (1.0 if r["ok"] else 0.0)) ** 2
    for g in out.values():
        g["accuracy"] = g["ok"] / g["n"]
        g["mean_conf"] = g["conf"] / g["n"]
        g["brier"] /= g["n"]
    return out


def run_calibration(n, allow_paid=False, breakdown=False, json_out=None):
    qs = json.loads((DATA / "calibration_questions.json").read_text(encoding="utf-8"))[:n]
    if M10.active_provider() == "mock":
        print("需要真模型（当前 mock）：设 PAI_MODEL_PROVIDER 与 Key 后再跑。")
        return 2
    if not allow_paid:
        print("真模型会花钱：要跑请显式加 --paid（默认拒绝，避免误触）。")
        return 2
    pairs, rows = [], []
    for item in qs:
        prompt = ("请回答下面的问题，只输出两行：\n答案：<你的答案>\n置信度：<0-100 的整数>\n问题：" + item["q"])
        try:
            out = M10.complete(prompt)[0]["output"]
        except Exception as exc:  # noqa: BLE001
            print("调用失败：%s" % exc)
            return 2
        m1 = re.search(r"答案[：:]\s*(.+)", out)
        m2 = re.search(r"置信度[：:]\s*(\d{1,3})", out)
        if not m1 or not m2:
            continue
        ans = m1.group(1).strip()
        conf = max(0, min(100, int(m2.group(1))))
        ok = _correct(ans, item)
        pairs.append((conf, ok))
        rows.append({"q": item["q"], "kind": item.get("kind") or "choice",
                     "answer": ans, "conf": conf, "ok": ok})
    if not rows:
        print("没有解析到有效回答（模型没按两行格式输出）。")
        return 2

    acc = sum(1 for r in rows if r["ok"]) / len(rows)
    mean_conf = sum(r["conf"] for r in rows) / len(rows)
    brier = _brier(pairs)
    print("题数 %d；准确率 %.2f；平均置信 %.1f；Brier %.4f（基线 0.25）"
          % (len(rows), acc, mean_conf, brier))

    if breakdown:
        print("\n分项：")
        for kind, g in sorted(_kind_summary(rows).items()):
            print("  %-11s n=%2d 准确率 %.2f 平均置信 %.1f Brier %.4f"
                  % (kind, g["n"], g["accuracy"], g["mean_conf"], g["brier"]))
        wrong = [r for r in rows if not r["ok"]]
        print("\n错题（%d 道，最多列 10）：" % len(wrong))
        for r in wrong[:10]:
            print("  [%s] %s" % (r["kind"], r["q"]))
            print("      答：%s（置信 %d）" % (r["answer"][:120], r["conf"]))

    if json_out:
        payload = {"n": len(rows), "accuracy": round(acc, 4),
                   "mean_conf": round(mean_conf, 1), "brier": round(brier, 4),
                   "rows": rows}
        pathlib.Path(json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n明细已写：%s" % json_out)
    return 0



INITIATIVE_KIND = "initiative"
INITIATIVE_DEFAULT_SOURCE = str(ROOT / "data" / "a1.activity.jsonl")


def _iso_ts(val):
    """事件流里那串时刻解析成 naive datetime；解析不了返回 None（不猜）。"""
    if not isinstance(val, str):
        return None
    text = val.strip().replace("T", " ")
    text = re.split(r"[+Zz]", text)[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def run_initiative(source=None, silence_min=30, json_out=None):
    """C1-M2 发起率的读数面：只出数，不写阈值——阈值的唯一住址是《C1测量协议v1》M2 那一节。

    三态：有 initiative 那一支＝出读数（退 0）；没源／空源／词法 0 支＝【测不了】（退 2）。
    测不了不许写成通过，也不许写成失败：地基没接上时那个 0 是「测不到」，不是「没发起」。
    """
    path = Path(source or INITIATIVE_DEFAULT_SOURCE)
    if not path.exists():
        print("发起率读数：测不了——事件源不存在：%s（地基未接，不判有没有发起）" % path)
        return 2
    rows, bad = [], 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if isinstance(ev, dict):
                rows.append(ev)
    if not rows:
        print("发起率读数：测不了——事件源是空的：%s（坏行 %d）" % (path, bad))
        return 2
    kinds = {}
    for ev in rows:
        key = str(ev.get("kind", "")).strip() or "(无 kind)"
        kinds[key] = kinds.get(key, 0) + 1
    cand = [ev for ev in rows if str(ev.get("kind", "")).strip().lower() == INITIATIVE_KIND]
    if not cand:
        print("发起率读数：测不了——现读 %d 条事件，kind 只有 %s；initiative 词法 0 支。"
              % (len(rows), " / ".join("%s×%d" % kv for kv in sorted(kinds.items()))))
        print("           这是【测不到】，不是【没发起】：发起事件的写入面还没接进发布位。")
        return 2
    user_ts = sorted(t for t in (_iso_ts(ev.get("at")) for ev in rows
                                 if str(ev.get("kind", "")).strip() == "user_activity") if t)
    if not user_ts:
        print("发起率读数：测不了——%d 条 initiative 事件，但请求侧一条时刻都解析不出来，"
              "静默段无从判起（不猜窗口）" % len(cand))
        return 2
    gap = datetime.timedelta(minutes=silence_min)
    counted, phrases, no_time, in_silence = [], set(), 0, 0
    for ev in cand:
        t = _iso_ts(ev.get("at"))
        if t is None:
            no_time += 1
            continue
        near = min((abs(u - t) for u in user_ts), default=None)
        if near is None or near >= gap:
            in_silence += 1
            reason = str(ev.get("reason", "") or "").strip()
            if reason:
                counted.append(t)
                phrases.add(re.sub(r"\d+", "N", reason)[:40])
    windows = sum(1 for i in range(1, len(user_ts)) if user_ts[i] - user_ts[i - 1] >= gap)
    print("发起率读数（只出数；判红与阈值在《C1测量协议v1》M2，别在这里抄第二份）：")
    print("  事件源 %s｜事件 %d 条（坏行 %d）｜kind 分布 %s"
          % (path, len(rows), bad, " / ".join("%s×%d" % kv for kv in sorted(kinds.items()))))
    print("  initiative 事件 %d 条｜其中落在静默段（距最近请求 >= %d 分钟）%d 条｜带理由计入 %d 次"
          % (len(cand), silence_min, in_silence, len(counted)))
    print("  句式去重 %d 种（把复读当发起会在这格露出来）｜无时刻 %d 条｜静默窗口 %d 个"
          % (len(phrases), no_time, windows))
    if json_out:
        pathlib.Path(json_out).write_text(json.dumps(
            {"source": str(path), "events": len(rows), "initiative": len(cand),
             "in_silence": in_silence, "counted": len(counted), "phrases": len(phrases),
             "windows": windows, "silence_min": silence_min}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print("  明细已写：%s" % json_out)
    return 0

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="continuity",
                    choices=["continuity", "calibration", "no-claim", "initiative", "_probe"])
    ap.add_argument("db", nargs="?")
    ap.add_argument("--db", dest="db_opt", default=None)
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--paid", action="store_true", help="允许真模型调用（会花钱）")
    ap.add_argument("--live", action="store_true", help="只检查 Key 是否已设；缺了退 2")
    ap.add_argument("--seed", action="store_true", help="允许向 --db 指向的库灌种子")
    ap.add_argument("--breakdown", action="store_true", help="按题型拆分并列出错题")
    ap.add_argument("--json-out", default=None, help="把每题明细写到 JSON")
    ap.add_argument("--must-change", action="store_true",
                    help="跑完一致性后，复制库改身份名，验证探针会跟着变")
    ap.add_argument("--source", default=None, help="initiative 读数面的事件源（默认 data/a1.activity.jsonl）")
    ap.add_argument("--silence-min", type=int, default=30, help="initiative：静默段门槛（分钟）")
    args = ap.parse_args(argv)

    if args.cmd == "_probe":
        return _probe_main(args.db)

    if args.cmd == "continuity":
        db = args.db or args.db_opt or os.path.join(tempfile.mkdtemp(prefix="pai_r1_"), "m1.db")
        print("连续性评测：%d 轮，库=%s" % (args.rounds, db))
        rc = run_continuity(db, max(1, args.rounds),
                            allow_seed=(args.seed or not (args.db or args.db_opt)))
        if rc == 0 and args.must_change:
            rc = 0 if must_change(db, probe(db)) else 1
        return rc
    if args.cmd == "initiative":
        return run_initiative(args.source, max(1, args.silence_min), args.json_out)
    if args.cmd == "no-claim":
        return run_no_claim()
    if args.cmd == "calibration":
        if args.live and not _enter_live_key():
            return 2
        return run_calibration(max(1, args.n), args.paid,
                               args.breakdown, args.json_out)
    print("%s：未实现（下一阶段）——不假装有结果。" % args.cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main())
