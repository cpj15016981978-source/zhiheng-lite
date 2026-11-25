"""X6 语音输入·命令级（1a）—— 见黑板 ADR-040 与 Boss 2026-10-22 的「命令级零依赖」。

只做三件事：开一次麦克风、用固定词表认【一条命令】、立刻关掉。
不常听、不做唤醒词、不联网、不写库、不留音频（record_until 写的那只 WAV 由
discard_record 连临时目录一起收掉；进程被硬杀时留在 %TEMP% 里，:purge 管不到那条目录）。

★边界是这台机器测出来的，不是估的：本机装了 System.Speech 的中文识别器
（Microsoft Speech Recognizer 8.0 / zh-CN），固定 Grammar 能建起来；而自由长句
在同一批 6 句上只有 0.718。所以零依赖能听清的是「命令」，不是「聊天」——
想听自由说话得走 Vosk，那要破零依赖底线 + 你单独点头（X6 的 1b，没选）。

四条纪律（前三条沿用 M14/ADR-035，第四条是采集设备特有的）：
  · 词表只走 stdin 的 JSON，绝不拼进命令行 —— 防命令注入。
  · 回来的字不写临时文件、不落库；不在词表里的直接丢，trace 的理由串里也不带它。
  · 后端坏了 / 没麦克风 / 超时 = 静默返回空并给一句原因，绝不抛给上层、绝不打断对话。
  · ★麦克风只在 listen_once()（实时听一句）与 record_until()（按住说话录一段）这两句里开着，
    返回即释放；两处都先过 _device_refusal()，开关没拧开时连 waveInOpen 都不碰。
    probe() 与 key_down() 这条读【永远不开麦克风】，所以它能替你回答「这台机器能不能听中文」
    而不用把你的话录进去。

两处非显然的坑，各自有测试钉住，改动前先读注释别顺手"清理"：
  ① PowerShell 5.1 读 stdin 按控制台代码页（这台是 cp936）解码，UTF-8 中文会被嚼碎，
    连 JSON 的引号都能被吃掉一个字节 —— 词表变成一条永不命中的空壳。所以显式用
    StreamReader(UTF-8) 读流，且 payload 用 ensure_ascii=True 的 \\uXXXX 纯 ASCII 写法。
  ② 同步 Recognize(TimeSpan) 在这台机器上实测挂住不返回；异步 + WaitOne 到点 Cancel 才可控，
    而识别引擎的音频线程会让 PowerShell 进程不退出，所以最后强制 Exit(0)（②是交付材料里的
    实测记录，本机未复现 —— 不复现也不影响正确性，异步本来就是安全的那一侧）。
"""
import ctypes
import json
import math
import os
import platform
import struct
import subprocess
import tempfile
import time
import wave

ENV_BACKEND = "PAI_EAR_BACKEND"
ENV_TIMEOUT = "PAI_EAR_TIMEOUT"

BACKEND_OFF = "off"
BACKEND_WINDOWS = "windows"
BACKENDS = (BACKEND_OFF, BACKEND_WINDOWS)

DEFAULT_TIMEOUT_S = 8
MIN_TIMEOUT_S = 1
MAX_TIMEOUT_S = 30
MAX_TEXT_CHARS = 200

# 词表唯一住址：加一个词只改这里；chat.py 引用下面的常量名，不抄第二份字符串。
# 四个词都对应【脊柱里已经存在】的动作 —— 不做没有落地动作的词，那是假能力。
CMD_STOP_SPEAK = "别念了"
CMD_REPEAT_LAST = "念最后一条"
CMD_PROACTIVE_OFF = "安静下来"
CMD_PROACTIVE_ON = "继续说话"
COMMANDS = (CMD_STOP_SPEAK, CMD_REPEAT_LAST, CMD_PROACTIVE_OFF, CMD_PROACTIVE_ON)

RECOGNIZER_CULTURE = "zh-CN"

