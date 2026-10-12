# -*- coding: utf-8 -*-
"""把「完善方案」拼成一张可核对的图：目标样本 + 底座 + 补充，都用实际出图。

为什么用实际出图而不是只用风格预览图：
  · 预览图是风格目录给的样例，名不副实的情况实测存在（Luminous Twilight Nostalgia
    — Amber-Magenta Clouds 的预览是一张蓝色晶体）；
  · 图鉴里那张图是被真实方案跑出来、并且被用户标记过认可的，
    它同时说明「这个风格在组合里长什么样」。核对配方时这是更强的证据。

输出：_完善方案.jpg
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
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

#: PIL 自带的位图字体没有中文，标签会全变成方块 —— 实测第一版就是这样，
#: 图文齐全但一个字也读不出来。用系统雅黑。
_FONT_PATHS = [r"C:/Windows/Fonts/msyh.ttc", r"C:/Windows/Fonts/msyhl.ttc",
               r"C:/Windows/Fonts/simhei.ttf"]
FONT = next((ImageFont.truetype(f, 15) for f in _FONT_PATHS if pathlib.Path(f).is_file()),
            ImageFont.load_default())

COMFY = r"E:/ComfyUI-aki/ComfyUI-aki-v3/ComfyUI"
DESK = pathlib.Path(r"C:/Users/wyj/Desktop")
ATLAS = json.loads((DESK / "_atlas_D.json").read_text(encoding="utf-8"))

TARGET = DESK / "风格LoRA_认可样图与本组20张"


def preview(style_name):
    inv = C.ResourceInventory(COMFY, [])
    for s in inv.snapshot()["styles"]:
        if s["name"] != style_name:
            continue
        t = s.get("thumbnail") or ""
        if "filename=" not in t:
            return None
        fn = urllib.parse.unquote(t.split("filename=")[1].split("&")[0])
        sub = urllib.parse.unquote(t.split("subfolder=")[1].split("&")[0]) if "subfolder=" in t else ""
        p = pathlib.Path(COMFY) / "input" / sub / fn
        return p if p.is_file() else None
    return None


_USED = set()


def outputs_of(style_name, want=1, min_size=1_500_000):
    """图鉴里带这个风格、且被标记为认可的实拍图。"""
    marked = set()
    mk = HERE / "_marks.json"
    if mk.is_file():
        marked = set(json.loads(mk.read_text(encoding="utf-8"))["sets"][0]["paths"])
    out = []
    for im in ATLAS["images"]:
        if style_name not in im["styles"]:
            continue
        p = pathlib.Path(im["path"])
        if not p.is_file() or im["size"] < min_size:
            continue
        if p in _USED:
            continue
        out.append((p, im["uid"] in marked))
    # 认可过的排前面
    out.sort(key=lambda x: (not x[1],))
    picked = [p for p, _ in out if p not in _USED][:want]
    _USED.update(picked)
    return picked


ROWS = [
    ("目标", (255, 215, 90), [
        (TARGET / "认可样图_01_灯笼.png", "认可样图·灯笼"),
        (TARGET / "认可样图_02_读书.png", "认可样图·读书"),
        (TARGET / "第二组_08.png", "第二组08"),
        (TARGET / "面部特写_03.png", "面部特写03"),
    ]),
    ("底座·你的已验证", (140, 200, 255), []),
    ("补充·我加的", (150, 235, 170), []),
]

STYLE_ROWS = {
    "底座·你的已验证": [
        ("Polished Mobile Gacha Style", "精致手游·主体", outputs_of("Polished Mobile Gacha Style", 1)),
        ("Cyber-Gothic Noir Manga", "黑暗漫画线稿", outputs_of("Cyber-Gothic Noir Manga", 1)),
    ],
    "补充·我加的": [
        ("Monumental Twilight Cloudscapes", "暮光云海·光影", outputs_of("Monumental Twilight Cloudscapes", 1) or [preview("Monumental Twilight Cloudscapes")]),
        ("Nostalgic Twilight Anime — Watercolor Sunset Wash", "水彩日落·氛围", [preview("Nostalgic Twilight Anime — Watercolor Sunset Wash")]),
        ("Luminous Twilight Horizons", "备选B·暮光Horizons", [preview("Luminous Twilight Horizons")]),
        ("Ethereal Pointillist Storybook", "备选A·换线稿", outputs_of("Ethereal Pointillist Storybook", 1) or [preview("Ethereal Pointillist Storybook")]),
    ],
}

CELL = 300


def fit(im, box):
    im = im.convert("RGB")
    w, h = im.size
    s = min(box / w, box / h)
    return im.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)


def main():
    cells = []
    for name, color, items in ROWS:
        if items:
            for p, lab in items:
                if p and pathlib.Path(p).is_file():
                    cells.append((color, f"{name}｜{lab}", pathlib.Path(p)))
        else:
            for style, lab, found in STYLE_ROWS.get(name, []):
                got = False
                for p in found:
                    if p and pathlib.Path(p).is_file():
                        cells.append((color, f"{name}｜{lab}", pathlib.Path(p)))
                        got = True
                        break
                if not got:
                    cells.append((color, f"{name}｜{lab}（无图）", None))

    cols = 4
    rows = (len(cells) + cols - 1) // cols
    W = cols * CELL + (cols + 1) * 8
    H = rows * (CELL + 34) + (rows + 1) * 8
    sheet = Image.new("RGB", (W, H), (22, 24, 28))
    d = ImageDraw.Draw(sheet)
    for i, (color, label, path) in enumerate(cells):
        c, r = i % cols, i // cols
        x = 8 + c * (CELL + 8)
        y = 8 + r * (CELL + 34)
        d.rectangle([x - 2, y - 2, x + CELL, y + CELL], outline=color, width=3)
        if path is not None:
            im = fit(Image.open(path), CELL)
            sheet.paste(im, (x + (CELL - im.size[0]) // 2, y + (CELL - im.size[1]) // 2))
        for k, line in enumerate(label.split("｜")):
            d.text((x, y + CELL + 4 + k * 16), line[:30], fill=color, font=FONT)
    out = HERE / "_完善方案.jpg"
    sheet.save(out, quality=88)
    print("→", out)
    for color, label, path in cells:
        print(("  ○ " if path else "  × ") + label)


if __name__ == "__main__":
    main()
