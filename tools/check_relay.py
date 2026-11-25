"""投递纪律自检器（守门人工具，非产品脊柱的一部分）——依据 ADR-011/020/024 把“规矩”变成机器可查。

为什么需要它：本会话里镜像被外部 AI 并发写入过 3 次，每次都靠我肉眼 diff 才发现，
且 .gitignore 的差异差点漏掉。散文式的规矩守不住，能跑的脚本才守得住（A10 精神）。

查五件事 + 一条线索：
  R1 镜像一致   —— 工作区 与 投递目录逐字节相同（排除隐私/缓存/隔离区）；
                   在飞口径（Boss 2026-11-16 点头，P171 第 2 款）：工作区改了还没提交那一族，
                   投递区等于【已提交（HEAD）那一版】就不判红、只念「在飞几件」；投递区两头都不等于 ＝ 照样红
  R2 隐私外泄   —— 投递目录里不得出现 data/、*.db、backups（记忆库是你的隐私，不是交付物）；
                   只判投递区那一棵——发布位的 data\\ 是自家运行态（实测反向那 6 条红全在这里，P119）
  R4 认领锁格式 —— 黑板 Backlog 同一行不得出现两个 [锁:]（撞车的前兆）；
                   R4 加强版（Boss 2026-11-06 点头）再加一刀：只有锁主能改自己那行的锁——
                   拿 --as 当 actor、--bb-before 当上一版黑板，逐行比锁的归属（键＝行头标题原文，不用行号）。
                   判据本体不住在这里：复用 agent_layer/agentd.py 的 Store.locks_detail（ADR-083 唯一住址）；
                   没带这两个入参时这条【明说没跑】，不静默绿。
  R5 排除面     —— R1/R2 排除掉的那些件（缓存目录、*.pyc、隔离区、隐私目录）不参与逐字节比对，
                   所以单独扫它们的隐私面；依据是 P31 那条实测「密钥塞进 pyc 里 rc=0」；
                   两棵都扫（P119：旧版入参钉死在 mirror 槽，正向盖不住发布位）
  R6 明文凭据   —— 扫描面里不得出现凭据类环境变量的【真实值】明文；扫描面 = 投递区两面 + 板根 + 发布位两面
                   （Boss 2026-10-28 令「从镜像扩到整个板根」：日志/信件/留底/黑板副本/包区都在镜像外）；
                   底样取 进程环境+持久层 两层（ADR-038），名字判据直接取 check_key.SECRET_NAME_RE，
                   报告只出（件名, 变量名），值绝不抄出。形状判据（sk- 前缀那类）对我们自己
                   六件测试字节码必假红，所以这条补的是 P31 留下的那半格而不是另立一份名单
  线索          —— 投递区哪些文件“比工作区新”，只作追查方向，不算违规（正常发布必然如此）

退出码：0 全通过；1 有违规（打印中文报告）。那句“通过”一定带比对面/排除面两个计数，
不写成“整棵树干净”——那不是我测出来的东西。

运行：
    python tools/check_relay.py                  # 查默认路径（工作区 与 D:\\Q）
    python tools/check_relay.py --ws A --mirror B
    python tools/check_relay.py --as Qoder --bb-before D:\\Q\\留底_xxx\\母本_黑板1_板根.txt
                                                 # 上面这条才跑 R4 加强版；不带就明说「这一刀没跑」
零第三方依赖，只用标准库。
"""
import argparse
import datetime
import os
import re
import subprocess
import sys
from pathlib import Path

# 与镜像比对时忽略的东西：运行态、缓存、隐私数据、外部AI投递的隔离区
# reports/ 与 *.bak* 是闸门自产物与留底（Boss 2026-11-22 ③ 点头的 (a) 口径，P174）：
# 它们每跑一遍就长一份，纳入 R1 等于让分母自己长大。摘出比对面【不是删掉、也不是免检】——
# _walk_excluded 会把它们收进 R5 隐私扫描面，密钥塞进 reports/ 照样被抓。
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "data", "backups",
                "_incoming_deepseek", "node_modules", ".git", "reports"}
EXCLUDE_NAMES = {"*.db", "*.db-journal", "*.pyc", ".writing.lock", ".writing.tmp", "*.bak*"}
# 投递目录根下这些前缀的目录是隔离区/备份区，本就该多出东西，不参与 R1/R3
QUARANTINE_PREFIXES = ("外部AI投递", "_backup")

