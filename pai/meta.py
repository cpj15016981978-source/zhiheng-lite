# -*- coding: utf-8 -*-
"""能力咨询的确定性回答：不让“写东西/发邮件”误触发写审批，也不让模型跑偏。"""
from __future__ import annotations

KEYS = ("能做什么", "会做什么", "会什么", "能力", "服务", "功能", "这些能做",
        "你能做", "能不能做", "可以做什么")



CAPABILITY = {
    "name": "确定性元回答",
    "switch_env": "PAI_META",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "只回固定文案；不命中就走正常对话",
    "approval": "ADR-046：本地规则，不新增开关",
}

def is_question(text: str) -> bool:
    t = str(text or "")
    return any(k in t for k in KEYS) and ("做" in t or "接" in t or "能" in t or "会" in t)


def answer() -> str:
    return """结论：你列的这些，知衡现在只能接一部分，不是全都能。

【能做】
- 对话与推理：能多轮对话、先结论后理由、按上下文判断；受模型能力限制，不宣称全知。
- 查资料：能打开网页、读页面、截图；联网行为要你批准。
- 写东西：文档、报告、表格、网页/小应用草稿。
- 图片/文档：能读图、OCR、提取文字。
- 语音：能连续听、转文字、朗读；本地 Vosk/sherpa + 标点。
- 记事：能做续线、提醒、到期提案。
- 文件：能整理、归类、生成清单；写操作走审批。
- 记忆：本地记忆库，不默认上云。

【部分能】
- 做图/视频/配音：能做基础素材和脚本，复杂成片不保证。
- 发消息/邮件：能起草，不能替你直接发。
- 浏览器/桌面：能打开、读、点一部分；桌面目前主要是聚焦和输入。
- 定时检查：能做本地定时/到期提示，不是云端全天候。

【不能】
- 订票、酒店、餐厅
- 购物、比价下单、支付
- 银行、余额、流水、订阅
- Google 邮箱/日历/文档/网盘
- Spotify、Instagram、Facebook、Threads、Messenger
- Shopify、Stripe、PayPal
- 智能家居、Apple Health、Health Connect
- 自动付款、自动下单、自动发消息

一句话：知衡是本地桌面助手，不是接了全量 OAuth 服务的云助理；上面“能接的服务”清单，目前基本都没有接。"""

def is_briefing_question(text: str) -> bool:
    t = str(text or "")
    return any(k in t for k in ("晨间简报", "早间简报", "早上简报", "每日简报", "晨报"))


def briefing_answer() -> str:
    from pai import continuity as CT
    import datetime
    now = datetime.datetime.now().astimezone()
    due = CT.due("work")
    open_threads = CT.all_threads("work")[:5]
    lines = ["早上好，大佬。", "今天是 %s。" % now.strftime("%Y-%m-%d")]
    if due:
        lines.append("【今天到期】")
        lines.extend("- " + (x.get("title") or "未命名") + "：下一步 " + (x.get("next_step") or "") for x in due[:3])
    else:
        lines.append("【今天到期】没有到期的线。")
    if open_threads:
        lines.append("【未完成】")
        lines.extend("- " + (x.get("title") or "未命名") + "：上次停在 " + (x.get("last_step") or "—") for x in open_threads[:3])
    else:
        lines.append("【未完成】没有打开的线。")
    lines.append("【建议】先做上面第一条；做完回来点“完成”，再进下一条。")
    lines.append("说明：这是本地版简报，只基于续线和待办；天气、新闻、邮件没有接数据源。")
    return "\n".join(lines)

def is_project_question(text: str) -> bool:
    t = str(text or "")
    return any(k in t for k in ("当前主线", "主线是什么", "项目进度", "做到哪", "进度如何", "现在做到"))


def project_answer() -> str:
    return """当前主线：知衡。

产品：知衡
AI 人格：月白
阶段：A 每天敢用 → B 自己会做事 → C 可测的自我

最近已完成：
- 品牌/图标/安装包统一为知衡
- 人格接入后端 prompt
- 单一真源 manifest 校验
- 全量构建 + 真机安装 + 看门狗 3 次自愈
- 连续听/VAD/sherpa/标点模型
- 续线/主动推进/审批台账

还没做：
- 手机端、跨设备同步
- 代码签名、自动更新
- 桌面点击/视觉定位

注：旧代号已废弃，不要当成当前产品名。"""
