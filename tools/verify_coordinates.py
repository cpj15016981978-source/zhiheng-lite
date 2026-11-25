"""verify_coordinates.py -- 把板上与日志里的「N B／12 位摘要」坐标拿回盘上重算。

为什么只有一种对不上算红：
  坐标描述的是写下那一刻的字节。活件（tools/ pai/ tests/ web/ 这些本来还要改的）
  过一小时必然对不上，判它红等于教自己忽略红。所以分三态：

  [红] 冻结族坐标与盘不符   -- 留底_*/bb_*/母本_*/board_写坏*/*.旧 这类按规矩永不再变的件，
                                摘要两种算法都不是，只有两种解释：当时抄错，或事后被人重写。两种都要指到行号。
                                算法认两把：md5 前 12 位与 sha256 前 12 位都算对得上（板上历史上两种混写，
                                旧格多用 sha256；只认 md5 会把 2026-11-09/10 那一批好坐标全判成红）。
  [知会] 活件坐标与盘不符   -- 只报条数与样例，不判红；这句绿不代表活件坐标可信，只代表它当场有效过。
  [知会] 文件不可寻          -- 名字找不到或同名多枚无法唯一确定，不猜。

用法：
  python tools/verify_coordinates.py            # 扫发布位板根＋投递区板根＋日志，出三态
  python tools/verify_coordinates.py --limit 12 # 红/知会各最多念几条（默认 8）
  python tools/verify_coordinates.py --quiet    # 只出结论行

零第三方依赖，只用标准库；退出码 0=无红，1=有红，2=扫不到任何可重算的坐标（尺哑了）。
"""
import argparse
import hashlib
import io
import os
import re
import sys
from pathlib import Path

WS = Path(r"C:\Users\Administrator\Documents\Qoder\2026-10-19\14868c32\personal-ai-system")
MIRROR = Path(r"D:\Q\personal-ai-system")
ROOT_Q = Path(r"D:\Q")
SESSION = Path(os.environ.get("PAI_SESSION_DIR", str(ROOT_Q / "_本次会话目录没设_")))  # 没设就当没有，别把 D:\Q 走第二遍

BOARD_NAME = "00_协作规程与接口契约.txt"
LOG_DIR = ROOT_Q / "日志"

# 记坐标的件：两份黑板＋当日与往日日志。信件不扫（信件里的坐标都指向板，扫板就够）。
SOURCES = (WS / BOARD_NAME, MIRROR / BOARD_NAME, *sorted(LOG_DIR.glob("*.txt")))

# 坐标形状：前面可带 path 或《件名》或标签（板根／镜像／日志），数字与摘要之间可有「md5 前 12 位」。
NAME = r"(?:[\w.\-]+/)*[\w.\-]+\.(?:py|txt|bin|md|db|jsonl|json|html|yaml|log|xml|csv|ini)"
COORD = re.compile(r"(?P<name>" + NAME + r"|《" + NAME + r"》|板根|镜像板根|投递区板根)"
                   r"[^0-9B]{0,40}?(\d[\d,]*) B／(?:md5 前 12 位 )?([0-9a-f]{12})")

# 冻结族：名字里带这些标记的件，按规矩不许再变。
FROZEN_MARKS = ("留底_", "bb_", "母本_", "board_写坏", ".旧", "_before_")

# 每遍 scan() 重置：对得上的坐标各由哪把算法命中（板上两种混写，这一刀专门用来量混写有多严重）。
ALGO_HITS = {"md5": 0, "sha256": 0}


def is_frozen(name):
    base = name.split("/")[-1].strip("《》")
    return any(mark in base for mark in FROZEN_MARKS)


def digests(path):
    """一枚字节的两种 12 位摘要。板上两种混着写（旧格用的是 sha256 前 12 位，
    近两天的刀用的是 md5 前 12 位），句子本身大多没标算法——只按一种重算就会把好人判成贼。"""
    b = path.read_bytes()
    return {"md5": hashlib.md5(b).hexdigest()[:12], "sha256": hashlib.sha256(b).hexdigest()[:12],
            "size": len(b)}