CAPABILITY = {
    "name": "X6 语音输入·命令级",
    "switch_env": ENV_BACKEND,
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "没麦克风/没中文识别器/PowerShell 起不来/超时 = 静默返回空并给原因，"
               "绝不打断对话、绝不抛给上层；开关没拧开时 record_until/key_down "
               "在碰设备【之前】就回拒绝（一个麦都不开、一个文件都不落）",
    "probe_cost": "probe() 只读环境变量与已装识别器清单，【不开麦克风】、不起采集进程、"
                  "不联网、不写库；key_down()/mic_devices() 关态同样不碰 ctypes",
    "approval": "Boss 2026-10-22 选 1a「命令级零依赖」，留底见黑板 ADR-048（三选一：1a/1b 上 "
                "Vosk/③不做）。只开一次麦克风认固定命令；常开采集设备与唤醒词是另一份隐私面，未批。"
                "2026-10-25 那句「P32 选②」= 把入口壳里 inline 的开麦/读按键/认 WAV 三样上收到本模块"
                "（见 ADR-079 的增补与 P32 回执），没有加新的采集能力",
}


def backend():
    """当前后端：off（默认）| windows。写错值一律当 off，不做兜底猜测。"""
    raw = (os.environ.get(ENV_BACKEND) or "").strip().lower()
    return raw if raw in BACKENDS else BACKEND_OFF


def listening_enabled():
    """只读环境变量和平台，【不开设备、不起进程】—— 关态必须零副作用（ADR-032）。"""
    return backend() == BACKEND_WINDOWS and platform.system() == "Windows"


def set_enabled(on):
    """会话内开关（chat.py 的 :ear 用它）。只动本进程环境变量，不落盘。"""
    os.environ[ENV_BACKEND] = BACKEND_WINDOWS if on else BACKEND_OFF


def clamp_timeout(value):
    """超时写坏一律回落默认值，合法区间 1-30 秒。"""
    try:
        seconds = int(str(value).strip())
    except (TypeError, ValueError):
        return min(MAX_TIMEOUT_S, max(MIN_TIMEOUT_S, DEFAULT_TIMEOUT_S))
    return max(MIN_TIMEOUT_S, min(MAX_TIMEOUT_S, seconds))


def match_command(text):
    """把识别文本对到词表：只对【去掉标点后完全相等】的那一个，不猜、不做模糊匹配。

    识别器偶尔在命令后面带个句号，所以剥掉收尾标点再比；剥完对不上就返回 None。
    """
    body = (text or "").strip().strip("。！？，,.!?、")
    return body if body in COMMANDS else None


# 常量脚本：里面【没有】任何词表文本，词表走 stdin、超时走环境变量。
_PS = r"""
$ErrorActionPreference = 'Stop'
$sr = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), [System.Text.Encoding]::UTF8)
$words = @(ConvertFrom-Json $sr.ReadToEnd())
$ci = [System.Globalization.CultureInfo]::GetCultureInfo([string]$env:PAI_EAR_CULTURE)
$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($ci)
$choices = New-Object System.Speech.Recognition.Choices
foreach ($w in $words) { [void]$choices.Add([string]$w) }
$gb = New-Object System.Speech.Recognition.GrammarBuilder
$gb.Culture = $ci
$gb.Append($choices)
$engine.LoadGrammar((New-Object System.Speech.Recognition.Grammar($gb)))
$engine.SetInputToDefaultAudioDevice()
$script:heard = $null
$done = New-Object System.Threading.ManualResetEvent($false)
$handler = [System.EventHandler[System.Speech.Recognition.RecognizeCompletedEventArgs]]{
    param($sender, $e)
    if ($e.Result) { $script:heard = $e.Result.Text }
    [void]$done.Set()
}
$engine.add_RecognizeCompleted($handler)
$engine.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Single)
if (-not $done.WaitOne([TimeSpan]::FromSeconds([int]$env:PAI_EAR_TIMEOUT))) {
    try { $engine.RecognizeAsyncCancel() } catch { }
}
$sr2 = New-Object System.IO.StreamWriter([Console]::OpenStandardOutput(), [System.Text.Encoding]::UTF8)
if ($script:heard) { $sr2.WriteLine([string]$script:heard) }
$sr2.Flush(); $sr2.Dispose()
try { $engine.Dispose() } catch { }
[Environment]::Exit(0)
"""

