# -*- coding: utf-8 -*-
"""X6 按键说话：按住 F8 录，松开停，送固定命令词表；不落音频。

用法：
    python tools\\push_to_talk.py --simulate 2      # 不用热键，直接录 2 秒
    python tools\\push_to_talk.py --wav test.wav    # 只测识别，不碰麦克风
    python tools\\push_to_talk.py                   # 按住 F8

★这件是【入口壳】：开麦克风（waveInOpen）、读按键（GetAsyncKeyState）、起 PowerShell 认
一段 WAV —— 三样设备动作全住在 pai/ear.py 里，归 X6 那条开关 PAI_EAR_BACKEND 管。
以前它们写在这件里，登记面和闸门都看不见（黑板 P32），Boss 2026-10-25 选【②上收 pai/】。
所以本文件里再出现任何 ctypes/winmm/System.Speech 就是违规，
tests/test_tool_entry_surface.py 会当场红。下面 set_enabled() 那行 = 运行这一次的人为授权，
只改本进程、不落盘（与 tools/say.py 同形）。
退出码：0 = 真跑过一次识别（听清与否写在 JSON 的 reason 里）/ 2 = 一步没跑
（开关没拧开、没录到、文件不在）—— 关态回 0 就是「静默的绿」，别把它当成功。
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SPINE = Path(os.environ.get("PAI_SPINE") or HERE)
sys.path.insert(0, str(SPINE))
from pai import ear  # noqa: E402
from pai import voice  # noqa: E402
from pai.memory import Memory  # noqa: E402

HOTKEYS = ear.HOTKEYS           # 按键表唯一住址在 ear.py，这里不抄第二份


def dispatch(command):
    """命中的命令落成机器上的动作。每个动作都走 pai/ 的上层入口，不自己碰表。"""
    if not command:
        return {"ok": False, "reason": "没有命中命令"}
    if command == ear.CMD_STOP_SPEAK:
        return {"ok": bool(voice.stop()), "action": "stop"}
    if command == ear.CMD_PROACTIVE_OFF:
        voice.set_proactive(False)
        return {"ok": True, "action": "proactive_off"}
    if command == ear.CMD_PROACTIVE_ON:
        voice.set_proactive(True)
        return {"ok": True, "action": "proactive_on"}
    if command == ear.CMD_REPEAT_LAST:
        if not voice.speaking_enabled():
            return {"ok": False, "action": "repeat",
                    "reason": "语音开关是关的；念最后一条不自行打开"}
        db = SPINE / "data" / "memory.db"
        if not db.exists():
            return {"ok": False, "action": "repeat", "reason": "记忆库不存在"}
        text = ""
        m = Memory(str(db))
        try:
            for ep in reversed(m.recent_episodes(50)):
                if ep.get("role") == "ai" and (ep.get("content") or "").strip():
                    text = ep["content"]
                    break
        finally:
            m.close()
        if not text:
            return {"ok": False, "action": "repeat", "reason": "记忆库里没有上一句"}
        trace = voice.speak(text)
        return {"ok": bool(trace.get("spoken")), "action": "repeat"}
    return {"ok": False, "reason": "未知命令"}


def report(text, command, action, extra=None):
    body = {"text": text, "command": command, "action": action}
    body.update(extra or {})
    print(json.dumps(body, ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulate", type=float, default=None, help="不用热键，直接录 N 秒")
    ap.add_argument("--wav", default=None, help="只识别已有 WAV，不碰麦克风")
    ap.add_argument("--hotkey", default="F8", choices=sorted(HOTKEYS))
    ap.add_argument("--delay", type=float, default=0.0, help="开始录音前等 N 秒")
    ap.add_argument("--keep-switch", action="store_true",
                    help="不替本进程拧开 PAI_EAR_BACKEND（关态跑法，用来验证它真不开设备）")
    args = ap.parse_args(argv)

    if not args.keep_switch:
        ear.set_enabled(True)          # 运行这一次就是你授权它听；只动本进程环境变量

    if args.wav:
        got = ear.recognize_wav(args.wav)
        report(got["text"], got["command"], dispatch(got["command"]),
               {"mode": "wav", "reason": got["reason"]})
        # 一步没跑（开关挡下/文件不在）就不许回 0 —— 双击的入口把 0 读成「认过了」
        return 0 if got["attempted"] else 2

    if args.delay:
        print("%d 秒后开始录音……" % int(args.delay), file=sys.stderr)
        time.sleep(args.delay)

    if args.simulate:
        print("录 %d 秒……" % int(args.simulate), file=sys.stderr)
        rec = ear.record_until(lambda: False, max_s=args.simulate)
    else:
        print("按住 %s 说话，松开结束……" % args.hotkey, file=sys.stderr)
        while not ear.key_down(args.hotkey):
            time.sleep(0.02)
        rec = ear.record_until(lambda: not ear.key_down(args.hotkey))

    if not rec["path"]:
        report("", None, {"ok": False, "reason": rec["reason"]},
               {"mode": "push_to_talk", "reason": rec["reason"], "bytes": 0, "level": {},
                "saved": False})
        return 2
    try:
        got = ear.recognize_wav(rec["path"])
    finally:
        ear.discard_record(rec)        # 临时 WAV 连它自己的目录一起收掉
    report(got["text"], got["command"], dispatch(got["command"]),
           {"mode": "push_to_talk", "reason": got["reason"], "bytes": rec["bytes"],
            "level": rec["level"], "saved": False})
    return 0


if __name__ == "__main__":
    sys.exit(main())
