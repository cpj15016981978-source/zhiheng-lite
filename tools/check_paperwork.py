r"""留底/日志卫生自检器（守门人工具，不是产品脊柱的一部分）。依据 ADR-062/077/081。

为什么需要它：2026-10-25 我一天内在同一类缺陷上栽了三次——探针脚本用文本模式落盘写出双写换行、
输出块留了一片裸 LF、路径里的反斜杠经命令行转发后变成真制表符。前几次都靠我自己回读才发现。
25 日 21:0x 第一次空跑本闸门又抓到一件，而且比那三次大得多：板根历史面 337 件正文类 .txt 里
【209 件不合格，共 268 处：H1 行尾 119 处、H3 引用摘要却没写时刻 149 处】——
这些年我写的信件与读数大片是裸 LF，因为 check_relay 的 R1 只逐字节比对两份镜像，
两边一起 LF 它就是绿的，这件事从来没有任何闸门查过。
散文式的「写完记得回读」守不住，会红的脚本才守得住。
（上面这组数是 25 日晚那一次的现场，不是现值；要当下的数就跑 --cutoff 2020-01-01，当场念。）

上线线 SHIP_MOMENT（2026-10-25 21:00:00）划在实测那一刻，是因为另一条更要紧的令：
历史归档件【不许回头改】——归一＝把证据换成我没跑过的形状（ADR-077/P55 记过病根，25 日那条「旧的两份也归一」
也只点到两份读数件）。所以本闸门判的是【从上线线起新写的每一件必须干净】，
上线线之前那一大片按令冻结，只报计数、不装成「查过且没问题」。

查五件事：
  H1 行尾纯   —— 正文类 .txt 必须纯 CRLF（裸 LF / 裸 CR / 双写换行都算红）。
                 豁免：文件名以「母本_」开头的原字节留底——它的行尾形状本身就是被留底那份的形状。
                 .py 不查行尾：归档脚本是「当时真跑过的执行物」，归一行尾＝篡改证据。
  H2 转义残留 —— 正文里不许出现制表符，也不许出现拼路径失手留下的字面拼接残渣。
  H3 摘要带时刻 —— 正文出现 12 位十六进制摘要（sha256_12 那类），同一份里就得能找到「时刻」二字。
                 依据是实测教训：「前后一致」那句写下时为真、八分钟后过期；没时刻的 sha 会被下一轮当现值念。
  H4 整面失效 —— 扫描根不存在、或一件正文类 .txt 都没扫到则红。只测「该拒的」盖不住「整面不跑」，
                 静默的绿比红更糟。上线线推到将来、比对面塌成零也照样红（那是自己把闸门关了）。
  H5 白名单挡不住新脏件 + H0 欠账被悄悄销掉 —— 见 KNOWN_DEBT。
  H7 未替换令牌 —— 正文出现「at 号＋大写串＋at 号」那族形状则红：那是刀里没换出去的占位符，不是读数。
                 起因是实测事故：格 P170 落盘时 9 行是这个形状，板上那一顶就是 24 小时，读数一个都没落（2026-11-16 发现）。
  H6 截断再读 —— 同一支 .py 里先 open(p, "w...") 截断、之后又读同一路径则红（读到空件，尺八）。
                 面是板根顶层那几刀 + 投递区脊柱 tools/ tests/；会话目录住址会变，钉不进闸门。
  归档族登记 ARCHIVE_DEBT —— 上线线之后那两片留底按目录冻结：处数涨=H5、处数少=H0、形状换=H5、整片不见=H4。

KNOWN_DEBT 冻结名单：键=相对板根、值=(形状指纹, 为什么不动)。判法三刀，缺一条就不是守卫：
  形状一致则计入【已知欠账】，不算违规，但每次都要念出来；
  长出新形状则[H5] 红（冻结的只是旧形状，新写的脏件不许藏在白名单后面）；
  形状销掉了则[H0] 红（那件被归一好了却没人签字；销账要板上一句令 + P 号）。
形状指纹粗到「哪一类脏」，不记条数——条数会随正常追加而变，变了不该算新账。

归档族登记 ARCHIVE_DEBT：按【目录】聚合冻结上线线之后那两片留底，判据同上三刀（涨=H5、销=H0、换形状=H5），
外加「整片扫不到＝H4」——归档面搬家或扫描口径变了不许当成绿。名单只有目录那么长，不会长成 210 条。

对方件（日志/ 里带 DeepSeek 那份）单走一条【知会】：只念形状，不判红、不进白名单、不算销账——
按 ADR-081 第 6 条我不代写 DS 的东西，他改好了也不该由我的闸门报「账被我销了」。

退出码：0 通过；1 有违规。那句「通过」一定带比对面/冻结面各几件，不念成「留底全干净」。

运行：
    python tools/check_paperwork.py                  # 默认扫板根下的留底/日志/信件 + 两份黑板
    python tools/check_paperwork.py --root A:\某处    # 副本演练（测试与红证明走这条，绝不碰真实板根）
    python tools/check_paperwork.py --cutoff 2020-01-01  # 把历史面也拉进来（红证明用；真实盘上会念出那 210 件）
零第三方依赖，只用标准库。
"""
import argparse
import datetime
import os
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

