# -*- coding: utf-8 -*-
"""密钥自检（tools/check_key.py）—— 零依赖，只读，绝不打印 Key 的值。

为什么需要它：接真模型这条路卡了几轮，卡点都是同一类——"到底配没配、
配在哪、有没有漏进不该漏的地方"，而这三件事谁也没法靠肉眼确认。
这个脚本把确认做成一条命令，并且【把"不许泄露"做成代码性质】：
所有输出只允许出现变量名与长度，任何分支都不打印值本身。

用法：
    python tools/check_key.py            只做本地检查（不联网，不花一分钱）
    python tools/check_key.py --probe    额外发一次最小请求验连通（★会真联网、真扣费）

它还有一项别人没有的：看【持久层】环境变量（Windows 注册表那层，setx 写进去的）。
进程里的密钥关掉终端就没了，持久层里的一份会跟着这台机器走，
把"缺省走 mock"在下一个终端里悄悄顶掉 —— 只看 os.environ 的自检正好看不见它。
这一项仍然只报变量名与长度；读不到时明说"读不到"，不当成"没有"（ADR-038）。

退出码：0=可以接真模型跑；1=还没配好；2=配了但发现泄露迹象（此时先别 --probe）。
"""
import argparse
import os
import platform
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                       # personal-ai-system/
sys.path.insert(0, str(ROOT))

PROVIDERS = ("openai", "anthropic", "deepseek")
# 记忆里可能出现的密钥长相（只报命中条数，不报内容）
LEAK_PATTERNS = [r"sk-[A-Za-z0-9\-]{12,}", r"ghp_[A-Za-z0-9]{20,}",
                 r"Bearer\s+[A-Za-z0-9\-_\.]{20,}"]


def current_state():
    provider = (os.environ.get("PAI_MODEL_PROVIDER") or "").strip().lower()
    key_var = f"{provider.upper()}_API_KEY" if provider else None
    key = (os.environ.get(key_var) if key_var else None) or \
          (os.environ.get("PAI_MODEL_API_KEY") or "").strip() or None
    return {
        "provider_raw": provider,
        "provider": provider or "（未设置 → 永远走 mock，不联网）",
        "provider_known": provider in PROVIDERS,
        "key_var": key_var or "（无：provider 未设置）",
        "key_present": bool(key),
        "key_len": len(key or ""),
        "model": os.environ.get("PAI_MODEL_NAME") or "（缺省按 provider）",
        "base_url": os.environ.get(f"PAI_{(provider or 'openai').upper()}_BASE_URL")
                    or "（缺省端点）",
    }


# ---- 持久层环境变量（ADR-038）----
# 为什么单列一项：进程环境里的密钥会随终端关闭消失，持久层（Windows 注册表 / setx）不会。
# 一份 PAI_MODEL_PROVIDER=openai + *_API_KEY 留在持久层里，"缺省走 mock"这句话
# 在下一台终端上就不成立 —— 而现有自检只看进程环境，正好看不见它。
SECRET_NAME_RE = re.compile(r"(API_?KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)", re.I)
PLAIN_VALUE_OK = ("PAI_MODEL_PROVIDER", "PAI_MODEL_NAME")   # 值不是凭据：一个是枚举、一个是模型名


def _read_persisted_env():
    """返回 (持久变量表, 备注)。备注非空 = 这台读不到，【不等于"没有"】。
    值只在本机内存里用于判断，任何分支都不打印。"""
    if platform.system() != "Windows":
        return {}, "这台不是 Windows，没有注册表这一层持久变量"
    import winreg
    vals, failed = {}, []
    for hive, sub, label in ((winreg.HKEY_CURRENT_USER, "Environment", "用户级"),
                             (winreg.HKEY_LOCAL_MACHINE,
                              r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                              "机器级")):
        try:
            key = winreg.OpenKey(hive, sub)
        except OSError as exc:
            failed.append("%s（%s）" % (label, exc.__class__.__name__))
            continue
        with key:
            i = 0
            while i < 4096:                    # 不靠 QueryInfoKey 计数：它的 [0] 是子键数，
                try:                           # 拿它当值的个数会一个都读不到却报「无」——
                    name, value, _t = winreg.EnumValue(key, i)   # 本机真踩过，测试钉着
                except OSError:
                    break
                i += 1
                vals.setdefault(str(name), str(value))
    if not vals and failed:
        return {}, "读不到：" + "、".join(failed)
    return vals, ""


