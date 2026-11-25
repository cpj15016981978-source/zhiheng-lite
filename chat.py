"""M7 客户端（最简 CLI） — 见《00_协作规程与接口契约.txt》PART B2 (M7)。
Phase1 用命令行验证内核；Web/PWA 待内核稳定后再加（ADR-008）。

运行：  python chat.py
清空记忆重来：  python chat.py --reset
接真实模型（在本终端悄悄输入密钥，不回显/不保存/不进对话）：  python chat.py --live
命令：  :facts 看已记住的事实   :principles 看它自写的处世原则
        :reflect 手动触发一次反思   :log 看最近对话   :exit 退出
        :history/:forget/:purge/:retire/:backup 见启动时的“记忆体”说明
        :mood/:self/:revisions 看心境估计、身份记录与改口流水（Phase5.6）
        :voice on 本机嗓子念回答   :ear on 命令级语音输入（默认关；只在你空回车那一瞬开麦）
"""
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pai.memory import Memory
from pai.orchestrator import Orchestrator
from pai import reflection as M4
from pai import scheduler as M6
from pai import model_gateway as M10
from pai import affect as M5
from pai import self_model as SM
from pai import voice as M14
from pai import ear as X6
from pai import self_review as SC
from tools.check_capability import scan as CAP_SCAN

DB = Path(__file__).resolve().parent / "data" / "memory.db"


def _remind_spec(typ, arg):
    """把 CLI 的类型关键字翻成 (trig_type, spec)。见 M6 结构化 spec（ADR-015）。
    fact_exists 的参数用 '主语/谓语'（避免与字段分隔符 | 冲突）。"""
    t = typ.lower()
    if t == "manual":
        return "manual", None
    if t in ("once", "date"):
        return "date", {"kind": "oneoff", "at": arg}
    if t == "daily":
        return "condition", {"kind": "daily"}
    if t == "annual":
        dpart, _, tm = arg.partition(" ")
        month, _, day = dpart.partition("-")
        return "date", {"kind": "annual", "month": int(month), "day": int(day), "time": tm or "09:00"}
    if t == "weekly":
        wd, _, tm = arg.partition(" ")
        return "date", {"kind": "weekly", "weekday": int(wd), "time": tm or "08:30"}
    if t == "silence_days":
        return "condition", {"kind": "silence_days", "days": int(arg)}
    if t == "fact_exists":
        subj, sep, pred = arg.partition("/")
        spec = {"kind": "fact_exists"}
        if subj:
            spec["subject"] = subj
        if sep and pred:
            spec["predicate"] = pred
        return "condition", spec
    raise ValueError(f"未知提醒类型：{typ}")


def _enter_live_key():
    """--live：在本终端用 getpass 悄悄输入密钥，只写进当前进程的环境变量。
    不回显、不落盘、不进对话/记忆库/D:\\Q。退出程序即消失。安全底线见 ADR-017。"""
    provider = (os.environ.get("PAI_MODEL_PROVIDER") or "").strip().lower()
    if provider in ("", "mock"):
        provider = "openai"                       # --live 默认 OpenAI；可先设 PAI_MODEL_PROVIDER=anthropic
    os.environ["PAI_MODEL_PROVIDER"] = provider
    key_var = f"{provider.upper()}_API_KEY"
    if (os.environ.get(key_var) or "").strip() or (os.environ.get("PAI_MODEL_API_KEY") or "").strip():
        print(f"[实时模型] 已从环境变量读到密钥，直接启用 {M10.active_provider()}。")
        return
    print(f"[实时模型] 需要 {provider} 的密钥。请在下一行输入——不会显示、不保存、仅本次会话有效。")
    print("           切勿把密钥贴进对话/文件/记忆库。直接回车则退回 mock。")
    try:
        secret = getpass.getpass(f"{key_var} > ").strip()
    except (EOFError, KeyboardInterrupt):
        secret = ""
    if not secret:
        os.environ["PAI_MODEL_PROVIDER"] = "mock"
        print("[退回 mock] 未输入密钥。")
        return
    os.environ[key_var] = secret                   # 只进当前进程环境变量
    print(f"[实时模型] 已启用 {M10.active_provider()}（密钥仅存于本进程，退出即消失）。")


