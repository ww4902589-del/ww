# -*- coding: utf-8 -*-
"""核对「方案声明」与「图鉴实测」是否一致 —— 只读分析，不改任何数据。

对每个当前方案（settings.json 的 style_lora_presets）：
  · 声明：它写了哪些风格词条、哪些 LoRA、什么强度
  · 实测：图鉴里挂在该方案名下的图，实际带的是哪些风格/LoRA/技法
两者不一致的地方，就是「方案没被忠实执行」或者「图片是改方案前生成的」。

输出用于后续「风格+LoRA 组合与分配」的事实底稿。
"""
import json
import os
import sys
import collections

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

ATLAS = r"C:\Users\wyj\Desktop\_atlas_D.json"
SETTINGS = os.path.expandvars(r"%LOCALAPPDATA%\ComfyBatch-S\settings.json")


def main():
    D = json.load(open(ATLAS, encoding="utf-8"))
    S = json.load(open(SETTINGS, encoding="utf-8"))
    presets = S.get("style_lora_presets") or {}
    tombstones = set((S.get("deleted_style_lora_presets") or {}).keys())
    imgs = D["images"]

    by_preset = collections.defaultdict(list)
    for im in imgs:
        by_preset[im["preset"]].append(im)

    names = {v["name"]: v for v in presets.values()}
    print("当前方案 %d 个；图鉴里 %d 个不同的方案名\n" % (len(names), len(by_preset)))

    # 图鉴里那些已经不存在的方案名，是否就是墓碑
    only = set(by_preset) - set(names) - {"(未命名)"}
    print("图鉴里有、软件里没有的方案名：%d 个" % len(only))
    print("  其中在墓碑（已删除）里的：%d 个" % len(only & tombstones))
    print("  既不在方案表也不在墓碑里的：%d 个" % len(only - tombstones))
    leftover = sorted(only - tombstones)
    if leftover:
        print("    例如：" + "、".join(leftover[:6]))
    print()

    print("=" * 100)
    for pid, p in presets.items():
        name = p["name"]
        rows = by_preset.get(name) or []
        d_styles = {(x["catalog"], x["name"]) for x in (p.get("styles") or [])}
        d_loras = {x["name"]: x.get("strength") for x in (p.get("loras") or [])}

        o_lib = collections.Counter()
        o_sty = collections.Counter()
        o_tec = collections.Counter()
        for im in rows:
            o_lib.update(im["libs"])
            o_sty.update(im["styles"])
            o_tec.update(im["techs"])
        def loranames(im):
            out = set()
            for l in im["loras"]:
                out.add(l["n"] if isinstance(l, dict) else l)
            return out
        o_lor = collections.Counter()
        for im in rows:
            o_lor.update(loranames(im))

        print("\n【%s】 id=%s   实测 %d 张" % (name, pid, len(rows)))
        print("  声明风格 %d 条：" % len(d_styles))
        for c, n in sorted(d_styles):
            hit = o_sty.get(n, 0)
            mark = "✓" if hit else "✗ 实际未出现"
            print("      %-6s %-46s  (实测命中 %d)" % (mark, n[:46], hit))
        print("  声明 LoRA %d 个：" % len(d_loras))
        for n, st in sorted(d_loras.items()):
            hit = o_lor.get(n, 0)
            mark = "✓" if hit else "✗ 实际未出现"
            print("      %-6s %-46s 声明强度 %s  实测 %d 张" % (mark, n[:46], st, hit))
        extra = {k: v for k, v in o_lor.items() if k not in d_loras}
        if extra:
            print("  实际还用了没声明的 LoRA：")
            for n, c in sorted(extra.items(), key=lambda kv: -kv[1]):
                print("      · %-46s %d 张" % (n[:46], c))
        if o_tec:
            print("  实测技法 top5：" + "、".join("%s(%d)" % (k, v)
                  for k, v in o_tec.most_common(5)))
    print("\n" + "=" * 100)


if __name__ == "__main__":
    main()
