# -*- coding: utf-8 -*-
"""比对几个标记夹的内容是否重合。只读，不改任何数据。

标记存的是 uid = `<绝对路径>#<批次>#<序号>`。比对分两层：
  · uid 严格比对（同一份文件、同一批次、同一序号）
  · path 比对（剥掉后两段）—— 同一张图可能有多个磁盘副本，
    换个副本路径就不同但图是同一张，只看 uid 会漏掉这种重合。
"""
import json
import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
MK = os.path.join(HERE, "_marks.json")


def uid_path(u):
    i = u.rfind("#")
    if i < 0:
        return u
    j = u.rfind("#", 0, i)
    return u[:i] if j < 0 else u[:j]


def main():
    m = json.load(open(MK, encoding="utf-8"))
    sets = m.get("sets") or []
    print("标记夹共 %d 个（%s）\n" % (len(sets), os.path.basename(MK)))

    meta = []
    for s in sets:
        paths = s.get("paths") or []
        uids = set(paths)
        files = {uid_path(p) for p in paths}
        meta.append({"id": s.get("id"), "name": s.get("name"),
                     "uids": uids, "files": files, "n": len(paths)})
        print("  %-10s %-8s uid %3d  去重文件 %3d%s"
              % (s.get("id"), s.get("name"), len(uids), len(files),
                 "   ← 空" if not paths else
                 ("   ⚠ 有重复 uid" if len(uids) != len(paths) else "")))

    # 只看非空的夹做两两比对
    live = [x for x in meta if x["n"]]
    print("\n参与比对（非空）：%d 个\n" % len(live))
    if len(live) < 2:
        print("  非空夹不足两个，没有可比的组合。")
        return 0

    print("两两重合：")
    for i in range(len(live)):
        for j in range(i + 1, len(live)):
            a, b = live[i], live[j]
            iu = a["uids"] & b["uids"]
            if_ = a["files"] & b["files"]
            inter = len(if_) or len(iu)
            union = len(a["files"] | b["files"]) or len(a["uids"] | b["uids"])
            jac = inter / union if union else 0
            verdict = ("完全相同" if inter == len(a["files"]) == len(b["files"])
                       else "部分重合" if inter else "完全不重合")
            print("  %s ↔ %s : uid 交集 %d · 文件交集 %d / 并集 %d · 重合度 %.0f%%  → %s"
                  % (a["id"], b["id"], len(iu), len(if_), union, jac * 100, verdict))

    # 内容指纹：把「同一批文件集合」归为一组，揭示重复的夹
    print("\n按内容分组（同一组 = 文件集合完全相同）：")
    groups = {}
    for x in live:
        groups.setdefault(frozenset(x["files"]), []).append(x)
    for k, v in sorted(groups.items(), key=lambda kv: -len(kv[0])):
        tag = "  ← 重复！" if len(v) > 1 else ""
        print("  %d 张：%s%s" % (len(k), "  ".join(x["id"] for x in v), tag))
        if len(v) > 1:
            for x in v:
                same = "一致" if x["files"] == v[0]["files"] else "不一致"
                print("      %s（%s）" % (x["id"], same))

    # 是否有夹是另一个的子集
    print("\n包含关系：")
    found = False
    for i in range(len(live)):
        for j in range(len(live)):
            if i == j:
                continue
            a, b = live[i], live[j]
            if a["files"] and a["files"] < b["files"]:
                print("  %s ⊂ %s （%d 张全部包含在 %d 张里）"
                      % (a["id"], b["id"], len(a["files"]), len(b["files"])))
                found = True
    if not found:
        print("  无真包含关系")

    # 全局：一共有多少张不同的图被标记
    allf = Counter()
    for x in live:
        allf.update(x["files"])
    dup = {k: v for k, v in allf.items() if v > 1}
    print("\n全局：%d 个夹共标记 %d 张不同图（去重后）"
          % (len(live), len(allf)))
    print("  被 2 个以上夹同时标记的图：%d 张" % len(dup))
    for k in list(dup)[:5]:
        who = [x["id"] for x in live if k in x["files"]]
        print("    %-58s  %s" % (os.path.basename(k)[-56:], " ".join(who)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