def persisted_overview(vals):
    """把持久层分成三类：像密钥的（只报名与长度）、可回显的配置（provider/模型名）、
    以及一个判据：新开终端直接跑 chat.py 会不会出门。"""
    secrets = sorted((n, len(v or "")) for n, v in vals.items() if SECRET_NAME_RE.search(n))
    plain = {}
    for name in PLAIN_VALUE_OK:
        raw = vals.get(name)
        if raw is None:
            continue
        value = str(raw).strip()
        if name == "PAI_MODEL_PROVIDER":
            low = value.lower()
            plain[name] = low if low in PROVIDERS else "（不在已知列表，值不回显）"
        else:
            plain[name] = value[:40]
    provider = str(vals.get("PAI_MODEL_PROVIDER") or "").strip().lower()
    key_present = bool((vals.get("%s_API_KEY" % provider.upper())
                        or vals.get("PAI_MODEL_API_KEY") or "").strip())
    # ★第三类：像"这份系统的配置"但不在上面两类里的（如 PAI_OPENAI_BASE_URL）。
    # 端点决定流量去哪台机器，看不见它=自检器替这台撒了个"干净"的谎，所以只报名与长度。
    already = {n for n, _ in secrets} | set(plain)
    others = sorted((n, len(v or "")) for n, v in vals.items()
                    if n not in already and (n.startswith("PAI_") or "BASE_URL" in n.upper()))
    return {"secrets": secrets, "plain": plain, "others": others,
            "goes_live": provider in PROVIDERS and key_present}


def print_persisted(vals, note):
    print("\n=== 持久层环境变量（setx 那种，关掉终端也在；只报名与长度）===")
    if note:
        print("  这台读不到：%s —— ★读不到不等于没有，别把它当「干净」" % note)
        return None
    ov = persisted_overview(vals)
    for name, length in ov["secrets"]:
        print("  ★持久密钥：%s  长度 %d（值永不显示）" % (name, length))
    for name, value in ov["plain"].items():
        print("  持久配置：%s = %s" % (name, value))
    for name, length in ov["others"]:
        print("  其他持久配置：%s  长度 %d（值不显示；端点类变量决定流量去哪）" % (name, length))
    if not ov["secrets"] and not ov["plain"] and not ov["others"]:
        print("  √ 无（持久层里没有密钥类、模型配置类、也没有 PAI_/BASE_URL 类变量）")
    if ov["goes_live"]:
        print("  结论：新开一个终端直接跑 chat.py →【会出门】——持久变量把「缺省 mock」顶掉了。")
        print("        这不是代码问题。清掉它：REG delete HKCU\\Environment /v <上面那个名字> /f")
        print("        改完要【关掉旧终端、重开】才生效（旧终端还带着内存里的副本）。")
    else:
        print("  结论：持久层不足以让它出门（缺 provider 或缺密钥）→ 直接跑仍走 mock。")
    return ov


_VAR_RE = re.compile(r"\b(secret|api_key|key_val|_require_key\([^)]*\))\b")


def line_leaks_value(code_line):
    """这一行是否可能把密钥【值】拼进异常/打印？

    判据：语句是 raise/assert/print，且里面用 f-string 或 + 拼接引用了装密钥的变量。
    只引用"变量名字符串"（如 export OPENAI_API_KEY）不算——那是给人看的提示。
    """
    if not re.search(r"\b(raise|assert|print)\b", code_line):
        return False
    if not _VAR_RE.search(code_line):
        return False
    return "{" in code_line or "+" in code_line or 'f"' in code_line or "f'" in code_line


def scan_source_for_key_leaks():
    """静态扫 pai/*.py：异常文本与 print 里是否可能拼进密钥值。"""
    bad = []
    for py in sorted((ROOT / "pai").glob("*.py")):
        for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if line_leaks_value(line.split("#")[0]):
                bad.append(f"{py.name}:{n}  {line.strip()[:90]}")
    return bad


def scan_runtime_store_for_leaks():
    """隐私面：记忆库与备份里有没有密钥样式的字符串（只报条数与位置，不报内容）。"""
    hits = []
    for f in [ROOT / "data" / "memory.db"] + sorted((ROOT / "data" / "backups").glob("*")
                                                    if (ROOT / "data" / "backups").exists()
                                                    else []):
        if not f.exists():
            continue
        try:
            blob = f.read_bytes()
        except OSError as exc:
            hits.append(f"{f.name}: 读不了（{exc.__class__.__name__}）")
            continue
        for pat in LEAK_PATTERNS:
            n = len(re.findall(pat, blob.decode("latin-1")))
            if n:
                hits.append(f"{f.name}: 命中 {n} 处形似密钥的串（{pat}）")
    return hits


