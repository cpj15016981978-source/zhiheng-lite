---
name: file-approval
description: 模型想写文件时，先放进"待审批"，用户点了批准才真正写进 workspace/。用于任何要把内容保存成文件的动作。
---

# 文件审批（file-approval）

## 何时使用
- 模型输出里出现 `[写文件: 文件名]` 的提案，或用户说"保存成文件""写进文件""存下来"。
- 文档导入（`POST /api/ingest`）转成的 Markdown 也要走这条审批，不能直接写。
- 任何写进 `workspace/` 的动作都必须先过审批，没有例外。

## 怎么做
1. 提案先存进 `pending_files` 表（`extract_file_proposals()` 从模型输出里提取 `[写文件: ...]...[/写文件]` 块；`ingest_document()` 从导入的文档生成提案）。
2. 用户在任务页的"待审批的文件"里看到提案（`GET /api/pending`），点"批准写入"或"拒绝"。
3. 批准时 `approve_pending()` 再做一次检查：文件名过 `safe_workspace_name()`（只允许 `workspace/` 下的直接文件，后缀限 txt/md/csv/json/html/py，禁路径穿越、禁前导点、禁 `.exe`），内容不超过 1MB；通过才写进 `workspace/`，并把状态改成 approved。
4. 拒绝时 `reject_pending()` 把状态改成 rejected，不写文件。
5. 不要绕开审批直接写文件；不要把审批表里的内容直接拼进路径。

## 验收标准
- 未批准的文件不在 `workspace/` 里；批准后才出现，且内容与提案一致。
- 文件名 `../x.py`、`.exe`、无后缀、带斜杠的提案一律被拒。
- 超过 1MB 的提案批准时被拒，并有明确提示。
- 批准/拒绝后状态正确更新，重复批准同一条不重复写文件。
