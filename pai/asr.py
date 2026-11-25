# -*- coding: utf-8 -*-
"""ASR 角色：优先 HTTP，缺省用本地 Vosk 小模型。"""
from __future__ import annotations

from pai import paths

import json
import os
import pathlib
import urllib.request
import uuid
import wave

DEFAULT_VOSK = os.environ.get("PAI_VOSK_MODEL") or str(paths.DATA_DIR / "models" / "vosk-model-small-cn")

CAPABILITY = {
    "name": "ASR 角色",
    "switch_env": "PAI_ASR_BASE_URL",
    "default_on": False,
    "stores_user_text": False,
    "network": True,
    "degrade": "优先 HTTP；否则本地 Vosk；都没有就明确报未配置",
    "approval": "ADR-044 本地模型/HTTP 接入登记",
}


def backend() -> str:
    if (os.environ.get("PAI_ASR_BASE_URL") or "").strip():
        return "http"
    if pathlib.Path(os.environ.get("PAI_VOSK_MODEL", DEFAULT_VOSK)).is_dir():
        try:
            import vosk  # noqa: F401
            return "vosk"
        except Exception:
            pass
    return "none"


def available() -> bool:
    return backend() != "none"


def transcribe(path, timeout: int = 180) -> str:
    path = pathlib.Path(path)
    base = (os.environ.get("PAI_ASR_BASE_URL") or "").strip().rstrip("/")
    if base:
        return _http_transcribe(path, base, timeout)
    model_dir = pathlib.Path(os.environ.get("PAI_VOSK_MODEL", DEFAULT_VOSK))
    if not model_dir.is_dir():
        raise RuntimeError("ASR 未配置：设置 PAI_ASR_BASE_URL 或 PAI_VOSK_MODEL")
    try:
        import vosk
    except Exception as exc:
        raise RuntimeError("ASR 未配置：vosk 未安装（%s）" % exc) from None
    model = vosk.Model(str(model_dir))
    wf = wave.open(str(path), "rb")
    if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
        wf.close()
        raise RuntimeError("Vosk 需要单声道 16-bit WAV")
    rate = wf.getframerate()
    raw = wf.readframes(wf.getnframes())
    wf.close()
    if rate != 16000:
        import array
        samples = array.array("h")
        samples.frombytes(raw)
        n = max(1, int(len(samples) * 16000 / rate))
        out = array.array("h", [0]) * n
        for i in range(n):
            src = min(len(samples) - 1, int(i * rate / 16000))
            out[i] = samples[src]
        raw = out.tobytes()
    rec = vosk.KaldiRecognizer(model, 16000)
    rec.SetWords(False)
    for off in range(0, len(raw), 8000):
        rec.AcceptWaveform(raw[off:off + 8000])
    return str(json.loads(rec.FinalResult()).get("text") or "").strip()


def _http_transcribe(path: pathlib.Path, base: str, timeout: int) -> str:
    from pai import offline as OFF
    OFF.assert_allowed("asr", RuntimeError, "HTTP 语音识别")
    boundary = "----dsh" + uuid.uuid4().hex
    data = path.read_bytes()
    body = (
        ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
         "Content-Type: audio/wav\r\n\r\n" % (boundary, path.name)).encode("utf-8")
        + data
        + ("\r\n--%s--\r\n" % boundary).encode("utf-8")
    )
    model = (os.environ.get("PAI_ASR_MODEL") or "whisper-1").strip()
    body = (
        ("--%s\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n%s\r\n" % (boundary, model)).encode("utf-8")
        + body
    )
    headers = {"Content-Type": "multipart/form-data; boundary=" + boundary}
    key = (os.environ.get("PAI_ASR_API_KEY") or os.environ.get("PAI_MODEL_API_KEY") or "").strip()
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(base + "/audio/transcriptions", data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode("utf-8"))
    return str(payload.get("text") or "").strip()
