# -*- coding: utf-8 -*-
"""离线图片 OCR：Windows.Media.Ocr，只在本机跑，不出网。

不是视觉理解；离线时把图片里的文字读出来。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile

CAPABILITY = {
    "name": "离线图片 OCR",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "非 Windows/没中文 OCR/识别为空=OcrError；只处理本地临时图片，不出网",
    "approval": "ADR-046：没有开关的既成常开登记，不发明新开关",
}

PS = r"""Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
  $asTask = $asTaskGeneric.MakeGenericMethod($type)
  $t = $asTask.Invoke($null, @($op))
  $t.Wait(-1) | Out-Null
  $t.Result
}
[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:IMG)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
[IO.File]::WriteAllText($env:OUT, $result.Text, [Text.Encoding]::UTF8)
"""


class OcrError(ValueError):
    pass


def extract(data, suffix=".png"):
    if not data:
        raise OcrError("图片为空")
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    out = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
    tmp.write(data)
    tmp.close()
    out.close()
    try:
        env = dict(os.environ, IMG=tmp.name, OUT=out.name)
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", PS],
            env=env, capture_output=True, timeout=60)
        text = pathlib.Path(out.name).read_text(encoding="utf-8", errors="replace").lstrip("\ufeff").strip()
        if not text:
            raise OcrError("没有识别到文字")
        return text[:16000]
    except subprocess.TimeoutExpired:
        raise OcrError("OCR 超时") from None
    finally:
        for name in (tmp.name, out.name):
            try:
                os.unlink(name)
            except OSError:
                pass
