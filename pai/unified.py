# -*- coding: utf-8 -*-
"""统一入口：主应用接管 /task、/tasks、/search 与代码块落盘。"""
from __future__ import annotations

from pai import paths

import datetime
import os
import pathlib
import re
import sqlite3
import threading

from pai import atomic as AT

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = paths.DATA_DIR / "tasks.db"
SCRIPTS = paths.WORKSPACE / "scripts"
_LOCK = threading.Lock()

CAPABILITY = {
    "name": "统一命令入口",
    "switch_env": "PAI_UNIFIED",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "未知命令=返回用法；任务失败=记录错误；代码只存不执行",
    "approval": "ADR-046：本地命令入口，不新增开关",
}


def _conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB), timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS reminders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        time_str TEXT NOT NULL,
        prompt TEXT NOT NULL,
        last_run TEXT,
        enabled INTEGER NOT NULL DEFAULT 1,
        action TEXT NOT NULL DEFAULT 'reminder',
        payload TEXT NOT NULL DEFAULT ''
    )""")
    cols = {r[1] for r in c.execute("PRAGMA table_info(reminders)")}
    if "action" not in cols:
        c.execute("ALTER TABLE reminders ADD COLUMN action TEXT NOT NULL DEFAULT 'reminder'")
    if "payload" not in cols:
        c.execute("ALTER TABLE reminders ADD COLUMN payload TEXT NOT NULL DEFAULT ''")
    c.commit()
    return c


def add_task(time_str, prompt, name="手动任务", action="reminder", payload=""):
    try:
        datetime.datetime.strptime(time_str, "%H:%M")
    except ValueError:
        return "时间格式不对，用 HH:MM，例如 21:30。"
    prompt = str(prompt or "").strip()
    if not prompt:
        return "任务内容为空。"
    c = _conn()
    try:
        c.execute("INSERT INTO reminders(name,time_str,prompt,action,payload) VALUES(?,?,?,?,?)",
                  (str(name or "手动任务"), time_str, prompt, str(action or "reminder"), str(payload or "")))
        c.commit()
    finally:
        c.close()
    return "已加%s「%s」，每天 %s：%s" % ("定时报表" if action == "report" else "提醒",
                                     name or "手动任务", time_str, prompt)


def list_tasks():
    c = _conn()
    try:
        rows = c.execute("SELECT id,name,time_str,prompt,last_run,action FROM reminders WHERE enabled=1 ORDER BY id").fetchall()
    finally:
        c.close()
    if not rows:
        return "暂无提醒。用“提醒：21:30 喝水”加一个。"
    return "\n".join("- #%s%s「%s」每天 %s：%s（上次 %s）" % (
        i, "[报表]" if a == "report" else "", n, t, p, r or "还没跑过") for i, n, t, p, r, a in rows)


def update_task(rid, time_str=None, prompt=None, name=None):
    c = _conn()
    try:
        row = c.execute("SELECT id,time_str,prompt,name FROM reminders WHERE id=?", (rid,)).fetchone()
        if not row:
            return "没有这条提醒：%s" % rid
        t = str(time_str or row[1])
        p = str(prompt or row[2])
        n = str(name or row[3])
        try:
            datetime.datetime.strptime(t, "%H:%M")
        except ValueError:
            return "时间格式不对，用 HH:MM"
        c.execute("UPDATE reminders SET time_str=?,prompt=?,name=? WHERE id=?", (t, p, n, rid))
        c.commit()
        return "已更新提醒 #%s：每天 %s %s" % (rid, t, p)
    finally:
        c.close()


def delete_task(rid):
    c = _conn()
    try:
        cur = c.execute("DELETE FROM reminders WHERE id=?", (rid,))
        c.commit()
        return "已删除提醒 #%s" % rid if cur.rowcount else "没有这条提醒：%s" % rid
    finally:
        c.close()


def _search(mem, q):
    rows = mem.recall(q, k=5) if mem is not None else []
    if not rows:
        return "记忆里没找到「%s」。" % q
    out = ["记忆里找到 %d 条：" % len(rows)]
    for r in rows:
        it = r.get("item") or {}
        if it.get("type") == "episode":
            out.append("- [%s] %s" % (it.get("ts", ""), str(it.get("content") or "")[:180]))
        else:
            out.append("- %s %s = %s" % (it.get("subject"), it.get("predicate"), it.get("object")))
    return "\n".join(out)


def save_code_blocks(text):
    blocks = re.findall(r"```(?:python|py)?\n(.*?)```", str(text or ""), re.S)
    if not blocks:
        return []
    SCRIPTS.mkdir(parents=True, exist_ok=True)
    saved = []
    for i, code in enumerate(blocks, 1):
        name = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-%d.py" % i
        p = SCRIPTS / name
        if p.exists():
            continue
        AT.write_text(p, code.strip() + "\n", encoding="utf-8")
        saved.append(str(p))
    return saved


def _jobs(text):
    from pai import job_runner as JR
    if not JR.enabled():
        return "长任务未开启（PAI_JOB_RUNNER）"
    if text in ("长任务", "任务队列"):
        rows = JR.list_jobs()
        if not rows:
            return "还没有长任务。用“长任务：标题”创建。"
        return "长任务：\n" + "\n".join(
            "- [%s] %s%s" % (x.get("status") or "", x.get("title") or "", " · 已批准" if x.get("approved") else " · 未批准")
            for x in rows[:20])
    m = re.match(r"^长任务[:：]\s*(.+)$", text)
    if m:
        row = JR.create(m.group(1).strip())
        return "已创建长任务：%s（%s）" % (row.get("title"), row.get("id"))
    return None


def _mcp(text="MCP"):
    from pai import mcp_client as MC
    import json
    if text.upper() in ("MCP市场", "MCP：市场", "MCP:市场"):
        return "MCP 市场：http://127.0.0.1:8753/mcp"
    rows = MC.servers()
    if text.upper() == "MCP":
        return "MCP 服务：\n" + ("\n".join("- " + str(x.get("name")) for x in rows) if rows else "没有配置 MCP 服务。") + \
               "\n\n调用：MCP：调用 <服务> <工具> {json参数}"
    m = re.match(r"^MCP[:：]\s*调用\s+(\S+)\s+(\S+)\s*(.*)$", text, re.I)
    if not m:
        return "MCP 调用格式：MCP：调用 demo echo {\"text\":\"hi\"}"
    server, tool, raw = m.group(1), m.group(2), (m.group(3) or "{}").strip()
    try:
        args = json.loads(raw or "{}")
        if not isinstance(args, dict):
            raise ValueError("参数必须是 JSON 对象")
    except Exception as e:
        return "MCP 参数不是合法 JSON：%s" % e
    from pai import approval_token as TOK
    TOK.issue("mcp", "MCP %s/%s" % (server, tool),
              action={"type": "mcp", "server": server, "tool": tool, "args": args})
    return "已提交待审批（MCP %s/%s），去 http://127.0.0.1:8753/approvals" % (server, tool)


def _continuity():
    from pai import continuity as CT
    from pai import profile as PF
    d = CT.all_threads(PF.current())
    due = CT.due(PF.current())
    lines = []
    for x in (d or [])[:10]:
        lines.append("- " + str(x.get("title") or x.get("id") or ""))
    for x in (due or [])[:10]:
        lines.append("- [到期] " + str(x.get("title") or x.get("id") or ""))
    return "续线：\n" + ("\n".join(lines) if lines else "没有续线。")


def _agents():
    from pai import agent_registry as AR
    rows = AR.list_agents()
    return "代理：\n" + ("\n".join("- " + str(x.get("name") or x.get("id")) for x in rows) if rows else "没有代理。")


def _models():
    from pai import model_packs as MP
    rows = MP.installed()
    return "模型包：\n" + ("\n".join("- " + str(x.get("name") or x.get("id") or x) for x in rows) if rows else "没有模型包。")


def _code(text, mem=None):
    from pai import code_agent as CODE
    from pai import paths
    root = str(paths.WORKSPACE)
    if text == "代码":
        return "代码命令：\n- 代码：搜 关键词\n- 代码：读 相对路径\n（写文件仍走 /api/code/apply 审批）"
    m = re.match(r"^代码[:：]\s*搜\s+(.+)$", text)
    if m:
        rows = CODE.search(root, m.group(1), 20)
        return "代码搜索：\n" + ("\n".join("- %s:%s %s" % (x.get("path"), x.get("line"), x.get("text")) for x in rows) if rows else "没有结果")
    m = re.match(r"^代码[:：]\s*运行\s+(.+)$", text)
    if m:
        try:
            return str(CODE.run_snippet(m.group(1)))
        except Exception as e:
            return "代码运行失败：%s" % e
    m = re.match(r"^代码[:：]\s*读\s+(.+)$", text)
    if m:
        out = CODE.read_file(root, m.group(1).strip())
        return "文件：%s\nsha：%s\n\n%s" % (out.get("path"), (out.get("sha256") or "")[:12], out.get("content") or "")
    return None


def _writing(text):
    m = re.match(r"^写作[:：]\s*(.+)$", text)
    if not m:
        return "写作命令：写作：主题"
    from pai import model_gateway as M10
    out = M10.complete("请写一段关于“%s”的文字。直接给正文。" % m.group(1).strip())
    return str(out[0].get("output") or "") if out else "模型没有返回"


def _misc(text, mem=None):
    if text.startswith("代理：") or text.startswith("代理:"):
        text = "规划：" + text[3:].lstrip()
    if text.upper() == "ROI":
        from pai import roi as ROI
        import json
        return "ROI：\n" + json.dumps(ROI.dashboard(), ensure_ascii=False)[:1500]
    if text == "贾维斯":
        from pai import jarvis as JV
        from pai import profile as PF
        return "贾维斯：\n" + str(JV.brief(PF.current(), None))
    if text in ("白名单", "出网白名单"):
        from pai import network_policy as NP
        return "出网白名单：\n" + "\n".join("- " + h for h in NP.allowed_hosts())
    m = re.match(r"^白名单[:：]\s*加\s+(\S+)$", text)
    if m:
        from pai import network_policy as NP
        try:
            NP.allow_host(m.group(1))
            return "已加入出网白名单：%s" % m.group(1)
        except Exception as e:
            return "加入失败：%s" % e
    if text in ("记忆整理", "记忆去重"):
        if mem is None:
            return "记忆未连接"
        return "记忆整理：%s" % mem.dedupe()
    if text in ("语音", "声音"):
        from pai import voice as V
        return "语音：%s（后端 %s）" % ("开" if V.speaking_enabled() else "关", V.backend())
    if text in ("语音：开", "语音:开"):
        from pai import voice as V
        V.set_enabled(True)
        return "语音已开"
    if text in ("语音：关", "语音:关", "停止", "别念了"):
        from pai import voice as V
        V.set_enabled(False); V.stop()
        return "语音已关"
    m = re.match(r"^念[:：]\s*(.+)$", text)
    if m:
        from pai import voice as V
        if not V.speaking_enabled():
            return "语音是关的，先说 语音：开"
        V.speak(m.group(1)); return "开始念"
    if text in ("屏幕", "屏幕：看", "屏幕:看"):
        from pai import desktop_control as DC
        d = DC.windows()
        if not d.get("ok"):
            return "屏幕窗口：%s" % d.get("reason")
        return "屏幕窗口：\n" + "\n".join("- " + str(x.get("title")) for x in d.get("windows", [])[:30])
    if text in ("屏幕：读", "屏幕:读", "看屏幕"):
        import tempfile, pathlib as _p
        from PIL import ImageGrab
        from pai import image_ocr as OCR
        try:
            img = ImageGrab.grab()
            tmp = _p.Path(tempfile.mkdtemp(prefix="zhiheng-screen-")) / "screen.png"
            img.save(tmp)
            text = OCR.extract(tmp.read_bytes(), ".png")
            return "屏幕文字：\n" + str(text)[:4000]
        except Exception as e:
            return "读屏失败：%s" % e
    if text in ("知识：扫描", "知识:扫描", "知识：入库", "知识:入库") or text.startswith("知识：入库 ") or text.startswith("知识:入库 "):
        import pathlib as _p
        from pai import doc_index as DI, file_extract as FE, profile as PF, paths as _paths
        raw = re.sub(r"^知识[:：]\s*(?:入库|扫描)\s*", "", text).strip()
        targets = [_p.Path(raw)] if raw else [_paths.DATA_DIR / "notes", _paths.APP_ROOT / "wiki"]
        roots = [_paths.DATA_DIR.resolve(), _paths.WORKSPACE.resolve(), _paths.APP_ROOT.resolve()]
        files, ok, skipped, errors = [], 0, 0, []
        for target in targets:
            try:
                p = target.expanduser().resolve()
                if not any(p == r or r in p.parents for r in roots):
                    errors.append("%s：不在允许目录" % target); continue
                if p.is_dir():
                    for f in p.rglob("*"):
                        if f.is_file() and f.suffix.lower() in (".txt", ".md", ".pdf", ".docx", ".xlsx", ".xlsm", ".pptx", ".csv", ".json", ".log"):
                            files.append(f)
                            if len(files) >= 200: break
                elif p.is_file():
                    files.append(p)
                else:
                    errors.append("%s：不存在" % target)
            except Exception as e:
                errors.append("%s：%s" % (target, e))
        for f in files:
            try:
                out = DI.index(PF.current(), f.name, FE.extract(f.name, f.read_bytes()))
                if out.get("skipped"): skipped += 1
                else: ok += 1
            except Exception as e:
                errors.append("%s：%s" % (f.name, e))
        return "知识入库：新增 %d，跳过重复 %d，失败 %d\n%s" % (
            ok, skipped, len(errors), "\n".join("- " + x for x in errors[:10])) if errors else "知识入库：新增 %d，跳过重复 %d" % (ok, skipped)
    if text == "图谱":
        return "记忆图谱：http://127.0.0.1:8753/graph"
    if text in ("安全", "安全中心", "凭据"):
        return "安全中心：http://127.0.0.1:8753/security（只看 Key 是否配置，不显示 Key）"
    if text in ("会议", "会议：列表", "会议:列表"):
        from pai import paths
        d = paths.DATA_DIR / "meetings"
        rows = sorted(d.glob("meeting-*.md"), reverse=True) if d.exists() else []
        return "会议：\n- 聊天页按“会议”开始/结束\n- 纪要目录：%s\n- 最近：%s" % (
            d, ("\n  " + "\n  ".join(x.name for x in rows[:5])) if rows else "暂无纪要")
    if text in ("会议：开始", "会议:开始"):
        return "会议开始需要麦克风，请在聊天页点“会议”按钮；命令行不能直接采音。"
    if text in ("视觉", "看图"):
        from pai import vision as VIS
        return "视觉：%s\n模型：%s\nKey 入口：http://127.0.0.1:8753/security" % (
            "已配置" if VIS.configured() else "未配置",
            os.environ.get("PAI_VISION_MODEL") or "Qwen/Qwen3-VL-32B-Instruct")
    m = re.match(r"^看图[:：]\s*(.+)$", text)
    if m:
        import pathlib as _p
        from pai import vision as VIS, image_ocr as OCR
        path = _p.Path(m.group(1).strip())
        try:
            data = path.read_bytes()
        except Exception as e:
            return "读图失败：%s" % e
        try:
            return str(VIS.describe(data, "image/%s" % path.suffix.lstrip("."), "提取图片中的文字并描述画面。"))
        except Exception as e:
            try:
                return "视觉失败，OCR：\n" + OCR.extract(data, path.suffix)
            except Exception:
                return "视觉/OCR 都失败：%s" % e
    if text in ("文件审批", "待批文件"):
        return "文件审批：http://127.0.0.1:8753/files"
    if text in ("文档导入", "收件箱"):
        from pai import file_extract as FE
        rows = FE.list_inbox()
        if not rows:
            return "收件箱为空：把文件放到 %s" % __import__("pai.paths", fromlist=["INBOX"]).INBOX
        return "收件箱：\n" + "\n".join("- %s（%dKB）" % (x["name"], x["size"] // 1024) for x in rows[:20])
    m = re.match(r"^导入文档[:：]\s*(.+)$", text)
    if m:
        from pai import file_extract as FE, job_runner as JR
        try:
            name, content, chars = FE.ingest_inbox(m.group(1).strip())
            out = JR.create_file_proposal(name, content)
            return "已转成待审批：%s（%d 字）\nhttp://127.0.0.1:8753/files" % (out["filename"], chars)
        except Exception as e:
            return "导入失败：%s" % e
    m = re.match(r"^写文件[:：]\s*(\S+)\s+(.+)$", text)
    if m:
        from pai import job_runner as JR
        try:
            out = JR.create_file_proposal(m.group(1), m.group(2))
            return "已提交文件审批：%s（#%s）\nhttp://127.0.0.1:8753/files" % (out["filename"], out["id"])
        except Exception as e:
            return "提交文件失败：%s" % e
    if text == "模板市场":
        return "模板市场：http://127.0.0.1:8753/templates"
    if text in ("模板库", "模板"):
        return "模板库：\n- 模板：周报 标题\n- 模板：会议 标题\n- 模板：预算 标题\n- 模板：项目计划 标题\n- 模板：报价单 标题\n- 模板：合同 标题\n- 模板：发票 标题\n- 模板：复盘 标题\n- 模板：OKR 标题"
    if text == "知识库":
        from pai import doc_index as DI, wiki as WK, profile as PF
        docs = DI.list_docs(PF.current())
        pages = WK.Wiki().list() if WK.enabled() else []
        mem_count = 0
        if mem is not None:
            try:
                mem_count = int((mem.health().get("memory") or {}).get("memory_item") or 0)
            except Exception:
                mem_count = 0
        return "知识库：\n- 文档：%d 份\n- Wiki：%d 页（%s）\n- 记忆档案：%d 条\n用“知识：关键词”检索" % (
            len(docs), len(pages), "开" if WK.enabled() else "关", mem_count)
    if text in ("知识：开", "知识:开"):
        from pai import wiki as WK
        WK.set_enabled(True)
        return "Wiki 已开"
    if text in ("知识：关", "知识:关"):
        from pai import wiki as WK
        WK.set_enabled(False)
        return "Wiki 已关"
    m = re.match(r"^知识[:：]\s*(.+)$", text)
    if m:
        q = m.group(1).strip()
        from pai import doc_index as DI, wiki as WK, profile as PF
        hits = []
        try:
            for x in DI.search_all(PF.current(), q, 5):
                hits.append(("文档:%s#%s" % (x.get("name"), x.get("seq")), x.get("text", "")[:300],
                             float(x.get("score") or 0)))
        except Exception:
            pass
        if WK.enabled():
            try:
                for p_ in WK.Wiki().search(q, 5):
                    hits.append(("wiki:%s" % p_.get("slug"), str(p_.get("_body") or "")[:300], 1.0))
            except Exception:
                pass
        try:
            for x in (mem.recall(q, k=5) if mem is not None else []):
                it = x.get("item") or {}
                if it.get("type") == "episode":
                    label = "记忆:对话"
                    text = str(it.get("content") or "")
                else:
                    label = "记忆:%s/%s" % (it.get("subject") or "", it.get("predicate") or "")
                    text = str(it.get("object") or "")
                if text.strip():
                    hits.append((label, text[:300], float(x.get("relevance") or 0)))
        except Exception:
            pass
        seen, uniq = set(), []
        for n, t, sc in sorted(hits, key=lambda x: -float(x[2] or 0)):
            key = t.replace("\n", " ").strip()[:120]
            if key in seen:
                continue
            seen.add(key); uniq.append((n, t, sc))
        if not uniq:
            return "知识库里没找到「%s」。" % q
        return "知识：「%s」\n" % q + "\n".join(
            "- %s（%.2f）%s" % (n, float(sc or 0), t.replace("\n", " ")) for n, t, sc in uniq[:10])
    if text in ("用量", "成本", "成本面板"):
        from pai import usage as US, model_usage as MU
        t = US.today(); m = MU.summary()
        return ("成本/延迟：\n今日请求 %s，成本 %.4f\n近 200 次成本 %.4f，平均延迟 %ss\n面板：http://127.0.0.1:8753/usage" %
                (t.get("requests", 0), float(t.get("cost") or 0), float(m.get("cost") or 0),
                 m.get("avg_latency", 0)))
    if text in ("规划页面", "计划"):
        return "规划页面：http://127.0.0.1:8753/plans"
    if text == "规划":
        from pai import job_runner as JR
        rows = JR.list_plans(10)
        if not rows:
            return "还没有规划。用“规划：目标”生成。"
        return "规划：\n" + "\n".join("- %s [%s] %s（%d 步）" % (
            x.get("id"), x.get("status"), x.get("goal"), len(x.get("steps") or [])) for x in rows)
    m = re.match(r"^规划[:：]\s*(?:生成\s*)?(?!执行|下一步)(.+)$", text)
    if m:
        goal = m.group(1).strip()
        try:
            from pai import model_gateway as M10
            out = M10.complete("把目标拆成 3-7 个可执行步骤，只输出编号列表，不要解释：%s" % goal)
            raw = str(out[0].get("output") or "") if out else ""
            steps = re.findall(r"(?:^|\n)\s*\d+[.、)]\s*(.+)", raw)
            if not steps:
                steps = [x.strip() for x in raw.splitlines() if x.strip()][:7]
        except Exception as e:
            return "规划生成失败：%s" % e
        from pai import job_runner as JR
        plan = JR.create_plan(goal, steps)
        return "已生成规划 %s：\n%s" % (plan["id"], "\n".join("- " + x for x in steps))
    m = re.match(r"^规划[:：]\s*下一步\s*(\S+)$", text)
    if m:
        from pai import job_runner as JR, job_worker as JW
        jobs = [x for x in JR.plan_jobs(m.group(1)) if x.get("status") == "pending"]
        if not jobs:
            return "没有待执行步骤：%s" % m.group(1)
        job = jobs[0]
        dep = job.get("depends_on") or ""
        if dep and (JR.get(dep) or {}).get("status") != "done":
            return "上一步还没完成：%s" % dep
        JR.approve(job["id"], "Boss")
        out = JW.run_once(dry_run=False)
        return "执行下一步：%s\n%s" % (job.get("title"), str(out.get("result") or out)[:1500])
    m = re.match(r"^规划[:：]\s*执行\s*(\S+)$", text)
    if m:
        from pai import job_runner as JR
        plan = JR.get_plan(m.group(1))
        if not plan:
            return "没有这个规划：%s" % m.group(1)
        ids = []
        prev = ""
        for i, step in enumerate(plan.get("steps") or []):
            action_type, action_payload = _plan_action(step)
            action_payload = dict(action_payload or {})
            action_payload.update({"goal": plan.get("goal") or "", "step": step})
            job = JR.create("规划：" + step[:80], prompt=step, agent="planner",
                            action_type=action_type, action_payload=action_payload,
                            depends_on=prev, plan_id=plan["id"], step_index=i)
            ids.append(job["id"])
            prev = job["id"]
        JR.update_plan(plan["id"], "created")
        try:
            from pai import exec_guard as EG
            EG.audit("plan_execute", "medium", "allow", plan["id"], ",".join(ids))
        except Exception:
            pass
        return "已为规划 %s 建 %d 个串行待批准作业：%s" % (plan["id"], len(ids), ", ".join(ids))
    if text in ("依赖", "依赖检查") or text.startswith("依赖：检查") or text.startswith("依赖:检查"):
        from pai import code_agent as CODE, paths
        raw = re.sub(r"^依赖[:：]?\s*检查?\s*", "", text).strip()
        root = raw or str(paths.WORKSPACE)
        try:
            d = CODE.check_deps(root)
        except Exception as e:
            return "依赖检查失败：%s" % e
        if not d.get("ok"):
            return "依赖检查：%s" % d.get("reason")
        return "依赖检查（%s）：\n缺少：%s\n已安装：%d 个" % (
            d.get("root"), ", ".join(d.get("missing") or []) or "无", len(d.get("present") or []))
    if text == "代码隔离":
        from pai import code_agent as CODE
        s_ = CODE.isolation_status()
        return "代码隔离：\n- 测试临时副本：%s\n- 任意片段执行：%s" % (
            "开" if s_["tests"] else "关", "开" if s_["snippet"] else "关（PAI_CODE_EXEC=on 才开）")
    if text in ("主动", "主动提案"):
        from pai import proactive as PA
        rows = PA.proposals()
        return "主动提案：\n" + ("\n".join("- %s %s → %s" % (x.get("id"), x.get("title"), x.get("next_step")) for x in rows) if rows else "暂无到期提案。")
    m = re.match(r"^主动[:：]\s*(批准|拒绝)\s*(\S+)$", text)
    if m:
        from pai import proactive as PA
        try:
            out = PA.approve(m.group(2)) if m.group(1) == "批准" else PA.reject(m.group(2))
            return "主动%s：%s" % (m.group(1), out)
        except Exception as e:
            return "主动%s失败：%s" % (m.group(1), e)
    if text in ("待审批", "审批队列"):
        from pai import approval_token as TOK
        rows = TOK.pending()
        return "待审批：\n" + ("\n".join("- %s %s %s" % (x.get("id"), x.get("kind"), x.get("detail")) for x in rows) if rows else "没有待审批。")
    if text in ("待审批页面",):
        return "待审批页面：http://127.0.0.1:8753/approvals"
    if text == "桌面":
        from pai import desktop_control as DC
        d = DC.windows()
        if not d.get("ok"):
            return "桌面：%s" % d.get("reason")
        return "桌面窗口：\n" + "\n".join("- " + str(x.get("title")) for x in d.get("windows", [])[:30])
    m = re.match(r"^桌面[:：]\s*控件\s+(.+)$", text)
    if m:
        from pai import desktop_control as DC
        d = DC.controls(m.group(1).strip())
        if not d.get("ok"):
            return "读控件失败：%s" % d.get("reason")
        return "控件（%s）：\n" % d.get("title") + "\n".join(
            "- [%s] %s %s" % (x.get("control_type"), x.get("name"), x.get("automation_id") or "")
            for x in d.get("controls", [])[:40])
    m = re.match(r"^桌面[:：]\s*点击\s+(\S+)\s+(.+)$", text)
    if m:
        from pai import approval_token as TOK
        TOK.issue("desktop", "点击 %s/%s" % (m.group(1), m.group(2)),
                  action={"type": "desktop", "kind": "click", "title": m.group(1), "control": m.group(2)})
        return "已提交待审批（桌面点击 %s/%s），去 http://127.0.0.1:8753/approvals" % (m.group(1), m.group(2))
    m = re.match(r"^桌面[:：]\s*输入\s+(\S+)\s+(\S+)\s+(.+)$", text)
    if m:
        from pai import approval_token as TOK
        TOK.issue("desktop", "输入 %s/%s" % (m.group(1), m.group(2)),
                  action={"type": "desktop", "kind": "type_control", "title": m.group(1),
                          "control": m.group(2), "text": m.group(3)})
        return "已提交待审批（桌面输入），去 http://127.0.0.1:8753/approvals"
    m = re.match(r"^桌面[:：]\s*聚焦\s+(.+)$", text)
    if m:
        from pai import approval_token as TOK
        TOK.issue("desktop", "聚焦 " + m.group(1).strip(),
                  action={"type": "desktop", "kind": "focus", "title": m.group(1).strip()})
        return "已提交待审批（桌面聚焦），去 http://127.0.0.1:8753/approvals"
    if text == "浏览器":
        return "浏览器命令：浏览器：任务 URL（高风险会进待审批）\n待审批：http://127.0.0.1:8753/approvals"
    m = re.match(r"^浏览器[:：]\s*点击\s+(\S+)\s+(\S+)$", text)
    if m:
        from pai import approval_token as TOK
        TOK.issue("browser", "点击 " + m.group(2),
                  action={"type": "browser", "url": m.group(1),
                          "actions": [{"action": "click", "selector": m.group(2)}]})
        return "已提交待审批（浏览器点击），去 http://127.0.0.1:8753/approvals"
    m = re.match(r"^浏览器[:：]\s*输入\s+(\S+)\s+(\S+)\s+(.+)$", text)
    if m:
        from pai import approval_token as TOK
        TOK.issue("browser", "输入 " + m.group(2),
                  action={"type": "browser", "url": m.group(1),
                          "actions": [{"action": "fill", "selector": m.group(2), "text": m.group(3)}]})
        return "已提交待审批（浏览器输入），去 http://127.0.0.1:8753/approvals"
    m = re.match(r"^浏览器[:：]\s*等待\s+(\S+)\s+(\d+)$", text)
    if m:
        from pai import approval_token as TOK
        TOK.issue("browser", "等待 %sms" % m.group(2),
                  action={"type": "browser", "url": m.group(1),
                          "actions": [{"action": "wait", "ms": int(m.group(2))}]})
        return "已提交待审批（浏览器等待），去 http://127.0.0.1:8753/approvals"
    m = re.match(r"^浏览器[:：]\s*(?:任务|打开)\s+(\S+)(?:\s+(.+))?$", text)
    if m:
        import json as _json
        from pai import approval_token as TOK
        url = m.group(1).strip()
        actions = []
        if m.group(2):
            try:
                actions = _json.loads(m.group(2))
                if not isinstance(actions, list):
                    raise ValueError
            except Exception:
                return "浏览器动作必须是 JSON 数组"
        TOK.issue("browser", "任务 " + url,
                  action={"type": "browser", "url": url, "actions": actions})
        return "已提交待审批（浏览器 %s，%d 步），去 http://127.0.0.1:8753/approvals" % (url, len(actions))
    if text == "人设":
        from pai import persona_distill as PD
        return "人设：\n" + "\n".join("- " + str(x) for x in PD.list_personas())
    return None


def _reminder_cmd(q):
    if q in ("提醒", "提醒列表"):
        return list_tasks()
    m = re.match(r"^提醒[:：]\s*删\s*(\d+)$", q)
    if m:
        return delete_task(int(m.group(1)))
    m = re.match(r"^提醒[:：]\s*改\s*(\d+)\s+(\d{1,2}:\d{2})\s+(.+)$", q)
    if m:
        return update_task(int(m.group(1)), m.group(2), m.group(3))
    m = re.match(r"^提醒[:：]\s*(\d{1,2}:\d{2})\s+(.+)$", q)
    if m:
        return add_task(m.group(1), m.group(2))
    return None


def _plan_action(step):
    """把自然语言步骤映射到 job_worker 白名单动作；映射不了就 noop。"""
    text = str(step or "").strip()
    m = re.match(r"^(?:读取|读|查看)\s+(.+)$", text)
    if m:
        return "read_text", {"path": m.group(1).strip()}
    m = re.match(r"^(?:列出|列目录|扫描目录)\s+(.+)$", text)
    if m:
        return "list_dir", {"path": m.group(1).strip()}
    m = re.match(r"^(?:总结|摘要|归纳)\s+(.+)$", text)
    if m:
        return "summarize_file", {"path": m.group(1).strip()}
    return "agent_step", {}


def _office_cmd(q, mem):
    from pai.orchestrator import Orchestrator
    o = Orchestrator(mem)
    m = re.match(r"^表格[:：]\s*转\s*CSV\s+(.+)$", q, re.I)
    if m: return str(o.office_excel_convert(m.group(1).strip()))
    m = re.match(r"^表格[:：]\s*清洗\s+(.+)$", q)
    if m: return str(o.office_excel_clean(m.group(1).strip()))
    m = re.match(r"^图表[:：]\s*生成\s+(\S+)(?:\s+(.+))?$", q)
    if m: return str(o.office_chart(m.group(1), (m.group(2) or "").strip()))
    m = re.match(r"^图表[:：]\s*分析\s+(\S+)\s+(\S+)\s+(\S+)$", q)
    if m: return str(o.office_chart_analysis(m.group(1), m.group(2), m.group(3)))
    m = re.match(r"^备份[:：]\s*压缩\s+(.+)$", q)
    if m: return str(o.office_backup(m.group(1).strip()))
    m = re.match(r"^报表[:：]\s*生成\s+(.+)$", q)
    if m: return str(o.office_report(m.group(1).strip()))
    m = re.match(r"^对比[:：]\s*(\S+)\s+(\S+)$", q)
    if m: return str(o.office_compare(m.group(1), m.group(2)))
    m = re.match(r"^表格[:：]\s*公式\s+(\S+)\s+(\S+)(?:\s+(SUM|AVERAGE|COUNT|MAX|MIN))?$", q, re.I)
    if m: return str(o.office_excel_formula(m.group(1), m.group(2), (m.group(3) or "SUM").upper()))
    m = re.match(r"^模板[:：]\s*(周报|会议|预算|项目计划|报价单|合同|发票|复盘|OKR)(?:\s+(.+))?$", q, re.I)
    if m: return str(o.office_template(m.group(1), (m.group(2) or "").strip()))
    m = re.match(r"^文件[:：]\s*批量重命名\s+(\S+)\s+(\S+)\s+(\S+)$", q)
    if m: return str(o.office_batch_rename(m.group(1), m.group(2), m.group(3)))
    m = re.match(r"^回滚[:：]\s*(.+)$", q)
    if m: return str(o.office_rollback(m.group(1).strip()))
    return None


def handle(text, mem=None):
    q = str(text or "").strip()
    if q.startswith("提醒"):
        out = _reminder_cmd(q)
        if out is not None:
            return out
    if q in ("验证DeepSeek", "验证 DeepSeek", "DeepSeek验证"):
        try:
            from pai import custom_select as CUS
            from pai import model_registry as MR
            if not CUS.status().get("name"):
                cand = next((x for x in MR.list_models() if x.get("provider") == "deepseek"), None)
                if cand:
                    CUS.select(cand.get("name"))
            CUS.apply()
            from pai.orchestrator import Orchestrator
            old = os.environ.get("PAI_QWEN_AGENT_PROVIDER")
            os.environ["PAI_QWEN_AGENT_PROVIDER"] = "deepseek"
            try:
                msg = Orchestrator(mem).qwen_agent("只回复：DeepSeek 验证通过")
            finally:
                if old is None: os.environ.pop("PAI_QWEN_AGENT_PROVIDER", None)
                else: os.environ["PAI_QWEN_AGENT_PROVIDER"] = old
            return "DeepSeek 验证：\n" + str(msg)
        except Exception as e:
            return "DeepSeek 验证失败：%s" % e
    if q.startswith("/task "):
        parts = q.split(" ", 2)
        if len(parts) < 3:
            return "格式：/task 21:30 提醒我喝水"
        return add_task(parts[1], parts[2])
    if q == "/tasks":
        return list_tasks()
    if q.startswith("/search "):
        return _search(mem, q[len("/search "):].strip())
    m = re.match(r"^报表[:：]\s*每天\s*(\d{1,2}:\d{2})\s+(.+)$", q)
    if m:
        return add_task(m.group(1), "报表 " + m.group(2).strip(), name="定时报表",
                        action="report", payload=m.group(2).strip())
    if q.startswith(("对比：", "对比:", "表格：", "表格:", "图表：", "图表:", "文件：", "文件:", "备份：", "备份:", "报表：", "报表:", "模板：", "模板:", "回滚：", "回滚:")):
        out = _office_cmd(q, mem)
        if out is not None:
            return out
    if q in ("长任务", "任务队列") or q.startswith("长任务：") or q.startswith("长任务:"):
        return _jobs(q)
    if q.upper().startswith("MCP"):
        return _mcp(q)
    if q in ("审计", "审计账"):
        from pai import exec_guard as EG
        rows = EG.audit_recent(20)
        if not rows:
            return "审计账：暂无记录。"
        return "审计账（最近 20 条）：\n" + "\n".join(
            "- %s [%s/%s] %s %s" % (x.get("ts"), x.get("risk"), x.get("decision"),
                                    x.get("action"), x.get("target") or "")
            for x in rows)
    if q in ("审计页面", "审计界面"):
        return "审计页面：http://127.0.0.1:8753/audit"
    if q == "续线":
        return _continuity()
    if q == "代理":
        return _agents()
    if q == "模型包":
        return _models()
    if q == "代码" or q.startswith("代码：") or q.startswith("代码:"):
        return _code(q, mem)
    if q == "写作" or q.startswith("写作：") or q.startswith("写作:"):
        return _writing(q)
    out = _misc(q, mem)
    if out is not None:
        return out
    return None


def due(now=None):
    now = now or datetime.datetime.now()
    c = _conn()
    try:
        rows = c.execute("SELECT id,name,time_str,prompt,last_run,action,payload FROM reminders WHERE enabled=1").fetchall()
    finally:
        c.close()
    out = []
    for rid, name, t, prompt, last, action, payload in rows:
        if t > now.strftime("%H:%M"):
            continue
        if last and str(last).startswith(now.strftime("%Y-%m-%d")):
            continue
        out.append((rid, name, prompt, action, payload))
    return out


def fire_due(mem=None, now=None):
    """到点提醒：写 outbox、审计、标记已发；返回本次触发列表。"""
    from pai import exec_guard as EG
    fired = []
    for rid, name, prompt, action, payload in due(now):
        try:
            if action == "report":
                from pai.orchestrator import Orchestrator
                out = Orchestrator(mem).office_report(payload)
                msg = "定时报表已生成：%s" % out.get("excel")
                if mem is not None:
                    mem.push_outbox(msg, trigger_id=rid, channel="report")
                EG.audit("report_schedule", "medium", "allow", str(rid), msg)
                fired.append({"id": rid, "name": name, "action": "report", "result": out})
            else:
                if mem is not None:
                    mem.push_outbox(str(prompt), trigger_id=rid, channel="reminder")
                EG.audit("reminder", "low", "allow", str(rid), str(prompt)[:500])
                fired.append({"id": rid, "name": name, "prompt": prompt})
            mark_done(rid, now)
        except Exception as e:
            mark_done(rid, now)
            if mem is not None:
                mem.push_outbox("定时报表失败：%s" % e, trigger_id=rid, channel="report")
            EG.audit("report_schedule", "medium", "deny", str(rid), "%s: %s" % (type(e).__name__, e))
    if fired:
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
    return fired


def run_reminder_loop(mem, stop=None, interval=20):
    """后台线程：每 interval 秒查一次；同一条 remind 当天只发一次。"""
    import time
    while stop is None or not stop.is_set():
        try:
            fire_due(mem)
        except Exception:
            pass
        if stop is None:
            time.sleep(interval)
        else:
            stop.wait(interval)


def mark_done(rid, when=None):
    c = _conn()
    try:
        c.execute("UPDATE reminders SET last_run=? WHERE id=?",
                  ((when or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M"), rid))
        c.commit()
    finally:
        c.close()