def probe():
    """一次最小真实调用。只报成功/失败与状态，不回显响应内容。

    M10.complete 返回的是【列表】（每个模型一份），不是 dict——
    这里曾按 dict 解读，真接通之后会打出整个结构体的字数、成本恒为 None。
    失败时同理：错误正文可能带服务商回显的凭据，只提 HTTP 状态码与大类。
    """
    from pai import model_gateway as M10
    try:
        res = M10.complete("只回复两个字：在的", opts={"max_tokens": 8})
    except Exception as exc:                      # 异常类型可以说，异常文本可能带凭据——不打
        msg = str(exc)
        code = re.search(r"HTTP (\d{3})", msg)
        kind = ("连不上（网络或代理）" if "连不上" in msg
                 else "不是合法 JSON" if "不是合法 JSON" in msg
                 else "响应结构不符（缺 choices）" if "choices" in msg
                 else "★这笔钱估不出来，派发前就被报账闸门拒了（ADR-080）"
                 if "估不出来" in msg else "其他")
        return 0, "失败：%s%s（错误正文已按红线隐去）" % (
            exc.__class__.__name__, "，HTTP " + code.group(1) if code else "，" + kind)
    first = res[0] if isinstance(res, list) and res else (res if isinstance(res, dict) else {})
    return 1, "通了：%s 收到 %d 字 / %d tokens / 成本 %s%s" % (
        first.get("model") or first.get("model_id") or "?",
        len(first.get("output") or ""), int(first.get("tokens") or 0),
        first.get("cost"),
        "" if first.get("priced") else "（★价目表没这个模型：按 ADR-080 这本出不了门，"
                                      "能走到这里说明闸门被绕过了）")


def main(argv=None):
    ap = argparse.ArgumentParser(description="密钥自检（不打印密钥值）")
    ap.add_argument("--probe", action="store_true", help="额外发一次最小请求（联网、真扣费）")
    args = ap.parse_args(argv)

    st = current_state()
    print("=== 密钥自检（输出只含变量名与长度）===")
    provider_label = st["provider"] + (
        "   ★不在已知列表" if st["provider_raw"] and not st["provider_known"] else "")
    print(f"  provider      : {provider_label}")
    print(f"  密钥变量       : {st['key_var']}")
    print(f"  密钥状态       : {('已配置（长度 %d）' % st['key_len']) if st['key_present'] else '未配置'}")
    print(f"  模型名         : {st['model']}")
    print(f"  端点           : {st['base_url']}")

    leaks_src = scan_source_for_key_leaks()
    leaks_db = scan_runtime_store_for_leaks()
    # 屏显字符必须 GBK 编得出：双击那条路的窗口是 cp936，一个对勾 emoji 就能把整份报告崩掉
    # （tests/test_console_encoding.py 会替这条守着）。√ × 【】都在 GBK 内。
    print("\n=== 红线检查 ===")
    print(f"  源码里把密钥值拼进异常/打印 : {'√ 无' if not leaks_src else '★发现 ' + str(len(leaks_src)) + ' 处'}")
    for line in leaks_src:
        print(f"     - {line}")
    print(f"  记忆库/备份里的形似密钥串   : {'√ 无' if not leaks_db else '★发现 ' + str(len(leaks_db)) + ' 处'}")
    for line in leaks_db:
        print(f"     - {line}")

    vals, note = _read_persisted_env()
    goes_live = bool((print_persisted(vals, note) or {}).get("goes_live"))

    if leaks_src or leaks_db:
        print("\n结论：配了也【先别 --probe】。先清干净再谈接真模型。（退出码 2）")
        return 2

    if not st["key_present"] or not st["provider_known"]:
        print("\n结论：还没配好。设 PAI_MODEL_PROVIDER 与对应 *_API_KEY（或跑 chat.py --live "
              "在本终端悄悄输入）。密钥只进环境变量，别贴进对话/代码/库。（退出码 1）")
        if goes_live:
            print("  ★但这个终端之外还有一份：持久层里 provider 与密钥都在（见上面），"
                  "新开终端跑 chat.py 会直接走真实模型。要清就用那行 REG delete。")
        return 1

    print("\n结论：环境变量层面已就绪，mock 会切成真实调用。")
    if args.probe:
        ok, msg = probe()
        print(f"  连通性：{msg}")
        return 0 if ok else 1
    print("  还没验连通（本地检查不联网）。要真发一次最小请求：python tools/check_key.py --probe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
