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
    except ImportError as e:
        raise ExtractError("解析失败：缺少可选依赖 %s" % (getattr(e, "name", None) or "（未知）"))
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




INGEST_EXTS = (".txt", ".md", ".csv", ".json", ".html", ".pdf",
               ".docx", ".xlsx", ".xlsm", ".pptx")


def _safe_inbox_name(name):
    name = str(name or "").strip().replace("\\", "/")
    if not name or "/" in name or name.startswith(".") or ".." in name:
        raise ExtractError("文件名不合法")
    if pathlib.Path(name).suffix.lower() not in INGEST_EXTS:
        raise ExtractError("不支持的格式")
    return name


def list_inbox(root=None):
    from pai import paths
    d = pathlib.Path(root) if root is not None else paths.INBOX
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not p.is_file():
            continue
        try:
            name = _safe_inbox_name(p.name)
        except ExtractError:
            continue
        out.append({"name": name, "size": p.stat().st_size,
                    "mtime": int(p.stat().st_mtime)})
    return out


def ingest_inbox(filename, root=None):
    """把 inbox 文件转成 Markdown 文本，返回 (out_name, content, chars)。不落 ws。"""
    from pai import paths
    name = _safe_inbox_name(filename)
    d = pathlib.Path(root) if root is not None else paths.INBOX
    p = d / name
    if not p.is_file():
        raise ExtractError("收件箱里没有这个文件")
    text = extract(name, p.read_bytes())
    content = "# 导入：%s\n\n%s\n" % (name, text[:MAX_CHARS])
    stem = pathlib.Path(name).stem
    out_name = (stem or "import") + ".md"
    return out_name, content, len(text)
