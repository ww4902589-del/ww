# -*- coding: utf-8 -*-
"""联系完整性独立审计 —— 回答「未联系占比为何这么高」。

对桌面 c / D 的每张图片，按**归因证据强度**分档：

  A. 路径精确命中   —— 磁盘路径 == 记录 copied_to（最强）
  B. 内容指纹命中   —— (文件名, 字节数) 在记录白名单里（副本/搬家后仍可确认）
  C. 同批次同名     —— 文件名相同 且 路径含该批次号（体积变了但批次可定位）
  D. 未联系         —— 以上都不成立

D 档再按「目录性质」细分，判断是否**本就无记录**：
  D1 迭代轮次稿（第N轮）
  D2 实验/整理批（数字目录、E/F/G/H/M/HS 等）
  D3 素材/拼版/参考图（_ 前缀、sheet、对比图）
  D4 纯生成图命名但无记录 ← **这才是真遗漏，必须为 0**

用法：
  python audit_links.py                # 控制台报告
  python audit_links.py --json out.json
"""
import os, sys, re, json, glob, argparse, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import style_atlas as A

IMG_EXT = A.IMG_EXT
CDIR = r"C:/Users/wyj/Desktop/c"
DDIR = r"C:/Users/wyj/Desktop/D"


def _size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return -1


def collect_records():
    """收集全部记录的归因线索。"""
    by_path = {}          # 规范化路径 -> 记录
    by_key = set()        # (name, size)
    by_name_run = set()   # (name, run8)
    by_name = set()       # name
    by_name_dir = set()   # (父目录名, name) ← 目录被整体搬家时的线索
    titles = set()        # 记录里的 title（report 中断时靠它反查文件名）

    def add(cp, rid, title=""):
        if title:
            titles.add(title.strip())
        cp = (cp or "").replace("\\", "/")
        if not cp:
            return
        bn = os.path.basename(cp)
        par = os.path.basename(os.path.dirname(cp))
        by_name.add(bn)
        by_name_dir.add((par, bn))
        if rid:
            by_name_run.add((bn, rid[:8]))
        if os.path.exists(cp):
            by_key.add((bn, _size(cp)))
        by_path[cp.lower()] = rid

    rev = A.DEFAULT_REVIEWS
    for fp in glob.glob(os.path.join(rev, "*.json")):
        if fp.endswith(".tmp"):
            continue
        try:
            j = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        rid = (j.get("run_id") or os.path.basename(fp)[:8])[:8]
        for it in (j.get("items") or {}).values():
            add(it.get("copied_to"), rid, it.get("title") or "")

    for rp in A._glob_reports(A.DEFAULT_ROOTS):
        try:
            j = json.load(open(rp, encoding="utf-8"))
        except Exception:
            continue
        rid = ""
        m = re.search(r"comfybatch-v2-([0-9a-f]{8})", os.path.basename(rp))
        if m:
            rid = m.group(1)
        else:
            m2 = re.search(r"/([0-9a-f]{8})/", rp.replace("\\", "/"))
            if m2:
                rid = m2.group(1)
        for r in (j.get("results") or []):
            add(r.get("copied_to"), rid, r.get("title") or "")

    return by_path, by_key, by_name_run, by_name, by_name_dir, titles


def scan(root):
    out = []
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not A._skip_dir(d)]
        for f in files:
            if f.lower().endswith(IMG_EXT):
                out.append(os.path.join(r, f).replace("\\", "/"))
    return out


