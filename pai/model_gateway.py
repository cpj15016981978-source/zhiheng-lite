"""M10 模型网关 — 见《00_协作规程与接口契约.txt》PART B2 (M10)。

签名与 Phase1 mock 时【完全一致】：complete(prompt, models=..., opts=...)，编排层(M1)
与其余模块无需改动（ADR-006/ADR-017）。区别只在内部实现：

  · 默认仍是 mock——不设 PAI_MODEL_PROVIDER 就永远走 mock，离线/单测不联网。
  · 显式设 PAI_MODEL_PROVIDER=openai|anthropic 才打真实 API（opt-in，防误触网）。
  · 【密钥只从环境变量读，绝不硬编码、绝不入库、绝不打进日志或异常信息】。
    这把线是安全底线：任何异常文本里都不能出现 Key 的值。
  · 零第三方依赖：只用标准库 urllib + json（R2）。境外端点能否连通取决于
    本机网络/代理；urllib 默认会读 HTTPS_PROXY 等环境变量。

环境变量（都可缺省）：
  PAI_MODEL_PROVIDER   = mock(默认) | openai | anthropic | deepseek
  PAI_MODEL_NAME       = 模型名，缺省按 provider 取 DEFAULT_NAMES
  OPENAI_API_KEY       = openai 必需（或 PAI_MODEL_API_KEY 兜底）
  ANTHROPIC_API_KEY    = anthropic 必需（或 PAI_MODEL_API_KEY 兜底）
  DEEPSEEK_API_KEY     = deepseek 必需（或 PAI_MODEL_API_KEY 兜底）
  PAI_OPENAI_BASE_URL  = 缺省 https://api.openai.com/v1
  PAI_ANTHROPIC_BASE_URL = 缺省 https://api.anthropic.com/v1
  PAI_DEEPSEEK_BASE_URL  = 缺省 https://api.deepseek.com/v1（也可指向 Kimi/千问等兼容端点）
  PAI_MODEL_TIMEOUT    = 秒，缺省 60

成本记账：
  PRICING 是唯一的价格表（人民币/百万 token），cost 字段现在是真的钱，
  不再是占位的 0。mock 恒为 0。
  ★真出网之前必须先报账：estimate()/guard_spend() 把「这次最多花多少」打在派发之前，
  价格表里没有这个模型 = 这笔钱估不出来 = 直接拒发、一次都不出门（ADR-080）。
  「没算钱」和「没花钱」是两件事，所以估不出来时 cost_cap=None，不是 0。
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import offline as OFF
from . import network_policy as NP
from . import exec_guard as EG

__all__ = ["complete", "active_provider", "price_of", "PRICING", "estimate",
           "guard_spend", "ModelGatewayError"]

# 能力契约登记（ADR-036），字段口径唯一住址 = tools/check_capability.py。
# M10 是唯一【真会出网】的一层：契约⑦/⑧ 要 approval + 真实 ADR 号，这里都齐。
ENV_PROVIDER = "PAI_MODEL_PROVIDER"

CAPABILITY = {
    "name": "M10 模型网关",
    "switch_env": ENV_PROVIDER,
    # 不设这个环境变量 = 永远走 mock = 一次都不出门。ADR-017 把它定成 opt-in，
    # 就是为了单测/离线不会因为"谁 export 了 Key"而去烧额度。
    "default_on": False,
    # 出门时 prompt 里带着对话原文，但它自己不写任何表（落库是 M1/M2 的事）。
    "stores_user_text": False,
    "network": True,
    "degrade": ("没 Key / 端点不通 / 超时 = 抛 ModelGatewayError，文本里绝不带 Key 值，"
                "由上层决定退回 mock 还是报错；★价格表里没有这个模型 = 估不出这笔钱 = "
                "派发前直接拒发（ADR-080），拒发生在任何字节出门之前。"
                "这条闸门【没有开关】：不设任何环境变量也照样拦，要放行只有一个办法 = "
                "在 PRICING 登记实价（唯一住址），别再写第二份估账"),
    "probe_cost": "★联网且真扣费：--probe / --live 必须由 Boss 当次明确要求，我不自发起",
    "approval": ("opt-in 与密钥红线由 ADR-017 定（2026-10-20）；端点/模型名只抄验通记录，"
                 "见 ADR-042；每次真出网必须由 Boss 当次明确要求，见 ADR-044；"
                 "「调用前先报账、估不出就拒」从只存在于外部实验室改成网关里常驻这条，"
                 "由 Boss 2026-10-25 点头、ADR-080 立规（2026-10-25）"),
}

DEFAULT_NAMES = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-sonnet-latest",
    "deepseek": "deepseek-chat",
    "aggregator": "openai/gpt-4o-mini",
    "siliconflow": "Qwen/Qwen3-VL-32B-Instruct",
}
ANTHROPIC_VERSION = "2023-06-01"

# OpenAI 兼容端点：openai / deepseek / aggregator 共用一套调用代码。
# aggregator 的 base 用 PAI_AGGREGATOR_BASE_URL 覆盖（OpenRouter、硅基流动、OneAPI 等）。
OPENAI_LIKE = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "aggregator": "https://openrouter.ai/api/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
}
LOCAL_PROVIDERS = {"ollama"}

_LAST_OLLAMA_METRICS = {}


def last_metrics():
    return dict(_LAST_OLLAMA_METRICS)

# 价格表：(输入价, 输出价)，单位【人民币元 / 百万 token】。
# 除 MiniMax 那两行是控制台抄来的实价外，其余是 2026-10 的量级估算，不是报价；
# 要改只改这里，别散落在各函数里。
# 键是 (provider, 模型名)；未登记的组合按 0 计并标 priced=False。
PRICING = {
    ("openai", "gpt-4o-mini"): (1.1, 4.4),
    ("anthropic", "claude-3-5-sonnet-latest"): (21.9, 109.5),
    ("deepseek", "deepseek-chat"): (1.0, 4.0),
    ("deepseek", "deepseek-reasoner"): (2.0, 8.0),
    # MiniMax：Boss 2026-10-22 从控制台抄的实付价。M3 那行是【永久五折后】(标价 4.2/16.8)。
    # 两行都只对 ≤512k 输入那一档成立；>512k 整档翻倍，本表不分档，真到那个量级要重算。
    # 现在跑的是 Flash（启动器 tools/minimax_quickstart.py 的 MODEL_NAME 决定）。
    ("openai", "MiniMax-M3.1-Flash"): (1.0, 4.0),
    ("openai", "MiniMax-M3"): (2.1, 8.4),
    # 本地模型：有价格行但为 0，账单上能明确看到“不是漏算”。
    ("ollama", "qwen3-4b-instruct:latest"): (0.0, 0.0),
    ("ollama", "qwen3:0.6b"): (0.0, 0.0),
    ("ollama", "qwen2.5:1.5b"): (0.0, 0.0),
    ("ollama", "deepseek-r1:1.5b"): (0.0, 0.0),
    # 硅基流动视觉模型（2026-11 控制台公开价，单位人民币 / 百万 token）。
    # vision.py 目前不走 M10 报账闸门；这两行先给账面对照，不把未登记说成免费。
    ("siliconflow", "Qwen/Qwen3-VL-32B-Instruct"): (1.0, 4.0),
    ("siliconflow", "Qwen/Qwen3-VL-8B-Instruct"): (0.5, 2.0),
}


def _aggregator_prices():
    """聚合平台模型价格用 JSON 显式登记，避免“未知价格也放行”。

    PAI_AGGREGATOR_PRICES_JSON={"gpt-4o-mini":[1.1,4.4]}
    """
    raw = os.environ.get("PAI_AGGREGATOR_PRICES_JSON") or ""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    out = {}
    for name, pair in (data or {}).items():
        try:
            out[("aggregator", str(name))] = (float(pair[0]), float(pair[1]))
        except Exception:
            continue
    return out


def price_of(provider, model, tokens_in, tokens_out):
    """按价格表算这次调用花了多少钱（人民币）。

    返回 (cost, priced)。priced=False 表示价格表里没有这个模型——
    调用方应当把它当成"未知"，而不是"免费"。
    """
    row = _price_row(provider, model)
    if row is None:
        return 0.0, False
    p_in, p_out = row
    cost = tokens_in / 1e6 * p_in + tokens_out / 1e6 * p_out
    return round(cost, 6), True


# 输出 token 的封顶假设：openai 兼容那条调用不带 max_tokens（见 _openai_complete），
# 真要吐多少在出门【之前】不可知。报账必须给一个数，就按这一档封顶，并在读数里
# 标明它是假设值 —— 这个数字不是报价，别当实付抄进任何表。
ASSUMED_MAX_OUT = 1024


def _price_row(provider, model):
    """取 (provider, 模型名) 那一行价，查的是【归一后的键】：忽略首尾空白与大小写。

    为什么要归一：PRICING 的键本来就大小写混用（"gpt-4o-mini" / "MiniMax-M3.1-Flash"），
    而模型名来自环境变量 PAI_MODEL_NAME，人写成大写就查不到 => priced=False。
    P19 那版「估不出照样放行」遇上了这个，就成了上限永远估不出、钱照样出门。
    归一只统一【同一个名字的写法】，不替没登记的模型编一个价：未知仍是未知。
    """
    if not model:
        return None
    want = (str(provider).strip().lower(), str(model).strip().lower())
    for (p, m), price in PRICING.items():
        if (str(p).strip().lower(), str(m).strip().lower()) == want:
            return price
    if want[0] == "aggregator":
        for (p, m), price in _aggregator_prices().items():
            if (str(p).strip().lower(), str(m).strip().lower()) == want:
                return price
    return None


def estimate(prompt, provider, model, opts=None):
    """出门前把这次【最多花多少】算清楚。返回的读数里不含任何 Key。

    · tokens_in_cap：按 1 字符 = 1 token 封顶估。中文接近真值，英文偏高 = 宁高不低。
    · tokens_out_cap：opts.max_tokens 有就用它；没有按 ASSUMED_MAX_OUT 封顶，
      并用 assumed_out=True 标明这是假设。
    · cost_cap：priced=False 时是 None（估不出），不是 0——0 会被读成"没花钱"。
    """
    tokens_in_cap = len(prompt or "")
    max_out = int((opts or {}).get("max_tokens") or 0)
    assumed_out = max_out <= 0
    tokens_out_cap = ASSUMED_MAX_OUT if assumed_out else max_out
    row = _price_row(provider, model)
    priced = row is not None
    if priced:
        p_in, p_out = row
        cost_cap = round(tokens_in_cap / 1e6 * p_in + tokens_out_cap / 1e6 * p_out, 6)
    else:
        cost_cap = None
    return {"provider": provider, "model": model,
            "tokens_in_cap": tokens_in_cap, "tokens_out_cap": tokens_out_cap,
            "assumed_out": assumed_out, "cost_cap": cost_cap, "priced": priced,
            "line": _spend_line(provider, model, tokens_in_cap, tokens_out_cap,
                                assumed_out, cost_cap, priced)}


def _spend_line(provider, model, tokens_in_cap, tokens_out_cap, assumed_out,
                cost_cap, priced):
    """报账那一行的排版【唯一住址】；别在 UI 或 CLI 里另写一行。"""
    out_text = "%d%s" % (tokens_out_cap, "（假设上限）" if assumed_out else "")
    money = ("≤%.6f 元" % cost_cap) if priced else "估不出（价格表里没有这个模型）"
    return ("[报账] provider=%s model=%s 输入≤%d token 输出≤%s token => %s"
            % (provider, model, tokens_in_cap, out_text, money))


def guard_spend(prompt, provider, model, opts=None):
    """真出网之前的闸门：先报账，报不出来就直接拒发。常开、没有开关（ADR-080）。

    priced=False => 抛 ModelGatewayError，一个字节都不出门。理由：契约「花钱前先报账，
    没报=没授权」以前只存在于 DS 实验室，靠我每轮人工复跑核对；搬进这里之后它是常驻的。
    mock 不经此函数（mock 恒 0 元，不需要授权）。
    """
    est = estimate(prompt, provider, model, opts)
    print(est["line"], flush=True)
    if not est["priced"]:
        raise ModelGatewayError(
            "拒发：价格表里没有 %s/%s，这笔钱估不出来 => 没报账就没授权，一次都不出门。"
            "要用它，先在 PRICING（唯一住址）登记实价。" % (provider, model))
    return est


class ModelGatewayError(RuntimeError):
    """网关层错误。文本里绝不携带密钥值。"""


def _env(name, default=None):
    v = os.environ.get(name)
    return v if (v is not None and v.strip() != "") else default


def _resolve_default_provider():
    """缺省走 mock；只有显式设 PAI_MODEL_PROVIDER 才可能打真实 API。"""
    return (_env(ENV_PROVIDER, "mock") or "mock").strip().lower()


def active_provider():
    """返回当前会走的 provider 名，供 UI/日志显示（不含任何 Key）。"""
    p = _resolve_default_provider()
    if p == "mock":
        return "mock"
    name = _env("PAI_MODEL_NAME", DEFAULT_NAMES.get(p, ""))
    return f"{p}:{name}" if name else p


def _explicit_provider():
    """PAI_MODEL_PROVIDER 显式且不是 mock 才算出网开关。"""
    raw = (_env(ENV_PROVIDER, "") or "").strip().lower()
    return raw if raw and raw != "mock" else ""


def resolve_model(spec):
    """'openai' -> (openai, env/default 名)；'openai:MiniMax-M3' -> (openai, MiniMax-M3)。"""
    text = str(spec or "").strip()
    if ":" in text:
        provider, model = text.split(":", 1)
        provider, model = provider.strip().lower(), model.strip()
    else:
        provider, model = text.lower(), ""
    if provider == "mock":
        return "mock", "mock"
    if provider not in OPENAI_LIKE and provider != "anthropic" and provider not in LOCAL_PROVIDERS:
        raise ModelGatewayError(
            "未知 provider：%s（应为 mock/%s/anthropic/ollama）"
            % (provider, "/".join(sorted(OPENAI_LIKE))))
    if not model:
        model = _env("PAI_MODEL_NAME", DEFAULT_NAMES.get(provider, ""))
    return provider, model


def _complete_many(prompt, models=None, opts=None, on_delta=None, on_reasoning=None):
    """对每个 model 生成一次补全。返回 [{model_id, model, output, cost, tokens,
    tokens_in, tokens_out, priced, latency}]。
    models=None 时按环境变量解析默认 provider。签名冻结，续 Phase1 契约。"""
    if models is None:
        models = (_resolve_default_provider(),)
    if not _explicit_provider():
        for m in models:
            provider, _name = resolve_model(m)
            if provider not in ("mock",) and provider not in LOCAL_PROVIDERS:
                raise ModelGatewayError(
                    "未显式设置 PAI_MODEL_PROVIDER：默认档不出网（含 provider:model）")
    outs = []
    for m in models:
        t0 = time.time()
        est = None
        provider, name = resolve_model(m)
        # 离线闸门收口到 OFF（单一入口）：provider 白名单判定交给 offline.py 注册表，
        # 网关不再自己写 if enabled() + provider not in LOCAL_PROVIDERS。
        OFF.assert_allowed(OFF.provider_service(provider), ModelGatewayError,
                           "云端 provider %s" % provider)
        if provider == "mock":
            output, t_in, t_out = _mock_complete(prompt), 0, 0
            if on_delta:
                on_delta(output)
            cost, priced = 0.0, True          # mock 是真的零成本，不是"没登记"
        elif provider in OPENAI_LIKE or provider == "anthropic":
            # ★出门前先报账（ADR-080）：估不出 = 直接拒发，一个字节都不出门。
            est = guard_spend(prompt, provider, name, opts)
            if provider in OPENAI_LIKE:
                if on_delta is None:
                    output, t_in, t_out = _openai_complete(provider, prompt, opts, model=name)
                else:
                    output, t_in, t_out = _openai_complete(
                        provider, prompt, opts, model=name, on_delta=on_delta,
                        on_reasoning=on_reasoning)
            else:
                output, t_in, t_out = _anthropic_complete(prompt, opts, model=name)
                if on_delta:
                    on_delta(output)
            cost, priced = price_of(provider, name, t_in, t_out)
            if on_delta and provider in OPENAI_LIKE and t_in == 0 and t_out == 0:
                # 流式响应常不带 usage：按出门前的成本上限记，绝不当 0 元。
                t_in = max(1, len(prompt) // 4)
                t_out = max(1, len(output) // 4)
                cost = float((est or {}).get("cost_cap") or 0.0)
                priced = True
        elif provider in LOCAL_PROVIDERS:
            output, t_in, t_out = _ollama_complete(prompt, name, opts, on_delta=on_delta)
            cost, priced = 0.0, True
            est = {"cost_cap": 0.0, "line": "[报账] provider=ollama 本地推理，无云端费用"}
        else:
            raise ModelGatewayError(
                f"未知 provider：{provider}（应为 mock/{'/'.join(sorted(OPENAI_LIKE))}/anthropic/ollama）")

        outs.append({
            "model_id": m,
            "model": name,
            "output": output,
            "cost": cost,                 # 人民币；mock 恒 0
            "priced": priced,             # False = 价格表里没这个模型，不是免费
            "cost_estimate": est["cost_cap"] if est else 0.0,   # 出门前估的上限
            "spend_report": est["line"] if est else "[报账] provider=mock 不花钱、不出网",
            "tokens": t_in + t_out,
            "tokens_in": t_in,
            "tokens_out": t_out,
            "latency": round(time.time() - t0, 4),
        })
    return outs


# ---------------------------------------------------------------- mock

def _mock_complete(prompt):
    n_lines = prompt.count("\n") + 1
    return f"[mock] 已读取 {n_lines} 行上下文并生成占位回应（接入真实模型后此处为真正的分析）。"


# ---------------------------------------------------------------- 真实 provider

def _require_key(provider):
    key = _env(f"{provider.upper()}_API_KEY") or _env("PAI_MODEL_API_KEY")
    if provider == "anthropic" and not key:
        key = _env("PAI_CLAUDE_API_KEY")
    if not key:
        raise ModelGatewayError(
            f"要用 {provider} 但未设置密钥：请在终端 export {provider.upper()}_API_KEY"
            f"（切勿把密钥贴进对话/代码/库）。")
    return key


def complete(prompt, models=None, opts=None, on_delta=None, on_reasoning=None):
    """默认单模型调用带 fallback；显式 models 列表保持原有“多模型都跑”语义。"""
    if models is not None:
        return _complete_many(prompt, models=models, opts=opts,
                              on_delta=on_delta, on_reasoning=on_reasoning)
    primary = _resolve_default_provider()
    chain = [primary]
    raw = os.environ.get("PAI_MODEL_FALLBACKS", "").strip()
    if raw:
        chain += [x.strip() for x in raw.split(",") if x.strip()]
    last = None
    for i, m in enumerate(chain):
        try:
            return _complete_many(prompt, models=(m,), opts=opts,
                                  on_delta=on_delta, on_reasoning=on_reasoning)
        except Exception as e:
            last = e
            try:
                EG.audit("model_fallback", "medium", "retry" if i < len(chain) - 1 else "deny",
                         str(m), "%s: %s" % (type(e).__name__, e))
            except Exception:
                pass
            text = str(e)
            transient = any(x in text for x in ("HTTP 429", "HTTP 500", "HTTP 502",
                                                "HTTP 503", "HTTP 504", "连不上", "超时",
                                                "timed out", "Temporary"))
            # 只有瞬时网络错误才切备用；计费/白名单/密钥/坏响应一律不切。
            if not transient:
                raise
            if on_delta is not None:
                # 流式已可能吐出部分内容，不能静默重放，直接失败。
                raise
    raise last if last else ModelGatewayError("没有可用模型")


def _post_json(url, headers, payload, timeout):
    if not NP.is_host_allowed(url):
        raise ModelGatewayError("出网白名单拒绝：%s" % url)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    def _once():
        with urllib.request.urlopen(req, timeout=timeout) as resp:     # 尊重环境代理
            return resp.read().decode("utf-8", errors="replace")
    try:
        body = EG.retry_call(_once, "model_http", retries=2)
    except urllib.error.HTTPError as e:
        # 读服务端错误体帮助定位，但绝不回显请求头（含 Key）
        detail = e.read().decode("utf-8", errors="replace")[:300] if hasattr(e, "read") else ""
        raise ModelGatewayError(f"{url} 返回 HTTP {e.code}：{detail}") from None
    except urllib.error.URLError as e:
        raise ModelGatewayError(f"连不上 {url}：{e.reason}（境外端点常需代理）") from None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise ModelGatewayError(f"{url} 返回的不是合法 JSON") from None


def _openai_complete(provider, prompt, opts, model=None, on_delta=None, on_reasoning=None):
    """OpenAI 兼容调用（openai / deepseek 共用）。返回 (文本, 输入tokens, 输出tokens)。"""
    key = _require_key(provider)
    base = (_env(f"PAI_{provider.upper()}_BASE_URL", OPENAI_LIKE[provider])).rstrip("/")
    name = model or _env("PAI_MODEL_NAME", DEFAULT_NAMES[provider])
    timeout = int(_env("PAI_MODEL_TIMEOUT", "60"))
    payload = {
        "model": name,
        "messages": [{"role": "user", "content": prompt}],   # grounded 上下文已拼在 prompt 里
    }
    if opts and "temperature" in opts:
        payload["temperature"] = opts["temperature"]
    if on_delta:
        return _openai_stream(base, key, payload, timeout, on_delta,
                              on_reasoning=on_reasoning)
    resp = _post_json(base + "/chat/completions",
                      {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      payload, timeout)
    try:
        output = resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ModelGatewayError(f"{provider} 响应结构不符合预期（缺 choices）") from None
    usage = resp.get("usage") or {}
    t_in = int(usage.get("prompt_tokens", 0))
    t_out = int(usage.get("completion_tokens", 0))
    if not t_in and not t_out:
        t_in = int(usage.get("total_tokens", 0))
    return output, t_in, t_out


def _openai_stream(base, key, payload, timeout, on_delta, on_reasoning=None):
    if not NP.is_host_allowed(base):
        raise ModelGatewayError("出网白名单拒绝：%s" % base)
    body = dict(payload)
    body["stream"] = True
    body["stream_options"] = {"include_usage": True}
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST")
    chunks = []
    t_in = t_out = 0
    _metrics = {"prompt_eval_count": 0, "prompt_eval_duration": 0,
                "eval_count": 0, "eval_duration": 0, "load_duration": 0}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                usage = obj.get("usage") or {}
                t_in = int(usage.get("prompt_tokens", t_in) or 0)
                t_out = int(usage.get("completion_tokens", t_out) or 0)
                try:
                    delta_obj = obj["choices"][0].get("delta", {}) or {}
                except (KeyError, IndexError, TypeError):
                    delta_obj = {}
                reason = delta_obj.get("reasoning_content") or ""
                if reason and on_reasoning:
                    on_reasoning(reason)
                delta = delta_obj.get("content") or ""
                if delta:
                    chunks.append(delta)
                    on_delta(delta)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300] if hasattr(e, "read") else ""
        raise ModelGatewayError(f"{base}/chat/completions 返回 HTTP {e.code}：{detail}") from None
    except urllib.error.URLError as e:
        raise ModelGatewayError(f"连不上 {base}：{e.reason}") from None
    return "".join(chunks), t_in, t_out


OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def _ollama_url():
    """「默认档不出网」对 ollama 免检的前提由这一刀守住：只许连回环。"""
    host = (urllib.parse.urlsplit(OLLAMA_URL).hostname or "").strip().lower()
    if host not in OLLAMA_LOOPBACK_HOSTS:
        raise ModelGatewayError(
            "ollama 只许连本机回环，现在指向 %s —— 要连远端就显式设 provider 并先报账。"
            % OLLAMA_URL)
    return OLLAMA_URL


def _fit_prompt(prompt, num_ctx):
    # CPU 上 4B 的 prefill 很贵：默认压到可承受长度，但优先保留【本轮用户输入】。
    cap = int(_env("PAI_LOCAL_PROMPT_MAX", "1600"))
    budget = min(max(4000, int(num_ctx * 0.8)), cap)
    budget = max(200, budget)
    text = str(prompt)
    if len(text) <= budget:
        return text
    marker = "[用户] "
    i = text.rfind(marker)
    if i >= 0:
        user = text[i:]
        head = text[:max(180, int(budget * 0.28))]
        user_budget = max(300, budget - len(head) - 80)
        if len(user) > user_budget:
            uh = user[:int(user_budget * 0.45)]
            ut = user[-int(user_budget * 0.45):]
            user = uh + "\n[...本轮输入中间已裁剪...]\n" + ut
        return head + "\n[...上下文已裁剪...]\n" + user
    head = text[:int(budget * 0.65)]
    tail = text[-int(budget * 0.30):]
    return head + "\n\n[...长上下文中间已裁剪...]\n\n" + tail


def _ollama_complete(prompt, model, opts=None, on_delta=None):
    """本地 Ollama 文本补全。只连 127.0.0.1:11434，无云端费用。"""
    name = model or _env("PAI_LOCAL_MODEL", "qwen3-4b-instruct:latest")
    timeout = int(_env("PAI_OLLAMA_TIMEOUT", "180"))
    num_ctx = int(_env("PAI_OLLAMA_NUM_CTX", "2048"))
    # 粗略预算：中文约 1 字/token；留 20% 给输出与模板。
    prompt = _fit_prompt(prompt, num_ctx)
    payload = {
        "model": name,
        "messages": [{"role": "user", "content": prompt}],
        "stream": bool(on_delta),
        "options": {"temperature": float((opts or {}).get("temperature", 0.3)),
                    "num_predict": int((opts or {}).get("max_tokens", 900)),
                    "num_ctx": num_ctx},
    }
    if "think" in (opts or {}):
        payload["think"] = bool(opts.get("think"))
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        _ollama_url(), data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"})
    chunks = []
    t_in = t_out = 0
    _metrics = {"prompt_eval_count": 0, "prompt_eval_duration": 0,
                "eval_count": 0, "eval_duration": 0, "load_duration": 0}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = data.get("message") or {}
                delta = str(msg.get("content") or "")
                if delta:
                    chunks.append(delta)
                    if on_delta:
                        on_delta(delta)
                t_in = int(data.get("prompt_eval_count", t_in) or t_in)
                t_out = int(data.get("eval_count", t_out) or t_out)
                for _k in _metrics:
                    try:
                        _metrics[_k] = int(data.get(_k, _metrics[_k]) or _metrics[_k])
                    except Exception:
                        pass
                if data.get("done"):
                    break
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300] if hasattr(e, "read") else ""
        raise ModelGatewayError("本地 Ollama 返回 HTTP %s：%s" % (e.code, detail)) from None
    except urllib.error.URLError as e:
        raise ModelGatewayError("连不上本地 Ollama：%s" % e.reason) from None
    output = "".join(chunks).strip()
    _LAST_OLLAMA_METRICS.clear()
    _LAST_OLLAMA_METRICS.update(_metrics)
    return output, t_in, t_out


def _anthropic_complete(prompt, opts, model=None):
    key = _require_key("anthropic")
    base = (_env("PAI_ANTHROPIC_BASE_URL")
            or _env("PAI_CLAUDE_BASE_URL")
            or "https://api.anthropic.com/v1").rstrip("/")
    name = model or _env("PAI_MODEL_NAME", DEFAULT_NAMES["anthropic"])
    timeout = int(_env("PAI_MODEL_TIMEOUT", "60"))
    payload = {
        "model": name,
        "max_tokens": int((opts or {}).get("max_tokens", 1024)),
        "messages": [{"role": "user", "content": prompt}],
    }
    resp = _post_json(base + "/messages",
                      {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION,
                       "Content-Type": "application/json"},
                      payload, timeout)
    parts = [b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text"]
    output = "".join(parts).strip()
    if not output:
        raise ModelGatewayError("anthropic 响应没有文本内容块")
    usage = resp.get("usage") or {}
    t_in = int(usage.get("input_tokens", 0))
    t_out = int(usage.get("output_tokens", 0))
    return output, t_in, t_out
