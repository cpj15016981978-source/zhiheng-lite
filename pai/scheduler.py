"""M6 调度器 (Scheduler) — 见《00_协作规程与接口契约.txt》PART B2 (M6) 与 ADR-015。

Phase3 骨架 + Phase3.5 增强（吸收了 DeepSeek Harness 的 M6 草案，见 ADR-015 第4节）：
    register_trigger(mem, type, msg, justify_reason, spec)  登记（强制带正当理由）
    on_tick(mem, now, ctx=None, catch_up=False)             外部时钟定期调用，返回该触发的触发器

四类设计升级：
  ① 结构化 spec：date 支持 oneoff/annual/monthly/weekly；condition 支持
     daily/silence_days/fact_exists/no_episode_since。仍兼容旧的字符串 spec
     （"2026-10-21 09:00" / "daily"）——老库、老调用不改。
  ② 时区感知：统一转成带偏移的 aware datetime 再比较，禁止 naive 参与比较
     （跨时区 bug 的唯一来源）。naive 输入按本地时区 LOCAL_TZ 补偏移。
  ③ catch_up 默认 False：错过不补发（补发会把“昨天的生日”变成今天的骚扰）。
  ④ on_tick 可选 ctx：ctx=None 时条件型一律不触发——fail-safe，宁可不醒不可乱醒。

设计约束（续 ADR-014/015）：
- 零依赖、离线可验证：on_tick 显式接收 now，不起真后台线程。常驻时由外层循环
  定期 on_tick(now=当前时间)，接口不变。
- 反骚扰护栏落在登记处：justify_reason 为空即拒（M11“正当理由”约束的体现）。
- 时间解析失败/未知 kind：跳过不触发（宁可漏触发，不可瞎触发打扰用户）。
"""
import datetime
import json

# 本系统单时区自用；写死规则防止以后上 VPS 出错（ADR-015）。
LOCAL_TZ = datetime.timezone(datetime.timedelta(hours=8))

# 能力契约登记（ADR-036），字段口径唯一住址 = tools/check_capability.py。
# 既成常开登记见 ADR-046：M6 从来没有开关，on_tick 一直在被 M1.tick 调。
CAPABILITY = {
    "name": "M6 提醒调度",
    "switch_env": "NONE",
    "default_on": True,
    # register_trigger 把用户口述的提醒原文写进 triggers 的 message_template/
    # justify_reason/spec 三列，那一行已在 _TEXT_HOSTS 登记；认领只写时间戳列。
    "stores_user_text": True,
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
    "network": False,
    "degrade": ("spec 解析失败/未知 kind = 这条跳过不触发（宁漏不瞎）；ctx=None 时条件型一律"
                "不触发；写库走 M2 的 add_trigger/mark_trigger_fired 入口，不自己拼 SQL。"
                "★时区写死 +08:00（LOCAL_TZ），换时区=提醒时刻会错"),
    "probe_cost": "只读模块常量，不建库、不起线程、不联网",
    "approval": ("未获单独批准：Phase3 交付时起既成常开，属【写库】（triggers 三列）；"
                 "要补开关或停掉，需 Boss 另行定（ADR-046、ADR-044）"),
}


# ---------------------------------------------------------------- 时间工具

def _aware(dt):
    """把 naive datetime 补成本地时区；aware 原样返回。比较前必经此步。"""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=LOCAL_TZ)


def _parse(dt_str):
    """尽力把字符串解析成 datetime（带偏移优先）；失败返回 None。"""
    if not dt_str:
        return None
    s = str(dt_str).strip().replace("Z", "+00:00")
    try:                                    # 先试 ISO8601（含 +08:00 偏移）
        return datetime.datetime.fromisoformat(s)
    except ValueError:
        pass
    s2 = s.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s2, fmt)
        except ValueError:
            continue
    return None


def _day(dt):
    dt = _aware(dt)
    return dt.date().isoformat() if dt else None


def _hm(spec_dict, key="time"):
    """把 spec 里的 "HH:MM" 解析成 (hour, minute)，缺省 00:00。"""
    raw = (spec_dict.get(key) or "00:00").strip()
    try:
        h, m = raw.split(":")
        return int(h), int(m)
    except (ValueError, AttributeError):
        return 0, 0


# ---------------------------------------------------------------- spec 归一化

