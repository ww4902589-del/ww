# -*- coding: utf-8 -*-
"""风格图鉴 · 缩略图缓存生成器
============================================================
为什么需要这个：
    图鉴网格原先直接 <img src=原图>。原图是 ComfyUI 直出的 PNG，
    单张 2-12MB，全库 7026 张合计 15GB。一页 48-60 张同时解码时，
    浏览器会放弃其中一部分 —— 表现就是「有些图显示不出来」（灰块）。
    这不是路径错，路径全部存在；是解码/内存扛不住。

    缩略图把每张压到宽 420px 的 webp（约 26KB，缩小 ~85 倍），
    网格只取缩略图；点开大图（viewer）仍读原图，清晰度不受影响。

产出（都在 --dir 下）：
    t/<hash>.webp        网格缩略图，宽 420
    manifest.json        {"grid_w":420,"count":N,"built_at":...}

    分组封面（文件夹 / 大文件夹 / 批次 / 日期）不在这里生成 ——
    见下方 manifest 那一段的说明。

幂等：
    目标文件已存在且比原图新 → 跳过。随时可重跑，只补新增的图。
    --force 强制全部重做。

用法：
    python build_thumbs.py                # 增量补齐
    python build_thumbs.py --force        # 全部重做
    python build_thumbs.py --dir D:\\x    # 换输出目录
    python build_thumbs.py --jobs 8       # 限并发
"""
import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DEFAULT_DIR = r"C:\Users\wyj\Desktop\_style_atlas_thumbs"
GRID_W = 420
QUALITY = 78


def thumb_hash(path):
    """图片路径 → 缩略图文件名（不含扩展名）。

    只用路径本身做摘要，不加时间戳 —— 同一张图每次构建必须得到
    同一个 hash，否则前端引用的名字和磁盘上的文件对不上。
    """
    return hashlib.sha1(str(path).strip().encode("utf-8")).hexdigest()[:16]


def cover_key(kind, name):
    """文件夹 / 批次的封面键，与图片 hash 用同一个命名空间。"""
    return hashlib.sha1(("%s:%s" % (kind, name)).encode("utf-8")).hexdigest()[:16]


def _work(job):
    """子进程：生成一张缩略图。返回 (状态, 说明)。"""
    src, dst, force = job
    try:
        if not force and os.path.exists(dst):
            if os.path.getmtime(dst) >= os.path.getmtime(src):
                return ("skip", None)
        from PIL import Image
        im = Image.open(src)
        im.thumbnail((GRID_W, 10 ** 6), Image.LANCZOS)
        # webp 不带 alpha 更小；带透明通道的白底合成，避免出现黑边
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        tmp = dst + ".tmp"
        im.save(tmp, "WEBP", quality=QUALITY, method=4)
        os.replace(tmp, dst)          # 原子替换：中断不会留下半个文件
        return ("ok", None)
    except Exception as e:
        return ("fail", "%s → %s" % (src, e))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=DEFAULT_DIR, help="输出目录")
    ap.add_argument("--force", action="store_true", help="忽略缓存全部重做")
    ap.add_argument("--jobs", type=int, default=0, help="并发数，0=CPU 核数")
    a = ap.parse_args()

    out = os.path.abspath(a.dir)
    tdir = os.path.join(out, "t")
    os.makedirs(tdir, exist_ok=True)

    print("读取图鉴数据 …", flush=True)
    import style_atlas as A
    data = A.build(A.DEFAULT_REVIEWS, A.DEFAULT_STYLES)
    imgs = data["images"]
    print("  %d 张图" % len(imgs), flush=True)

    jobs = []
    hashes = {}
    for im in imgs:
        p = im["path"]
        h = thumb_hash(p)
        hashes[p] = h
        jobs.append((p, os.path.join(tdir, h + ".webp"), a.force))

    # 去重：同一路径在数据里只应出现一次，但表里可能有多条引用同一文件
    seen = set()
    uniq = []
    for j in jobs:
        if j[0] in seen:
            continue
        seen.add(j[0])
        uniq.append(j)
    jobs = uniq

    jobs_n = len(jobs)
    jobs = [j for j in jobs if a.force or not os.path.exists(j[1])
            or os.path.getmtime(j[1]) < os.path.getmtime(j[0])]
    print("  待生成 %d 张（已有缓存 %d 张）" % (len(jobs), jobs_n - len(jobs)), flush=True)

    t0 = time.time()
    ok = skip = fail = 0
    fails = []
    if jobs:
        workers = a.jobs or min(os.cpu_count() or 4, 12)
        done = 0
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for st, msg in ex.map(_work, jobs, chunksize=8):
                done += 1
                if st == "ok":
                    ok += 1
                elif st == "skip":
                    skip += 1
                else:
                    fail += 1
                    if len(fails) < 20:
                        fails.append(msg)
                if done % 500 == 0 or done == len(jobs):
                    el = time.time() - t0
                    print("  %d/%d  %.0fs  预计剩余 %.0fs"
                          % (done, len(jobs), el, el / done * (len(jobs) - done)), flush=True)

    # ---- 封面不在这里算 ----
    # 分组封面（文件夹 / 大文件夹 / 批次 / 日期）由 build_atlas.enrich() 计算：
    # 那两个派生维度（bigfolder / day）和每张图的 th 都只有在那边才齐。
    # 以前这里也算一份 folder/run 的封面，结果是同样的逻辑写两遍、
    # 改一处忘一处 —— 现在只有 enrich 一个出处。
    manifest = {
        "grid_w": GRID_W,
        "quality": QUALITY,
        "count": len(hashes),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    mpath = os.path.join(out, "manifest.json")
    tmp = mpath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    os.replace(tmp, mpath)

    # ---- 回收孤儿：manifest 不再引用的缩略图 ----
    # 不删，只报告。删文件是不可逆的，而它们占的地方很小。
    keep = set(hashes.values())
    on_disk = {f[:-5] for f in os.listdir(tdir) if f.endswith(".webp")}
    orphans = on_disk - keep

    total = sum(os.path.getsize(os.path.join(tdir, f))
                for f in os.listdir(tdir) if f.endswith(".webp"))
    print()
    print("完成：新增 %d · 跳过 %d · 失败 %d  用时 %.0fs"
          % (ok, skip, fail, time.time() - t0))
    print("目录：%s   %d 个文件，%.0f MB"
          % (out, len(on_disk), total / 1024 / 1024))
    if orphans:
        print("提示：%d 个缩略图已不被引用（图鉴里已移除的图），未删除。" % len(orphans))
    if fails:
        print("失败样例：")
        for m in fails:
            print("  " + m)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