_HERE = Path(__file__).resolve().parent
DEFAULT_WS = _HERE.parent                       # personal-ai-system/
DEFAULT_MIRROR = Path(os.environ.get("PAI_RELAY_MIRROR") or (_HERE.parent / "mirror"))
# 跨树断言必带树名（板上老规矩）：--ws 那一棵在报告行里叫这个，与黑板「发布位／镜像」的叫法对齐。
WS_LABEL = "发布位"


def _excluded(name, patterns):
    from fnmatch import fnmatch
    return any(fnmatch(name, p) for p in patterns)


def _walk_files(root):
    """列出参与比对的相对路径 -> 绝对路径。命中排除目录/名字的一律跳过。

    root 收 str 也收 Path：本轮实测 run() 接到 str 的板根会在 _face_of 里 AttributeError 崩，
    而「这个面读不了」和「这个面干净」是两件事，先规范化再说。
    """
    root = Path(root)
    out = {}
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in EXCLUDE_DIRS
                       and not d.startswith(QUARANTINE_PREFIXES)]
        for fn in filenames:
            if _excluded(fn, EXCLUDE_NAMES):
                continue
            p = Path(dirpath) / fn
            out[str(p.relative_to(root)).replace("\\", "/")] = p
    return out


def _walk_excluded(root):
    """列出 R1 排除掉的那一面（缓存目录、隔离区、隐私目录、*.pyc 等）——R5 的扫描面。

    为什么单独立一条：_walk_files 的排除面就是它的盲区，而 R2 那份隐私判据复用的正是同一个函数，
    所以 P31 实测过「密钥塞进 __pycache__ 里一个 .pyc，check_relay rc=0、一个字不报」。
    这条不比对内容（pyc 头里嵌源 mtime，两侧永远不同，纳入比对会永远红），只扫隐私。
    """
    out = {}
    root = Path(root)
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        hidden = [d for d in dirnames
                  if d in EXCLUDE_DIRS or d.startswith(QUARANTINE_PREFIXES)]
        for d in hidden:
            for sub, _sd, sfn in os.walk(Path(dirpath) / d):
                for fn in sfn:
                    p = Path(sub) / fn
                    out[str(p.relative_to(root)).replace("\\", "/")] = p
        dirnames[:] = [d for d in dirnames if d not in hidden]
        for fn in filenames:
            if _excluded(fn, EXCLUDE_NAMES):
                p = Path(dirpath) / fn
                out[str(p.relative_to(root)).replace("\\", "/")] = p
    return out


def _read(p):
    return p.read_bytes()


BLACKBOARD_NAME = "00_协作规程与接口契约.txt"


def resolve_blackboard(ws, blackboard=None):
    """黑板住址只在这一处定：给了就用给的，否则取工作区的上一级。

    run() 与 main() 都要知道同一件事（R4 加强版比的是同一块板），写两遍就会出现
    「一处指镜像一处指工作区」那种谁红不代表谁对的局面。
    """
    return Path(blackboard) if blackboard else Path(ws).parent / BLACKBOARD_NAME


def _safe(s):
    """报告行进控制台前的兜底（v0.96 记在我名下的那条欠账）。

    违规行里会带黑板原文，里面常有 cp936 没有的字符（U+21D2 那类双线箭头）；旧版让它直接进 print，
    于是闸门抛 UnicodeEncodeError、rc=1 来自异常而不是来自判定——那句“有违规”就说不清了。
    """
    try:
        s.encode(getattr(sys.stdout, "encoding", None) or "gbk")
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode("gbk", "replace").decode("gbk", "replace")


def _tag(rule, label):
    """违规行的前缀：label=None 保持老形状 [R5]；带面名时写成 [R5] 面=发布位：

    同一条判据现在两棵都要扫，光看相对路径分不出是哪一棵响的——跨树断言必带树名（板上老规矩）。
    """
    return "[%s]" % rule if label is None else "[%s] 面=%s：" % (rule, label)


# ---------------------------------------------------------------- R1 镜像一致

def _git_rows(ws, args):
    """跑 git 取原始字节行；git 不在／不是仓库／超时一律 None＝这一刀没跑，退回严判（不静默降级成绿）。"""
    try:
        r = subprocess.run(["git", "-C", str(ws)] + args, capture_output=True, timeout=25)
    except (OSError, subprocess.SubprocessError):
        return None
    return [x for x in r.stdout.split(b"\n") if x.strip()] if r.returncode == 0 else None


def _git_dirty_files(ws):
    """发布位工作树里【已改未提交】的相对路径集合（正斜杠）；git 拿不到则 None。"""
    rows = _git_rows(ws, ["diff", "--name-only", "HEAD"])
    if rows is None:
        return None
    return {x.decode("utf-8", "replace").strip().replace("\\", "/") for x in rows}