DEFAULT_ROOT = Path(os.environ.get("PAI_PAPERWORK_ROOT") or _HERE.parent)
# 发布位那支脊柱黑板：真要入库的那份字节在这里，过去不在面内（面只盖 D:\Q 那两支）。
SPINE_ROOT = Path(os.environ.get("PAI_SPINE_ROOT") or _HERE.parent)
SPINE_BOARD = SPINE_ROOT / "00_协作规程与接口契约.txt"
SPINE_REL = "发布位脊柱/00_协作规程与接口契约.txt"
BOARD_NAMES = ("00_协作规程与接口契约.txt", "personal-ai-system/00_协作规程与接口契约.txt")
ARCHIVE_PREFIX = "留底_"
LOG_DIR = "日志"
LETTER_PREFIX = "给DeepSeek-"
MASTER_PREFIX = "母本_"
OTHER_AI_MARK = "DeepSeek"

# 上线线：本闸门第一次空跑实测在 2026-10-25 20:5x，划在 21:00:00 整。
SHIP_MOMENT = "2026-10-25 21:00:00"

RESIDUE_TOKENS = ('" + B + "', "' + B + '", '" + BS + "', "' + BS + '")
SHA12_RE = re.compile(rb"(?<![0-9a-f])[0-9a-f]{12}(?![0-9a-f])")
MOMENT_CN = "时刻".encode("utf-8")

# 冻结欠账：键=相对板根（用 / 分隔），值=(形状指纹元组, 为什么不动)。
# 每条必须带为什么，否则就成了自我豁免。上线时实测的历史那一大片由 SHIP_MOMENT 冻结，不逐条抄进来——
# 210 条白名单等于没有白名单，只会让我下次直接跳过这句绿。
KNOWN_DEBT = {}

# 归档族登记：键=留底目录前缀（相对板根，以 / 结尾），值=(处数, 形状指纹元组, 为什么不动, 签字处)。
# 这一族不逐件进 KNOWN_DEBT——210 条白名单等于没有白名单（见上面那句）。刀一条没少，只是换了聚合的尺：
#   处数涨了 [H5] 红（新写进归档的脏件不许躲在这里）；
#   处数少了 [H0] 红（有人回头改了旧归档却没在板上签字）；
#   处数没变但形状换了 [H5] 红（换个脏法也要签字）；整片扫不到 [H4] 红。
# 为什么登记而不是归一：这些件是被留底那份的原字节，归一＝篡改当时真跑过的证据（与 .py/母本_ 同一条理由）。
# 定性出处：Boss 2026-11-16 点头（板 P171 第 3 款）。
ARCHIVE_DEBT = {
    "留底_20261106_225432_k814族独立复验补归档/": (
        77, ("H1-lf", "H1-rrrn", "H3-sha-no-moment"),
        "k814 族独立复验当时的控制台与读数件原字节，归一＝篡改证据",
        "P171 第 3 款（Boss 2026-11-16 点头）"),
    "留底_20261109_032529_第四十二刀_k904至k905v族/": (
        6, ("H1-lf", "H1-rrrn", "H3-sha-no-moment"),
        "第四十二刀 k904—k905v 齿痕件原字节，同上一条理由",
        "P171 第 3 款（Boss 2026-11-16 点头）"),
}

