# -*- coding: utf-8 -*-
"""Code Graph v1：Python AST 的符号 / 导入 / 调用 / 测试索引。

只读源码，生成 SQLite 索引；不执行代码、不调模型、不出网。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import pathlib
import sqlite3

CAPABILITY = {
    "name": "Code Graph v1",
    "switch_env": "PAI_CODE_GRAPH",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "关态拒绝构建/查询；开态只读 .py 源码、写 code_graph.db，不执行代码",
    "probe_cost": "只读模块常量与文件",
    "approval": "未获单独批准；由调用方显式开启，不自动扫描",
}

SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "node_modules", "_incoming_deepseek", "pack"}


def enabled():
    return (os.environ.get("PAI_CODE_GRAPH") or "").strip().lower() == "on"


def _sha12(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def _modname(root, path):
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class _Visitor(ast.NodeVisitor):
    def __init__(self, file, module, is_test):
        self.file = str(file)
        self.module = module
        self.is_test = is_test
        self.symbols = []
        self.edges = []
        self.stack = [module]
        self.module_id = "%s:%s" % (self.file, module)
        self.symbol_stack = [self.module_id]

    def _qual(self, name):
        return ".".join(self.stack + [name])

    def _add_symbol(self, node, kind, name):
        sid = "%s:%s" % (self.file, self._qual(name))
        self.symbols.append({
            "id": sid, "kind": kind, "name": name, "qualname": self._qual(name),
            "file": self.file, "line": int(getattr(node, "lineno", 0) or 0),
            "end_line": int(getattr(node, "end_lineno", 0) or 0),
        })
        return sid

    def visit_ClassDef(self, node):
        sid = self._add_symbol(node, "class", node.name)
        self.stack.append(node.name)
        self.symbol_stack.append(sid)
        self.generic_visit(node)
        self.symbol_stack.pop()
        self.stack.pop()

    def visit_FunctionDef(self, node):
        kind = "test" if (self.is_test and node.name.startswith("test_")) else "function"
        sid = self._add_symbol(node, kind, node.name)
        self.stack.append(node.name)
        self.symbol_stack.append(sid)
        self.generic_visit(node)
        self.symbol_stack.pop()
        self.stack.pop()

    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)

    def visit_Import(self, node):
        for a in node.names:
            self.edges.append({"src": self.module_id, "dst": a.name,
                               "kind": "imports", "name": a.name,
                               "file": self.file, "line": node.lineno})

    def visit_ImportFrom(self, node):
        mod = node.module or ""
        for a in node.names:
            self.edges.append({"src": self.module_id, "dst": mod + "." + a.name,
                               "kind": "imports", "name": a.name,
                               "file": self.file, "line": node.lineno})

    def visit_Call(self, node):
        name = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name:
            self.edges.append({"src": self.symbol_stack[-1], "dst": "name:" + name,
                               "kind": "calls", "name": name,
                               "file": self.file, "line": node.lineno})
        self.generic_visit(node)


class CodeGraph:
    def __init__(self, db):
        self.db = str(db)
        self.conn = sqlite3.connect(self.db)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS cg_file(path TEXT PRIMARY KEY, sha12 TEXT, mtime REAL);
        CREATE TABLE IF NOT EXISTS cg_symbol(
          id TEXT PRIMARY KEY, kind TEXT, name TEXT, qualname TEXT,
          file TEXT, line INTEGER, end_line INTEGER);
        CREATE TABLE IF NOT EXISTS cg_edge(
          src TEXT, dst TEXT, kind TEXT, name TEXT, file TEXT, line INTEGER);
        CREATE INDEX IF NOT EXISTS idx_cg_symbol_name ON cg_symbol(name);
        CREATE INDEX IF NOT EXISTS idx_cg_edge_src ON cg_edge(src);
        CREATE INDEX IF NOT EXISTS idx_cg_edge_name ON cg_edge(name);
        """)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def _paths(self, root):
        for p in root.rglob("*.py"):
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            yield p

    def build(self, root):
        if not enabled():
            raise RuntimeError("PAI_CODE_GRAPH 未开")
        root = pathlib.Path(root).resolve()
        stats = {"files": 0, "changed": 0, "symbols": 0, "edges": 0, "errors": []}
        for path in self._paths(root):
            rel = str(path.relative_to(root))
            try:
                h = _sha12(path)
                old = self.conn.execute("SELECT sha12 FROM cg_file WHERE path=?", (rel,)).fetchone()
                if old and old["sha12"] == h:
                    continue
                src = path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(src, filename=str(path))
                is_test = rel.startswith("tests" + os.sep) or path.name.startswith("test_")
                v = _Visitor(rel, _modname(root, path), is_test)
                v.visit(tree)
                self.conn.execute("DELETE FROM cg_symbol WHERE file=?", (rel,))
                self.conn.execute("DELETE FROM cg_edge WHERE file=?", (rel,))
                for s in v.symbols:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO cg_symbol(id,kind,name,qualname,file,line,end_line)"
                        " VALUES(?,?,?,?,?,?,?)",
                        (s["id"], s["kind"], s["name"], s["qualname"],
                         s["file"], s["line"], s["end_line"]))
                for e in v.edges:
                    self.conn.execute(
                        "INSERT INTO cg_edge(src,dst,kind,name,file,line) VALUES(?,?,?,?,?,?)",
                        (e["src"], e["dst"], e["kind"], e["name"], e["file"], e["line"]))
                self.conn.execute(
                    "INSERT OR REPLACE INTO cg_file(path,sha12,mtime) VALUES(?,?,?)",
                    (rel, h, path.stat().st_mtime))
                stats["files"] += 1
                stats["changed"] += 1
                stats["symbols"] += len(v.symbols)
                stats["edges"] += len(v.edges)
            except Exception as e:  # noqa: BLE001
                stats["errors"].append({"file": rel, "error": "%s: %s" % (type(e).__name__, e)})
        self.conn.commit()
        stats.update(self.stats())
        return stats

    def stats(self):
        return {
            "files_total": self.conn.execute("SELECT COUNT(*) FROM cg_file").fetchone()[0],
            "symbols_total": self.conn.execute("SELECT COUNT(*) FROM cg_symbol").fetchone()[0],
            "edges_total": self.conn.execute("SELECT COUNT(*) FROM cg_edge").fetchone()[0],
        }

    def find(self, name, limit=20):
        if not enabled():
            return []
        like = "%" + str(name) + "%"
        rows = self.conn.execute(
            "SELECT * FROM cg_symbol WHERE name LIKE ? OR qualname LIKE ? "
            "ORDER BY CASE WHEN name=? THEN 0 ELSE 1 END, "
            "CASE kind WHEN 'class' THEN 0 WHEN 'function' THEN 1 ELSE 2 END, file, line LIMIT ?",
            (like, like, str(name), int(limit))).fetchall()
        return [dict(r) for r in rows]

    def callers(self, name, limit=20):
        if not enabled():
            return []
        rows = self.conn.execute(
            "SELECT e.* FROM cg_edge e WHERE e.kind='calls' AND e.name=? LIMIT ?",
            (str(name), int(limit))).fetchall()
        return [dict(r) for r in rows]

    def callees(self, symbol_id, limit=20):
        if not enabled():
            return []
        rows = self.conn.execute(
            "SELECT * FROM cg_edge WHERE src=? AND kind='calls' LIMIT ?",
            (str(symbol_id), int(limit))).fetchall()
        return [dict(r) for r in rows]

    def tests_for(self, name, limit=20):
        if not enabled():
            return []
        rows = self.conn.execute(
            "SELECT e.* FROM cg_edge e JOIN cg_symbol s ON s.id=e.src "
            "WHERE s.kind='test' AND e.kind='calls' AND e.name=? LIMIT ?",
            (str(name), int(limit))).fetchall()
        return [dict(r) for r in rows]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parents[1]))
    ap.add_argument("--db", default=str(pathlib.Path(__file__).resolve().parents[1] / "data" / "code_graph.db"))
    ap.add_argument("--find")
    ap.add_argument("--callers")
    ap.add_argument("--tests-for")
    ns = ap.parse_args(argv)
    if not enabled():
        ap.error("PAI_CODE_GRAPH 未开")
    g = CodeGraph(ns.db)
    try:
        if ns.build:
            print(json.dumps(g.build(ns.root), ensure_ascii=False))
        if ns.find:
            print(json.dumps(g.find(ns.find), ensure_ascii=False, indent=2))
        if ns.callers:
            print(json.dumps(g.callers(ns.callers), ensure_ascii=False, indent=2))
        if ns.tests_for:
            print(json.dumps(g.tests_for(ns.tests_for), ensure_ascii=False, indent=2))
        if not any((ns.build, ns.find, ns.callers, ns.tests_for)):
            print(json.dumps(g.stats(), ensure_ascii=False))
    finally:
        g.close()


if __name__ == "__main__":
    main()