# 读已装识别器：不调用 SetInputToDefaultAudioDevice、不建 Grammar 之外的东西 —— 不开设备。
_PS_PROBE = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$rec = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
$sr = New-Object System.IO.StreamWriter([Console]::OpenStandardOutput(), [System.Text.Encoding]::UTF8)
$sr.WriteLine("count=" + $rec.Count)
$want = [string]$env:PAI_EAR_CULTURE
$hit = $false
foreach ($r in $rec) {
    $sr.WriteLine("culture=" + [string]$r.Culture.Name)
    if (([string]$r.Culture.Name) -eq $want) { $hit = $true }
}
if ($hit) {
    try {
        $e = New-Object System.Speech.Recognition.SpeechRecognitionEngine(
            [System.Globalization.CultureInfo]::GetCultureInfo($want))
        $sr.WriteLine("engine=OK")
        $e.Dispose()
    } catch { $sr.WriteLine("engine=FAIL") }
} else { $sr.WriteLine("engine=NONE") }
$sr.Flush(); $sr.Dispose()
"""


def _run_ps(script, payload, env, timeout_s):
    """起 PowerShell 跑一段常量脚本：argv 里没有任何内容，词表走 stdin。"""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        input=payload, env=env, capture_output=True, timeout=timeout_s)      # noqa: S603
    if proc.returncode != 0:
        lines = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise OSError(lines[-1] if lines else "powershell 退出码 %d" % proc.returncode)
    return (proc.stdout or b"").decode("utf-8", "replace")


def _base_env(timeout_s):
    env = dict(os.environ)
    env[ENV_TIMEOUT] = str(clamp_timeout(timeout_s))
    env["PAI_EAR_CULTURE"] = RECOGNIZER_CULTURE          # 直接覆盖：这条不许被环境里的值带走
    return env


def probe(run=None):
    """这台机器能不能听中文 —— 【不开麦克风】的只读检查。

    返回 {"can_hear","cultures","engine","reason"}；失败一律降级成 can_hear=False，
    绝不抛出去：问一句"能不能听"不该把对话弄断。
    """
    out = {"can_hear": False, "cultures": [], "engine": "", "reason": ""}
    if platform.system() != "Windows":
        out["reason"] = "本机后端只支持 Windows，未落其他平台"
        return out
    try:
        text = (_run_ps if run is None else run)(_PS_PROBE, b"", _base_env(DEFAULT_TIMEOUT_S), 60)
    except Exception as exc:
        out["reason"] = "问不了已装识别器（已静默跳过）：%s" % exc.__class__.__name__
        return out
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("culture="):
            out["cultures"].append(line[len("culture="):])
        elif line.startswith("engine="):
            out["engine"] = line[len("engine="):]
    out["can_hear"] = RECOGNIZER_CULTURE in out["cultures"] and out["engine"] == "OK"
    if not out["can_hear"]:
        out["reason"] = ("没装 %s 的 System.Speech 识别器（本机看到：%s）"
                         % (RECOGNIZER_CULTURE, "、".join(out["cultures"]) or "一个都没有"))
    return out


def listen_once(timeout_s=None, spawn=None):
    """开一次麦克风，认一条命令，关掉。返回 trace，不抛异常。

      命中词表 -> {"command": 那个词, "reason": "ok"}
      没听清   -> command=None，reason 说明是超时、还是"听清了但不在词表里"
    ★不在词表里的字只留在返回值 text 里给界面显示一次，不进 trace 的理由串、不写库。
    spawn(payload, env) -> str 供测试注入；默认起本机 PowerShell。
    """
    seconds = clamp_timeout(DEFAULT_TIMEOUT_S if timeout_s is None else timeout_s)
    trace = {"text": "", "command": None, "reason": "", "backend": backend(),
             "chars": 0, "timeout_s": seconds}
    if backend() != BACKEND_WINDOWS:
        trace["reason"] = "语音输入是关的（:ear on 打开）"
        return trace
    if platform.system() != "Windows":
        # 开关明明开着，就不能回「开关是关的」——那是把人往错的方向支走。
        trace["reason"] = "本机后端只支持 Windows，未落其他平台"
        return trace
    payload = json.dumps(list(COMMANDS), ensure_ascii=True).encode("ascii")
    try:
        out = (_spawn_windows if spawn is None else spawn)(payload, _base_env(seconds))
    except OSError as exc:
        trace["reason"] = "没听成：麦克风或识别器不可用（%s）" % exc
        return trace
    except Exception as exc:
        trace["reason"] = "这次没听成（已静默跳过）：%s" % exc.__class__.__name__
        return trace

    text = (out or "").strip()[:MAX_TEXT_CHARS]
    if not text:
        trace["reason"] = "没听清或超时（%d 秒内没有识别结果）" % seconds
        return trace
    trace["text"] = text
    trace["chars"] = len(text)
    trace["command"] = match_command(text)
    trace["reason"] = "ok" if trace["command"] else "听清了，但不在命令词表里"
    return trace


def _spawn_windows(payload, env):
    timeout = clamp_timeout(env.get(ENV_TIMEOUT, DEFAULT_TIMEOUT_S)) + 20
    return _run_ps(_PS, payload, env, timeout)


# ============================ 按住说话（X6 的第二条采集路径）============================
# 这三样动作原先写在 tools/push_to_talk.py 里：waveInOpen 开麦、GetAsyncKeyState 读按键、
# 起 PowerShell 认一段 WAV。住在入口壳里 = 登记面和闸门都看不见（黑板 P32 / ADR-079），
# 而且那条开关根本挡不住它们。Boss 2026-10-25 选【②设备动作上收 pai/】，就搬到这里来：
# 词表住址仍是上面的 COMMANDS，开关住址仍是 ENV_BACKEND，降级口径仍是「静默给一句原因」。
# ★开关在这里是真管用的：backend() 不是 windows（默认就是 off）时，下面每个函数都在
#   碰 ctypes / 起子进程【之前】返回拒绝，一个设备也不开、一个文件也不落。

ENV_WAV = "PAI_EAR_WAV"

HOTKEYS = {"F8": 0x77, "F9": 0x78, "F10": 0x79, "RIGHT_CTRL": 0xA3, "RIGHT_ALT": 0xA5}
RATE, CHANNELS, BITS = 16000, 1, 16
WAVE_MAPPER = 0xFFFFFFFF
MAX_PUSH_S = 30
TEMP_DIR_PREFIX = "pai_ptt_"
GAIN_TARGET_PEAK = 28000
GAIN_PEAK_BELOW = 10000
GAIN_MAX = 8.0


class WaveFormatEx(ctypes.Structure):
    _fields_ = [("wFormatTag", ctypes.c_ushort), ("nChannels", ctypes.c_ushort),
                ("nSamplesPerSec", ctypes.c_uint), ("nAvgBytesPerSec", ctypes.c_uint),
                ("nBlockAlign", ctypes.c_ushort), ("wBitsPerSample", ctypes.c_ushort),
                ("cbSize", ctypes.c_ushort)]


class WaveInCaps(ctypes.Structure):
    _fields_ = [("wMid", ctypes.c_ushort), ("wPid", ctypes.c_ushort),
                ("vDriverVersion", ctypes.c_uint), ("szPname", ctypes.c_wchar * 32),
                ("dwFormats", ctypes.c_uint), ("wChannels", ctypes.c_ushort),
                ("wReserved1", ctypes.c_ushort)]


class WaveHdr(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", ctypes.c_uint),
                ("dwBytesRecorded", ctypes.c_uint), ("dwUser", ctypes.c_void_p),
                ("dwFlags", ctypes.c_uint), ("dwLoops", ctypes.c_uint),
                ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_void_p)]


def _device_refusal():
    """设备动作的统一拒绝口径；空字符串 = 允许开设备。

    顺序不能反：先看开关再看平台 —— 开关是关的时候，连"这台机器支不支持"都不必问。
    """
    if backend() != BACKEND_WINDOWS:
        return "语音输入是关的（%s 没开；:ear on 或本进程显式拧开才录）" % ENV_BACKEND
    if platform.system() != "Windows":
        return "本机后端只支持 Windows，未落其他平台"
    return ""


def _user32():
    return ctypes.windll.user32


def _winmm():
    return ctypes.windll.winmm


def key_down(name):
    """某个键此刻按住了没有：读一次全局按键状态，不装钩子、不常驻、不开麦克风。

    开关关着一律回 False —— 入口壳据此不会进入"等你按键"的循环。名字不在 HOTKEYS 里
    是编程错误（会抛），不是设备故障，所以不降级成 False。
    """
    vk = HOTKEYS.get(str(name).strip().upper())
    if vk is None:
        raise ValueError("不认识的按键名 %r，可写 %s" % (name, "、".join(sorted(HOTKEYS))))
    if _device_refusal():
        return False
    return bool(_user32().GetAsyncKeyState(vk) & 0x8000)


def mic_devices(winmm=None):
    """本机录音设备清单 [(编号, 名字)]；开关关着 = 一个都不问，回空表。"""
    if _device_refusal():
        return []
    handle = _winmm() if winmm is None else winmm
    out = []
    for i in range(handle.waveInGetNumDevs()):
        caps = WaveInCaps()
        if handle.waveInGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            out.append((i, caps.szPname.strip()))
    return out


def pick_device(winmm=None):
    """优先挑名字里带 USB 的那只（Boss 那台的外接麦），否则用系统默认映射。"""
    devs = mic_devices(winmm)
    for i, name in devs:
        if "USB" in name.upper():
            return i, name
    return WAVE_MAPPER, (devs[0][1] if devs else "默认输入")


def record_until(release, max_s=MAX_PUSH_S, winmm=None):
    """按住说话：开一次麦克风，录到 release() 为真（或到 max_s 秒），关掉，写成临时 WAV。

    返回 {"path","bytes","level","reason"} —— path=None 时 reason 就是原因，
    录音失败/没麦克风也走这条路，不抛给上层（和 listen_once 同一个降级口径）。
    临时目录归本模块，调用方用 discard_record() 收尾。
    ★太小的声音就地线性放大（这台的外接麦实测偏轻），只放大数值，不额外存原始音频。
    winmm=<假的句柄> 供测试注入；默认取本机 winmm —— 注入点只有一处，
    所以「测试没碰真设备」这件事是能证出来的，不是嘴上说的。
    """
    seconds = clamp_timeout(MAX_PUSH_S if max_s is None else max_s)
    out = {"path": None, "bytes": 0, "level": {}, "reason": _device_refusal()}
    if out["reason"]:
        return out
    if not os.path.isdir(tempfile.gettempdir()):          # 连临时目录都不在，别硬写
        out["reason"] = "临时目录不可用，录不了"
        return out
    max_bytes = int(RATE * CHANNELS * BITS / 8 * seconds)
    buf = ctypes.create_string_buffer(max_bytes)
    hdr = WaveHdr(ctypes.cast(buf, ctypes.c_void_p), max_bytes, 0, 0, 0, 0, None, None)
    fmt = WaveFormatEx(1, CHANNELS, RATE, RATE * CHANNELS * BITS // 8,
                       CHANNELS * BITS // 8, BITS, 0)
    handle = ctypes.c_void_p()
    winmm = _winmm() if winmm is None else winmm
    winmm.waveInOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint,
                                 ctypes.POINTER(WaveFormatEx), ctypes.c_void_p,
                                 ctypes.c_void_p, ctypes.c_uint]
    dev, dev_name = pick_device(winmm)
    rc = winmm.waveInOpen(ctypes.byref(handle), dev, ctypes.byref(fmt), 0, 0, 0)
    if rc != 0:
        out["reason"] = "开不了麦克风（waveInOpen rc=%d，设备 %s）" % (rc, dev_name)
        return out
    try:
        winmm.waveInPrepareHeader(handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
        winmm.waveInAddBuffer(handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
        winmm.waveInStart(handle)
        t0 = time.time()
        while not release() and time.time() - t0 < seconds:
            time.sleep(0.02)
    finally:
        winmm.waveInStop(handle)
        winmm.waveInReset(handle)
        winmm.waveInUnprepareHeader(handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
        winmm.waveInClose(handle)
    n = int(hdr.dwBytesRecorded)
    vals = struct.unpack("<%dh" % (n // 2), bytes(buf[:n])) if n else ()
    rms = math.sqrt(sum(v * v for v in vals) / len(vals)) if vals else 0.0
    peak = max((abs(v) for v in vals), default=0)
    gain = 1.0
    if peak and peak < GAIN_PEAK_BELOW:
        gain = min(GAIN_MAX, GAIN_TARGET_PEAK / peak)
        vals = tuple(max(-32768, min(32767, int(v * gain))) for v in vals)
    directory = tempfile.mkdtemp(prefix=TEMP_DIR_PREFIX)
    path = os.path.join(directory, "ptt.wav")
    try:
        with wave.open(path, "wb") as wav_file:
            wav_file.setnchannels(CHANNELS)
            wav_file.setsampwidth(BITS // 8)
            wav_file.setframerate(RATE)
            wav_file.writeframes(struct.pack("<%dh" % len(vals), *vals) if vals else b"")
    except (OSError, ValueError) as exc:
        _discard(directory)
        out["reason"] = "录到了但写不下（%s）" % exc.__class__.__name__
        return out
    out["path"] = path
    out["bytes"] = n
    out["level"] = {"rms": round(rms, 1), "peak": peak,
                    "dbfs": round(20 * math.log10(rms / 32768), 1) if rms else -999.0,
                    "device": dev_name, "gain": round(gain, 2)}
    return out


def discard_record(record):
    """把 record_until 落的临时目录删干净（目录也删，不只删里面的 wav）。"""
    path = (record or {}).get("path")
    if not path:
        return False
    _discard(os.path.dirname(path))
    record["path"] = None
    return True


def _discard(directory):
    import shutil
    shutil.rmtree(directory, ignore_errors=True)


# 认一段 WAV：词表照旧走 stdin 的 JSON（绝不拼进脚本正文），WAV 住址走环境变量。
_PS_WAV = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$sr = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), [System.Text.Encoding]::UTF8)
$words = @(ConvertFrom-Json $sr.ReadToEnd())
$ci = [System.Globalization.CultureInfo]::GetCultureInfo([string]$env:PAI_EAR_CULTURE)
$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($ci)
$choices = New-Object System.Speech.Recognition.Choices
foreach ($w in $words) { [void]$choices.Add([string]$w) }
$gb = New-Object System.Speech.Recognition.GrammarBuilder
$gb.Culture = $ci
$gb.Append($choices.ToGrammarBuilder())
$engine.LoadGrammar((New-Object System.Speech.Recognition.Grammar($gb)))
$engine.SetInputToWaveFile([string]$env:PAI_EAR_WAV)
$res = $engine.Recognize()
$out = New-Object System.IO.StreamWriter([Console]::OpenStandardOutput(), [System.Text.Encoding]::UTF8)
if ($res) { $out.WriteLine([string]$res.Text) }
$out.Flush(); $out.Dispose()
try { $engine.Dispose() } catch { }
[Environment]::Exit(0)
"""


