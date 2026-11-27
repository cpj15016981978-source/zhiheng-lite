# -*- coding: utf-8 -*-
"""技能库 v1：只读的 SKILL.md + SKILL.json 注册表。

一个技能不是提示词，是：
  名字 / 何时用 / 输入 / 输出 / 工具 / 权限 / 验收 / 入口
"""
from __future__ import annotations

from pai import paths

import json
import os
import pathlib

from pai import atomic as AT
import re
import shutil
import time

CAPABILITY = {
    "name": "技能库 v1",
    "switch_env": "PAI_SKILLS",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "PAI_SKILLS 关态：用户技能不加载；内置只读技能仍可匹配（自动注入）；开态同时加载用户技能",
    "probe_cost": "只读模块常量与文件",
    "approval": "未获单独批准；由调用方显式开启，不自动加载",
}

ROOT = pathlib.Path(os.environ.get("PAI_SKILLS_DIR") or (paths.DATA_DIR / "skills"))
BUILTIN_ROOT = paths.APP_ROOT / "skills"
REQUIRED = ("name", "version", "description", "when", "inputs", "outputs",
            "tools", "permissions", "tests", "entry")


def enabled():
    return (os.environ.get("PAI_SKILLS") or "").strip().lower() == "on"


def _tokens(text):
    out = []
    for part in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", str(text or "")):
        if part.isascii():
            out.append(part.lower())
        elif len(part) <= 2:
            out.append(part)
        else:
            out.extend(part[i:i + 2] for i in range(len(part) - 1))
    return list(dict.fromkeys(out))


class SkillError(ValueError):
    pass


