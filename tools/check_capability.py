# -*- coding: utf-8 -*-
"""能力契约自检器（M15）—— 见《00_协作规程与接口契约.txt》ADR-036 与 Backlog M15。

干什么：pai/ 里每个模块要么【自己登记】一份 CAPABILITY，要么在下面的欠账名单里
写明为什么还没登记。两个都没占 = 违规。这样"能自己长"才不是靠自觉：
新模块扔进来却没人认领、没登记，这份自检当场红。

为什么用 ast 解析而不是 import：import 会执行模块（连库、起线程、读环境变量都可能），
一个检查器不该有这些副作用；登记也必须是【字面量】，否则"开关在哪"这种事实
到了运行期才知道，等于没登记。

运行：python tools/check_capability.py        （0 = 契约无违规）

扫描面【只有 pai/】，这是定稿不是疏漏（ADR-079）：tools/ 下的是人手动跑一次的入口壳，
不给它们造一个并不存在的开关 —— 「没有环境变量开关」是事实，硬写一个 PAI_XXX 才是谎。
入口壳要动用的能力必须住在 pai/ 且【已登记或挂在欠账名单里】，这条由
tests/test_tool_entry_surface.py 守着。它同时还守第二条（P32 定档之后加的）：
入口壳里不许出现【自己下场】的设备动作 —— 开麦 / 读按键 / 起 PowerShell 认语音那三类
调用与脚本正文，只许住在 pai/ 里归那条开关管。以前它们 inline 在 tools/push_to_talk.py，
任何登记面都看不见它 = 盲区；Boss 2026-10-25 选【②设备动作上收 pai/】，盲区已闭，
这一面从「查不到」变成「查得到、而且会红」。
"""
import ast
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAI_DIR = os.path.join(ROOT, "pai")

# 必填字段。口径写在这里（唯一住址），模块里不许再抄一份解释。
REQUIRED = ("name", "switch_env", "default_on", "stores_user_text", "network", "degrade")
OPTIONAL = ("probe_cost", "approval", "text_hosts_ref")

# 契约④：存过用户原文的列，住址是那一张表。检查器只引用它，绝不复制它的内容。
TEXT_HOSTS_ADDR = "pai/memory.py:_TEXT_HOSTS"

STRUCTURAL = ("__init__.py",)

# 地基条款（ADR-036 增补二）：M2 这类模块不是"能力"，它没有开关、本来就常开。
# 只有列在这里的模块允许 switch_env = "NONE" 且 default_on = True，且必须写 approval
# （谁点头接受的常开）。这份名单归守门人，往里加一个名字就要配一条 ADR。
SWITCH_NONE = "NONE"
SUBSTRATE = ("memory.py", "self_model.py")   # M11 身份层：没有开关，关掉=“同一个我”断裂

# 契约① 的常开例外（ADR-086，Boss 2026-10-30 定调「补 ADR」）：非地基能力也可以缺省开，
# 条件是 approval 引一条【黑板标题行里查得到】的 ADR 号。本板目前立过的常开批准＝ADR-086
# （只批 R0.1 入口闸这一支）；别的模块要常开各自再立一条，不许共用别人的号，没住址仍按 ① 红。
# 这一层查得到的是「号立过」，查不到「这条 ADR 批的是哪支能力」——那句写在 ADR-086 第 4 款。
# 契约⑧（ADR-046）：【常开】和【出网】两类登记，approval 里必须引一个真实存在的 ADR 号。
# 为什么只查"引了 ADR-"不够：那是一块橡皮图章，谁都能敲一行 "ADR-999" 过去。所以住址
# 要拿去黑板里对：搜不到的号 = 编的。黑板文件在代码目录的上一层（工作区根、发布镜像都这样），
# 搬走代码单独跑时黑板可能不在，那种情况返回 None：只查格式，并在报告里明说这一层没验，
# 不假装验过了。
BLACKBOARD = os.path.join(os.path.dirname(ROOT), "00_协作规程与接口契约.txt")
_ADR_RE = re.compile(r"ADR-\d{3}")