def recognize_wav(path, spawn=None):
    """把一段 WAV 对着固定命令词表认一遍（不实时开麦，读的是文件）。

    返回 {"text","command","reason","attempted"}；关态/文件不在/识别器起不来 = text 空 +
    一句原因，不抛出去。词表在外的话（不在词表里）只在 text 里给一次，不进原因串。
    ★attempted 区分的是【有没有真跑过这一次识别】：False = 一步没跑（开关挡下/文件不在），
    入口壳据此回非零退出码。P33 那一族就是这个坑 —— 关态却打印 rc=0，等于给了个静默的绿。
    """
    out = {"text": "", "command": None, "reason": _device_refusal(), "attempted": False}
    if out["reason"]:
        return out
    if not path or not os.path.isfile(path):
        out["reason"] = "没有这段音频文件，认不了"
        return out
    env = _base_env(DEFAULT_TIMEOUT_S)
    env[ENV_WAV] = path                                  # 走环境变量，不进 argv
    payload = json.dumps(list(COMMANDS), ensure_ascii=True).encode("ascii")
    out["attempted"] = True
    try:
        text = (_spawn_wav if spawn is None else spawn)(payload, env)
    except OSError as exc:
        out["reason"] = "没认成：识别器不可用（%s）" % exc
        return out
    except Exception as exc:
        out["reason"] = "这次没认成（已静默跳过）：%s" % exc.__class__.__name__
        return out
    body = (text or "").strip().lstrip("\ufeff")[:MAX_TEXT_CHARS]
    out["text"] = body
    out["command"] = match_command(body)
    out["reason"] = "ok" if out["command"] else ("听清了，但不在命令词表里" if body else "没听清")
    return out


def _spawn_wav(payload, env):
    timeout = clamp_timeout(env.get(ENV_TIMEOUT, DEFAULT_TIMEOUT_S)) + 20
    return _run_ps(_PS_WAV, payload, env, timeout)