def _git_head_bytes(ws, rel):
    """HEAD 那一版里这份件的原字节；拿不到返回 None（宁缺勿猜）。"""
    try:
        r = subprocess.run(["git", "-C", str(ws), "show", "HEAD:" + rel.replace("\\", "/")],
                           capture_output=True, timeout=25)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def check_mirror_equal(ws, mirror, inflight=None):
    """R1 逐字节比对。传进一个 list 就启用【在飞口径】：

    工作区改了还没提交、而投递区等于 HEAD 那一版 ＝ 不判红，rel 落进 inflight（那句绿话会念出来）；
    投递区既不是工作区也不是 HEAD 那一版 ＝ 仍然红，新写的脏东西躲不进这一格。
    不传 inflight＝旧行为（严判），单元测试靠这个开关出两臂。"""
    a, b = _walk_files(ws), _walk_files(mirror)
    v = []
    dirty = _git_dirty_files(ws) if inflight is not None else None
    for rel in sorted(set(a) | set(b)):
        if rel not in a:
            v.append(f"[R1/R3] 投递目录多出文件（工作区没有）：{rel}")
        elif rel not in b:
            v.append(f"[R1] 投递目录缺文件：{rel}")
        elif _read(a[rel]) != _read(b[rel]):
            if (dirty is not None and rel in dirty
                    and _git_head_bytes(ws, rel) == _read(b[rel])):
                inflight.append(rel)
                continue
            v.append(f"[R1] 内容不一致：{rel}")
    return v


# ---------------------------------------------------------------- R2 隐私

def _private_hits(p):
    """R2/R5 共用的那份隐私判据：记忆库文件本体。别立第二份名单。"""
    return p.suffix == ".db" or p.name.endswith(".db-journal")


def check_no_private_data(mirror):
    v = []
    data_dir = mirror / "data"
    if data_dir.exists() and any(data_dir.iterdir()):
        v.append(f"[R2] 投递目录里有运行态记忆库目录：{data_dir}"
                 " ——记忆库是用户隐私，绝不进共享区（ADR-024）")
    for p in mirror.rglob("*"):
        if p.is_file() and _private_hits(p):
            v.append(f"[R2] 投递目录里有数据库文件：{p}")
    return v


# ---------------------------------------------------------------- R5 排除面

_R5_MAX_BYTES = 8 * 1024 * 1024


def _pyc_source_rel(rel):
    """字节码相对路径 -> 期望的源件相对路径；不在 __pycache__ 里则返回 None。

    CPython 规则：a/b.py 编译成 a/__pycache__/b.<tag>.pyc（tag 可能是 cpython-313，
    也可能带 -pytest-8.3.2 这类后缀），所以取第一个点之前的名字、并退回 __pycache__ 的上一层。
    这一层我第一版推导错过两次（先没剥 tag、后没退层），把 75 件全部报成孤儿，
    闸门永远红——那条比没有更糟，所以这里把规则写死并配守卫测试。
    """
    parts = list(Path(rel).parts)
    if not parts:
        return None
    fn = parts.pop()
    stem = fn.split(".")[0]
    if not parts or parts[-1] != "__pycache__":
        return None
    parts.pop()
    parts.append(stem + ".py")
    return "/".join(parts)