def md5_12(path):
    return hashlib.md5(path.read_bytes()).hexdigest()[:12]


def size_of(path):
    return len(path.read_bytes())


_INDEX = None
_SKIP_DIRS = (".git", "__pycache__", ".venv", "node_modules", "venv")


def _index():
    """按 basename 建一次索引。早期版本每个找不到的名字都 rglob 一遍全盘，
    一条坐标几秒——几百条坐标就是永远跑不完的尺；尺跑不完和尺哑了同等坏。"""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    idx = {}
    roots = [r for r in (WS, MIRROR, ROOT_Q, SESSION) if Path(r).is_dir()]
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".ipynb")]
            for fn in filenames:
                if not fn.lower().endswith(SUFFIXES):
                    continue
                idx.setdefault(fn, []).append(Path(dirpath) / fn)
    for lst in idx.values():
        del lst[6:]
    _INDEX = idx
    return idx


SUFFIXES = (".py", ".txt", ".bin", ".md", ".db", ".jsonl", ".json", ".html",
            ".yaml", ".log", ".xml", ".csv", ".ini")


def locate(name):
    """返回 (唯一路径 or None, 命中枚数)。同名多枚且字节不同 -> 不猜，交「不可寻」。"""
    base = name.strip("《》")
    cands = []
    if "/" in base or "\\" in base:
        rel = base.replace("\\", "/")
        for root in (WS, MIRROR, ROOT_Q, SESSION):
            p = root / rel
            if p.is_file():
                cands.append(p)
    else:
        idx = _index()
        for d in (WS, MIRROR, ROOT_Q, SESSION, LOG_DIR, WS / "tools", WS / "tests"):
            p = Path(d) / base
            if p.is_file() and p not in cands:
                cands.append(p)
        if not cands:
            cands = idx.get(base, [])
    uniq = []
    for p in cands:
        if p not in uniq:
            uniq.append(p)
    if not uniq:
        return None, 0
    digests = {}
    for p in uniq:
        try:
            digests.setdefault(md5_12(p), []).append(p)
        except OSError:
            continue
    if len(digests) > 1:
        return None, len(uniq)  # 同名不同字节，谁是我不知道
    return uniq[0], len(uniq)


def mtime_str(path):
    import datetime
    return datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def recorded_moment(line, src):
    """从这一行里取「时刻」。日志文件名带日期，板上有的行只带 HH:MM:SS——没日期就说没日期，不拿今天的日期凑。"""
    day = re.search(r"(20\d{2}-\d{2}-\d{2})", line)
    if not day:
        fm = re.search(r"(20\d{2})-(\d{2})-(\d{2})", Path(src).name)
        if fm:
            day = re.match(r"20\d{2}-\d{2}-\d{2}", "-".join(fm.groups()))
    tm = re.search(r"\d{2}:\d{2}:\d{2}", line)
    if day and tm:
        return "%s %s" % (day.group(0), tm.group(0)), True
    if tm:
        return tm.group(0), False
    return None, False


