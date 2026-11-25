# -*- coding: utf-8 -*-
"""Office 生成：从一句话生成 Word / PPT / Excel，不新开页面。"""
from __future__ import annotations

from pai import paths

import datetime
import pathlib
import re

CAPABILITY = {
    "name": "Office 生成",
    "switch_env": "PAI_OFFICE_GEN",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "库缺失/内容为空=明确报错；只写固定输出目录，不覆盖源文件",
    "approval": "ADR-046：本地生成到固定目录，不新增开关",
}

OUT = paths.OFFICE_OUT


def _safe(s):
    s = re.sub(r'[\\/:*?"<>|]+', "_", str(s or "未命名")).strip(" .")
    return (s[:60] or "未命名")


def _path(title, ext):
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return OUT / ("%s-%s%s" % (stamp, _safe(title), ext))


def make_docx(title, paragraphs):
    from docx import Document
    doc = Document()
    doc.add_heading(str(title or "文档"), level=1)
    for p in paragraphs or []:
        doc.add_paragraph(str(p))
    p = _path(title, ".docx")
    doc.save(str(p))
    return p


def make_pptx(title, bullets):
    from pptx import Presentation
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = str(title or "演示")
    slide.placeholders[1].text = "知衡 · 时中"
    for b in bullets or []:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = str(b)[:80]
        s.placeholders[1].text = str(b)
    p = _path(title, ".pptx")
    prs.save(str(p))
    return p


def make_xlsx(title, columns, rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = _safe(title)[:31] or "Sheet1"
    ws.append([str(c) for c in (columns or ["列1"])])
    for r in rows or []:
        ws.append(list(r))
    p = _path(title, ".xlsx")
    wb.save(str(p))
    return p


def parse(text):
    raw = str(text or "").strip()
    low = raw.lower()
    if "ppt" in low or "演示" in low:
        kind = "pptx"
    elif "excel" in low or "表格" in low or "xlsx" in low:
        kind = "xlsx"
    else:
        kind = "docx"
    title = "未命名"
    m = re.search(r"标题[：:]\s*([^\n，,；;]+)", raw)
    if m:
        title = m.group(1).strip()
    else:
        m = re.search(r"(?:Word|word|文档|PPT|ppt|演示|Excel|excel|表格)[：:]\s*([^\n，,；;]+)", raw)
        if m:
            title = m.group(1).strip()
        else:
            title = {"pptx": "演示", "xlsx": "表格", "docx": "文档"}[kind]
    # 内容：只取冒号后的正文；没有就留空，绝不把命令整句当正文
    body = ""
    for marker in ("内容：", "内容:", "正文：", "正文:", "要点：", "要点:"):
        if marker in body:
            body = body.split(marker, 1)[1]
            break
    if kind == "pptx":
        items = [x.strip(" --\t") for x in re.split(r"[；;\n]|、", body) if x.strip()]
    elif kind == "xlsx":
        items = []
    else:
        items = [x.strip() for x in re.split(r"\n+", body) if x.strip()] or [body.strip()]
    return {"kind": kind, "title": title, "body": body, "items": items}


def generate(text):
    d = parse(text)
    if d["kind"] == "pptx":
        p = make_pptx(d["title"], d["items"][:20])
    elif d["kind"] == "xlsx":
        # 简单两列：条目 / 说明
        rows = []
        for line in re.split(r"[；;\n]", d["body"]):
            if not line.strip():
                continue
            parts = re.split(r"[,，|]", line, 1)
            rows.append([parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""])
        p = make_xlsx(d["title"], ["条目", "说明"], rows)
    else:
        p = make_docx(d["title"], d["items"][:200])
    return {"path": str(p), "kind": d["kind"], "title": d["title"]}