def check_excluded_surface(root, label=None):
    """R1 看不见的那一面也扫一遍（依据 P31 实测：旧版这里整块豁免，谁都没看）。

    旧判据写着 "__pycache__ 里的 .db 跳过"，等于把整个排除面交给信任；P31 复跑证实
    往镜像 __pycache__ 塞一个 x.py、或塞一件库体，check_relay rc=0、一个字不报。
    逐字节比对做不到（pyc 头里嵌源 mtime，两侧永远不同，纳入比对必永远红），
    所以这条只判四件确定能判的事，且不新立第二份名单（库体判据与 R2 共用 _private_hits）：
      a) 排除面里出现 SQLite 库体（后缀没写 .db 也算）
      b) 字节码查无对应源件：缓存目录里有码无源，或 __pycache__ 之外的裸 .pyc（可直接 import）
      c) 缓存目录里出现非 .pyc 的件
      d) 读不了 / 超过扫描上限 - 报出来给人看，不静默跳过
    明文凭据那一类不在这里判（形状判据对我们自己那六件测试字节码必假红），
    它由同文件的 R6 按【环境变量真实值逐字节比】扫这两个面、并扩到板根——见 check_plaintext_credentials。
    隐私目录（data/、backups/）由 R2 按同一份判据报，这里不重复报第二遍。
    """
    v = []
    t5 = _tag("R5", label)
    inv = _walk_excluded(root)
    for rel, p in sorted(inv.items()):
        if not p.is_file():
            continue
        parts = [x.lower() for x in Path(rel).parts]
        if "data" in parts or "backups" in parts:
            continue
        try:
            raw = _read(p)
        except OSError as e:
            v.append(f"{t5} R1 排除面里的件读不了（视同可疑，不静默跳过）：{rel}（{type(e).__name__}）")
            continue
        if len(raw) > _R5_MAX_BYTES:
            v.append(f"{t5} 排除面里有件超过扫描上限 {len(raw)} B，没扫，请人工看一眼：{rel}")
            continue
        low = rel.lower()
        in_cache = "__pycache__" in parts
        if in_cache and not low.endswith(".pyc"):
            v.append(f"{t5} 缓存目录里出现非字节码件（这个目录不参与任何比对，不该有别的件）：{rel}")
        if b"SQLite format 3" in raw[:4096] and not _private_hits(p):
            v.append(f"{t5} 排除面里有 SQLite 库体（后缀没写 .db 也算）：{rel}")
        if low.endswith(".pyc"):
            if not in_cache:
                # 不在 __pycache__ 里的 .pyc 是可以被直接 import 的（源码不存在也算合理），
                # 而 R1 按名字把它整块排除 - 一份谁都没读过的代码躺在投递区里。
                v.append(f"{t5} 排除面里有可直接 import 的裸字节码（不在 __pycache__ 里，无源可读）：{rel}")
            else:
                src = _pyc_source_rel(rel)
                if not (root / src).is_file():
                    v.append(f"{t5} 缓存目录里有字节码查无源件（有码无源，源码不在任何比对面）：{rel}")
    return v


# ---------------------------------------------------------------- R6 明文凭据

# 「名字像不像凭据」这条判据只有【一个住址】：tools/check_key.py 的 SECRET_NAME_RE（ADR-038）。
# 持久层怎么读也只有它那一份。R6 不立第二份名单，只加自己的长度闸与扫描面。
_CRED_MIN_LEN = 12
_R6_MAX_BYTES = 8 * 1024 * 1024


def _load_key_judge():
    """取邻居 check_key 当判据住址：脚本直跑时 sys.path[0] 就是 tools/，被 import 时不是。"""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import check_key as ck
        return ck, ""
    except Exception as exc:
        return None, "取不到判据住址 tools/check_key.py（%s）= R6 这一刀空转" % type(exc).__name__


_CK, _CK_IMPORT_NOTE = _load_key_judge()
CRED_NAME_RE = _CK.SECRET_NAME_RE if _CK is not None else None


def credential_samples(environ=None, persisted=None):
    """底样 = 名字像凭据（判据取自 CRED_NAME_RE）且值长够的那些变量的【真实值】。

    为什么按真实值逐字节比，不按形状（sk- 前缀、赋值式那类）扫：
    形状扫法实测必假红——我们自己六件测试字节码里就塞着假密钥形状，那正是 R6 空到今天的理由。
    真实值在这台机器上只有一份，命中即外泄，误报面几乎为零；报告只出变量名，值一字节都不抄。
    长度闸（>=12）是挡假红的另一刀：off / 1 / 路径 这类开关值再像凭据也不能进底样。
    """
    out = set()
    for source in (os.environ if environ is None else environ,
                   {} if persisted is None else persisted):
        for name, val in source.items():
            if CRED_NAME_RE is None or not CRED_NAME_RE.search(str(name)):
                continue
            for variant in {(val or ""), (val or "").strip()}:
                if len(variant) >= _CRED_MIN_LEN:
                    out.add((str(name), variant.encode("utf-8", "surrogateescape")))
    return sorted(out)


def credential_sources():
    """底样 + 备注。备注非空 = 有一面【没扫成】，读不到不等于没有，所以这条必须响不能绿过去。

    取两层：进程环境 + 持久层（ADR-038 实测过——只看进程会漏掉 setx 写进注册表那一份，
    而那一份正是别人新开一个终端复制文件时真正在外的值）。
    """
    notes = []
    if _CK is None:
        return [], ["取不到凭据名字判据的住址 tools/check_key.py（%s）"
                    " = R6 这一刀空转，不能当成「干净」"
                    % (_CK_IMPORT_NOTE or "原因未记")]
    try:
        persisted, note = _CK._read_persisted_env()
    except Exception as exc:
        persisted, note = {}, "%s" % type(exc).__name__
    if note:
        notes.append("持久层环境变量这一面没扫成：%s（读不到不等于没有，别当「干净」）" % note)
    return credential_samples(os.environ, persisted), notes