def _run_heard(cmd, last_out):
    """把听到的命令落到【脊柱里已经存在的动作】上。

    四个词各自对应一个本来就有键盘入口的动作，这里不新增任何能力：
    加了词却没有落地动作 = 假能力，比不加更坏（有一条测试专门钉这个）。
    """
    if cmd == X6.CMD_STOP_SPEAK:
        print("   [已掐掉]" if M14.stop() else "   现在没在念东西。")
        return
    if cmd == X6.CMD_REPEAT_LAST:
        if last_out is None:
            print("   这一轮还没有回答可念。")
            return
        r = M14.speak(last_out.get("response") or "")
        print("   [重念上一条]" if r["spoken"] else "   " + r["reason"])
        return
    if cmd in (X6.CMD_PROACTIVE_OFF, X6.CMD_PROACTIVE_ON):
        # ★只动【本进程】环境变量，重启即回到默认。读它的那一行在 pai/orchestrator.py；
        #   这里不碰那个文件 —— 它是 X8/X9 两条补丁的落点（串行锁 ADR-045）。
        want = "off" if cmd == X6.CMD_PROACTIVE_OFF else "on"
        os.environ["PAI_PROACTIVE_POLICY"] = want
        print("   [现在安静] 它不会自己开口了" if want == "off"
              else "   [恢复主动] 回到打扰政策管着的 3 次/天")
        print("   （只改本窗口，重启回到默认；政策本身一个字没动）")
        return
    print("   听到「%s」，但这个词没有落地动作 —— 不去猜它想干什么。" % cmd)


