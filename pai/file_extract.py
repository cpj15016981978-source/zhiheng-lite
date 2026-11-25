# -*- coding: utf-8 -*-
"""Web 对话附件提取：文本 / PDF / Word / Excel。

只做本地解析，不出网；不接收图片（图片 OCR 仍走桌面端）。
"""
from __future__ import annotations

import io
import pathlib

MAX_BYTES = 10 * 1024 * 1024
MAX_CHARS = 24000

CAPABILITY = {
    "name": "附件文本提取",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "不支持的类型/空文件/超 10MB=ExtractError；解析失败明确报类型，不返回空串冒充成功",
    "approval": "ADR-046：没有开关的既成常开登记，不发明新开关",
}


class ExtractError(ValueError):
    pass


def _text_from_pdf(data):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages[:40]:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _text_from_docx(data):
    import docx
    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for table in d.tables[:5]:
        for row in table.rows[:30]:
            parts.append("\t".join(c.text.strip() for c in row.cells))
    return "\n".join(parts)


def _text_from_xlsx(data):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets[:5]:
        parts.append("[%s]" % ws.title)
        for row in ws.iter_rows(max_row=200, values_only=True):
            parts.append("\t".join("" if v is None else str(v) for v in row))
    return "\n".join(parts)


def _text_from_pptx(data):
    from pptx import Presentation
    prs = Presentation(io.BytesIO(data))
    parts = []
    for i, slide in enumerate(prs.slides, 1):
        if i > 40:
            break
        parts.append("[第 %d 页]" % i)
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                parts.append(shape.text)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    parts.append("\t".join(c.text for c in row.cells))
    return "\n".join(parts)


def extract(name, data):
    if not data:
        raise ExtractError("文件为空")
    if len(data) > MAX_BYTES:
        raise ExtractError("文件超过 10MB")
    ext = pathlib.Path(str(name or "")).suffix.lower()
    try:
        if ext in (".txt", ".md", ".py", ".json", ".csv", ".log", ".yaml", ".yml",
                   ".html", ".js", ".ts", ".sql"):
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("gbk", errors="replace")
        elif ext == ".pdf":
            text = _text_from_pdf(data)
        elif ext == ".docx":
            text = _text_from_docx(data)
        elif ext in (".xlsx", ".xlsm"):
            text = _text_from_xlsx(data)
        elif ext == ".pptx":
            text = _text_from_pptx(data)
        else:
            raise ExtractError("暂不支持：%s" % (ext or "无扩展名"))
    except ExtractError:
        raise
    except Exception as e:
        raise ExtractError("解析失败：%s" % type(e).__name__)
    text = (text or "").strip()
    if not text:
        raise ExtractError("没有提取到文字")
    try:
        from pai import exec_guard as EG
        EG.audit("parse", "low", "allow", str(name)[:200], "chars=%d" % len(text))
    except Exception:
        pass
    return text[:MAX_CHARS]