def _coerce_input_spec(typ, spec):
    """把 register 传入的 spec（字符串/dict/None）归一成 dict，供校验与存储。
    旧字符串写法自动映射成结构化 dict，保证新老接口都能用。"""
    if typ == "manual":
        return {"kind": "manual"}
    if isinstance(spec, dict):
        return dict(spec)
    if isinstance(spec, str) and spec.strip():
        txt = spec.strip()
        try:                                # 允许直接传 JSON 字符串
            obj = json.loads(txt)
            if isinstance(obj, dict):
                return obj
        except (ValueError, TypeError):
            pass
        if typ == "date":
            return {"kind": "oneoff", "at": txt}          # 旧：裸 ISO 时间=一次性
        if typ == "condition":
            if txt.lower() == "daily":
                return {"kind": "daily"}
            return {"kind": txt.lower()}
    return None


def register_trigger(mem, type, message_template, justify_reason, spec=None):
    """登记一个触发器。返回 id。type ∈ {manual, date, condition}。
    spec 可为结构化 dict（推荐）或旧式字符串：
      date:      {"kind":"oneoff","at":"2026-11-01T09:00:00+08:00"}
                 {"kind":"annual","month":4,"day":5,"time":"09:00"}
                 {"kind":"monthly","day":1,"time":"08:30"}
                 {"kind":"weekly","weekday":1,"time":"08:30"}      # 1=周一
      condition: {"kind":"daily"}
                 {"kind":"silence_days","days":3}
                 {"kind":"fact_exists","subject":"母亲","predicate":"生日"}
                 {"kind":"no_episode_since","days":7}
      旧字符串： date→"2026-11-01 09:00"；condition→"daily"。
    强制 justify_reason 非空（由 M2.add_trigger 把关）。"""
    if type not in ("manual", "date", "condition"):
        raise ValueError(f"未知触发器类型：{type}（应为 manual/date/condition）")

    norm = _coerce_input_spec(type, spec)
    if type != "manual":
        if not norm or not norm.get("kind"):
            raise ValueError(f"{type} 触发器缺少 spec.kind")
        _validate_spec(type, norm)

    # 存 JSON（列是 TEXT，向后兼容：读取端 json 失败会退回旧字符串处理）。
    stored = json.dumps(norm, ensure_ascii=False) if norm else spec
    return mem.add_trigger(type, message_template, justify_reason, spec=stored)


_DATE_KINDS = {"oneoff", "annual", "monthly", "weekly"}
_COND_KINDS = {"daily", "silence_days", "fact_exists", "no_episode_since"}


def _validate_spec(typ, norm):
    kind = norm.get("kind")
    if typ == "date":
        if kind not in _DATE_KINDS:
            raise ValueError(f"date 不支持的 kind：{kind}（可用 {sorted(_DATE_KINDS)}）")
        if kind == "oneoff":
            if _parse(norm.get("at")) is None:
                raise ValueError("oneoff 需要可解析的 at，例如 2026-11-01T09:00:00+08:00")
        if kind == "annual" and not (norm.get("month") and norm.get("day")):
            raise ValueError("annual 需要 month 与 day")
        if kind == "monthly" and not norm.get("day"):
            raise ValueError("monthly 需要 day")
        if kind == "weekly" and norm.get("weekday") is None:
            raise ValueError("weekly 需要 weekday(0=周一..6=周日)")
    elif typ == "condition":
        if kind not in _COND_KINDS:
            raise ValueError(f"condition 不支持的 kind：{kind}（可用 {sorted(_COND_KINDS)}）")
        if kind in ("silence_days", "no_episode_since") and not norm.get("days"):
            raise ValueError(f"{kind} 需要 days")
        if kind == "fact_exists" and not (norm.get("subject") or norm.get("predicate")):
            raise ValueError("fact_exists 需要 subject 或 predicate")


def _read_spec(trig):
    """把库里存的 spec 反解成 dict（兼容旧字符串与 'daily'）。"""
    raw = trig.get("spec")
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except (ValueError, TypeError):
        pass
    # 旧格式回退
    if trig["type"] == "date":
        return {"kind": "oneoff", "at": str(raw)}
    if str(raw).lower() == "daily":
        return {"kind": "daily"}
    return {"kind": str(raw).lower()}


# ---------------------------------------------------------------- 到期判定

