# -*- coding: utf-8 -*-
"""把「候选风格预览图」和「认可样图」拼到一张图上，用于视觉比对。只读。

为什么必须视觉确认：目录里的库名和内容经常不一致 —— 实测 `Photo-BW High Contrast`
这个库里装着 `Luminous Cobalt Nocturne — Wet Bokeh Shimmer`（彩色、湿反光），
`Drawing-Charcoal_Pencil` 里装着 `Luminous Abyssal Noir — Foggy Volumetric Beams`。
按库名挑必错，按词条名挑也会错，只有看图才算数。

用法：
    python 拼候选对比图.py A     # 三坑人物组
    python 拼候选对比图.py B     # 电影感场景组
输出：_cand_A.jpg / _cand_B.jpg，并用文件名标出候选编号
"""
import json
import os
import pathlib
import sys
import urllib.parse

sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".." / "src"))
import comfybatch_v2_core as C  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

COMFY = r"E:/ComfyUI-aki/ComfyUI-aki-v3/ComfyUI"
DESK = pathlib.Path(r"C:/Users/wyj/Desktop")

GROUPS = {
    "A": ["ethereal", "translucent", "flowing", "sparkle", "glitter", "delicate",
          "luminous", "soft glow", "pastel", "floral", "hanfu", "kimono", "dress",
          "costume", "ornate", "shimmer"],
    "B": ["cinematic", "dusk", "sunset", "golden hour", "backlight", "rim light",
          "volumetric", "cloudscape", "seascape", "firework", "nightscape",
          "painterly", "bokeh", "twilight"],
}
#: 认可样图（每组挑几张做并排参照）。标记集用图鉴标记里的，认可集用桌面认可的。
REFS = {
    "A": [DESK / "c/2/Krea2_Gemma3高质量风格逆推验证_v3_00001_.png",
          DESK / "c/HS/029_提示词_029--汉服_00001_.png",
          DESK / "c/2/Krea2_高保真优化验证_月窗_00001_.png"],
    "B": [DESK / "认可/2026-09/GPT_20260926_134336_054.png",
          DESK / "认可/2026-09/GPT_20260926_134519_110.png",
          DESK / "认可/2026-09/GPT_20260926_133631_104.png"],
}

CELL = 250

#: 认可集是**动漫插画**，所以摄影 / 写实类的库要整库排除。
#:
#: 这是这次比对里最要紧的一条：按文案检索时 `Photo-Golden Hour`、
#: `Painting-Oil Classical`、`Photo-BW High Contrast` 这些库得分一样高，
#: 但拼出来一看全是**真人照片**（相机虚化、胶片颗粒、真皮肤）——
#: 库名写的是 Golden Hour / Oil Classical，装的是摄影作品。
#: 项目自己的「本地风格组合推荐」也早就定过同一条口径：
#: 「动漫/插画与摄影/写实等明确不同媒介的条目不互荐」。
#: 不按媒介先切一刀，检索出来的高分候选有一大半是废的。
MEDIUM_EXCLUDE = ("photo-", "photography", "photo_", "documentary", "bw high contrast")


def is_illustration(style):
    lib = str(style.get("library") or "").lower()
    return not any(bad in lib for bad in MEDIUM_EXCLUDE)


def preview_path(style):
    t = style.get("thumbnail") or ""
    if "filename=" not in t:
        return None
    fn = urllib.parse.unquote(t.split("filename=")[1].split("&")[0])
    sub = ""
    if "subfolder=" in t:
        sub = urllib.parse.unquote(t.split("subfolder=")[1].split("&")[0])
    p = pathlib.Path(COMFY) / "input" / sub / fn
    return p if p.is_file() else None


def ratio_fit(im, box):
    im = im.convert("RGB")
    w, h = im.size
    s = min(box / w, box / h)
    return im.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)


def main():
    group = (sys.argv[1] if len(sys.argv) > 1 else "A").upper()
    words = GROUPS[group]

    inv = C.ResourceInventory(COMFY, [])
    styles = inv.snapshot()["styles"]
    scored = []
    for s in styles:
        if not is_illustration(s):
            continue
        hay = " ".join([str(s.get("name") or ""), str(s.get("name_cn") or ""),
                        str(s.get("display_name") or ""), str(s.get("prompt") or "")]).lower()
        hits = [w for w in words if w in hay]
        if len(hits) >= 3:
            p = preview_path(s)
            if p:
                scored.append((len(hits), s, p))
    scored.sort(key=lambda x: (-x[0], x[1]["library"]))

    # 同一风格库最多取 3 条，让候选覆盖多个来源，而不是被某一个库刷屏
    picked, per_lib = [], {}
    for n, s, p in scored:
        lib = s["library"]
        if per_lib.get(lib, 0) >= 3:
            continue
        per_lib[lib] = per_lib.get(lib, 0) + 1
        picked.append((n, s, p))
        if len(picked) >= 18:
            break

    cols = 6
    rows = 4                      # 第一行放参照图，后三行放候选
    W = cols * CELL + (cols + 1) * 8
    H = rows * (CELL + 26) + (rows + 1) * 8
    sheet = Image.new("RGB", (W, H), (24, 26, 30))
    d = ImageDraw.Draw(sheet)

    def put(idx, img, label, color):
        c, r = idx % cols, idx // cols
        x = 8 + c * (CELL + 8)
        y = 8 + r * (CELL + 34)
        sheet.paste(ratio_fit(img, CELL), (x, y))
        d.rectangle([x - 1, y - 1, x + CELL, y + CELL], outline=color, width=2)
        for k, line in enumerate(label.split("\n")[:2]):
            d.text((x, y + CELL + 3 + k * 12), line[:40], fill=color)

    idx = 0
    for ref in REFS[group]:
        if ref.is_file():
            put(idx, Image.open(ref), "★ 认可样本\n" + ref.name[:34], (255, 210, 90))
            idx += 1
    first_cand = idx
    for n, s, p in picked:
        put(idx, Image.open(p), f"#{idx - first_cand + 1} [{n}] {s['display_name'][:22]}\n{s['library'][:36]}",
            (150, 200, 255))
        idx += 1

    out = HERE / f"_cand_{group}.jpg"
    sheet.save(out, quality=88)
    print(f"候选 {len(picked)} 条（≥3 语汇；同一库最多 3 条）→ {out}")
    for i, (n, s, p) in enumerate(picked, 1):
        print(f"  #{i:2d} [{n}] {s['display_name'][:34]:36s} {s['library'][:42]}")


if __name__ == "__main__":
    main()