# 契约⑨（X12，Boss 2026-10-22 点头「装」）：approval 里【声称 Boss 已经批过】的措辞，
# 必须引一个真实存在的 ADR 号——引了还要在黑板里搜得到（复用契约⑧ 那套住址核验）。
# 为什么⑦⑧不够：⑦只查"出网"、⑧只查"常开/出网"，而四次造假全住在【默认关】的登记里
# （M8 的「Boss 2026-10-21 点头 J2『能查』」、X10 的「Boss 原话『做 X10』」），
# 默认关从来不过这两道门 = 守卫的盲区。
# 措辞只认【肯定句 + Boss 在附近】：照 ADR-044 诚实写「未获单独批准」「需 Boss 另行定」
# 「待 Boss 点头」的一律豁免——否则等于逼投递方把话说空来躲检查。
_BOSS_RE = re.compile(r"Boss|老板|BOSS")
_NOD_RE = re.compile(r"点头|批准|同意|拍板|定调|指定|原话|授权|下令|首肯")
_NOT_APPROVED_RE = re.compile(r"未获|未得|没有|没对|没经|尚未|无需|需|须|待|另行|必须|不许|禁止")
_NOD_WINDOW = 24      # 「Boss 2026-10-21 点头」：Boss 和动词之间最多隔这么多字还算一句话
_NEG_BACK = 12        # 动词往前这么多字里出现"需/待/未"，就是"要求批准"而不是"已经批过"

# 欠账名单：只能减，不能拿来躲新模块。每条要有理由 + 归谁排。
PENDING = {
    "orchestrator.py": "M1 总装线：它自己的开关就是「有没有 M1」，登记形状要另定（ADR-046 里"
                       "留了一条待定）。★它是 X8/X9 两条补丁的落点（串行锁 ADR-045，X7 已在"
                       "2026-10-22 落地）；它体内新增 4 个 PAI_PROACTIVE_*，登记时须一起写进去",
}


_ADR_HEAD_RE = re.compile(r"(?m)^\s*(ADR-\d{3})\s*(?=[\[:：（(])")


def _adr_index(path=None):
    """黑板里【真正立过条】的 ADR 号集合；黑板不在本目录外时返回 None（= 无法核验）。

    只认标题行（`ADR-0nn [状态]`／`ADR-0nn：日期`／`ADR-0nn（日期`），不认正文里的提及：
    黑板自己就有一句「引 ADR-999→红」当作反面例子，用全文 findall 会把 999 收进"真实住址"，
    等于给造假发了一张通行证——栽赃实测过：假点头 + ADR-999 在旧算法下是绿的。
    """
    path = path or BLACKBOARD
    if not os.path.exists(path):
        return None
    with io.open(path, encoding="utf-8") as handle:
        return set(_ADR_HEAD_RE.findall(handle.read()))


def _claims_boss_nod(approval):
    """approval 是否在说"Boss 已经批过了"。写"未获批准/需 Boss 点头"的诚实登记返回 False。"""
    text = str(approval or "")
    for match in _NOD_RE.finditer(text):
        before = text[max(0, match.start() - _NEG_BACK):match.start()]
        if _NOT_APPROVED_RE.search(before):
            continue
        near = text[max(0, match.start() - _NOD_WINDOW):match.end() + _NOD_WINDOW]
        if _BOSS_RE.search(near):
            return True
    return False


def _module_strings(source):
    """取模块级 NAME = 字面量 的表，供 switch_env 这类字段引用常量名（不抄第二份值）。"""
    table = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                table[target.id] = node.value.value
    return table


def _capability_node(source):
    """返回模块级 CAPABILITY 字典的 (字段名 -> 解析后的值)，解析不出来的记 None。"""
    table = _module_strings(source)
    for node in ast.parse(source).body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Name) and target.id == "CAPABILITY"):
            continue
        if not isinstance(node.value, ast.Dict):
            return None, ["CAPABILITY 不是字典字面量"]
        fields, problems = {}, []
        for key, val in zip(node.value.keys, node.value.values):
            if not isinstance(key, ast.Constant):
                problems.append("CAPABILITY 的键必须是字符串字面量")
                continue
            if isinstance(val, ast.Constant):
                fields[key.value] = val.value
            elif isinstance(val, ast.Name) and val.id in table:
                fields[key.value] = table[val.id]      # 引用本模块常量，值仍只有一份
            else:
                fields[key.value] = None
                problems.append("%s: 值必须是字面量或本模块的字符串常量" % key.value)
        return fields, problems
    return None, []