def _face_of(root):
    """一棵树里【所有】可列件：比对面 + 排除面（P31 那次漏的就是后一半：密钥塞进 pyc 里 rc=0）。"""
    faces = dict(_walk_files(root))
    faces.update(_walk_excluded(root))
    return faces


def _r6_faces(mirror, board_root=None):
    """R6 的扫描面 = 投递区两面，再并上板根那一面（Boss 2026-10-28 令「从镜像扩到整个板根」）。
    返回 {绝对路径键: (报告用的件名, Path)} 与按名字跳过的件数。

    为什么板根也要扫：镜像只是板根里的一棵树。本轮实测（j285_face_prep_191805）板根 1323 件里
    有 674 件住在镜像之外——黑板副本、日志、给对方的信、留底母本、DS 的包区。凭据一旦被抄进
    这些地方，只扫镜像的 R6 一个字不报，而那句绿话照样念成「扫过 189 件、干净」。
    去重按解析后的绝对路径：镜像本身在板根里，同一件被两棵树各列一次，只算一次，
    且沿用投递区相对那个件名（老报告行的件名不变）。板根独有的件用板根相对的件名。
    隐私目录与库体按名字在任何 stat 之前跳过：记忆库绝不打开（ADR-024），那一面归 R2 报。
    """
    labelled = {}
    for rel, p in _face_of(mirror).items():
        labelled[_abs_key(p)] = (rel, p)
    if board_root is not None:
        for rel, p in _face_of(board_root).items():
            labelled.setdefault(_abs_key(p), (rel, p))
    kept, skipped = {}, 0
    for key, (rel, p) in labelled.items():
        parts = [x.lower() for x in Path(rel).parts]
        if "data" in parts or "backups" in parts or _private_hits(p):
            skipped += 1
            continue
        kept[key] = (rel, p)
    return kept, skipped


def _abs_key(p):
    """去重用的键：绝对路径 + 大小写归一（Windows 上 PATH/path 是同一件，不能各扫一遍）。"""
    return os.path.normcase(os.path.abspath(str(p)))


def check_plaintext_credentials(root, samples=None, board_root=None, label=None):
    """R6：扫描面（投递区两面 + 板根）里不得出现凭据变量的真实值明文。只报（件名, 变量名），值绝不抄进报告。

    为什么发布位也扫（P119）：那一棵是【要被复制进投递区的源头】——明文一旦躺在发布位，
    下一趟并线就把它带进共享区。旧版这里写着「工作区在用户自己机器上，他机器上有 Key 不是投递违规」，
    那句说的是环境变量里有 Key，不是「Key 的值写进了文件」，两码事；而反向那遍虽然会扫到发布位，
    却必带 6 条槽位假红（发布位的 data\\ 是合法运行态），等于没有可用的判定面。
    板根这一面是 Boss 2026-10-28 点头才扩的：它把「写在镜像外的明文」也纳进了判定，
    代价是每次多读几百件（实测 +2.4s），好处是不再出现「镜像干净、板根躺着 Key」这种绿。
    本轮实测：并入发布位两面再多读 1333 件、+0.97s，正向仍 rc=0（读数件 k571_probe_闸门面_135841）。
    """
    root = Path(root)
    t6 = _tag("R6", label)
    if samples is None:
        creds, notes = credential_sources()
    else:
        creds, notes = list(samples), []
    v = ["%s %s" % (t6, n) for n in notes]
    faces, _skipped = _r6_faces(root, board_root)
    for _key, (rel, p) in sorted(faces.items(), key=lambda kv: (kv[1][0], kv[0])):
        try:
            raw = _read(p)
        except OSError as exc:
            v.append("%s 扫描面里的件读不了（没扫成就是没扫成，不静默放过）：%s（%s）"
                     % (t6, rel, type(exc).__name__))
            continue
        if len(raw) > _R6_MAX_BYTES:
            v.append("%s 扫描面里有件超过扫描上限 %d B，这条没扫，请人工看一眼：%s"
                     % (t6, len(raw), rel))
            continue
        for name, val in creds:
            if val in raw:
                v.append("%s 扫描面 %s 里出现环境变量 %s 的真实值明文（只报案名，值未抄出）"
                         % (t6, rel, name))
    return v


