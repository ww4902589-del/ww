# -*- coding: utf-8 -*-
"""合并重复的标记夹，并备份原文件。只动 _marks.json，不动图片、不动图鉴数据。

处理规则（保守优先，宁可不合也不丢）：
  1. 先按「文件集合」分组：路径集合完全相同的夹是同一份内容，只留一个。
  2. 再按「名字」处理：同名的夹里，
     · 空的 → 丢掉（它什么也没装）；
     · 内容被另一个非空同名夹完全包含 → 丢掉（它的标记在那边都有了，
       只把 note 备注并过去）；
     · 内容不相同 → **保留**（同名但装的是不同图，是用户有意分的两份）。
  3. 名字不重复的夹一律原样保留 —— 空夹也可能是用户建来准备收集的。
  4. 没有同名冲突的空夹也保留（同理）。

保留哪一个：优先当前激活的那个；否则内容多的；再否则第一个。
所有被丢弃夹的 note 备注（uid→文字）会合并进保留者，不覆盖已有值。

用法：
    python 清理标记夹.py            # 预演，只打印将要发生什么，不写文件
    python 清理标记夹.py --写入      # 备份后写回
"""
import argparse
import json
import os
import shutil
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
MK = os.path.join(HERE, "_marks.json")


def uid_path(u):
    """uid → 文件路径（剥掉最后两段 #run#index）。"""
    i = u.rfind("#")
    if i < 0:
        return u
    j = u.rfind("#", 0, i)
    return u[:i] if j < 0 else u[:j]


def files_of(s):
    return frozenset(uid_path(p) for p in (s.get("paths") or []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--写入", dest="write", action="store_true",
                    help="备份后写回 _marks.json；默认只预演")
    a = ap.parse_args()

    store = json.load(open(MK, encoding="utf-8"))
    sets = store.get("sets") or []
    active = store.get("active") or ""
    print("读入 %d 个夹（active=%s）\n" % (len(sets), active))

    def rank(s):
        """越大越优先保留：激活的 > 内容多的 > 备注多的。"""
        return (1 if s["id"] == active else 0,
                len(s.get("paths") or []),
                len(s.get("note") or {}))

    drop = {}          # id -> 原因
    merge_note = {}    # 保留者 id -> {uid: 文字}（来自被丢弃者）

    # 规则 1：内容完全相同（按文件集合）
    by_files = {}
    for s in sets:
        if s.get("paths"):
            by_files.setdefault(files_of(s), []).append(s)
    for fs, group in by_files.items():
        if len(group) < 2:
            continue
        group = sorted(group, key=rank, reverse=True)
        keeper = group[0]
        for s in group[1:]:
            drop[s["id"]] = "内容与 %s 完全相同（%d 张）" % (keeper["id"], len(fs))
            merge_note.setdefault(keeper["id"], {}).update(s.get("note") or {})
        print("同内容组 %d 张：保留 %s，丢弃 %s"
              % (len(fs), keeper["id"], ", ".join(s["id"] for s in group[1:])))

    # 规则 2：同名夹
    alive = [s for s in sets if s["id"] not in drop]
    by_name = {}
    for s in alive:
        by_name.setdefault(s.get("name") or "", []).append(s)
    for name, group in by_name.items():
        if len(group) < 2:
            continue
        nonempty = sorted([s for s in group if s.get("paths")], key=rank, reverse=True)
        empties = [s for s in group if not s.get("paths")]
        if nonempty:
            keeper = nonempty[0]
            kf = files_of(keeper)
            # 空的一律丢：什么也没装
            for s in empties:
                drop[s["id"]] = "同名「%s」且为空" % name
                merge_note.setdefault(keeper["id"], {}).update(s.get("note") or {})
            # 非空但内容被 keeper 完全包含的也丢
            for s in nonempty[1:]:
                if files_of(s) <= kf:
                    drop[s["id"]] = "同名「%s」且内容已被 %s 包含" % (name, keeper["id"])
                    merge_note.setdefault(keeper["id"], {}).update(s.get("note") or {})
            kept = [keeper] + [s for s in nonempty[1:] if s["id"] not in drop]
        else:
            # 全空：只留一个
            keeper = sorted(group, key=rank, reverse=True)[0]
            kept = [keeper]
            for s in group:
                if s["id"] != keeper["id"]:
                    drop[s["id"]] = "同名「%s」且全为空" % name
        print("同名「%s」：%d 个 → 保留 %s"
              % (name, len(group), ", ".join(s["id"] for s in kept)))

    if not drop:
        print("\n没有需要合并的夹。")
        return 0

    out = []
    for s in sets:
        if s["id"] in drop:
            continue
        s = dict(s)
        if s["id"] in merge_note and merge_note[s["id"]]:
            note = dict(s.get("note") or {})
            for k, v in merge_note[s["id"]].items():
                note.setdefault(k, v)      # 不覆盖保留者已有的备注
            s["note"] = note
        out.append(s)

    print("\n将丢弃 %d 个夹：" % len(drop))
    for sid, why in drop.items():
        print("  %s  ← %s" % (sid, why))
    print("保留 %d 个：%s" % (len(out), ", ".join(
        "%s(%d张)" % (s["id"], len(s.get("paths") or [])) for s in out)))

    # 标记总数必须不变 —— 丢了标记就说明规则错了，直接中止
    before = sum(len(s.get("paths") or []) for s in sets)
    after = sum(len(s.get("paths") or []) for s in out)
    uniq_before = len({p for s in sets for p in (s.get("paths") or [])})
    uniq_after = len({p for s in out for p in (s.get("paths") or [])})
    print("\n标记数：条目 %d → %d，去重后 %d → %d" % (before, after, uniq_before, uniq_after))
    if uniq_after != uniq_before:
        print("✗ 去重后的标记数变了 —— 会丢标记，已中止。")
        return 1
    print("✓ 去重后的标记数不变，没有标记会丢失。")

    if not a.write:
        print("\n（预演模式，未写文件。加 --写入 才会备份并写回。）")
        return 0

    stamp = time.strftime("%Y%m%d_%H%M%S")
    bak = MK + ".bak-" + stamp
    shutil.copy2(MK, bak)
    print("\n已备份 → %s" % os.path.basename(bak))

    new_active = active if any(s["id"] == active for s in out) else (out[0]["id"] if out else "")
    if new_active != active:
        print("激活夹原为 %s（已丢弃），改指 %s" % (active, new_active))
    tmp = MK + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"sets": out, "active": new_active,
                   "schema_version": store.get("schema_version", 2),
                   "saved_at": time.time()}, f, ensure_ascii=False)
    os.replace(tmp, MK)
    print("已写回 %s（%d 个夹）" % (os.path.basename(MK), len(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
