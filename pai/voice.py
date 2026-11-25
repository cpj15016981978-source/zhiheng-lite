"""M14 语音层 —— 见《00_协作规程与接口契约.txt》PART B2 (M14) 与 ADR-035。

第一阶段只做【"说"】，不做"听"：本模块不碰麦克风、不申请录音权限、
不 import 任何采集设备的路径。要说"听"，得另开一次申请（要常开麦克风 + 判"你说完了没"）。

后端 = 本机 Windows 自带的 System.Speech（起 PowerShell 子进程）。为什么是它：
  ① 原话和回答一步不出这台机器 —— 与 ADR-031 否决云端 embedding 是同一条纪律；
  ② 零第三方依赖：PowerShell 是系统自带，subprocess/threading 是标准库；
  ③ 关掉 = 什么都不做，一个字都不改（ADR-032 同一条：默认关，且关态零副作用）。
云端 TTS（声音像真人，但要把回答文本发给第三方）【本模块刻意不实现】，
需要 Boss 单独点头才能开，且开了也要在界面上看得见"这句出门了"。

四条纪律，各有测试钉住：
  · 文本只走 stdin，绝不拼进命令行 —— 防命令注入（回答里可能有引号、分号、$）。
  · 文本不落临时文件 —— 不新开一个 purge 管不到的"原文住址"（%TEMP% 不归 :purge 管）。
  · 后端失败/超时/没装 → 静默降级成"没声音"，绝不打断对话、绝不抛给上层。
  · speak() 不等念完就返回（贾维斯该能被打断）：stop() 或下一次 speak() 会掐掉上一条。
加一条（P32 上收之后才有）：installed_voices() 读的是【合成器清单】，不出声、不开麦克风、
脚本正文是常量 —— 它归本模块管，是因为「会不会出声」这个边界只能由说的人守着。
"""
import os
import platform
import subprocess
import json
import threading
import tempfile
from pathlib import Path
from pai import atomic as AT


ENV_BACKEND = "PAI_VOICE_BACKEND"
ENV_VOICE_NAME = "PAI_VOICE_NAME"
ENV_RATE = "PAI_VOICE_RATE"

BACKEND_OFF = "off"
BACKEND_WINDOWS = "windows"
BACKENDS = (BACKEND_OFF, BACKEND_WINDOWS)

MAX_SPEAK_CHARS = 600           # 念三分钟不如不念；超出按句子边界截
_STOP_SENTENCES = ("。", "！", "？", "；", "…", "\n", ".", "!", "?", ";")

# 能力登记（M15 契约，ADR-036）。字段口径的唯一住址在 tools/check_capability.py，
# 这里只填事实、不抄解释；switch_env 引用上面的常量名，环境变量名仍只有一处。
CAPABILITY = {
    "name": "M14 语音·说",
    "switch_env": ENV_BACKEND,
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "非 Windows / 没中文嗓 / 后端坏了 = 静静不念并给一条补救提示，绝不打断对话",
    "probe_cost": "backend()/speaking_enabled() 只读环境变量与 platform，不起进程、不联网、不写库；"
                  "installed_voices() 会起一次 PowerShell【只读嗓子清单】：不出声、不开麦克风、"
                  "脚本正文里没有用户文本；★开态 speak()/stop() 会起一次 Get-CimInstance Win32_Process"
                  " 查询进程身份，并在 %TEMP%\\pai_control 写 voice.json/proactive.txt 临时件"
                  "（不存用户原文，不归 :purge 管）",
    "approval": "云端 TTS 后端要把回答发给第三方（撞 S3），需 Boss 单独点头",
}

_rate_lock = threading.Lock()
_current = None                 # 正在念的子进程（同时只有一条）
_CONTROL_DIR = Path(tempfile.gettempdir()) / "pai_control"


def _control(name):
    _CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    return _CONTROL_DIR / name


def clear_voice_state():
    try:
        _control("voice.json").unlink()
    except OSError:
        pass


def _proc_identity(pid):
    """拿 pid 的 exe / 命令行 / 创建时刻；拿不到返回 None（宁可不杀）。"""
    script = ("$p = Get-CimInstance Win32_Process -Filter 'ProcessId=%d'; "
              "$p | Select-Object ProcessId,ExecutablePath,CommandLine,CreationDate "
              "| ConvertTo-Json -Compress" % int(pid))
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        if out.returncode != 0 or not out.stdout.strip():
            return None
        obj = json.loads(out.stdout)
        return {"pid": int(obj.get("ProcessId")),
                "exe": obj.get("ExecutablePath") or "",
                "cmdline": obj.get("CommandLine") or "",
                "created": str(obj.get("CreationDate") or "")}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def set_voice_state(info):
    if info:
        AT.write_text(_control("voice.json"), json.dumps(info, ensure_ascii=False), encoding="utf-8")