def _due(trig, now, ctx):
    """判断单个触发器此刻是否该触发。now 为 aware datetime。
    ctx 为 None 时，依赖上下文的条件一律 False（fail-safe）。"""
    now = _aware(now)
    t = trig["type"]
    spec = _read_spec(trig)
    kind = spec.get("kind")
    last = _aware(_parse(trig["last_fired_ts"])) if trig["last_fired_ts"] else None

    if t == "manual":
        return True

    if t == "date":
        if kind == "oneoff":
            target = _aware(_parse(spec.get("at")))
            if target is None or last is not None:
                return False
            return now >= target                     # 一次性：过了点且没发过
        # 周期性 date：应触发 当且仅当 目标时刻≤now 且 今日目标≠上次触发日
        target_today = _next_annual_or_recurring_now(spec, now)
        if target_today is None:
            return False
        if last is not None and _day(last) == _day(now):
            return False                             # 当天已发过一次
        return now >= target_today

    if t == "condition":
        if kind == "daily":
            return last is None or _day(last) != _day(now)
        if ctx is None:
            return False                             # 条件型缺上下文 → 不触发
        if kind == "silence_days":
            lu = _aware(_parse(ctx.get("last_user_ts")))
            if lu is None:
                return False
            quiet = (now - lu).days >= int(spec.get("days", 0))
            return quiet and (last is None or _day(last) != _day(now))
        if kind == "no_episode_since":
            le = _aware(_parse(ctx.get("episodes_after_ts")))
            if le is None:
                return False
            return (now - le).days >= int(spec.get("days", 0)) and (
                last is None or _day(last) != _day(now))
        if kind == "fact_exists":
            if last is not None:
                return False                         # 一次性里程碑
            subj, pred = spec.get("subject"), spec.get("predicate")
            for f in ctx.get("facts", []):
                if (subj in (None, f.get("subject"))) and (pred in (None, f.get("predicate"))):
                    return True
            return False
    return False


def _next_annual_or_recurring_now(spec, now):
    """返回本周期内“目标时刻”的 aware datetime；now 不在目标日返回 None。"""
    kind = spec.get("kind")
    hour, minute = _hm(spec)
    if kind == "annual":
        if (now.month, now.day) != (int(spec["month"]), int(spec["day"])):
            return None
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if kind == "monthly":
        if now.day != int(spec["day"]):
            return None
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if kind == "weekly":
        if now.weekday() != int(spec["weekday"]):
            return None
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return None


def _is_one_shot(trig, spec):
    """触发后是否失活：manual / oneoff / fact_exists 一次性；其余循环保留。"""
    t = trig["type"]
    if t == "manual":
        return True
    if t == "date":
        return spec.get("kind") == "oneoff"
    if t == "condition":
        return spec.get("kind") == "fact_exists"
    return False


def _claim(mem, trig, now_aware):
    """把某触发器标记为已触发（一次性即失活，循环保留并记当日）。now_aware 为 aware datetime。"""
    spec = _read_spec(trig)
    now_str = now_aware.isoformat(timespec="seconds")
    mem.mark_trigger_fired(trig["id"], now_str, deactivate=_is_one_shot(trig, spec))
    trig["last_fired_ts"] = now_str


def claim_fired(mem, trig, now):
    """公开：调用方在【发送成功之后】再认领该触发器（commit-after-send）。
    配合 on_tick(mark=False) 实现“发送失败可重试”，见 ADR-018。now 传 datetime 或 ISO 串。"""
    if isinstance(now, str):
        now = _parse(now)
    _claim(mem, trig, _aware(now))


def on_tick(mem, now, ctx=None, catch_up=False, mark=True):
    """扫描所有 active 触发器，返回此刻该触发的 TriggerEvent 列表。
    now 可为 datetime 或 ISO 字符串。
    ctx（可选）：{"last_user_ts","episodes_after_ts","facts"} —— 条件型依赖它；
      为 None 时依赖上下文的条件一律不触发（fail-safe）。
    catch_up（默认 False）：一次性 oneoff 若目标日已过且不等于今天，默认不补发。
    mark（默认 True）：是否顺手认领(写 last_fired_ts/失活)。设 False 则【只选不认领】，
      由调用方发送成功后再 claim_fired —— 这样发送失败不会永久丢消息(见 ADR-018)。"""
    if isinstance(now, str):
        now = _parse(now)
    if now is None:
        return []
    now_aware = _aware(now)

    fired = []
    for trig in mem.list_triggers(active_only=True):
        spec = _read_spec(trig)
        # catch_up=False 时，挡掉“目标日在过去且从未触发”的一次性 oneoff（不补历史）。
        if (not catch_up) and trig["type"] == "date" and spec.get("kind") == "oneoff":
            target = _aware(_parse(spec.get("at")))
            already_fired = bool(trig["last_fired_ts"])
            if (not already_fired) and target is not None and _day(target) != _day(now_aware):
                continue
        if not _due(trig, now_aware, ctx):
            continue
        if mark:
            _claim(mem, trig, now_aware)
        fired.append(trig)
    return fired