def _read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def _registry_ready():
    """契约④指向的那张表真在不在（X3 落地前不该有任何模块声称"我存过原文"）。"""
    module, _, attr = TEXT_HOSTS_ADDR.partition(":")
    path = os.path.join(PAI_DIR, os.path.basename(module))
    if not os.path.exists(path):
        return False
    names = set()
    for node in ast.parse(_read(path)).body:
        if isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return attr in names


def _check_one(name, fields, problems, adrs=None):
    out = []
    if fields is None:
        return out + ["%s: %s" % (name, p) for p in problems]
    out += ["%s: %s" % (name, p) for p in problems]
    for key in REQUIRED:
        if key not in fields:
            out.append("%s: 缺必填字段 %s" % (name, key))
    for key in fields:
        if key not in REQUIRED and key not in OPTIONAL:
            out.append("%s: 字段 %s 不在契约里（口径改了要同步改检查器，别私加）" % (name, key))
    if out:
        return out
    env = fields["switch_env"]
    approval = str(fields.get("approval") or "")
    cited = set(_ADR_RE.findall(approval))
    # 住址核验：引了号还得【每一个号】都查得到（adrs=None = 本目录外没有黑板，只查格式）
    missing = sorted(cited - adrs) if adrs is not None else []
    addr_ok = bool(cited) and not missing
    if fields["default_on"] is True or fields["network"] is True:
        if not cited:
            out.append("%s: 契约⑧ %s的登记，approval 里必须引一个真实存在的 ADR 号"
                       "（谁批准的写在黑板里），当前 %r" % (
                           name,
                           "常开" if fields["default_on"] is True else "出网",
                           approval))
        elif missing:
            out.append("%s: 契约⑧ approval 引的 %s 在黑板里搜不到 = 住址可能是编的，"
                       "要新立一条 ADR 再来登记" % (name, "、".join(missing)))
    # 契约⑨：不看开关状态，只看措辞——声称"Boss 批过"就得给住址，默认关也躲不掉
    if _claims_boss_nod(approval) and not addr_ok:
        out.append("%s: 契约⑨ approval 写了【已获 Boss 批准】这类话（点头/批准/原话/定调…）"
                   "却没引一个查得到的 ADR 号——要么补真实住址，要么照 ADR-044 如实改成"
                   "「未获单独批准」。当前 %r" % (name, approval))
    if not isinstance(env, str) or not env or (env != SWITCH_NONE and not env.isupper()):
        out.append("%s: switch_env 必须是大写环境变量名，当前 %r" % (name, env))
    if name in SUBSTRATE:
        if env != SWITCH_NONE:
            out.append("%s: 地基没有开关，switch_env 要如实写 \"%s\"" % (name, SWITCH_NONE))
        if fields["default_on"] is not True:
            out.append("%s: 地基本来就开着，default_on 要如实写 True" % name)
        if not approval.strip():
            out.append("%s: 常开是有人点头的，approval 要写明谁接受" % name)
    elif env == SWITCH_NONE:
        # 非地基也能声明"没有开关"，但只能如实登记成【既成常开】：要引真实 ADR 住址（ADR-046）
        if not addr_ok:
            out.append("%s: 想躲开关？只有地基能免住址地声明 NONE；确实一直没有开关，"
                       "就按契约⑧ 在 approval 里引真实 ADR 号登记成既成常开（ADR-046）" % name)
        elif fields["default_on"] is not True:
            out.append("%s: 声明没有开关（NONE）= 它其实一直在跑，default_on 不许写成 False" % name)
    elif fields["default_on"] is not False:
        # 契约① 的常开例外（ADR-086，Boss 2026-10-30 定调）：缺省开要有【黑板里查得到】的
        # ADR 号当批准住址。核验不在这里重做——用的就是上面契约⑧ 那一份 cited／missing／addr_ok，
        # 所以「引一个编出来的号」在这一格同样过不去。没立过常开 ADR 的能力照旧红。
        if fields["default_on"] is not True:
            out.append("%s: 契约① default_on 只许 True/False 两种，含糊值＝没登记（当前 %r）"
                       % (name, fields["default_on"]))
        elif not addr_ok:
            out.append("%s: 契约① 缺省必须关（default_on = False）；按 ADR-086 要走常开，"
                       "approval 就得引一条黑板里查得到的 ADR 号，当前引的号=%s"
                       % (name, "、".join(sorted(cited)) or "没引"))
    if not isinstance(fields["degrade"], str) or not fields["degrade"].strip():
        out.append("%s: 契约② degrade 要写清坏了怎么办，不许空" % name)
    if fields["network"] is True and not approval.strip():
        out.append("%s: 契约⑦ 会出网的登记必须写 approval（谁点头才能开）" % name)
    if fields["stores_user_text"] is True:
        if fields.get("text_hosts_ref") != TEXT_HOSTS_ADDR:
            out.append("%s: 契约④ 存原文的登记要把 text_hosts_ref 指向 %s" % (name, TEXT_HOSTS_ADDR))
        elif not _registry_ready():
            out.append("%s: 契约④ 声称存过原文，但 %s 里还没有那张表" % (name, TEXT_HOSTS_ADDR))
    for flag in ("stores_user_text", "network"):
        if fields[flag] not in (True, False):
            out.append("%s: %s 必须是 True/False，不许含糊" % (name, flag))
    return out


