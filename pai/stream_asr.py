# -*- coding: utf-8 -*-
"""流式 ASR v2：优先 sherpa-onnx Zipformer-CTC 中文，回退 Vosk。"""
from __future__ import annotations

from pai import paths

import json
import os
import pathlib
import threading
import time
import uuid
import wave

from pai import punctuate as PT


CAPABILITY = {
    "name": "流式 ASR",
    "switch_env": "PAI_STREAM_ASR",
    "default_on": False,
    "stores_user_text": True,
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
    "network": False,
    "degrade": "模型缺失=返回未配置；不把静音当识别结果",
    "approval": "ADR-046：本地语音，不新增开关",
}

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHERPA_DEFAULT = ROOT / "data" / "sherpa-ctc"
VAD_DEFAULT = ROOT / "data" / "silero_vad.onnx"
VOSK_DEFAULT = os.environ.get("PAI_VOSK_MODEL") or str(paths.DATA_DIR / "models" / "vosk-model-small-cn")
_LOCK = threading.Lock()
_SHERPA = None
_VOSK_MODEL = None
_VAD = None
_SESSIONS = {}


def _sherpa_dir():
    return pathlib.Path(os.environ.get("PAI_SHERPA_MODEL", SHERPA_DEFAULT))


def backend():
    d = _sherpa_dir()
    if (d / "model.int8.onnx").is_file() and (d / "tokens.txt").is_file():
        try:
            import sherpa_onnx  # noqa: F401
            return "sherpa"
        except Exception:
            pass
    try:
        import vosk  # noqa: F401
        if pathlib.Path(os.environ.get("PAI_VOSK_MODEL", VOSK_DEFAULT)).is_dir():
            return "vosk"
    except Exception:
        pass
    return "none"


def available():
    return backend() != "none"


def _recognizer():
    global _SHERPA
    if _SHERPA is None:
        import sherpa_onnx
        d = _sherpa_dir()
        _SHERPA = sherpa_onnx.OnlineRecognizer.from_zipformer2_ctc(
            tokens=str(d / "tokens.txt"), model=str(d / "model.int8.onnx"),
            num_threads=2, enable_endpoint_detection=True)
    return _SHERPA


def _vosk_model():
    global _VOSK_MODEL
    if _VOSK_MODEL is None:
        import vosk
        _VOSK_MODEL = vosk.Model(os.environ.get("PAI_VOSK_MODEL", VOSK_DEFAULT))
    return _VOSK_MODEL


def _vad():
    global _VAD
    if _VAD is None:
        p = pathlib.Path(os.environ.get("PAI_VAD_MODEL", VAD_DEFAULT))
        if not p.is_file():
            return None
        try:
            import sherpa_onnx
            cfg = sherpa_onnx.VadModelConfig(
                silero_vad=sherpa_onnx.SileroVadModelConfig(
                    model=str(p), threshold=0.5, min_silence_duration=0.5,
                    min_speech_duration=0.2, window_size=512))
            _VAD = sherpa_onnx.VadModel.create(cfg)
        except Exception:
            _VAD = None
    return _VAD