# 形状指纹只有这六种，粗到「哪一类脏」，不记条数：条数会随正常追加而变，变了不该算新账。
SHAPES = ("H1-lf", "H1-cr", "H1-rrrn", "H2-tab", "H2-residue", "H3-sha-no-moment", "H7-token")

# H7 那一族：刀里没换出去的模板令牌。只认大写开头那族；小写那种是名字或邮箱，判红就是假红。
TOKEN_SHAPE = re.compile(r"@{1,2}([A-Z][A-Z0-9_]{0,24})@{1,2}")
# H6 的扫描面：板根顶层那几刀 + 投递区脊柱 tools/ tests/（与发布位逐字节同形，R1 担保）。
# 会话目录里的刀不在面内——那个住址随日子变，钉不进闸门；落板刀一旦常驻化进 tools/ 就自动进面。
PY_FACE_TOP = "*.py"
PY_FACE_SUB = ("personal-ai-system/tools/*.py", "personal-ai-system/tests/*.py")
_OPEN_CALL = re.compile(r"""open\(\s*([^,()]+?)\s*,\s*["']([rwa+][^"']*)["']""")
_READ_ATTR = re.compile(r"""([A-Za-z_][\w.]*)\s*\.(?:read_bytes|read_text)\s*\(""")


def check_truncate_read(path):
    """H6：同一支 .py 里先截断打开、之后又读同一路径则红（读回来的是空件）。

    读在写之前不算——`data = rd(p)` 再 `open(p, "wb")` 正是合规写法（尺八给的改法）。
    判据按路径表达式的原文配对，不做变量求值：别名（换个名字指同一路径）躲得过这一刀，
    所以这句只说「挡住直呼其名那一族」，不吹「挡住所有截断」。
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return ["[H6] %s 读不出来（%s）——扫不到不等于干净" % (path.name, exc.__name__)]
    writes = []
    for idx, line in enumerate(lines, 1):
        for m in _OPEN_CALL.finditer(line):
            expr, mode = m.group(1).strip(), m.group(2).strip()
            if mode[:1] == "w" or (mode[:1] == "r" and "+" in mode):
                writes.append((idx, expr))
    out = []
    for idx, line in enumerate(lines, 1):
        reads = [m.group(1).strip() for m in _OPEN_CALL.finditer(line) if m.group(2).startswith("r")]
        reads += [m.group(1).strip() for m in _READ_ATTR.finditer(line)]
        for w_idx, expr in writes:
            if w_idx < idx and expr in reads:
                out.append('[H6] %s 第 %d 行截断打开 %s，第 %d 行又读它——读回来的是空件；'
                           '要追加先读回来，或换 open(..., "ab")（尺八）'
                           % (path.name, w_idx, expr, idx))
    return out


def py_face_files(root):
    """H6 那两片面：板根顶层 .py（不递归）+ 投递区脊柱 tools/ tests/。"""
    root = Path(root)
    out = []
    if not root.exists() or not root.is_dir():
        return out
    seen = set()
    for pat in (PY_FACE_TOP,) + PY_FACE_SUB:
        for p in sorted(root.glob(pat)):
            key = p.resolve().as_posix()
            if key not in seen:
                seen.add(key)
                out.append((key, p))
    return out


def face_expected(root):
    """H6 那一面是不是该在这棵树上比：真实板根，或树里确实长着 tools/ tests/ personal-ai-system/ 之一。

    别人的合成临时树没有这一面，拿「面塌」判它红＝让它替真实板根背红（实测把 DS 的 23 条带红，
    第二版又拿 tools/ 判宽了一次，第三版拿 personal-ai-system/ 还是宽（那棵树只放了黑板副本，
    三面一件都没有）。判据只能跟面同形：真实板根、或 personal-ai-system/{tools,tests} 那两片子目录在、
    或顶层本来就长着 .py。
    这一刀只在 run() 层，run_h6 本身照旧：那一面被预期而零件没有，就响。
    """
    p = Path(root).resolve()
    if p == Path(DEFAULT_ROOT).resolve():
        return True
    sp = p / "personal-ai-system"
    return (sp / "tools").is_dir() or (sp / "tests").is_dir() or bool(list(p.glob("*.py")))


def run_h6(root):
    """H6 单独算一遍：返回 (违规, 扫过件数)。那一面塌成零件也要响一声。"""
    targets = py_face_files(root)
    v = []
    for _key, p in targets:
        v.extend(check_truncate_read(p))
    if not targets:
        v.append("[H6] H6 的 .py 面一件也没扫到（板根顶层与脊柱 tools/tests 都空）——面塌了不叫绿")
    return v, len(targets)



def line_endings(raw):
    """返回 (CRLF, 裸LF, 裸CR, 双写换行) 的计数。读法一律 bytes：文本模式会把 CRLF 折成 LF，数出来必然假。"""
    crlf = raw.count(b"\r\n")
    rrrn = raw.count(b"\r\r\n")
    bare_lf = raw.count(b"\n") - crlf
    bare_cr = raw.count(b"\r") - crlf - rrrn
    return crlf, bare_lf, bare_cr, rrrn


def is_master(rel):
    return Path(rel).name.startswith(MASTER_PREFIX)


def is_other_ai(rel):
    """按天日志住址是 日志/YYYY-MM-DD-<AI名>.txt；带 DeepSeek 那支是对方件。"""
    top, _sep, rest = rel.partition("/")
    return top == LOG_DIR and OTHER_AI_MARK in rest


def scan_targets(root):
    """扫这些件：留底_*/**、日志/**、板根下的给DeepSeek-*.txt、两份黑板，外加发布位那支脊柱黑板。只收正文类 .txt。

    为什么要把发布位那支也拉进面：板根按字节写坏（每行多一个 CR 那一族）过去只有投递区那份会被扫到，
    而 R1 允许两边各有一段时间差——我要提交的那份字节反而没人查。判据一条没新增，只是把面盖到真要入库那一支。
    """
    root = Path(root)
    out = []
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix != ".txt":
            continue
        rel = p.relative_to(root).as_posix()
        top = rel.split("/", 1)[0]
        if (top.startswith(ARCHIVE_PREFIX) or top == LOG_DIR
                or (top.startswith(LETTER_PREFIX) and "/" not in rel)
                or rel in BOARD_NAMES):
            out.append((rel, p))
    spine = SPINE_BOARD
    if spine.is_file() and Path(root).resolve() == DEFAULT_ROOT.resolve():
        # 只在真根那一次注入：临时树里凭空多一支真板，会把「面只有 N 件」这类形状断言全打红（1193 里红 9 起）。
        rel = SPINE_REL
        if not any(r == rel for r, _ in out):
            out.append((rel, spine))
    return out


def check_one(rel, path):
    """单件三查，返回 [(形状指纹元组, 人话)]。

    母本_ 开头的原字节留底整件豁免：那种件按令不许改（改＝篡改当时的原字节），
    拿判据去逼它，唯一出路就是回头改归档换绿——正是要防的那个动作。
    """
    raw = path.read_bytes()
    if is_master(rel):
        return []
    hits = []
    crlf, bare_lf, bare_cr, rrrn = line_endings(raw)
    if not is_master(rel) and (bare_lf or bare_cr or rrrn):
        sig = []
        if bare_lf:
            sig.append("H1-lf")
        if bare_cr:
            sig.append("H1-cr")
        if rrrn:
            sig.append("H1-rrrn")
        hits.append((tuple(sig),
                     f"[H1] {rel} 行尾不纯：裸LF={bare_lf} 裸CR={bare_cr} 双写换行={rrrn}"
                     f"（正文类 .txt 必须纯 CRLF；合格的 {crlf} 行）｜指纹 {'/'.join(sig)}"))
    tab = raw.count(b"\t")
    if tab:
        hits.append((("H2-tab",),
                     f"[H2] {rel} 正文里有制表符 {tab} 处——多半是路径里的反斜杠加字母 t 被命令行转义了"))
    for token in RESIDUE_TOKENS:
        if token.encode("utf-8") in raw:
            hits.append((("H2-residue",),
                         f"[H2] {rel} 有字符串拼接残渣「{token}」：拼路径没执行成功，落下了字面量"))
            break
    if SHA12_RE.search(raw) and MOMENT_CN not in raw:
        hits.append((("H3-sha-no-moment",),
                     f"[H3] {rel} 引用了 12 位摘要却没写「时刻」：这种 sha 下一轮会被当成现值念"))
    for idx, ln in enumerate(raw.decode("utf-8", "replace").splitlines(), 1):
        mt = TOKEN_SHAPE.search(ln)
        if mt:
            hits.append((("H7-token",),
                         f"[H7] {rel} 第 {idx} 行留着未替换令牌「{mt.group(0)[:30]}」"
                         "——这一行没有读数，别当读数念（刀里令牌没换干净就落盘，格 P170 空壳那一族）"))
            break
    return hits


def _to_stamp(text):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError("改线格式要是 YYYY-MM-DD[ HH:MM[:SS]]，收到的是：" + text)


def _arch_of(rel, archive_debt):
    """相对板根的顶层目录前缀命中归档登记则返回该前缀，否则 None。"""
    top = rel.split("/", 1)[0] + "/"
    return top if top in archive_debt else None


def _arch_map_for(root):
    """默认那份归档登记，只在它确实长在这棵树上的时候套用。

    以前是无条件默认：任何临时树、演练根只要没长着那几片目录，就被判 [H4]「整片不见」，
    等于让每个副本替真实板根背一次红（实测：DS 的 test_paperwork_hygiene 23 条因此红）。
    这些归档片是本机留底、不进公开仓；干净 clone 不该替投递机背红。
    显式注入 archive_debt 永远生效，红证明与随包测试走这条；
    投递机自检可设 PAI_PAPERWORK_REQUIRE_ARCHIVES=1，把缺失判成 [H4]。
    不套用时不许静默，知会一行。
    """
    p = Path(root).resolve()
    require_archives = os.environ.get(
        "PAI_PAPERWORK_REQUIRE_ARCHIVES", "").strip().lower() in ("1", "on", "true", "yes")
    if require_archives or any((p / k.rstrip("/")).is_dir() for k in ARCHIVE_DEBT):
        return ARCHIVE_DEBT
    return {}


def run(root=DEFAULT_ROOT, cutoff=SHIP_MOMENT, known_debt=None, archive_debt=None, h6=True):
    """返回 (违规, 已知欠账, 对方件知会, 统计)。入参都可注入，测试与红证明用。"""
    debt_map = KNOWN_DEBT if known_debt is None else known_debt
    arch_map = _arch_map_for(root) if archive_debt is None else archive_debt
    arch_default = archive_debt is None and not arch_map
    root = Path(root)
    empty_stats = {"files": 0, "checked": 0, "frozen": 0,
                   "debt_total": len(debt_map), "debt_hit": 0, "cutoff": cutoff,
                   "arch_skipped": len(ARCHIVE_DEBT) if arch_default else 0, "h6_skipped": bool(h6)}
    if not root.exists():
        return ([f"[H4] 扫描根不存在：{root}（整面失效，不等于「没问题」）"], [], [], empty_stats)
    targets = scan_targets(root)
    if not targets:
        return ([f"[H4] 扫描根里一件正文类 .txt 都没扫到：{root}"
                 "（板根住址或扫描口径变了；空扫不算绿）"], [], [], empty_stats)
    line = _to_stamp(cutoff)
    violations, debt, notice = [], [], []
    debt_hit = frozen = 0
    h6_v, h6_files = run_h6(root) if h6 else ([], 0)
    h6_skip = bool(h6) and h6_files == 0 and not face_expected(root)
    if h6_skip:
        h6_v = []
    arch_stat = {k: [0, 0, set()] for k in arch_map}
    for rel, path in targets:
        born = datetime.datetime.fromtimestamp(path.stat().st_mtime)
        if is_other_ai(rel):
            hits = check_one(rel, path)
            shape = "/".join(sorted(set(s for sig, _m in hits for s in sig)))
            notice.append(f"[知会] {rel}｜对方件（DS 自己那份日志）形状：{shape or '干净'}"
                          "｜按 ADR-081 第 6 条我不代写不代改，只在板上知会")
            continue
        if rel not in BOARD_NAMES and born < line:
            frozen += 1
            continue
        hits = check_one(rel, path)
        pref = _arch_of(rel, arch_map)
        if pref is not None:
            arch_stat[pref][0] += len(hits)
            arch_stat[pref][1] += 1
            for sig, _msg in hits:
                arch_stat[pref][2].update(sig)
            continue
        if rel not in debt_map:
            violations.extend(msg for _sig, msg in hits)
            continue
        want, why = debt_map[rel]
        debt_hit += 1
        now = set()
        for sig, _msg in hits:
            now.update(sig)
        grown = sorted(now - set(want))
        gone = sorted(set(want) - now)
        if grown:
            violations.append(
                f"[H5] {rel} 挂在白名单里，形状【变脏了】：新出现 {'/'.join(grown)}"
                f"｜冻结的只是旧形状，新写的脏件不许藏在白名单后面｜登记的原形状：{'/'.join(sorted(want)) or '无'}")
        if grown or not gone:
            debt.append(f"[欠账] {rel}｜{'/'.join(sorted(want))}｜为什么不动：{why}")
        if gone:
            violations.append(
                f"[H0] {rel} 挂在白名单里但欠账【已销】：{'/'.join(gone)} 不见了"
                "——要么它被归一好了却没人在板上签字（销账要一句令 + P 号），"
                "要么这条白名单本来就该删；两种都得写清是谁点的头，不许悄悄绿掉")
    for pref, (n0, shapes0, why, nod) in arch_map.items():
        got_n, got_files, got_shapes = arch_stat[pref]
        want = tuple(sorted(shapes0))
        if got_files == 0:
            violations.append(f"[H4] 归档登记 {pref} 现在一件也没扫到——那面整片不见了"
                              "（目录搬家或扫描口径变了），关掉一面不叫绿")
        elif got_n > n0:
            violations.append(f"[H5] 归档登记 {pref} 处数涨了：登记 {n0} 处、现扫 {got_n} 处"
                              "——冻结的只是登记那一族，新写进归档的脏件不许躲在这里；"
                              "要么就地归一新件，要么另加一句令并写明是谁点的头")
        elif got_n < n0:
            violations.append(f"[H0] 归档登记 {pref} 处数少了：登记 {n0} 处、现扫 {got_n} 处"
                              "——有人回头改了旧归档却没在板上签字（销账要一句令 + P 号），不许悄悄绿掉")
        elif tuple(sorted(got_shapes)) != want:
            violations.append(f"[H5] 归档登记 {pref} 处数没变但形状换了：登记 {'/'.join(want)}、"
                              f"现扫 {'/'.join(sorted(got_shapes)) or '无'}——换个脏法也要签字")
        else:
            debt.append(f"[归档欠账] {pref}｜{got_n} 处 / {got_files} 件｜{'/'.join(sorted(got_shapes))}"
                        f"｜为什么不动：{why}｜签字：{nod}")
    arch_files = sum(v[1] for v in arch_stat.values())
    arch_hits = sum(v[0] for v in arch_stat.values())
    violations += h6_v
    checked = len(targets) - frozen - len(notice) - arch_files
    if checked == 0:
        violations.append(
            f"[H4] 比对面塌成零件（扫到 {len(targets)} 件，全被划成冻结/对方件）——"
            "把该查的一面整个关掉不叫通过；改上线线要有人签字")
    return violations, debt, notice, {"files": len(targets),
                                      "checked": checked,
                                      "frozen": frozen, "debt_total": len(debt_map),
                                      "debt_hit": debt_hit, "arch_files": arch_files,
                                      "arch_hits": arch_hits, "py_face": h6_files,
                                      "arch_skipped": len(ARCHIVE_DEBT) if arch_default else 0,
                                      "h6_skipped": h6_skip, "cutoff": cutoff}


def _skipped_line(st):
    """两声「本次没比」只进 stats 与这里，不进 notice：notice 的行数在 run() 里当作少比一件文件用。"""
    bits = []
    if st.get("arch_skipped"):
        bits.append("归档族登记 %s 条未套用（这一棵树不是 %s，也没长着那几片目录）" % (st["arch_skipped"], DEFAULT_ROOT))
    if st.get("h6_skipped"):
        bits.append("H6 那一面未比（这一棵树既没有 personal-ai-system/{tools,tests}，顶层也没有 .py）")
    if not bits:
        return ("注四：登记面与 H6 面本次都在这棵树上比过——归档族按住址判：别的树要么显式注入 archive_debt，"
                "要么自己长出那一面。")
    return "注四：本次没比的口径＝" + "；".join(bits) + "。按住址判，不是把闸门关了：换真实板根或显式注入照样判。"


def _print(line):
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("gbk", "replace").decode("gbk", "replace"))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="留底/日志卫生自检（H1 行尾 / H2 转义残留 / H3 摘要带时刻 / H4 整面失效 / H5+H0 白名单两刀）")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="投递板根（默认 D:\\Q）")
    ap.add_argument("--cutoff", default=SHIP_MOMENT,
                    help="上线线：早于此时刻写的历史件按令冻结（默认 " + SHIP_MOMENT + "）")
    ns = ap.parse_args(argv)
    v, debt, notice, st = run(ns.root, ns.cutoff)
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    _print(f"=== 留底/日志卫生自检 · {stamp} ===")
    _print(f"扫描根：{ns.root}    上线线：{st['cutoff']}")
    head = (f"扫到 {st['files']} 件正文类 .txt：比对面 {st['checked']} 件、"
            f"上线线之前冻结 {st['frozen']} 件、归档登记面 {st['arch_files']} 件/{st['arch_hits']} 处、"
            f"白名单命中 {st['debt_hit']}/{st['debt_total']} 条；"
            f"H6 那一面 .py {st['py_face']} 件（板根顶层＋脊柱 tools/tests）")
    if not v:
        _print(f"\n通过（只说测过的面）：{head}；比对面全过 H1/H2/H3，归档面按登记的形状与处数逐条念在下面。")
        for d in debt:
            _print("  " + d)
        for n in notice:
            _print("  " + n)
        _print(f"注一：那 {st['frozen']} 件历史面是【没查】不是【查过且干净】——按令不回头改归档；"
               "要看那一面就跑 --cutoff 2020-01-01，数当场念。"
               "25 日晚第一次实测是【209 件脏 / 268 处】，那只是当天现场，别当现值抄——"
               "坐标与逐类拆分见 留底_20261025_文书卫生闸门上线/读数_三问探针_文书卫生闸门.txt。")
        _print("注二：.py 归档件按令不比行尾（归一＝篡改当时真跑过的执行物）；母本_ 开头的原字节留底【整件不比】——它按令不许改，判它红只会诱使人回头改归档换绿。")
        _print("注三：这句绿只覆盖留底与日志面，不代表镜像一致——那是 check_relay 的活。")
        _print(_skipped_line(st))
        return 0
    _print(f"\n发现 {len(v)} 处违规（{head}）：")
    for line in v:
        _print(line)
    for d in debt:
        _print("  " + d)
    for n in notice:
        _print("  " + n)
    _print(_skipped_line(st))
    _print("\n处置：本轮新写的件就地归一（二进制读写 + 去行尾后正文逐字节断言），并作废旧 sha 的对外记录；"
           "白名单里那些要动必须先有一句令，我不自行改旧归档。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