def r6_counts(mirror, board_root=None):
    """那句绿话要的 R6 口径：实际扫过几件、按名字跳过几件（跳过的那几件一次都没打开）。"""
    kept, skipped = _r6_faces(Path(mirror), board_root)
    return len(kept), skipped


# ---------------------------------------------------------------- 参考信息：mtime

def note_recent_touches(ws, mirror, min_delta_seconds=60):
    """列出“投递区比工作区新”的文件及时刻，仅作【线索】，不构成违规。

    为什么不算违规：正常发布就是 工作区→投递区 复制，镜像 mtime 必然更新；
    若把它当违规，每次发布都会 self-flag，自检器就成了假警报制造机。
    真正的入侵证据是内容不一致或单侧独有文件（都由 R1 负责）。
    这里的用途是：当我没有 Recently 发布过、却看到“30 秒前被动过”，就该去查 R1。
    """
    a = _walk_files(ws)
    notes = []
    now = datetime.datetime.now().timestamp()
    for rel, mp in sorted(_walk_files(mirror).items()):
        if rel not in a:
            continue
        delta = mp.stat().st_mtime - a[rel].stat().st_mtime
        if delta > min_delta_seconds:
            age = int(now - mp.stat().st_mtime)
            notes.append(f"{rel} 比工作区新 {delta:.0f}s（{age}s 前被写过）")
    return notes


# ---------------------------------------------------------------- R4 认领锁

_LOCK_RE = re.compile(r"\[锁:([^\]]+)\]")


def check_claim_locks(blackboard_path):
    """黑板 Backlog 区：同一行两个不同 [锁:] = 撞车前兆。"""
    p = Path(blackboard_path)
    if not p.exists():
        return [f"[R4] 找不到黑板文件：{p}"]
    v = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        owners = _LOCK_RE.findall(line)
        distinct = {o.strip() for o in owners if o.strip()}
        if len(owners) > 1 and len(distinct) > 1:
            v.append(f"[R4] 第{i}行出现两个认领锁 {sorted(distinct)} = 撞车：{line.strip()}")
    return v


# ---- R4 加强版：锁主才能改自己那行的锁（Boss 2026-11-06 点头）----------------
#
# 判据不抄第二份：哪些行算锁行、锁主名字怎么认、缩进多深算明细、带了 [已并入/[结案/
# [退回/[已关闭 的行算不算活动——这四件事 agentd.py 里已经有一套（ADR-083，产品那侧认锁就是它）。
# 这里把那三个纯文本方法（只读 self.blackboard）绑到一个只带路径的对象上，
# 于是黑板面上「锁」这件事只剩一个住址；板上 P44 记的那笔「两把尺不同面」也一并收掉。


def lock_authority():
    """返回「能读一个黑板路径的锁面」的类，或（None, 空转说明）。"""
    al = Path(__file__).resolve().parent.parent / "agent_layer"
    if not al.is_dir():
        return None, f"取不到判据住址 {al} → R4 加强版空转"
    if str(al) not in sys.path:
        sys.path.insert(0, str(al))
    try:
        import agentd
    except Exception as exc:                            # noqa: BLE001 - 空转必须响一声
        return None, f"取不到判据住址 agent_layer/agentd.py（{type(exc).__name__}）→ R4 加强版空转"

    class _BoardLocks:
        def __init__(self, path):
            self.blackboard = Path(path)

        _backlog_lines = agentd.Store._backlog_lines
        _lock_title = staticmethod(agentd.Store._lock_title)
        locks_detail = agentd.Store.locks_detail

    return _BoardLocks, None


def _lock_map(board):
    """locks_detail 的行列成 {行头标题：{锁主名}}；同一行名出现多次时把名字并起来。"""
    out = {}
    for x in board.locks_detail():
        out.setdefault(x["title"], set()).add(x["owner"].strip())
    return out


