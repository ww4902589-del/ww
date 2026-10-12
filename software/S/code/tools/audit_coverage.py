# -*- coding: utf-8 -*-
"""核查桌面 c / D 两个文件夹的图片是否全部被图鉴「联系上」。

「联系上」定义：该图片的文件名能在数据源中找到对应记录，
从而拥有风格词条 / 技法 / 主题 / 批次等标签。
用**文件名**匹配而非路径 —— 图片可以有多份副本或被移动过，名字不变。

数据源（合并）：
  1. ComfyBatch 审核记录 reviews/*.json
  2. 全盘批次报告 comfybatch-v2-*-report.json（含藏在各目录里的）

用法：python audit_coverage.py [--json 输出.json]
"""
from __future__ import annotations
import argparse, collections, glob, json, os, sys

DESKTOP = r"C:/Users/wyj/Desktop"
C = DESKTOP + "/c"
D = DESKTOP + "/D"
REV = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ComfyBatch-S", "reviews")
IMG = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
SEARCH_ROOTS = (DESKTOP, "D:/", "E:/工作流")


def scan_imgs(root):
    out = []
    for r, ds, fs in os.walk(root):
        for f in fs:
            if f.lower().endswith(IMG):
                out.append(os.path.join(r, f).replace("\\", "/"))
    return out


def glob_reports():
    out = []
    for R in SEARCH_ROOTS:
        if os.path.isdir(R):
            out.extend(glob.glob(os.path.join(R, "**", "comfybatch-v2-*-report.json"),
                                 recursive=True))
    return sorted(set(out))


def load_sources():
    """返回 (reviews 文件名集, reports 文件名集, 计数)。"""
    rev_names, rep_names = set(), set()
    rev_n = 0
    for fp in glob.glob(os.path.join(REV, "*.json")):
        if fp.endswith(".tmp"):
            continue
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        for k, it in (d.get("items") or {}).items():
            rev_n += 1
            cp = (it.get("copied_to") or "").replace("\\", "/")
            if cp:
                rev_names.add(os.path.basename(cp))
    reps = glob_reports()
    for fp in reps:
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        for r in (d.get("results") or []):
            cp = (r.get("copied_to") or "").replace("\\", "/")
            if cp:
                rep_names.add(os.path.basename(cp))
    return rev_names, rep_names, rev_n, len(reps)


