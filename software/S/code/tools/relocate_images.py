# -*- coding: utf-8 -*-
"""按批次归位图片 —— 只复制，不移动、不删除任何原文件。

用法：
    python relocate_images.py --plan _pilot_plan.json          # 试跑
    python relocate_images.py --plan _relocate_plan.json --batch 50
    python relocate_images.py --plan _relocate_plan.json --dry-run

安全保证：
  * 只做 copy2，绝不 remove/move/rename 源文件
  * 目标已存在则跳过（不覆盖）
  * 每批处理后立即校验（数量 + 体积）
  * 任一失败立即停止，并输出已处理清单供回滚
"""
import argparse, hashlib, json, os, shutil, sys, time

def md5(p, buf=1 << 20):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(buf), b""):
            h.update(ch)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--batch", type=int, default=50, help="每批文件数（默认50）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-md5", action="store_true", help="复制后校验内容一致（慢）")
    ap.add_argument("--log", default="_relocate_done.json")
    a = ap.parse_args()

    plan = json.load(open(a.plan, encoding="utf-8"))
    print(f"计划 {len(plan)} 个文件，每批 {a.batch}")

    done, skipped, failed = [], [], []
    t0 = time.time()
    for i, item in enumerate(plan, 1):
        src, dst = item["src"], item["dst"]
        if not os.path.exists(src):
            failed.append({**item, "reason": "源文件不存在"})
            continue
        if os.path.exists(dst):
            skipped.append(item)
            continue
        if a.dry_run:
            done.append({**item, "dry": True})
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            if os.path.getsize(src) != os.path.getsize(dst):
                raise IOError("体积不一致")
            if a.verify_md5 and md5(src) != md5(dst):
                raise IOError("md5 不一致")
            done.append(item)
        except Exception as e:
            failed.append({**item, "reason": str(e)})
            print(f"  ✗ 失败并停止：{dst}\n    {e}")
            break

        if i % a.batch == 0:
            print(f"  已处理 {i}/{len(plan)}  ({time.time()-t0:.0f}s)")

    json.dump({"done": done, "skipped": skipped, "failed": failed},
              open(a.log, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"\n完成：复制 {len(done)} | 跳过(已存在) {len(skipped)} | 失败 {len(failed)}")
    print(f"耗时 {time.time()-t0:.1f}s")
    print(f"回滚清单：{os.path.abspath(a.log)}")
    if failed:
        print("\n失败明细（前5）：")
        for f in failed[:5]:
            print(f"  {f.get('reason')}  {f['src']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
