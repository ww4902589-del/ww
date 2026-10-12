# -*- coding: utf-8 -*-
"""排布 D 与 c/D 中未归位的图片 —— 生成归位计划（只出计划，不动文件）。

规则（按批次目录）：
  * D/ 内未排布图片  → c/D/<批次号>/<文件名>
  * c/D 根散落图片   → c/D/<批次号>/<文件名>
批次号来源优先级：
  1) 文件所在目录名形如 8 位 hex（如 2b51e7a7）
  2) 审核记录 reviews/*.json 中 copied_to 的 basename 匹配 → 该 run_id
  3) 无法定位 → 归入 _unsorted/

用法：
    python plan_relocate_d.py --out _plan_d.json
"""
from __future__ import annotations
import argparse, collections, glob, json, os, sys

DESKTOP = r"C:/Users/wyj/Desktop"
D = DESKTOP + "/D"
CD = DESKTOP + "/c/D"
REVIEWS = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ComfyBatch-S", "reviews")
IMG = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
HEX8 = __import__("re").compile(r"^[0-9a-f]{8}$")


def is_img(f):
    return f.lower().endswith(IMG)


def build_run_by_name():
    """文件名 -> 批次号（来自审核记录的 copied_to 与 image）。"""
    m = {}
    for fp in glob.glob(os.path.join(REVIEWS, "*.json")):
        if fp.endswith(".tmp"):
            continue
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        rid = (d.get("run_id") or "")[:8]
        if not rid:
            continue
        for k, it in (d.get("items") or {}).items():
            for cand in ((it.get("copied_to") or ""),
                         os.path.join((it.get("image") or {}).get("subfolder") or "",
                                      (it.get("image") or {}).get("filename") or "")):
                cand = cand.replace("\\", "/")
                if cand:
                    m.setdefault(os.path.basename(cand), rid)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_plan_d.json")
    ap.add_argument("--report", default=None)
    a = ap.parse_args()

    run_by_name = build_run_by_name()
    print(f"审核记录覆盖文件名 {len(run_by_name):,} 个")

    # c/D 已有文件（任意批次目录内）——用于去重
    in_batch = set()
    for e in os.listdir(CD):
        p = os.path.join(CD, e)
        if os.path.isdir(p):
            in_batch.update(os.listdir(p))
    print(f"c/D 批次目录内已有 {len(in_batch):,} 个文件")

    plan, miss_run = [], []
    seen = set()

    def add(src, run, tag):
        name = os.path.basename(src)
        dst = f"{CD}/{run}/{name}"
        if os.path.normpath(src) == os.path.normpath(dst):
            return
        key = (os.path.normpath(dst))
        if key in seen:
            return
        seen.add(key)
        plan.append({"src": src.replace("/", os.sep), "dst": dst.replace("/", os.sep),
                     "run": run, "name": name, "from": tag})

    def is_asset(f):
        """拼接/参考素材：下划线开头、或名字含 ref/bg/torso/lower 等部件词。"""
        n = f.lower()
        if n.startswith("_"):
            return True
        return any(k in n for k in ("_ref", "_bg", "torso", "lower", "_sheet"))

    # ---- 1) c/D 根散落 ----
    for f in sorted(os.listdir(CD)):
        p = os.path.join(CD, f)
        if not os.path.isfile(p) or not is_img(f):
            continue
        if f in in_batch:          # 批次目录已有同名副本 → 跳过（用户选择保留）
            continue
        run = run_by_name.get(f)
        if not run:
            if is_asset(f):
                run = "_assets"
            else:
                run = "_unsorted"
                miss_run.append(f)
        add(p, run, "cD-root")

    # ---- 2) D 内未排布（递归） ----
    for root, dirs, files in os.walk(D):
        for f in sorted(files):
            if not is_img(f):
                continue
            if f in in_batch:      # c/D 已有同名 → 视为已排布
                continue
            src = os.path.join(root, f)
            base = os.path.basename(root)

            # 拼版/素材图：保留相对路径，不混进生成图批次
            rel = os.path.relpath(root, D).replace("\\", "/")
            if base == "_contactsheet":
                parent = os.path.basename(os.path.dirname(root))
                run = parent if HEX8.match(parent) else "_assets"
                dst = f"{CD}/{run}/_contactsheet/{f}"
                plan.append({"src": src.replace("/", os.sep), "dst": dst.replace("/", os.sep),
                             "run": run, "name": f, "from": "D-contactsheet"})
                seen.add(os.path.normpath(dst))
                continue

            run = base if HEX8.match(base) else run_by_name.get(f)
            if not run:
                # 素材（下划线开头、部件图等）单列，不与生成图混淆
                if is_asset(f):
                    run = "_assets"
                else:
                    run = "_unsorted"
                    miss_run.append(f)
            add(src, run, "D")

    # 丢重：同一目标只保留一条
    json.dump(plan, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    cnt = collections.Counter(x["run"] for x in plan)
    src_tag = collections.Counter(x["from"] for x in plan)
    print(f"\n计划 {len(plan)} 个文件 → {a.out}")
    print("  来源：", dict(src_tag))
    print("  目标批次分布：")
    for k, v in cnt.most_common(30):
        print(f"    {v:4d}  c/D/{k}/")
    if miss_run:
        print(f"\n  ⚠ 无法定位批次 {len(miss_run)} 张 → 归入 _unsorted/")
        for f in miss_run[:8]:
            print("     ", f)


if __name__ == "__main__":
    main()