def check_lock_ownership(blackboard_path, before_path, actor):
    """返回（违规行，口径说明）。没给 actor 或上一版 → 不判，但说明里必须写明「这一刀没跑」。"""
    if not actor or not before_path:
        return [], ("R4 加强版【没跑】：缺 %s（只有锁主能改自己那行的锁这一刀需要 "
                    "--as 与 --bb-before，缺任何一边都不算通过）"
                    % "、".join([x for x, y in (("--as", actor), ("--bb-before", before_path)) if not y]))
    cls, idle = lock_authority()
    if idle:
        return [f"[R4] {idle}"], idle
    bb, bef = Path(blackboard_path), Path(before_path)
    if not bb.exists() or not bef.exists():
        miss = bb if not bb.exists() else bef
        return ([f"[R4] 加强版取不到黑板：{miss} → 这一刀空转"],
                f"加强版取不到黑板：{miss} → 空转")
    before, after = _lock_map(cls(bef)), _lock_map(cls(bb))
    who = actor.strip().lower()
    v = []
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key, set()), after.get(key, set())
        if b == a:
            continue
        if not b:
            # 上一版这行没有锁 → 新挂的第一把锁（含把活派给别人）不判，锁主规矩只管已有主的行
            continue
        if who not in {o.lower() for o in b}:
            v.append(f"[R4] 锁「{key[:40]}」原本在 {sorted(b)} 手上，这次变成 {sorted(a) if a else '【撤空】'}，"
                     f"而跑闸门的是 {actor} → 非锁主改了自己那行之外的锁")
    note = (f"R4 加强版跑过：上一版锁行 {len(before)} 条／现版 {len(after)} 条、"
            f"锁主变更 {len([1 for k in set(before) | set(after) if before.get(k, set()) != after.get(k, set())])} 处、"
            f"actor={actor}、判据住址 agent_layer/agentd.py")
    return v, note


def run(ws, mirror, blackboard=None, board_root=None, actor=None, bb_before=None):
    """老接口：只要违规列表。口径说明见 relay()（main 用那一版，好把「跑没跑」念出来）。"""
    return relay(ws, mirror, blackboard, board_root, actor, bb_before)[0]


def relay(ws, mirror, blackboard=None, board_root=None, actor=None, bb_before=None):
    """返回（违规列表, R4 加强版的口径说明）。违规列表空=干净。mtime 线索不在此列，见 note_recent_touches。

    board_root=None 表示【投递区那一面不含板根】——留给单元测试自己选；
    真跑（main）默认把板根并进来，所以「镜像干净而板根躺着明文」这一格不再可能绿过去。
    发布位那一棵（--ws）自 P119 起也进 R5/R6 的扫描面：旧版这三条的入参钉死在 mirror 槽，
    正向那遍根本盖不住发布位，要看发布位的排除面只能把两棵对调跑反向，而反向必带 6 条槽位假红。

    actor／bb_before 只喂 R4 加强版（Boss 2026-11-06 点头那一半）：两者缺一就不判锁的归属，
    老调用点（三参数与四参数）行为不变，所以随包测试里那句 run(...) == [] 不会因为我长了新刀就变味。
    这一遍的说明随返回值交出去——同一把刀只读一次黑板，绿话与红话都念同一份口径。
    """
    ws, mirror = Path(ws), Path(mirror)
    if not ws.exists():
        return [f"[R0] 工作区不存在：{ws}"], "R4 加强版【没跑】：闸门在比对前就退了"
    if not mirror.exists():
        return [f"[R0] 投递目录不存在：{mirror}"], "R4 加强版【没跑】：闸门在比对前就退了"
    bb = Path(resolve_blackboard(ws, blackboard))
    creds, r6_notes = credential_sources()
    violations = []
    # R2 只判投递区：发布位的 data\ 是自家运行态（实测那 6 条红全在 data\ 下，搬进镜像就 0 条）。
    violations += check_no_private_data(mirror)
    # 底样取不到＝这一刀空转，响一声；只在这一处念，免得两棵树各报一遍同一件事。
    violations += ["[R6] %s" % n for n in r6_notes]
    violations += check_excluded_surface(mirror)
    violations += check_excluded_surface(ws, label=WS_LABEL)
    inflight = []
    violations += check_mirror_equal(ws, mirror, inflight)
    violations += check_plaintext_credentials(mirror, creds, board_root)
    violations += check_plaintext_credentials(ws, creds, label=WS_LABEL)
    violations += check_claim_locks(bb)
    # R4 加强版：只有锁主能改自己那行的锁；缺 --as／--bb-before 时这条不判（下面那句口径会明说没跑）
    own_v, own_note = check_lock_ownership(bb, bb_before, actor)
    violations += own_v
    if inflight:
        own_note += ("\n  R1 在飞口径：投递区＝已提交（HEAD）那一版、发布位工作树还没提交的 %d 件：%s"
                     "｜这一格不判红（Boss 2026-11-16 令，P171 第 2 款）；提交之后仍不齐才是红。"
                     % (len(inflight), "、".join(sorted(inflight)[:12])
                        + ("…" if len(inflight) > 12 else "")))
    return violations, own_note


