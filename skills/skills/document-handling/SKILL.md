---
name: document-handling
version: 1.0.0
description: 把 PDF/Word/Excel 等放进 inbox，转 Markdown 后走文件审批。
---

# 文档导入

## 何时使用
把 PDF/Word/Excel 等放进 inbox，转 Markdown 后走文件审批。

## 怎么做
把文件放到 %LOCALAPPDATA%\Zhiheng\data\inbox；在 /files 点“导入”，批准后才写入 workspace。

## 验收标准
- 只在本地执行，不绕过审批。
- 结果可追溯；未执行的步骤不写成已完成。
