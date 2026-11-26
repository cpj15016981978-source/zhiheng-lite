# -*- coding: utf-8 -*-
"""诗词知识库：SQLite FTS5 检索，给 M1 提供“可引用的诗词背景”。

数据来自 chinese-poetry/chinese-poetry（MIT），只读，不写记忆、不联网。
"""
from __future__ import annotations

from pai import paths

import os
import pathlib
import re
import sqlite3

try:
    import zhconv
except ImportError:
    # 可选依赖：缺失时诗词库仍可启动，只是不做简繁转换。
    class _IdentityZhconv:
        @staticmethod
        def convert(text, _locale):
            return str(text)

    zhconv = _IdentityZhconv()

CAPABILITY = {
    "name": "诗词知识库",
    "switch_env": "PAI_POETRY",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "数据库不存在/开关关：返回空；查询失败：返回空，不影响对话",
    "approval": "由 Boss 显式接进知识库；只读检索",
}

DB = pathlib.Path(os.environ.get("PAI_POETRY_DB") or (paths.DATA_DIR / "poetry.db"))
CUES = ("诗", "词", "曲", "赋", "诗人", "词人", "唐诗", "宋词", "飞花令",
        "名句", "绝句", "律诗", "写一首", "来一首", "背一首", "引用", "出处",
        "作者", "朝代", "古诗", "古词",
        "静夜思", "水调歌头", "念奴娇", "如梦令", "声声慢", "满江红",
        "将进酒", "蜀道难", "春江花月夜", "长恨歌", "琵琶行",
        "李白", "杜甫", "白居易", "王维", "孟浩然", "李商隐", "杜牧",
        "苏轼", "李清照", "辛弃疾", "陆游", "柳永", "欧阳修", "纳兰性德")
STRIP = ("写一首", "来一首", "背一首", "关于", "有关", "古诗", "古词",
         "唐诗", "宋词", "诗人", "词人", "名句", "飞花令", "是谁写的",
         "谁写的", "是谁", "谁", "写的", "这首", "那首", "介绍一下",
         "介绍", "说说", "讲讲", "一下", "什么", "哪首", "哪一位",
         "诗", "词", "曲", "赋")
LIMIT = 3


def enabled():
    return (os.environ.get("PAI_POETRY") or "").strip().lower() == "on"


def _looks_poetry(text):
    t = str(text or "")
    return any(c in t for c in CUES)


def _rows(sql, args):
    if not DB.exists():
        return []
    try:
        c = sqlite3.connect("file:%s?mode=ro" % DB.as_posix(), uri=True)
        try:
            return c.execute(sql, args).fetchall()
        finally:
            c.close()
    except Exception:
        return []


def _terms(query):
    s = str(query or "")
    for c in sorted(STRIP, key=len, reverse=True):
        s = s.replace(c, " ")
    s = s.replace("关于", " ").replace("有关", " ").replace("的", " ")
    s = re.sub(r"[，。！？、：；,.!?;:\s]+", " ", s)
    out = []
    for part in re.findall(r"[\u4e00-\u9fff]{1,12}", s):
        if part and part not in out:
            out.append(part)
    return out[:4]


def _search_one(term, k):
    qs = zhconv.convert(term, "zh-cn")
    if len(qs) >= 3:
        fts = '"%s"' % qs.replace('"', '""')
        rows = _rows(
            "select title,author,dynasty,content,source from poems "
            "where id in (select rowid from poems_fts where poems_fts match ? "
            "order by rank limit ?)", (fts, k))
        if rows:
            return rows
    like = "%" + qs + "%"
    qt = zhconv.convert(term, "zh-tw")
    liket = "%" + qt + "%"
    return _rows(
        "select title,author,dynasty,content,source from poems "
        "where title like ? or author like ? or content like ? "
        "or title like ? or author like ? or content like ? limit ?",
        (like, like, like, liket, liket, liket, k))


def retrieve_poetry(query, k=LIMIT):
    """返回 [{ref,text,source,author,title,dynasty}]，失败返回 []。"""
    if not enabled() or not _looks_poetry(query):
        return []
    q = str(query or "").strip()
    if not q:
        return []
    terms = _terms(q) or [q]
    rows = []
    for term in terms:
        rows = _search_one(term, k)
        if rows:
            break
    # FTS 的 rank 会让“正文里出现过”压过“标题就是它”；按标题/作者命中重排
    needle = terms[0] if terms else q
    ns = zhconv.convert(needle, "zh-cn")
    nt = zhconv.convert(needle, "zh-tw")
    def _score(row):
        title, author, _dynasty, content, _source = row
        t = zhconv.convert(str(title or ""), "zh-cn")
        a = zhconv.convert(str(author or ""), "zh-cn")
        c = zhconv.convert(str(content or ""), "zh-cn")
        s = 0
        if t == ns or t == needle:
            s += 100
        elif ns in t:
            s += 60
        if a == ns or a == needle:
            s += 80
        elif ns in a:
            s += 40
        if ns in c:
            s += 10
        if nt and (nt in str(title or "") or nt in str(author or "")):
            s += 20
        return s
    rows = sorted(rows, key=_score, reverse=True)[:k]
    out = []
    for title, author, dynasty, content, source in rows:
        text = zhconv.convert(str(content or ""), "zh-cn")
        text = re.sub(r"\s+", " ", text).strip()[:360]
        out.append({
            "ref": "%s《%s》（%s·%s）" % (author or "佚名", title or "无题",
                                         dynasty or "?", source or "chinese-poetry"),
            "text": text,
            "source": source or "chinese-poetry",
            "author": author or "",
            "title": title or "",
            "dynasty": dynasty or "",
        })
    return out


def context(items):
    if not items:
        return ""
    return "\n".join("- [%s] %s" % (x["ref"], x["text"]) for x in items)


def count():
    if not DB.exists():
        return 0
    rows = _rows("select count(*) from poems", ())
    return rows[0][0] if rows else 0
