---
name: document-handling
description: 把一份文档（PDF/Word/Excel/txt/csv/html）转成 Markdown，先审批再进 workspace/。用于用户说"读一下这个文档""把这份文件转成 Markdown""导入这份文档"。
---

# 文档处理（document-handling）

## 何时使用
- 用户想把一份现成的文档（PDF、Word、Excel、txt、csv、html）转成 Markdown，放进工作区。
- 用户说"读一下这个文档""导入这份文件""转成 Markdown"。
- 不用于直接写新文件（那种走文件审批技能里模型输出的提案）。

## 怎么做
1. 用户先把文档放进 `inbox/` 文件夹（App 目录下的 `inbox/`，没有就新建）。
2. 调 `GET /api/inbox` 看收件箱里有哪些文档，再调 `POST /api/ingest`，传 `{"filename": "文档名"}`。
3. `ingest_document()` 会转换：优先用 `markitdown`（装了 `markitdown[docx,xlsx,pdf]` 才能读 docx/xlsx/pdf；没装时只读 txt/md/csv/json/html，读不了的格式会明确报错）；没装 markitdown 时用内置转换器（txt/csv/json/html 直接转）。
4. 转换结果是 Markdown，文件名是原名加 `.md`，先存进待审批（`pending_files`，`plan_id=0`、`step_idx=-1`），不直接写进 `workspace/`。
5. 用户在任务页的"待审批的文件"里批准后，`approve_pending()` 才把 `.md` 写进 `workspace/`（同样过文件名与 1MB 检查）。
6. 转换后内容为空、文件超过 1MB、文件名不合法、格式不支持时，明确报错，不建提案。

## 验收标准
- 转换出的 Markdown 保留原文结构（标题、表格、列表不丢）。
- 转换结果先审批、批准后才进 `workspace/`；未批准时工作区里没有这个文件。
- 不支持的格式、空内容、超大文件都有明确报错，不静默失败。
- `markitdown` 没装时，txt/csv/json/html 仍能转，docx/xlsx/pdf 明确提示要装什么。