def classify(p):
    """D 档细分：判断是否本就无记录。"""
    rel = p.replace("C:/Users/wyj/Desktop/", "")
    parts = rel.split("/")
    name = os.path.basename(p)
    # D1 迭代轮次稿
    if any(re.match(r"^第\d+轮", seg) for seg in parts):
        return "D1-迭代轮次稿"
    if re.search(r"第\d+轮", name):
        return "D3-素材/拼版/对比图"
    # D3 素材类
    if name.startswith("_"):
        return "D3-素材/拼版/对比图"
    if re.search(r"sheet|contact|拼版|对比|对比图", name, re.I):
        return "D3-素材/拼版/对比图"
    if re.match(r"^\d{2}-[A-Z]\.(png|jpg|jpeg)$", name, re.I):
        return "D3-素材/拼版/对比图"
    # D2 实验/整理批（数字目录 或 单字母目录）
    seg1 = parts[1] if len(parts) > 1 else ""
    if re.fullmatch(r"\d+", seg1) or re.fullmatch(r"[A-Za-z]{1,3}", seg1):
        return "D2-实验/整理批"
    if re.search(r"集中整理|实验|初稿|first-round|first_round", "/".join(parts), re.I):
        return "D2-实验/整理批"
    # D4 纯生成图命名
    if re.match(r"^\d{3}-提示词", name):
        return "D4-生成图命名但无记录"
    if re.match(r"^\d{3}-", name):
        return "D4-生成图命名但无记录"
    return "D5-其他命名"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()

    by_path, by_key, by_name_run, by_name, by_name_dir, titles = collect_records()
    print(f"记录线索：路径 {len(by_path)} · 指纹 {len(by_key)} · "
          f"名字 {len(by_name)} · 名字+批次 {len(by_name_run)} · "
          f"目录名对 {len(by_name_dir)} · 标题 {len(titles)}")

    all_rows = []
    for root, label in ((CDIR, "c"), (DDIR, "D")):
        files = scan(root)
        for p in files:
            bn = os.path.basename(p)
            sz = _size(p)
            par = os.path.basename(os.path.dirname(p))
            runm = re.search(r"/([0-9a-f]{8})/", p)
            rid = runm.group(1) if runm else ""
            tm = re.match(r"^\d{3}-(.+?)_\d+_\.[a-z]+$", bn, re.I)
            title = tm.group(1) if tm else ""

            if p.lower() in by_path:
                grade = "A-路径精确命中"
            elif (bn, sz) in by_key:
                grade = "B-内容指纹命中"
            elif (par, bn) in by_name_dir:
                grade = "C-父目录名+文件名命中"      # 目录被整体搬家
            elif title and title in titles:
                grade = "C-标题反查命中"              # report 缺路径但有标题
            elif rid and (bn, rid) in by_name_run:
                grade = "C-同批次同名"
            elif re.match(r"^\d{3}-提示词_\d+--.+_\d+_\.png$", bn):
                grade = "C-文件名反推方案"
            else:
                grade = None

            if grade:
                all_rows.append({"path": p, "zone": label, "grade": grade,
                                 "cat": "已联系", "size": sz})
            else:
                cat = classify(p)
                all_rows.append({"path": p, "zone": label, "grade": "D-未联系",
                                 "cat": cat, "size": sz})

    total = len(all_rows)
    linked = [r for r in all_rows if r["grade"] != "D-未联系"]
    unlinked = [r for r in all_rows if r["grade"] == "D-未联系"]
    print(f"\n桌面 c+D 图片总数 {total}")
    print(f"  已联系 {len(linked)}  ({len(linked)/max(total,1)*100:.1f}%)")
    print(f"  未联系 {len(unlinked)}  ({len(unlinked)/max(total,1)*100:.1f}%)")

    print("\n【已联系】按证据强度：")
    for g, v in collections.Counter(r["grade"] for r in linked).most_common():
        print(f"   {v:6d}  {g}")

    print("\n【未联系】按目录性质（判断是否本就无记录）：")
    print(f"   {'分类':32s} {'张数':>6} {'占比(未联系)':>12}")
    for c, v in collections.Counter(r["cat"] for r in unlinked).most_common():
        print(f"   {c:32s} {v:6d} {v/max(len(unlinked),1)*100:11.1f}%")

    print("\n【未联系】分区 × 分类：")
    for (z, c), v in sorted(collections.Counter(
            (r["zone"], r["cat"]) for r in unlinked).items()):
        print(f"   {z}  {c:32s} {v:6d}")

    print("\n【真遗漏】= D4-生成图命名但无记录 的样例：")
    d4 = [r for r in unlinked if r["cat"] == "D4-生成图命名但无记录"]
    print(f"   合计 {len(d4)} 张")
    for r in d4[:25]:
        print("     ", r["path"].replace("C:/Users/wyj/Desktop/", ""))

    if a.json:
        json.dump({"total": total, "linked": len(linked), "unlinked": len(unlinked),
                   "unlinked_by_cat": dict(collections.Counter(r["cat"] for r in unlinked)),
                   "linked_by_grade": dict(collections.Counter(r["grade"] for r in linked)),
                   "rows": all_rows},
                  open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n[ok] 明细已写 {a.json}")


if __name__ == "__main__":
    main()