def coverage_counts(ws, mirror):
    """那句绿话要的口径数字：比对面几件、两棵各自的排除面几件（R5 扫过的那些）。"""
    ws, mirror = Path(ws), Path(mirror)
    compared = len(set(_walk_files(ws)) | set(_walk_files(mirror)))
    excluded = len([p for p in _walk_excluded(mirror).values() if p.is_file()])
    excluded_ws = len([p for p in _walk_excluded(ws).values() if p.is_file()])
    return compared, excluded, excluded_ws


def main(argv=None):
    ap = argparse.ArgumentParser(description="投递纪律自检（R1/R2/R4/R5/R6）")
    ap.add_argument("--ws", default=str(DEFAULT_WS), help="工作区项目目录")
    ap.add_argument("--mirror", default=str(DEFAULT_MIRROR), help="投递目录（默认 D:\\Q）")
    ap.add_argument("--blackboard", default=None, help="黑板文件路径")
    ap.add_argument("--board-root", default=None,
                    help="R6 的额外扫描面（默认=投递区的上一级，即板根 D:\\Q 那一棵）")
    ap.add_argument("--touches", action="store_true",
                    help="附带打印“投递区比工作区新”的线索（排查谁在赛跑时用）")
    ap.add_argument("--as", dest="actor", default=None, metavar="Qoder|DeepSeek",
                    help="R4 加强版的 actor：跑闸门的是谁（锁主才能改自己那行的锁）")
    ap.add_argument("--bb-before", default=None, metavar="PATH",
                    help="R4 加强版的上一版黑板（留底母本或发布前的快照）；不给＝这条不判、并且明说没判")
    ns = ap.parse_args(argv)
    board = Path(ns.board_root) if ns.board_root else Path(ns.mirror).parent
    v, own_note = relay(ns.ws, ns.mirror, ns.blackboard, board, ns.actor, ns.bb_before)
    compared, excluded, excluded_ws = coverage_counts(ns.ws, ns.mirror)
    r6_scanned, r6_skipped = r6_counts(ns.mirror, board)
    r6_ws_scanned, r6_ws_skipped = r6_counts(ns.ws)
    r6_samples, _r6_notes = credential_sources()
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"=== 投递纪律自检 · {stamp} ===")
    print(f"工作区（{WS_LABEL}）：{ns.ws}\n投递区：{ns.mirror}\n板根（R6 扩面）：{board}\n"
          f"两棵都扫的隐私面：R5 排除面＋R6 明文凭据（{WS_LABEL}那一面的行带『面={WS_LABEL}』前缀）")
    if ns.touches:
        notes = note_recent_touches(Path(ns.ws), Path(ns.mirror))
        print(f"\n线索（不算违规）：{len(notes)} 个文件比工作区新")
        for n in notes[:8]:
            print("  " + n)
        if len(notes) > 8:
            print(f"  …另 {len(notes) - 8} 个（多半只是我刚发布过）")
    if not v:
        print(_safe(f"\n通过（口径照 P31 收口，只说测过的面）：比对面 {compared} 件逐字节一致（两棵逐件==）；"
                    f"排除面【不参与逐字节比对】，隐私判据两棵各扫一遍——投递区 {excluded} 件、{WS_LABEL} {excluded_ws} 件；"
                    f"R2 只判投递区那一棵（{WS_LABEL}的 data\\ 是自家运行态，反向那遍的红是槽位假红）；"
                    f"R6 明文凭据按真实值逐字节扫过 {r6_scanned}＋{r6_ws_scanned} 件"
                    f"（面=投递区两面+板根，另并{WS_LABEL}两面；同件按绝对路径只算一次；"
                    f"凭据底样 {len(r6_samples)} 条＝进程环境+持久层，隐私那 {r6_skipped}＋{r6_ws_skipped} 件按名字跳过、没打开）；"
                    f"认领锁无撞车；{own_note}。"))
        print(f"注：排除面＝缓存/隔离区/隐私目录/*.pyc 这类；这两面 {excluded}＋{excluded_ws} 件只扫隐私、没比内容，"
              "别把这句绿念成「两棵树逐字节一致」。")
        return 0
    print(f"\n发现 {len(v)} 处违规（比对面 {compared} 件 / 排除面 投递区 {excluded}＋{WS_LABEL} {excluded_ws} 件 / "
          f"R6 扫描面 {r6_scanned}＋{r6_ws_scanned} 件）：")
    for line in v:
        print(_safe(line))
    print(_safe("R4 加强版口径：" + own_note))
    print("\n处置次序（ADR-011/020/024）：① 把违规新增保全进隔离区，不删；"
          "② 从工作区复位镜像；③ 重跑全量测试；④ 再跑本自检确认归零；⑤ 写回执说明。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