def classify(line, src, path):
    """把「同尺寸不同字节」分成两种解释；只给读数提示，不给判决。"""
    moment, has_date = recorded_moment(line, src)
    if not moment or not has_date:
        return "盘上件 mtime=%s｜这句没有可比的日期，分不出是抄错还是被改" % mtime_str(path)
    import datetime
    try:
        rec = datetime.datetime.strptime(moment, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return "盘上件 mtime=%s｜那句时刻读不出（%s）" % (mtime_str(path), moment)
    now = datetime.datetime.fromtimestamp(path.stat().st_mtime)
    if now > rec + datetime.timedelta(minutes=2):
        return "盘上件 mtime=%s 晚于那句记的 %s 两分钟以上 -> 记完之后这份字节被动过（含「留底被后一遍覆盖」那一族）" % (mtime_str(path), moment)
    return "盘上件 mtime=%s 不晚于那句记的 %s -> 写下的那一刻它就不是这份字节（抄错/算错那一族）" % (mtime_str(path), moment)


def scan(sources=SOURCES, limit=8):
    global ALGO_HITS
    ALGO_HITS = {"md5": 0, "sha256": 0}
    red, live, missing, ok = [], 0, 0, 0
    seen = set()
    for src in sources:
        src = Path(src)
        if not src.is_file():
            continue
        text = src.read_bytes().decode("utf-8", "replace")
        for ln, line in enumerate(text.splitlines(), 1):
            for m in COORD.finditer(line):
                name = m.group("name")
                want_sz = int(m.group(2).replace(",", ""))
                want_dg = m.group(3)
                key = (str(src), ln, name, want_sz, want_dg)
                if key in seen:
                    continue
                seen.add(key)
                if name in ("板根", "镜像板根", "投递区板根"):
                    path = WS / BOARD_NAME if name == "板根" else MIRROR / BOARD_NAME
                    hits = 1
                else:
                    path, hits = locate(name)
                if path is None:
                    missing += 1
                    continue
                got = digests(path)
                hit = [a for a in ("md5", "sha256") if got[a] == want_dg]
                if hit:
                    ok += 1
                    ALGO_HITS[hit[0]] += 1
                    continue
                rec = "%s 第 %d 行｜%s 记 %d B／%s，盘上现读 md5 %s／sha256 前 12 %s，两种都不是（%s）｜%s" % (
                    src.name, ln, name, want_sz, want_dg, got["md5"], got["sha256"],
                    "尺寸也对不上" if got["size"] != want_sz else "同尺寸不同字节",
                    classify(line, src, path))
                if is_frozen(name):
                    red.append(rec)
                else:
                    live += 1
    red = list(dict.fromkeys(red))
    return red, live, missing, ok


def main(argv=None):
    ap = argparse.ArgumentParser(description="重算板上与日志里的字节坐标")
    ap.add_argument("--limit", type=int, default=8, help="红与知会各最多念几条")
    ap.add_argument("--quiet", action="store_true", help="只出结论行")
    args = ap.parse_args(argv)
    srcs = [s for s in SOURCES if Path(s).is_file()]
    red, live, missing, ok = scan(srcs, args.limit)
    total = red_count = len(red)
    if not srcs:
        print("[尺哑] 一份记坐标的件都读不到，退出码 2")
        return 2
    if ok + red_count + live + missing == 0:
        print("[尺哑] 扫了 %d 件，一条可重算的坐标都没有命中——这条绿不算数，退出码 2" % len(srcs))
        return 2
    print("重算坐标：%d 件里找到 %d 处带文件名的坐标（时刻=本遍现量，不做缓存）" % (len(srcs), ok + total + live + missing))
    print("  对得上 %d 处｜[红] 冻结族对不上 %d 处｜[知会] 活件对不上 %d 处｜[知会] 文件不可寻或多枚同名 %d 处" % (ok, total, live, missing))
    print("  对得上那 %d 处按算法分：md5 前 12 %d 处、sha256 前 12 %d 处——板上两种混写，只认一把就会把另一把写的真坐标判成假" % (
        ok, ALGO_HITS["md5"], ALGO_HITS["sha256"]))
    for r in red[:args.limit]:
        print("  [红] " + r)
    if len(red) > args.limit:
        print("  [红] …另有 %d 处，--limit 放大" % (len(red) - args.limit))
    if live:
        print("  [知会] 活件坐标本就只对写下那一刻有效；上面不判红，也不许引用成「已复核」。")
    if missing:
        print("  [知会] 不可寻那 %d 处没猜；要它们进比对，把件名写成带路径的形状。" % missing)
    print("结论：%s（退出码 %d）" % ("冻结族坐标全部对得上盘" if not red else "有冻结族坐标对不上盘，要么当时抄错要么事后被改", 1 if red else 0))
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
