# -*- coding: utf-8 -*-
"""按「认可集」的美术语汇，在风格目录里检索候选词条。只读。

为什么先做文本检索再谈比对：
  · 3945 条 × 316 张认可图逐对做视觉比对做不到（时间和上下文都不允许）；
  · 但目录里每条都带 name / name_cn / display_name / prompt 模板 ——
    模板正文就是"这个风格是什么"的自然语言描述，按语汇先筛出几十条，
    再对这几条做视觉确认，才是一个能在有限步数里做完的流程。

检索是**子串匹配**，和项目里提炼层的口径一致：不下语义判断，
只把"哪些词条的文字里出现了这些概念"如实列出来，供人再挑。
"""
import collections
import json
import os
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".." / "src"))
import comfybatch_v2_core as C  # noqa: E402

#: 两个认可集各自的语汇。分两组是因为**它们看起来是两套不同的目标**：
#: 标记集以三坑/古风人物为主（服装是主角），认可文件夹以电影感场景为主
#: （环境是主角）。混在一起检索会把两套目标搅成一锅。
GROUPS = {
    "A 三坑人物（标记集）": [
        "ethereal", "translucent", "flowing", "fabric", "sparkle", "glitter",
        "delicate", "luminous", "soft glow", "pastel", "floral", "hanfu",
        "kimono", "dress", "costume", "ornate", "elegant", "shimmer",
    ],
    "B 电影感场景（认可文件夹）": [
        "cinematic", "dusk", "sunset", "golden hour", "backlight", "rim light",
        "volumetric", "atmospheric", "key visual", "film", "cloudscape",
        "seascape", "firework", "nightscape", "painterly", "glow", "bokeh",
    ],
}


def main():
    inv = C.ResourceInventory(r"E:/ComfyUI-aki/ComfyUI-aki-v3/ComfyUI", [])
    styles = inv.snapshot()["styles"]
    print(f"风格目录 {len(styles)} 条\n")
    for label, words in GROUPS.items():
        print("=" * 74)
        print(f"{label}  语汇 {len(words)} 个")
        print("=" * 74)
        scored = []
        for s in styles:
            hay = " ".join([
                str(s.get("name") or ""),
                str(s.get("name_cn") or ""),
                str(s.get("display_name") or ""),
                str(s.get("prompt") or ""),
            ]).lower()
            hits = [w for w in words if w in hay]
            if len(hits) >= 2:
                scored.append((len(hits), hits, s))
        scored.sort(key=lambda x: (-x[0], x[2]["library"]))
        print(f"  命中 ≥2 个语汇的词条：{len(scored)} 条\n")
        for n, hits, s in scored[:18]:
            print(f"  [{n}] {s['display_name'][:30]}")
            print(f"      {s['library'][:44]}")
            print(f"      {s['name'][:66]}")
            print(f"      命中: {', '.join(hits[:6])}")
        libs = collections.Counter(s["library"] for _, _, s in scored)
        print("\n  按风格库分布 top6：")
        for k, v in libs.most_common(6):
            print(f"    {v:4d}  {k}")
        print()


if __name__ == "__main__":
    main()