class Stream:
    def __init__(self):
        self.backend = backend()
        self.finalized = ""
        self.updated = time.time()
        self.vad = _vad()
        self.vad_active = False
        self.silence_chunks = 0
        self.prev_pcm = b""
        self.last = {"text": "", "partial": "", "final": ""}
        if self.backend == "sherpa":
            self.rec = _recognizer()
            self.handle = self.rec.create_stream()
        elif self.backend == "vosk":
            import vosk
            self.rec = vosk.KaldiRecognizer(_vosk_model(), 16000)
            self.rec.SetWords(False)
        else:
            raise RuntimeError("没有可用的流式 ASR 模型")

    def _has_speech(self, pcm):
        if self.vad is None:
            return True
        import numpy as np
        a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        ws = 512
        for i in range(0, len(a) - ws + 1, ws):
            try:
                if self.vad.is_speech(a[i:i + ws].tolist()):
                    return True
            except Exception:
                return True
        return False

    def feed(self, pcm):
        with _LOCK:
            self.updated = time.time()
            if self.vad is not None:
                orig_pcm = pcm
                speech = self._has_speech(pcm)
                if speech:
                    was_active = self.vad_active
                    self.vad_active = True
                    self.silence_chunks = 0
                    if not was_active and self.prev_pcm:
                        pcm = self.prev_pcm + pcm
                elif self.vad_active:
                    self.silence_chunks += 1
                    if self.silence_chunks > 3:
                        self.vad_active = False
                if not speech and not self.vad_active:
                    self.prev_pcm = orig_pcm
                    full = self.last.get("text") or self.finalized
                    return {"text": full, "partial": self.last.get("partial") or "",
                            "final": "", "punctuated": PT.punctuate(full, final=False),
                            "backend": self.backend, "vad": True}
                self.prev_pcm = orig_pcm
            if self.backend == "sherpa":
                import numpy as np
                a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                if a.size:
                    self.handle.accept_waveform(16000, a)
                    while self.rec.is_ready(self.handle):
                        self.rec.decode_stream(self.handle)
                    text = self.rec.get_result(self.handle) or ""
                    if self.rec.is_endpoint(self.handle):
                        self.finalized = (self.finalized + " " + text).strip()
                        self.rec.reset(self.handle)
                        text = ""
                    full = (self.finalized + " " + text).strip()
                    res = {"text": full, "partial": text, "final": "",
                           "punctuated": PT.punctuate(full, final=False),
                           "backend": "sherpa", "vad": self.vad is not None}
                    self.last = res
                    return res
            # Vosk fallback
            self.rec.AcceptWaveform(pcm)
            partial = json.loads(self.rec.PartialResult()).get("partial") or ""
            res = json.loads(self.rec.Result()).get("text") or ""
            if res:
                self.finalized = (self.finalized + " " + res).strip()
            full = (self.finalized + " " + partial).strip()
            res = {"text": full, "partial": partial, "final": res,
                   "punctuated": PT.punctuate(full, final=False),
                   "backend": "vosk", "vad": self.vad is not None}
            self.last = res
            return res

    def stop(self):
        with _LOCK:
            if self.backend == "sherpa":
                try:
                    self.handle.input_finished()
                    while self.rec.is_ready(self.handle):
                        self.rec.decode_stream(self.handle)
                    text = self.rec.get_result(self.handle) or ""
                except Exception:
                    text = ""
                full = (self.finalized + " " + text).strip()
                return full
            res = json.loads(self.rec.FinalResult()).get("text") or ""
            return (self.finalized + " " + res).strip()


def start():
    if not available():
        raise RuntimeError("流式识别不可用")
    sid = uuid.uuid4().hex
    _SESSIONS[sid] = Stream()
    return sid


def feed(sid, pcm):
    s = _SESSIONS.get(sid)
    if s is None:
        raise ValueError("会话不存在或已过期")
    return s.feed(pcm)


def stop(sid):
    s = _SESSIONS.pop(sid, None)
    if s is None:
        return ""
    return s.stop()


def cleanup(max_age=300):
    now = time.time()
    for sid, s in list(_SESSIONS.items()):
        if now - s.updated > max_age:
            _SESSIONS.pop(sid, None)


_MEETINGS = {}
_DIAR_SEG = paths.DATA_DIR / "diarization" / "sherpa-onnx-pyannote-segmentation-3-0" / "model.int8.onnx"
_DIAR_EMB = paths.DATA_DIR / "diarization" / "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"


def diarization_status():
    seg = pathlib.Path(os.environ.get("PAI_DIARIZATION_SEG", _DIAR_SEG))
    emb = pathlib.Path(os.environ.get("PAI_DIARIZATION_EMB", _DIAR_EMB))
    return {"segmentation": seg.is_file(), "embedding": emb.is_file(),
            "seg": str(seg), "emb": str(emb)}