def get_voice_state():
    try:
        return json.loads(_control("voice.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _same_identity(a, b):
    return bool(a and b and a.get("exe") == b.get("exe")
                and a.get("cmdline") == b.get("cmdline")
                and a.get("created") == b.get("created"))


def clear_proactive():
    try:
        _control("proactive.txt").unlink()
    except OSError:
        pass


def set_proactive(on):
    AT.write_text(_control("proactive.txt"), "on" if on else "off", encoding="utf-8")


def get_proactive():
    try:
        v = _control("proactive.txt").read_text(encoding="utf-8").strip().lower()
    except OSError:
        return None
    return v if v in ("on", "off") else None


# 常量脚本：里面【没有】任何用户文本，文本走 stdin、嗓子和语速走环境变量。
_PS = r"""
$ErrorActionPreference = 'Stop'
try { [Console]::InputEncoding = [System.Text.Encoding]::UTF8 } catch { }
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$want = $env:PAI_VOICE_NAME
if ($want) { try { $s.SelectVoice($want) } catch { } }
else {
  $zh = $s.GetInstalledVoices() | Where-Object {
    $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'zh*' } | Select-Object -First 1
  if ($zh) { $s.SelectVoice($zh.VoiceInfo.Name) }
}
if ($env:PAI_VOICE_RATE -match '^-?\d+$') { $s.Rate = [int]$env:PAI_VOICE_RATE }
$t = [Console]::In.ReadToEnd()
if ($t.Trim()) { try { $s.Speak($t) } catch { } }
"""


def backend():
    """当前后端：off（默认）| windows。写错值一律当 off，不做兜底猜测。"""
    raw = (os.environ.get(ENV_BACKEND) or "").strip().lower()
    return raw if raw in BACKENDS else BACKEND_OFF


def speaking_enabled():
    """只读环境变量和平台，【不起任何进程】——关态必须零副作用（ADR-032）。"""
    return backend() == BACKEND_WINDOWS and platform.system() == "Windows"


def set_enabled(on):
    """会话内开关（chat.py 的 :voice 用它）。只动本进程环境变量，不落盘。"""
    os.environ[ENV_BACKEND] = BACKEND_WINDOWS if on else BACKEND_OFF


def _clip(text):
    """截到句子边界，别把一句话劈成半句念出去。"""
    if len(text) <= MAX_SPEAK_CHARS:
        return text, False
    window = text[:MAX_SPEAK_CHARS]
    cut = max((window.rfind(s) for s in _STOP_SENTENCES), default=-1)
    if cut >= 0:
        return window[:cut + 1], True
    return window, True


def speak(text, rate=None, voice=None, spawn=None, wait=False):
    """把这句念出来。关掉时【什么都不做】。

    返回 trace dict（只含计数与原因，绝不含原文 —— 红线同 ADR-017）：
      {"spoken","backend","chars","truncated","rate","reason"}
    失败一律降级为 spoken=False + reason，不抛异常：没声音不该打断人说话。
    spawn/rate/voice 是给测试和上层注入的，默认走本机 PowerShell 与环境变量。
    wait=True 时等念完再返回（tools/say.py 要这个；对话里不等，边念边聊）。
    """
    body = (text or "").strip()
    trace = {"spoken": False, "backend": backend(), "chars": 0,
             "truncated": False, "rate": 0, "reason": ""}
    if not body:
        trace["reason"] = "没内容要念"
        return trace
    if backend() != BACKEND_WINDOWS:
        trace["reason"] = "语音开关是关的（:voice on 打开）"
        return trace
    if platform.system() != "Windows":
        trace["reason"] = "本机后端只支持 Windows，未落其他平台"
        return trace

    spoken_text, trace["truncated"] = _clip(body)
    trace["chars"] = len(spoken_text)
    clamped = _clamp_rate(rate if rate is not None else os.environ.get(ENV_RATE))
    trace["rate"] = clamped

    env = dict(os.environ)
    env[ENV_VOICE_NAME] = (voice or os.environ.get(ENV_VOICE_NAME) or "").strip()
    env[ENV_RATE] = str(clamped)
    try:
        proc = (_spawn_windows if spawn is None else spawn)(spoken_text.encode("utf-8"), env)
        trace["spoken"] = True
        if wait and proc is not None:
            _reap(proc)
    except OSError as exc:                     # 起不进程、PowerShell 不在、内存不够
        trace["reason"] = "念不出来（已静默跳过）：%s" % exc.__class__.__name__
    except Exception as exc:                   # 兜底：任何后端毛病都不许冒到对话里
        trace["reason"] = "念不出来（已静默跳过）：%s" % exc.__class__.__name__
    return trace


def _clamp_rate(value):
    try:
        rate = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return max(-10, min(10, rate))             # System.Speech 的合法区间


def _spawn_windows(payload, env):
    """起 PowerShell 念出来：argv 是常量，文本用 stdin 喂进去，不等它念完。"""
    argv = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _PS]
    proc = subprocess.Popen(                       # noqa: S603 固定 argv，无 shell=True
        argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, env=env)
    _adopt(proc, payload)
    return proc


def _adopt(proc, payload):
    """把文本写进子进程 stdin 并收着它；写失败也必须收尸，不留孤儿进程。"""
    global _current
    with _rate_lock:
        prev = _current
        _current = proc
        pid = getattr(proc, "pid", None)
        if pid:
            set_voice_state(_proc_identity(pid))
    if prev is not None:
        _kill(prev)

    def _feed():
        try:
            proc.stdin.write(payload)
            proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            _reap(proc)

    threading.Thread(target=_feed, daemon=True).start()


def _reap(proc):
    global _current
    try:
        proc.wait(timeout=90)
    except Exception:
        _kill(proc)
    with _rate_lock:
        if _current is proc:
            clear_voice_state()


def _kill(proc):
    try:
        proc.kill()
    except OSError:
        pass


def stop():
    """掐掉正在念的那句（"别念了"）。没在念就什么都不做。"""
    global _current
    with _rate_lock:
        proc, _current = _current, None
    if proc is not None:
        _kill(proc)
        clear_voice_state()
        return True
    state = get_voice_state()
    if not state:
        return False
    cur = _proc_identity(state.get("pid"))
    if not _same_identity(state, cur):
        clear_voice_state()
        return False
    try:
        os.kill(int(state["pid"]), 9)
    except OSError:
        pass
    clear_voice_state()
    return True


def is_speaking():
    with _rate_lock:
        proc = _current
    return bool(proc is not None and proc.poll() is None)


# 查已装嗓子：这段脚本正文里没有任何用户文本，也不 SelectVoice、不 Speak。
_PS_LIST = r"""
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
foreach ($v in $s.GetInstalledVoices()) {
  if ($v.Enabled) { [Console]::Out.WriteLine($v.VoiceInfo.Name + ' | ' + $v.VoiceInfo.Culture.Name) }
}
"""


def installed_voices(run=None):
    """本机装了哪些嗓子 —— 读合成器清单，【不出声、不开麦克风】。

    返回 {"rows": ["名字 | zh-CN", ...], "reason": ""}；非 Windows / PowerShell 起不动
    就回空表加一句原因，绝不抛出去：查一眼嗓子不该把界面上的对话弄断。
    这条原先 inline 在 tools/say.py 里（黑板 P32 那一族），上收到这里才有人管它出不出声。
    """
    out = {"rows": [], "reason": ""}
    if platform.system() != "Windows":
        out["reason"] = "本机嗓子清单只有 Windows 后端能查"
        return out
    try:
        text = (_spawn_list_voices if run is None else run)()
    except OSError as exc:
        out["reason"] = "查不到嗓子（PowerShell 起不动：%s）" % exc
        return out
    except Exception as exc:
        out["reason"] = "查不到嗓子（已静默跳过）：%s" % exc.__class__.__name__
        return out
    out["rows"] = [r.strip() for r in (text or "").splitlines() if r.strip()]
    if not out["rows"]:
        out["reason"] = "本机一个语音引擎都没装（System.Speech 没可用嗓子）"
    return out


def _spawn_list_voices():
    proc = subprocess.run(                            # noqa: S603 常量 argv，无 shell=True
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _PS_LIST],
        capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise OSError((proc.stderr or b"").decode("utf-8", "replace")[-200:]
                      or "powershell 退出码 %d" % proc.returncode)
    return (proc.stdout or b"").decode("utf-8", "replace")


# 每个新进程默认回到环境变量；跨进程动作只在当前会话内有效
clear_proactive()