def main():
    os.environ.setdefault("PAI_TRACE", "on")
    os.environ.setdefault("PAI_A1_ACTIVITY", "on")
    if "--reset" in sys.argv and DB.exists():
        DB.unlink()
        print("[已清空记忆，重新开始]")

    if "--live" in sys.argv:
        _enter_live_key()

    mem = Memory(DB)
    orch = Orchestrator(mem)

    print(f"=== 个人AI系统 · 当前模型：{M10.active_provider()} ===")
    print(f"记忆库：{DB}")
    if M10.active_provider() == "mock":
        print("（现在是 mock 占位。要接真实模型：运行 python chat.py --live 后在本终端悄悄输入密钥；")
        print("  或自己设环境变量 PAI_MODEL_PROVIDER=openai + OPENAI_API_KEY。密钥只进环境变量，别贴进对话/代码/库。）")
    print("直接输入内容对话；:facts 看事实；:principles 看它自写的原则；")
    print(":reflect 反思一次；:remind 设一条主动提醒；:tick 模拟时钟滴答；")
    print(":inbox 看它主动推了什么；:log 看最近对话；:exit 退出。")
    print("记忆体：:facts 带编号；:history 看它改过口的旧事实；:forget 编号 忘掉一条；")
    print("        :purge 关键词 把命中的原文全库忘掉（对话/事实/主动消息/提醒删行，")
    print("               情绪线索复位，命中原则停用并置占位，改口流水只清文本不删行）——误贴密钥用它；")
    print("        :principles 带编号；:retire 编号 理由 作废一条自写原则；:backup 拍一份记忆库快照。")
    print("情感与自我：:mood 看当前心境估计（是估计不是诊断）；:self 看它的身份记录、")
    print("           以及它登记在册的【会什么 / 不会什么】（没登记的它不会假装会）；")
    print("           :revisions 看它改过哪些口、为什么改。")
    print(":remind 类型 支持 manual/once|时间、daily、annual|月-日 时:分、weekly|星期 时:分、")
    print("                  silence_days|天数、fact_exists|主语/谓语（星期0=周一）。")
    print("验证「关掉也在」：:remind 设个提醒 → :tick 触发 → :inbox 看它主动找你的话。")
    print(":council mock,mock 开两模型互审（只让分歧可见、不综合）；:council off 关。")
    print(":review on 打开自我批判（会多花一次模型调用，关掉=一个字都不改）；"
          ":review status 看上一轮它发现了什么、多花了多少钱。")
    print(":voice on 让它用本机嗓子把回答念出来（默认关；原话不出这台机器）；"
          ":voice stop 别念了。嗓子清单：python tools\\say.py --list")
    print(":ear on 打开命令级语音输入（默认关）：开着时空回车=开一次麦克风认一条命令，说完就关；"
          ":ear status 看这台机器能不能听中文（这条问话【不开麦】）。词表：" + " / ".join(X6.COMMANDS))
    print("真实模型可换 deepseek 等国产 OpenAI 兼容端点（见 README 接真实模型段）。\n")

    last_out = None      # :review status 要说清上一轮自省发现了什么
    last_review_on = None   # ★记住【那一轮】开关的状态：拿现在的状态解释过去=撒谎
    while True:
        try:
            line = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            if X6.listening_enabled():
                # ★回车 = 开一次麦克风认一条命令，认完就关。开关关着时这条分支根本进不来，
                #   空回车和以前一模一样（ADR-032：关 = 一个字都不改）。
                print("   (听着……说一条命令，或等它自己断)")
                heard = X6.listen_once()
                if heard["command"]:
                    print("   听到「%s」" % heard["command"])
                    _run_heard(heard["command"], last_out)
                elif heard["text"]:
                    # 听清了但不在词表里：把这句丢回到屏幕上认一下，然后【不入库、不当话理解】
                    print("   听到「%s」，不在命令词表里 → 这句丢掉，不存、也不猜。" % heard["text"])
                else:
                    print("   " + heard["reason"])
            continue
        if line in (":exit", ":quit", "exit", "quit"):
            break
        if line == ":facts":
            facts = mem.all_facts()
            if not facts:
                print("   (还没记住任何事实。试试「我叫阿明」或「我爷爷1959年在挨饿」)")
            for f in facts:
                print(f'   #{f["id"]} {f["subject"]} · {f["predicate"]} = {f["object"]}')
            continue
        if line.startswith(":history"):
            arg = line[len(":history"):].strip()
            subj, sep, pred = arg.partition(" ") if arg else ("", "", "")
            rows = mem.fact_history(subj or None, pred or None)
            if not rows:
                print("   (它没改过口——目前所有事实都还是第一次记住的值。)")
            for h in rows:
                print(f'   [{h["ts"]} 到 {h["replaced_ts"]}] '
                      f'{h["subject"]} · {h["predicate"]} 曾 = {h["object"]}')
            continue
        if line.startswith(":forget"):
            arg = line[len(":forget"):].strip()
            try:
                ok = mem.forget_fact(int(arg))
                print(f"   [已忘掉事实 #{arg}]" if ok else f"   [没有这条事实：{arg}]")
            except ValueError:
                print("   用法：:forget 事实编号（编号见 :facts）")
            continue
        if line.startswith(":purge"):
            pat = line[len(":purge"):].strip()
            if not pat:
                print("   用法：:purge 关键词或正则 —— 命中的用户原文全库清除（用于误贴密钥等）：")
                print("         对话/事实/主动消息/提醒整行删除，情绪线索复位，命中原则置占位并停用，")
                print("         改口流水只把文本置占位、行保留（删流水等于造假账）。")
                continue
            n = mem.purge(pat)
            extra = "、情绪线索已复位" if n["affect_reset"] else ""
            why = f'、原则理由置空 {n["blanked_reasons"]} 条' if n["blanked_reasons"] else ""
            print(f'   [已清除] 对话 {n["episodes"]} 条、事实 {n["facts"]} 条、'
                  f'主动消息 {n["outbox"]} 条、提醒 {n["triggers"]} 条{extra}；'
                  f'改口流水置空 {n["blanked_histories"]} 条{why}、原则停用 '
                  f'{n["principles_deactivated"]} 条（流水行保留，只清文本）。'
                  "建议随后 :backup 存一份。")
            continue
        if line == ":principles":
            ps = mem.get_principles()
            if not ps:
                print("   (还没有原则。先聊几句关于你的事，或输入 :reflect 触发一次反思)")
            for p in ps:
                print(f'   #{p["id"]} · {p["text"]}')
            continue
        if line.startswith(":retire"):
            arg = line[len(":retire"):].strip()
            pid, sep, why = arg.partition(" ")
            if not sep or not why.strip():
                print("   用法：:retire 原则编号 为什么作废（理由必须写，见 :principles 的编号）")
                continue
            try:
                r = mem.set_principles([{"op": "retire", "id": int(pid)}], why)
            except ValueError:
                print(f"   [没听懂] 编号要是数字：:retire 3 这条不对我")
                continue
            print(f"   [已作废 {r['retired']} 条原则]" if r["retired"] else f"   [没有这条原则：{pid}]")
            continue
        if line == ":mood":
            st = M5.read_affect(mem)
            print(f'   当前心境估计：{st["mood"]}'
                  f'（强度 {st["intensity"]:.2f}，愉悦度 {st["valence"]:+.2f}，'
                  f'线索：{"、".join(st["cues"]) or "无"}）')
            print(f'   上次更新：{st["updated_ts"] or "（还没记过）"}'
                  "　※这是系统的【功能态估计】，不是诊断，也不代表它有感")
            print("   它只影响语气轻重；对方说自己怎么样，以对方说的为准。")
            continue
        if line == ":self":
            print("   " + SM.describe(mem).replace("\n", "\n   "))
            issues = SM.consistency(mem)
            if issues:
                print(f"   [自查发现 {len(issues)} 处要留意] "
                      + "；".join(f'{i["kind"]}:{i.get("id") or i.get("ids")}'
                                  for i in issues[:6]))
            reg, pend, _violations = CAP_SCAN()
            print("   " + SM.capability_block(reg, pend).replace("\n", "\n   "))
            continue
        if line == ":revisions":
            log = SM.revision_log(mem)
            if not log:
                print("   (它还没改过自己写下的原则。改口要写理由，见 :principles/:retire)")
            for r in log:
                print(f'   [{r["ts"]}] #{r["id"]} {r["kind"]}：'
                      f'{r["old_text"]} → {r["new_text"] or "（作废）"}')
                print(f'       理由：{r["reason"]}')
            continue
        if line == ":backup":
            import datetime as _dt
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = DB.parent / "backups" / f"memory-{stamp}.db"
            path = mem.backup(dest)
            print(f"   [已备份记忆库] {path}")
            continue
        if line == ":reflect":
            r = M4.reflect(mem)
            print(f'   [反思完成] 扫描{r["scanned"]}条新对话，'
                  f'新增事实{r["new_facts"]}条、更新{r["updated_facts"]}条，'
                  f'新增原则{r["new_principles"]}条（当前共{r["principles_total"]}条）')
            continue
        if line == ":remind":
            # 语法： :remind <类型> | <参数> | 消息 | 正当理由
            # 类型: manual | once(一次性,参数=时间) | daily | annual(参数=月-日 时:分)
            #       weekly(参数=星期0-6 时:分) | silence_days(参数=天数) | fact_exists(参数=主语|谓语)
            try:
                raw = input(
                    "   格式 类型|参数|消息|理由  "
                    "(manual/once/daily/annual/weekly/silence_days/fact_exists) > ")
                parts = [p.strip() for p in raw.split("|")]
                typ = parts[0]
                arg = parts[1] if len(parts) > 1 else ""
                msg = parts[2] if len(parts) > 2 else ""
                reason = parts[3] if len(parts) > 3 else ""
                trig_type, spec = _remind_spec(typ, arg)
                tid = M6.register_trigger(mem, trig_type, msg, reason, spec=spec)
                print(f"   [已登记提醒 #{tid}] {msg}  （理由：{reason}）")
            except (ValueError, IndexError) as e:
                print(f"   [登记失败] {e}  ——主动推送必须写明正当理由。")
            continue
        if line == ":tick":
            import datetime as _dt
            fired = orch.tick(_dt.datetime.now())
            if not fired:
                print("   [滴答] 此刻没有到期的主动推送。")
            for r in fired:
                print(f'   [主动推送 #{r["outbox_id"]}]\n{r["message"]}')
            continue
        if line == ":inbox":
            items = mem.read_outbox()
            if not items:
                print("   (收件箱空。它还没主动找过你。:remind 设一条 → :tick 触发)")
            for o in items:
                tag = "已读" if o["is_read"] else "未读"
                print(f'   [{o["ts"]}] ({tag})\n{o["message"]}\n')
            continue
        if line == ":log":
            for e in mem.recent_episodes(20):
                print(f'   [{e["ts"]}] {e["role"]}: {e["content"][:50]}')
            continue
        if line.startswith(":council"):
            arg = line[len(":council"):].strip()
            if arg in ("", "off"):
                os.environ.pop("PAI_COUNCIL_MODELS", None)
                print("   [议会关闭] 回到单模型。")
            else:
                os.environ["PAI_COUNCIL_MODELS"] = arg
                print(f"   [议会开启] 模型={arg}"
                      "（需 >=2 个才真正互审；分歧只提示、不综合，判断权在你）。")
            continue

        if line.startswith(":voice"):
            arg = line[len(":voice"):].strip().lower()
            if arg in ("on", "开", "1"):
                M14.set_enabled(True)
                print("   [开口了] 之后每条回答都用本机嗓子念出来"
                      "（原话和回答都不出这台机器）。试一句：")
                print("   " + str(M14.speak("在的，我在。")["reason"] or "已念出"))
            elif arg in ("off", "关", "0"):
                M14.set_enabled(False)
                M14.stop()
                print("   [闭嘴了] 回到只看字，一个字都不改。")
            elif arg == "stop":
                print("   [已掐掉]" if M14.stop() else "   现在没在念东西。")
            elif arg in ("", "status"):
                print("   [开口状态] %s · 后端 %s%s" % (
                    "开着" if M14.speaking_enabled() else "关着（默认）",
                    M14.backend(),
                    " · 正在念" if M14.is_speaking() else ""))
                print("   用法：:voice on 让它念 · :voice off 收声 · :voice stop 别念了")
            else:
                print("   没听懂。:voice on / :voice off / :voice stop")
            continue

        if line.startswith(":ear"):
            arg = line[len(":ear"):].strip().lower()
            if arg in ("on", "开", "1"):
                X6.set_enabled(True)
                p = X6.probe()
                if p["can_hear"]:
                    print("   [开麦了] 空回车 = 说一条命令（只开一次，说完就关）。")
                    print("   这台机器装了中文识别器：%s" % "、".join(p["cultures"]))
                    print("   词表（说整条，别只说半截）：" + " / ".join(X6.COMMANDS))
                    print("   ★认得准不准要你的嗓子来验，测试代不了；听不认就 :ear off。")
                else:
                    X6.set_enabled(False)
                    print("   [开不了] " + p["reason"])
                    print("   这条路是 1a（零依赖、只用本机自带识别器），不改后端、不装东西、不联网。")
            elif arg in ("off", "关", "0"):
                X6.set_enabled(False)
                print("   [收麦了] 麦克风一次也没开过 —— 空回车回到原来的样子。")
            elif arg in ("", "status"):
                p = X6.probe()
                print("   [耳朵状态] %s · 后端 %s · 超时 %d 秒（%s）" % (
                    "开着" if X6.listening_enabled() else "关着（默认）",
                    X6.backend(), X6.clamp_timeout(os.environ.get(X6.ENV_TIMEOUT)),
                    "能听中文" if p["can_hear"] else "听不了中文：" + p["reason"]))
                print("   词表：" + " / ".join(X6.COMMANDS))
                print("   用法：:ear on 开 · :ear off 关 · 开关开着时空白回车=听一次")
            else:
                print("   没听懂。:ear on / :ear off / :ear status")
            continue

        if line.startswith(":review"):
            arg = line[len(":review"):].strip().lower()
            if arg in ("on", "开", "1"):
                os.environ[SC.ENV_SELF_REVIEW] = "1"
                print("   [自省开着] 它会先审自己一遍再说；碰到越界句会换成拒绝话，"
                      "措辞有问题会【再多调一次模型】（成本与延迟都翻倍）。"
                      "每次多花的钱写在 :review status。")
            elif arg in ("off", "关", "0"):
                os.environ[SC.ENV_SELF_REVIEW] = "0"
                print("   [自省关着] 一个字都不改——它发现的越界句也只进 trace，"
                      "不换句子、不多调一次模型。")
            elif arg in ("", "status"):
                sr = (last_out or {}).get("trace", {}).get("self_review")
                print("   [开关] %s（变量名 %s）" % (
                    "开着" if os.environ.get(SC.ENV_SELF_REVIEW) == "1" else "关着（默认）",
                    SC.ENV_SELF_REVIEW))
                if sr is None:
                    print("   这一轮还没跑过对话，没有可报的自省结果。")
                else:
                    print("   上一轮（那时开关【%s】）：判定 %s · 发现问题 %d 处 · "
                          "真的改写 %s · 多调 %d 次（成本 %s / %d tokens）" % (
                              "开着" if last_review_on else "关着",
                              sr["decision"], len(sr["issues"]),
                              "是" if sr["applied"] else "否",
                              sr["calls"], sr["cost"], sr["tokens"]))
                    for i in sr["issues"]:
                        print("     - [%s] %s" % (i["severity"], i["detail"]))
                    if sr["issues"] and not sr["applied"]:
                        print("     %s" % (
                            "（开关开着却【没改动一个字】：它只发现了问题，没找到能改好的写法——"
                            "改坏了要能退回，所以宁可原样留着）"
                            if last_review_on else
                            "（那一轮开关关着：上面这些【只记录、没改动一个字】）"))
                print("   用法：:review on 开 · :review off 关")
            else:
                print("   没听懂。:review on / :review off / :review status")
            continue

        last_review_on = os.environ.get(SC.ENV_SELF_REVIEW) == "1"
        try:
            out = orch.handle(line)
        except Exception as e:
            print("   [回答失败] %s：%s" % (type(e).__name__, str(e)[:200]))
            print("   （这一轮不写记忆，接着聊。）\n")
            continue
        last_out = out
        print("AI >", out["response"], "\n")
        if M14.speaking_enabled():
            # 不等念完：边念边聊。下一条 :voice stop 或 :voice off 能掐掉。
            try:
                M14.speak(out["response"])
            except Exception as e:
                print("   [朗读失败] %s（字还在，继续聊。）" % type(e).__name__)

    M14.stop()          # 人都走了，别还在念
    mem.close()
    print(f"\n已退出。你的记忆已存进：{DB}")


if __name__ == "__main__":
    main()