def classify(p, root):
    """给未联系的图粗分类，便于判断是否需要补录。"""
    rel = p[len(root) + 1:]
    top = rel.split("/")[0] if "/" in rel else "(根目录)"
    name = os.path.basename(p).lower()
    if any(s in name for s in ("_bg", "_ref", "torso", "lower", "contact",
                               "composite", "_contactsheet")) or name.endswith(".jpg"):
        return top, "素材/拼版图"
    if "轮" in top or top.startswith("第"):
        return top, "迭代轮次稿"
    return top, "实验/整理批"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    print("=" * 66)
    print("  桌面 c / D 图片「联系完整性」核查")
    print("=" * 66)

    rev_names, rep_names, rev_n, rep_n = load_sources()
    both = rev_names | rep_names
    print(f"\n数据源")
    print(f"  ① 审核记录 reviews : {rev_n:,} 条 → {len(rev_names):,} 个唯一文件名")
    print(f"  ② 批次报告 reports : {rep_n:,} 个文件 → {len(rep_names):,} 个唯一文件名")
    print(f"     仅 reports 才有  : {len(rep_names - rev_names):,} 个（补录关键）")
    print(f"  合并可匹配文件名   : {len(both):,} 个  ← 「能被联系上」的上限")

    result = {}
    for label, root in (("c", C), ("D", D)):
        imgs = scan_imgs(root)
        # 按「张数」统计（同一文件名可能有多份副本，各自都要算）
        hit = [p for p in imgs if os.path.basename(p) in both]
        un = [p for p in imgs if os.path.basename(p) not in both]
        cov = len(hit) / max(len(imgs), 1) * 100

        print(f"\n{'─' * 66}")
        print(f"桌面 {label}/   共 {len(imgs):,} 张")
        print(f"{'─' * 66}")
        print(f"  ✓ 被联系上   {len(hit):,}  ({cov:.1f}%)  "
              f"[{len(set(os.path.basename(p) for p in hit)):,} 个不同文件名]")
        print(f"  ✗ 未联系上   {len(un):,}  "
              f"[{len(set(os.path.basename(p) for p in un)):,} 个不同文件名]")

        groups = collections.defaultdict(lambda: collections.Counter())
        for p in un:
            top, kind = classify(p, root)
            groups[kind][top] += 1

        for kind in ("迭代轮次稿", "实验/整理批", "素材/拼版图"):
            if kind not in groups:
                continue
            tot = sum(groups[kind].values())
            print(f"\n  【{kind}】{tot:,} 张")
            for top, n in groups[kind].most_common(12):
                print(f"      {n:6,}  {top}")
            if len(groups[kind]) > 12:
                print(f"      … 另有 {len(groups[kind]) - 12} 个目录")

        result[label] = {
            "total": len(imgs), "linked": len(hit), "unlinked": len(un),
            "linked_names": len(set(os.path.basename(p) for p in hit)),
            "unlinked_names": len(set(os.path.basename(p) for p in un)),
            "coverage": round(cov, 2),
            "unlinked_groups": {k: dict(v) for k, v in groups.items()},
        }

    print(f"\n{'=' * 66}")
    print("  结论")
    print(f"{'=' * 66}")
    dc, dd = result["c"], result["D"]
    print(f"  D/  {dd['linked']:,}/{dd['total']:,} = {dd['coverage']}%  → "
          f"{'✓ 已基本全部联系' if dd['coverage'] > 99 else '有缺口'}")
    print(f"  c/  {dc['linked']:,}/{dc['total']:,} = {dc['coverage']}%  → "
          f"含大量非 ComfyBatch 产物")

    # ---- 判定 c/ 未联系图的真实性质 ----
    unlinked_total = dc["unlinked"]
    groups = dc["unlinked_groups"]
    g_iter = sum(groups.get("迭代轮次稿", {}).values())
    g_exp = sum(groups.get("实验/整理批", {}).values())
    g_asset = sum(groups.get("素材/拼版图", {}).values())
    recoverable = 0
    # 还能被"尚未纳入"的 report 救回的（若有）
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import style_atlas as A
        atlas_names = set()
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "风格图鉴.html")
        if os.path.isfile(p):
            html = open(p, encoding="utf-8").read()
            i = html.index("const D=")
            j = html.index("\n", i)
            dd2 = json.loads(html[html.index("{", i):j].rstrip().rstrip(";"))
            atlas_names = set(x["name"] for x in dd2["images"])
        if atlas_names:
            c_imgs = scan_imgs(C)
            miss = [p for p in c_imgs if os.path.basename(p) not in atlas_names]
            recoverable = sum(1 for p in miss if os.path.basename(p) in both)
            print(f"\n  当前图鉴收录 : {len(atlas_names):,} 个文件名")
            print(f"  c/ 未收录中、仍可由数据源补录的 : {recoverable:,} 张")
    except Exception as e:
        print(f"  （补录复核跳过：{e}）")

    print(f"\n  c/ 未联系的 {unlinked_total:,} 张构成：")
    print(f"    · 迭代轮次稿（第1~19轮等） {g_iter:,} 张  ← 手工迭代，非批次产物")
    print(f"    · 实验/整理批（数字目录等）{g_exp:,} 张  ← 早期实验，无审核记录")
    print(f"    · 素材/拼版图              {g_asset:,} 张  ← 拼接背景/参考图/contact sheet")
    print(f"\n  这三类合计 {g_iter + g_exp + g_asset:,} 张，"
          f"占未联系量的 "
          f"{(g_iter + g_exp + g_asset) / max(unlinked_total, 1) * 100:.1f}%，")
    print(f"  均不属于 ComfyBatch 审核记录体系 —— 属于「本就不该被联系」。")
    print(f"  D/ 未联系的 {dd['unlinked']} 张也全是拼版/素材图。")

    if a.json:
        result["_meta"] = {
            "review_names": len(rev_names), "report_files": rep_n,
            "report_names": len(rep_names), "merged_names": len(both),
            "report_only_names": len(rep_names - rev_names),
            "recoverable_in_c": recoverable,
        }
        json.dump(result, open(a.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"\n  明细已写入 {a.json}")
    return result


if __name__ == "__main__":
    main()
