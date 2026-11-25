"""契约文档单一真源一致性闸门（只读，不改任何文件）。

依据 CONTRACT_MASTER.json 声明的唯一主本，核对所有副本是否漂移：
  - policy=mirror        : 须与主本逐字节一致，漂移/缺失 -> FAIL
  - policy=frozen        : 历史/归档快照，永不追平，跳过判定
  - policy=counterparty  : 其他 AI 的件，只知会差距，不计 FAIL

用法：
    python tools/check_contract_master.py
    python tools/check_contract_master.py --json
    python tools/check_contract_master.py --master-dir DIR   # 声明文件所在目录

退出码：mirror 有漂移=1，全部一致=0（frozen/counterparty 不影响退出码）。
设计约束：本脚本绝不写文件、不联网、不起进程；纯 sha256 读。
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

DEFAULT_MASTER_FILE = "CONTRACT_MASTER.json"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_declaration(master_dir):
    mf = Path(master_dir) / DEFAULT_MASTER_FILE
    if not mf.is_file():
        raise FileNotFoundError("找不到主本声明文件: %s" % mf)
    return json.loads(mf.read_text(encoding="utf-8")), mf


def resolve_master_path(decl, mf):
    """主本路径：优先用绝对路径，否则相对声明文件所在目录。"""
    m = decl["master"]
    p = Path(m.get("path_abs") or m["path"])
    if not p.is_absolute():
        p = mf.parent / p
    return p


def check(decl, mf):
    master = resolve_master_path(decl, mf)
    results = {"master": str(master), "master_exists": master.is_file(),
               "master_sha256": None, "copies": [], "fail": 0, "warn": 0}

    if not master.is_file():
        results["error"] = "主本不存在"
        return results

    results["master_sha256"] = sha256(master)

    for c in decl.get("copies", []):
        p = Path(c["path_abs"])
        policy = c["policy"]
        item = {"path": str(p), "role": c["role"], "policy": policy,
                "status": None, "detail": ""}

        if not p.is_file():
            if policy == "mirror":
                item["status"] = "FAIL"
                item["detail"] = "mirror 副本缺失"
                results["fail"] += 1
            else:
                item["status"] = "SKIP"
                item["detail"] = "非 mirror 副本缺失（%s），只知会" % policy
                results["warn"] += 1
            results["copies"].append(item)
            continue

        if policy == "frozen":
            item["status"] = "FROZEN"
            item["detail"] = "冻结快照，跳过一致性判定"
            results["copies"].append(item)
            continue

        same = sha256(p) == results["master_sha256"]
        if same:
            item["status"] = "OK"
            item["detail"] = "与主本逐字节一致"
        elif policy == "mirror":
            item["status"] = "FAIL"
            item["detail"] = "已漂移：与主本 sha256 不一致"
            results["fail"] += 1
        else:  # counterparty
            item["status"] = "INFO"
            item["detail"] = "对方件与主本有差异（ADR-081 不代改），只知会"
            results["warn"] += 1
        results["copies"].append(item)

    return results


def render_text(r):
    lines = []
    lines.append("=" * 66)
    lines.append("契约文档单一真源一致性检查")
    lines.append("=" * 66)
    lines.append("主本: %s" % r["master"])
    if not r["master_exists"]:
        lines.append("[FAIL] 主本不存在：%s" % r.get("error", ""))
        return "\n".join(lines)
    lines.append("主本 sha256: %s" % r["master_sha256"])
    lines.append("-" * 66)
    for c in r["copies"]:
        lines.append("[%-7s] %s" % (c["status"], c["path"]))
        lines.append("          角色: %s" % c["role"])
        lines.append("          说明: %s" % c["detail"])
    lines.append("-" * 66)
    lines.append("结论: mirror 漂移 %d 处｜知会/冻结 %d 处｜%s" % (
        r["fail"], r["warn"], "FAIL" if r["fail"] else "PASS"))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="契约文档单一真源一致性闸门（只读）")
    ap.add_argument("--master-dir", help="CONTRACT_MASTER.json 所在目录（默认脚本上级目录）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args(argv)

    master_dir = args.master_dir or str(Path(__file__).resolve().parent.parent)
    try:
        decl, mf = load_declaration(master_dir)
    except Exception as e:
        print("[FAIL] 加载主本声明失败: %s" % e)
        return 2

    r = check(decl, mf)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(render_text(r))
    return 1 if r["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