class SkillLibrary:
    def __init__(self, root=None, include_builtin=False):
        self.root = pathlib.Path(root or ROOT)
        self.include_builtin = bool(include_builtin or root is None)

    def _dirs(self):
        out = []
        seen_bases = set()
        for base in ((self.root, BUILTIN_ROOT) if self.include_builtin else (self.root,)):
            if not base.exists() or base in seen_bases:
                continue
            seen_bases.add(base)
            out.extend((p, base == BUILTIN_ROOT) for p in sorted(base.iterdir())
                       if p.is_dir() and not p.name.startswith("_"))
        return [(p, builtin) for p, builtin in out if (p / "SKILL.json").exists() or (p / "SKILL.md").exists()]

    def _read(self, d, builtin=False):
        meta_path = d / "SKILL.json"
        md_path = d / "SKILL.md"
        if not meta_path.exists():
            # 兼容只有 SKILL.md 的 v2.2 包：从 frontmatter 生成最小元数据。
            if not md_path.exists():
                raise SkillError("%s 缺 SKILL.md" % d.name)
            body = md_path.read_text(encoding="utf-8")
            head = body.split("---", 2)[1] if body.startswith("---") and body.count("---") >= 2 else ""
            meta = {}
            for line in head.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            meta.setdefault("name", d.name)
            meta.setdefault("version", "0.0.0")
            meta.setdefault("description", meta.get("description", ""))
            for key, val in (("when", meta.get("description", "")), ("inputs", ["text"]),
                             ("outputs", ["text"]), ("tools", []), ("permissions", ["local"]),
                             ("tests", []), ("entry", "")):
                meta.setdefault(key, val)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as e:
            raise SkillError("%s SKILL.json 坏：%s" % (d.name, e)) from e
        for key in REQUIRED:
            if key not in meta:
                raise SkillError("%s 缺字段 %s" % (d.name, key))
        meta["status"] = str(meta.get("status") or "approved").lower()
        meta["approved_by"] = str(meta.get("approved_by") or "")
        meta["approved_at"] = str(meta.get("approved_at") or "")
        meta["_dir"] = str(d)
        meta["_builtin"] = bool(builtin)
        meta["_body"] = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        return meta

    def list(self, include_drafts=False):
        if not enabled():
            return []
        out = []
        for d, builtin in self._dirs():
            try:
                s = self._read(d, builtin)
            except SkillError:
                continue
            if not include_drafts and s["status"] != "approved":
                continue
            out.append(s)
        return out

    def builtin_find(self, query, limit=1):
        """内置技能只读匹配，不受 PAI_SKILLS 开关影响。"""
        q = _tokens(query)
        scored = []
        for d, builtin in self._dirs():
            if not builtin:
                continue
            try:
                s = self._read(d, True)
            except SkillError:
                continue
            text = " ".join(str(s.get(k) or "") for k in ("name", "description", "when", "tags"))
            toks = _tokens(text)
            hit = sum(1 for t in q if t in toks)
            if hit:
                scored.append((hit, s))
        scored.sort(key=lambda x: -x[0])
        return [s for _n, s in scored[:limit]]

    def find(self, query, limit=5):
        if not enabled():
            return self.builtin_find(query, limit)
        q = _tokens(query)
        scored = []
        for s in self.list():
            text = " ".join(str(s.get(k) or "") for k in ("name", "description", "when", "tags"))
            toks = _tokens(text)
            hit = sum(1 for t in q if t in toks)
            if hit:
                scored.append((hit, s))
        scored.sort(key=lambda x: -x[0])
        return [s for _n, s in scored[:limit]]

    def load(self, name, allow_draft=False):
        if not enabled():
            raise SkillError("PAI_SKILLS 未开")
        for s in self.list(include_drafts=True):
            if s.get("name") == name:
                if s.get("status") != "approved" and not allow_draft:
                    raise SkillError("技能未批准：%s（status=%s）" % (name, s.get("status")))
                return s
        raise SkillError("没有技能：%s" % name)

    def _write_meta(self, name, meta):
        if meta.get("_builtin"):
            raise SkillError("内置技能只读；请复制到用户技能目录后再改")
        d = pathlib.Path(meta["_dir"])
        meta = {k: v for k, v in meta.items() if not k.startswith("_")}
        AT.write_text(d / "SKILL.json",
                      json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def _snapshot(self, skill):
        d = pathlib.Path(skill["_dir"])
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = self.root / "_versions" / skill["name"] / stamp
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("SKILL.json", "SKILL.md"):
            src = d / name
            if src.exists():
                shutil.copy2(src, dest / name)
        return dest

    def approve(self, name, by="boss"):
        s = self.load(name, allow_draft=True)
        if s.get("_builtin"):
            raise SkillError("内置技能只读；不能 approve")
        self._snapshot(s)
        s["status"] = "approved"
        s["approved_by"] = by
        s["approved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._write_meta(name, s)
        return self.load(name)

    def disable(self, name):
        s = self.load(name, allow_draft=True)
        s["status"] = "disabled"
        self._write_meta(name, s)
        return s

    def rollback(self, name):
        base = self.root / "_versions" / name
        if not base.exists():
            raise SkillError("没有历史版本：%s" % name)
        versions = sorted([p for p in base.iterdir() if p.is_dir()])
        if not versions:
            raise SkillError("没有历史版本：%s" % name)
        src = versions[-1]
        dest = self.root / name
        for f in ("SKILL.json", "SKILL.md"):
            if (src / f).exists():
                shutil.copy2(src / f, dest / f)
        return self.load(name, allow_draft=True)

    def validate(self, name):
        s = self.load(name)
        problems = []
        if not str(s.get("entry") or "").strip():
            problems.append("entry 为空")
        if not isinstance(s.get("inputs"), list) or not s["inputs"]:
            problems.append("inputs 必须是非空数组")
        if not isinstance(s.get("outputs"), list) or not s["outputs"]:
            problems.append("outputs 必须是非空数组")
        if not isinstance(s.get("tests"), list) or not s["tests"]:
            problems.append("tests 必须是非空数组")
        return {"name": name, "ok": not problems, "problems": problems}