def diarize_file(wav_path):
    """sherpa-onnx 离线说话人分离；返回 [{start,end,speaker}]。失败抛异常。"""
    st = diarization_status()
    if not (st["segmentation"] and st["embedding"]):
        raise RuntimeError("说话人分离模型缺失：%s / %s" % (st["seg"], st["emb"]))
    import wave
    import numpy as np
    import sherpa_onnx
    with wave.open(str(wav_path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise RuntimeError("分离只支持单声道 16-bit WAV")
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(st["seg"]))),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(st["emb"])),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.5),
        min_duration_on=0.3, min_duration_off=0.5,
    )
    if not cfg.validate():
        raise RuntimeError("说话人分离配置无效")
    sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)
    if sr != sd.sample_rate:
        # 线性重采样到模型采样率
        n = max(1, int(len(samples) * sd.sample_rate / sr))
        out = np.zeros(n, dtype=np.float32)
        for i in range(n):
            out[i] = samples[min(len(samples) - 1, int(i * sr / sd.sample_rate))]
        samples = out
    result = sd.process(samples)
    if result is None:
        return []
    segs = result.sort_by_start_time()
    return [{"start": float(x.start), "end": float(x.end), "speaker": int(x.speaker),
             "confidence": float(getattr(x, "confidence", 0) or 0)} for x in segs]


def meeting_start():
    sid = start()
    _MEETINGS[sid] = {"start": time.time(), "segments": [], "last": "", "pcm": bytearray()}
    return sid


def meeting_feed(sid, pcm):
    meta = _MEETINGS.get(sid)
    if meta is None:
        raise ValueError("会议不存在")
    res = feed(sid, pcm)
    try:
        if len(meta["pcm"]) < 16000 * 2 * 60 * 60:
            meta["pcm"].extend(pcm)
    except Exception:
        pass
    text = str(res.get("text") or "").strip()
    if text and text != meta["last"]:
        elapsed = int(time.time() - meta["start"])
        meta["segments"].append({"sec": elapsed, "text": text})
        meta["last"] = text
    return res


def meeting_stop(sid):
    meta = _MEETINGS.pop(sid, None)
    text = stop(sid)
    if meta is None:
        return {"ok": False, "reason": "会议不存在"}
    if text and text.strip() and text.strip() != meta.get("last"):
        meta["segments"].append({"sec": int(time.time() - meta["start"]), "text": text.strip()})
    from pai import paths
    out = paths.DATA_DIR / "meetings"
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out / ("meeting-%s.md" % stamp)
    speakers, derr = [], ""
    try:
        import tempfile
        wav = pathlib.Path(tempfile.mkdtemp(prefix="zhiheng-meeting-")) / "meeting.wav"
        with wave.open(str(wav), "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
            wf.writeframes(bytes(meta["pcm"]))
        speakers = diarize_file(wav)
    except Exception as e:
        derr = "%s: %s" % (type(e).__name__, e)
    def _spk(sec):
        for x in speakers:
            if x["start"] <= sec <= x["end"]:
                return "说话人%d" % (x["speaker"] + 1)
        return "说话人?"
    lines = ["# 会议纪要 %s" % time.strftime("%Y-%m-%d %H:%M"), ""]
    if derr:
        lines += ["> 说话人分离未完成：%s" % derr, ""]
    for seg in meta["segments"]:
        lines.append("- [%02d:%02d] %s %s" % (seg["sec"] // 60, seg["sec"] % 60, _spk(seg["sec"]), seg["text"]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "sid": sid, "path": str(path), "segments": len(meta["segments"]),
            "speakers": len({x["speaker"] for x in speakers}) if speakers else 0,
            "diarization": derr or "ok", "text": "\n".join(lines)}