def scan(pai_dir=None, pending=None, adrs="auto"):
    """走查一个模块目录。返回 (已登记名单, 欠账名单, 违规清单)。

    欠账名单是【关于被扫的那个目录】的说法，所以跟着目录一起传：检查真实脊柱用
    PENDING，测试用临时目录时传自己的（否则每个临时目录都会被判成"名单过期"）。
    adrs 同理：默认去本目录外那份黑板里取真实 ADR 号，测试可以传自己的集合，
    传 None 表示"这一层不验，只查格式"。
    """
    pai_dir = pai_dir or PAI_DIR
    pending = PENDING if pending is None else pending
    adrs = _adr_index() if adrs == "auto" else adrs
    files = sorted(f for f in os.listdir(pai_dir) if f.endswith(".py"))
    registered, violations, seen = {}, [], set()
    for fname in files:
        if fname in STRUCTURAL:
            seen.add(fname)
            continue
        source = _read(os.path.join(pai_dir, fname))
        fields, problems = _capability_node(source)
        if fields is not None or problems:
            seen.add(fname)
            violations += _check_one(fname, fields, problems, adrs)
            if fields:
                registered[fname] = fields
                if fname in pending:
                    violations.append("%s: 已登记的模块不许再挂在欠账名单里" % fname)
            continue
        if fname in pending:
            if not str(pending[fname]).strip():
                violations.append("%s: 欠账名单里必须有理由，不能空挂" % fname)
            seen.add(fname)
            continue
        violations.append("%s: 既没登记 CAPABILITY，也不在欠账名单里" % fname)
    for fname in pending:
        if fname not in seen:
            violations.append("欠账名单过期：%s 已不在目录里，条目要删掉" % fname)
    return registered, {f: pending[f] for f in files
                        if f in pending and f not in registered}, violations


def main(argv=None):
    registered, pending, violations = scan()
    adrs = _adr_index()
    print("=== 能力契约自检 ===")
    print("已登记 %d 个，欠账 %d 个，违规 %d 条" % (len(registered), len(pending), len(violations)))
    if adrs is None:
        print("  ！契约⑧⑨ 的住址核验没跑：黑板不在 %s 的上一层，这一层只查了格式"
              % os.path.basename(ROOT))
    else:
        print("  契约⑧⑨ 住址对得上黑板：%d 个 ADR 号（文件在 %s 上一层）"
              % (len(adrs), os.path.basename(ROOT)))
    for fname, fields in registered.items():
        print("  [登记] %-18s %s  开关=%s 关=%s 原文=%s 出网=%s" % (
            fname, fields["name"], fields["switch_env"],
            "缺省关" if fields["default_on"] is False
            else ("地基常开" if fname in SUBSTRATE else "★缺省开"),
            "存" if fields["stores_user_text"] else "不存",
            "会" if fields["network"] else "不会"))
        print("         降级：%s" % fields["degrade"])
    for fname, reason in pending.items():
        print("  [欠账] %-18s %s" % (fname, reason))
    for v in violations:
        print("  ★违规 %s" % v)
    if violations:
        print("结论：契约有缺口 —— 补登记或补名单，别改检查器来将就。")
        return 1
    print("结论：契约无违规。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
