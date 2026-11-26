# LowSpec Muse v2.2 变更日志（2026-11-25～26）

在 v2.1 基础上加四个能力，都是加法；v2 的规划循环、文件审批、自我检查和 v2.1 的输入法守卫都没动。

## 2026-11-26 新增：标准对话接口

- `POST /v1/chat/completions`（OpenAI 兼容，本机回环 `127.0.0.1:8080`）+ `GET /v1/models` + `GET /v1/status`。接口做法学 PolyStrata（只学接口，不抄引擎/代码）。
- 请求走现有的 `run_ai_agent`，和网页 `/api/chat` 同一条路：记忆、技能、规划格式、代码块保存、文件审批全都不变，不另开执行路径。回复是标准 `chat.completion` 形状，另附 `lowspec` 字段（来源/技能/存下的脚本）。
- 请求里的 `model` 字段只作参考，实际用的永远是本机配置好的模型；`stream: true` 明确返回 400（暂不支持流式）；`load`/`unload` 没做——本地没有要加载/卸载的重型模型，Ollama 自己管模型（未实现的路径返回 404，不装假接口）。
- 测试：`tests/test_v22.py` 新增第 5 节 14 项（标准形状、Issue 文字直达、校验与错误、models/status、回环绑定），本机无可选依赖环境 65/65 通过；另用真实 Issue #1 全文走了一次标准接口（桩模型），10/10 通过（Issue 直达、记忆两条、代码块存进 scripts/、models/status 正常）。
- 边界：本机实测用的是桩模型（只 stub 掉模型调用，其余全是真实代码）；真机 Ollama / 真调免费 API 仍待在用户电脑上验证。

## 2026-11-26 修复：Issue #1 Shift+Enter 会直接发送

- 对话输入框 keydown 加一行：`Enter && shiftKey` 时直接返回，不发送（单行输入框，没有换行可插，就只不发送）。搜索框与任务目标框的 Enter 处理顺手加了同样的 `!shiftKey` 判断。
- 验证：Node 脚本抽 index.txt 三处处理函数原文跑了 11 项用例（组字中 / isComposing / keyCode 229 / Shift+Enter 不发送；普通 Enter 与组字结束后 Enter 正常发送；空输入不发送；搜索框与任务框各 2 项），11/11 通过。新增 `tests/test_ime_guard.py`（5 项静态检查，离线可跑）把守卫钉住。
- 注意：main（知衡）那边的聊天 UI（web/chat.html、web/app.js、dashboard.html、webapp.py）本来就有 `!shiftKey` 守卫，还有 tests/test_web_ime_guard.py 钉着，所以 Issue #1 只修 LowSpec 这边，main 不用动。

## 新增

1. **记忆语义搜索**（思路学 sqlite-vec，Apache-2.0）
   - 记忆除了关键词搜，还能按意思搜：记忆页搜框旁多了"关键词 / 语义 / 混合"三种模式。
   - 做法：记忆的向量以 float32 BLOB 存在 `memory_vectors` 表；嵌入用 Ollama 的 BGE-M3（`ollama pull bge-m3`，可选）。
   - 装了 `sqlite-vec` 就用它的 vec0 虚拟表做 KNN 加速；没装就用 Python 算余弦；嵌入模型不可用时退回关键词搜索，App 不会崩。
   - 新增：`GET /api/search?mode=semantic|hybrid`、`POST /api/memories/reindex`（给旧记忆补向量）。
   - 旧记忆不会自动有向量：第一次用语义搜索前，去记忆页搜一次会自动补，或调 reindex 接口。
2. **技能包**（格式学 anthropics/skills，内容自己写，没抄它的文字）
   - `skills/` 目录里 6 份 SKILL.md：规划、文件审批、自我检查、对话、定时任务、文档处理；每份写明触发条件、怎么做、验收标准。
   - 任务匹配到技能时自动引用它的步骤（只引用一个，不全塞进提示词）；对话回答下面会显示用到的技能；设置页能看全部技能。
   - 新增：`GET /api/skills`、`GET /api/skill?name=...`、`GET /api/skill/match?q=...`。
3. **文档导入**（转换思路学 microsoft/markitdown，MIT）
   - 把 PDF/Word/Excel/txt/csv/html 放进 `inbox/`，在任务页点"导入"转成 Markdown；转完先到"待审批"，批准后才写进 `workspace/`，和模型写文件走同一条审批。
   - 转换优先用 `markitdown`（装了 `markitdown[docx,xlsx,pdf]` 才能读 docx/xlsx/pdf）；没装时内置转换器只读 txt/md/csv/json/html，读不了的格式明确报错。
   - 新增：`GET /api/inbox`、`POST /api/ingest`。

## 依赖变化

- `start.bat` 的安装行多了两个可选包：`"markitdown[docx,xlsx,pdf]"` 和 `sqlite-vec`。没装上也不影响启动，只是对应功能会降级（见上）。
- 语义搜索是可选的：想用就再拉 `ollama pull bge-m3`，不拉也能用关键词搜索。

## 文件变化

- `app.py`：808 行 → 1336 行（只加了新能力，没改 v2/v2.1 的逻辑）。
- `index.txt`：加了搜索模式选择、导入文档区、技能包卡片、技能显示；输入法守卫三处没动。
- `start.bat`：标题改成 v2.2，安装行加了两个可选包。
- `README.md`：加了 v2.2 说明、验证步骤 9/10/11。
- 新增：`skills/`（6 份 SKILL.md）、`tests/test_v22.py`、本文件。

## 已知边界（本机实测说明）

- 本机用桩模块验证了 55 项（见 `tests/test_v22.py`，含回归项）：有 markitdown + sqlite-vec 时 55 项全过；无可选依赖时 51 项全过（4 项 vec0 跳过）。没在真 Windows 电脑上跑过 Ollama / 真调过 API。
- 语义搜索的真机效果（BGE-M3 向量质量、vec0 在 Windows 上加载）要到用户电脑上按 README 第 9 条验。
- `markitdown` 的 PDF 转换质量不如专业 PDF 工具；OCR、图片描述没装（不依赖）。
