# -*- coding: utf-8 -*-
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pai import poetry_kb as PK


class PoetryKBTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "poetry.db"
        c = sqlite3.connect(str(self.tmp))
        c.executescript("""
        create table poems(id integer primary key, title text, author text,
                           dynasty text, content text, source text);
        create virtual table poems_fts using fts5(title,author,content, tokenize='trigram');
        """)
        rows = [
            ("静夜思", "李白", "唐", "床前明月光，疑是地上霜。举头望明月，低头思故乡。", "全唐诗"),
            ("水调歌头", "苏轼", "宋", "明月几时有，把酒问青天。", "全宋词"),
        ]
        for title, author, dynasty, content, source in rows:
            cur = c.execute("insert into poems(title,author,dynasty,content,source) values(?,?,?,?,?)",
                            (title, author, dynasty, content, source))
            c.execute("insert into poems_fts(rowid,title,author,content) values(?,?,?,?)",
                      (cur.lastrowid, title, author, content))
        c.commit()
        c.close()
        self.old_db = PK.DB
        self.old_env = os.environ.get("PAI_POETRY")
        PK.DB = self.tmp
        os.environ["PAI_POETRY"] = "on"

    def tearDown(self):
        PK.DB = self.old_db
        if self.old_env is None:
            os.environ.pop("PAI_POETRY", None)
        else:
            os.environ["PAI_POETRY"] = self.old_env

    def test_title_lookup(self):
        out = PK.retrieve_poetry("静夜思是谁写的诗")
        self.assertTrue(out)
        self.assertIn("李白", out[0]["ref"])
        self.assertIn("静夜思", out[0]["ref"])

    def test_author_lookup(self):
        out = PK.retrieve_poetry("苏轼 水调歌头")
        self.assertTrue(out)
        self.assertIn("苏轼", out[0]["ref"])

    def test_non_poetry_query_empty(self):
        self.assertEqual(PK.retrieve_poetry("今天有什么"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
