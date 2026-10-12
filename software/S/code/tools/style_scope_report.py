# -*- coding: utf-8 -*-
"""风格能力范围统计（可复用）—— 一键重跑，增量反映最新生成批次。

用法：
    python tools/style_scope_report.py                    # 用默认路径
    python tools/style_scope_report.py --out-dir ./output  # 自定义输出目录
    python tools/style_scope_report.py --reviews <dir> --styles <dir> --images <dir>

数据源（只读，不写用户数据）：
    --reviews  ComfyBatch 审核记录目录（默认 %LOCALAPPDATA%/ComfyBatch-S/reviews）
    --styles   ComfyUI 风格库目录（comfyui-easy-use/styles）
    --images   成图落盘根目录（含 comfybatch-v2-*-report.json，作为审核记录的补充）

产出：
    <out-dir>/风格能力范围统计报告.html   可视化报告（单文件，数据内嵌）
    <out-dir>/style_scope_deep.json       结构化数据，供二次分析

设计要点：
  * 审核记录（reviews/*.json）为权威来源；同一 run 的批次报告不重复计数。
  * 技法维度由词条自身 prompt 文本归类，词表见 DIMS，可自行扩充。
"""
from __future__ import annotations
import argparse, collections, glob, json, os, re, sys

# ---------------- 技法维度词表（可扩充） ----------------
DIMS: dict[str, list[str]] = {
    "线稿·描线": [r"\bline ?art\b", r"\blinework\b", r"clean line", r"delicate line",
                 r"fine-line", r"contour", r"\bink line", r"outline"],
    "手绘·铅笔炭笔": [r"\bpencil\b", r"\bcharcoal\b", r"\bsketch", r"\bhatching\b", r"cross-hatch",
                    r"\bstippl", r"\bpointill", r"doodle", r"graphite"],
    "厚涂·可见笔触": [r"impasto", r"brush ?stroke", r"brushwork", r"visible (paint|brush)",
                    r"palette knife", r"gestural", r"painterly"],
    "赛璐璐·平涂": [r"cel[- ]?shad", r"flat[- ]?(color|shad)", r"hard[- ]edge", r"posteriz",
                   r"spot color", r"limited color"],
    "水彩·晕染": [r"watercolou?r", r"\bgouache", r"ink wash", r"\bwash\b", r"wet-on-wet",
                r"pigment bleed", r"soft pigment"],
    "油画·古典": [r"oil (paint|on)", r"classical paint", r"renaissance", r"baroque",
                r"chiaroscuro", r"sfumato", r"glazing", r"old master"],
    "半调·印刷": [r"halftone", r"screen ?tone", r"screentone", r"risograph", r"riso\b",
                r"screen-print", r"ben-?day", r"offset print", r"misregist"],
    "矢量·扁平": [r"\bvector\b", r"flat design", r"geometric", r"minimalis", r"\bbauhaus",
                r"swiss style", r"glyphic", r"iconograph"],
    "3D·渲染": [r"3d render", r"\boctane", r"ray ?trac", r"subsurface", r"\bpbr\b", r"unreal",
              r"blender", r"polygonal", r"low-?poly", r"\bsculpt", r"cgi\b", r"physically based"],
    "胶片·颗粒": [r"film grain", r"analog", r"35mm", r"\blomo", r"kodak", r"polaroid",
                r"light leak", r"chromatic aberration", r"\bgrain\b"],
    "霓虹·辉光": [r"neon", r"\bglow", r"bloom\b", r"luminous", r"bioluminesc", r"holograph",
                r"cyberpunk", r"synthwave", r"vaporwave", r"\bled\b", r"sheen"],
    "噪点·故障": [r"glitch", r"datamosh", r"scanline", r"pixelat", r"\bvhs\b", r"distortion",
                r"compression artifact", r"\bdecay\b", r"smear"],
    "纹理·肌理": [r"\btexture", r"mixed media", r"collage", r"tactile", r"paper",
                r"woven", r"surface detail"],
    "光影·氛围": [r"cinematic light", r"dramatic light", r"backlit", r"golden hour",
                r"soft light", r"volumetric", r"atmospheric", r"rim light", r"dappled"],
    "超现实·梦幻": [r"surreal", r"dreamlike", r"ethereal", r"psychedelic", r"whimsical",
                  r"fantastical", r"otherworldly", r"dream"],
    "极简·留白": [r"negative space", r"sparse", r"\bsubtle\b", r"understated", r"austere",
                r"reductive", r"minimal"],
    "装饰·图案": [r"art nouveau", r"ornamental", r"\bpattern", r"decorative", r"floral",
                r"mandala", r"arabesque", r"gothic ornament", r"botanica", r"folkloric"],
    "质感·材质": [r"glossy", r"matte\b", r"metallic", r"translucent", r"airbrush",
                r"high-polish", r"rendering quality", r"luminous texture"],
    "摄影·写实": [r"photograph", r"\bphoto\b", r"portrait", r"depth[- ]of[- ]field",
                r"studio light", r"retouch", r"lens\b", r"fashion-lookbook",
                r"figure-photography", r"editorial", r"photographic clarity",
                r"framing", r"publication-ready", r"shadow detail"],
    "构图·景别": [r"framing", r"composition", r"full (length|figure|body)",
                r"cropping", r"proportion", r"posture", r"silhouette", r"stance",
                r"negative space", r"centered", r"rule of thirds"],
}
_COMP = {k: [re.compile(p, re.I) for p in v] for k, v in DIMS.items()}


def lib_of(catalog: str) -> str:
    c = catalog
    if c.endswith("_styles"):
        c = c[:-len("_styles")]
    if c.startswith("krea2_"):
        c = c[len("krea2_"):]
    return c or "未知"


def load_catalogs(styles_dir: str) -> dict[tuple[str, str], dict]:
    libs: dict[tuple[str, str], dict] = {}
    for fp in glob.glob(os.path.join(styles_dir, "*.json")):
        cid = os.path.splitext(os.path.basename(fp))[0]
        try:
            data = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for it in data:
            nm = str(it.get("name") or "").strip()
            if nm:
                libs[(cid, nm)] = {
                    "catalog": cid, "name": nm,
                    "name_cn": str(it.get("name_cn") or "").strip(),
                    "prompt": str(it.get("prompt") or ""),
                    "negative_prompt": str(it.get("negative_prompt") or ""),
                }
    return libs


def harvest(obj, src, records, seen):
    items = None
    if isinstance(obj, dict):
        if isinstance(obj.get("items"), dict):
            items = list(obj["items"].values())
        elif isinstance(obj.get("results"), list):
            items = obj["results"]
    if not items:
        return
    run_id = obj.get("run_id") or ""
    for it in items:
        if not isinstance(it, dict):
            continue
        g = it.get("generation") or {}
        styles, loras = g.get("styles") or [], g.get("loras") or []
        if not styles and not loras:
            continue
        sig = (run_id, it.get("index"), src)
        if sig in seen:
            continue
        seen.add(sig)
        records.append({
            "src": src, "run_id": run_id, "index": it.get("index"),
            "title": it.get("title") or "", "preset_name": it.get("preset_name") or "",
            "status": it.get("status") or "", "model": g.get("model") or "",
            "styles": [{"catalog": str(s.get("catalog") or s.get("library") or "").strip(),
                        "name": str(s.get("name") or "").strip(),
                        "name_cn": str(s.get("name_cn") or "").strip(),
                        "display_name": str(s.get("display_name") or "").strip()}
                       for s in styles if isinstance(s, dict) and s.get("name")],
            "loras": [{"name": str(l.get("name") or "").strip(), "strength": l.get("strength"),
                       "use_triggers": bool(l.get("use_triggers"))}
                      for l in loras if isinstance(l, dict) and l.get("name")],
        })


def collect(reviews_dir: str, images_dir: str) -> list[dict]:
    records, seen = [], set()
    for fp in glob.glob(os.path.join(reviews_dir, "*.json")):
        if fp.endswith(".tmp"):
            continue
        try:
            harvest(json.load(open(fp, encoding="utf-8")), "reviews", records, seen)
        except Exception as e:
            print(f"  跳过 {os.path.basename(fp)}: {e}", file=sys.stderr)
    covered = {r["run_id"] for r in records if r["run_id"]}
    if images_dir and os.path.isdir(images_dir):
        for fp in glob.glob(os.path.join(images_dir, "comfybatch-v2-*-report.json")):
            try:
                obj = json.load(open(fp, encoding="utf-8"))
            except Exception:
                continue
            if obj.get("run_id") in covered:
                continue
            harvest(obj, "report", records, seen)
    return records


def analyze(records, catalogs):
    total = len(records) or 1
    item_use, item_cn = collections.Counter(), {}
    item_presets = collections.defaultdict(collections.Counter)
    lib_use, lib_items, lib_presets = collections.Counter(), collections.defaultdict(set), collections.defaultdict(collections.Counter)
    for r in records:
        for s in r["styles"]:
            k = (s["catalog"], s["name"])
            item_use[k] += 1
            if s.get("name_cn") or s.get("display_name"):
                item_cn[k] = s.get("name_cn") or s.get("display_name")
            item_presets[k][r["preset_name"] or "(未命名)"] += 1
            lib_items[s["catalog"]].add(s["name"])
            lib_presets[s["catalog"]][r["preset_name"] or "(未命名)"] += 1
        for c in {s["catalog"] for s in r["styles"]}:
            lib_use[c] += 1

    def tech_of(k):
        txt = (catalogs.get(k, {}).get("prompt") or "") + " " + (catalogs.get(k, {}).get("negative_prompt") or "")
        return [d for d, pats in _COMP.items() if any(p.search(txt) for p in pats)]

    def influences_of(k):
        txt = catalogs.get(k, {}).get("prompt") or ""
        m = re.search(r"Influenced by:\s*(.+?)(?:\.\.|$)", txt, re.S | re.I)
        if not m:
            return []
        return [t.strip(" .·\n") for t in re.split(r",|\band\b", m.group(1)) if 2 < len(t.strip()) < 60]

    item_role, item_mates = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
    for r in records:
        ks = [(s["catalog"], s["name"]) for s in r["styles"]]
        for k in ks:
            item_role[k]["独奏" if len(ks) == 1 else "组合"] += 1
            for k2 in ks:
                if k2 != k:
                    item_mates[k][k2] += 1

    items = []
    for k, v in item_use.most_common():
        items.append({
            "catalog": k[0], "lib": lib_of(k[0]), "name": k[1], "name_cn": item_cn.get(k, ""),
            "images": v, "pct": round(v / total * 100, 1), "tech": tech_of(k),
            "influences": influences_of(k), "prompt": catalogs.get(k, {}).get("prompt", ""),
            "role": dict(item_role[k]),
            "mates": [{"name": m[0][1], "lib": lib_of(m[0][0]), "name_cn": item_cn.get(m[0], ""),
                       "images": m[1]} for m in item_mates[k].most_common(5)],
            "presets": dict(item_presets[k].most_common(4)),
        })

    lora_use, lora_s, lora_libs = collections.Counter(), collections.defaultdict(list), collections.defaultdict(collections.Counter)
    for r in records:
        for l in r["loras"]:
            lora_use[l["name"]] += 1
            if isinstance(l["strength"], (int, float)):
                lora_s[l["name"]].append(float(l["strength"]))
            for s in r["styles"]:
                lora_libs[l["name"]][s["catalog"]] += 1

    tech_imgs, tech_items = collections.Counter(), collections.defaultdict(set)
    for r in records:
        for d in {d for s in r["styles"] for d in tech_of((s["catalog"], s["name"]))}:
            tech_imgs[d] += 1
    for k, dims in ((k, tech_of(k)) for k in item_use):
        for d in dims:
            tech_items[d].add(k)

    recipes = []
    for pname, pcount in collections.Counter(r["preset_name"] for r in records if r["preset_name"]).most_common():
        iset, lor = collections.Counter(), collections.Counter()
        ls = collections.defaultdict(list)
        for r in records:
            if r["preset_name"] != pname:
                continue
            for s in r["styles"]:
                iset[(s["catalog"], s["name"])] += 1
            for l in r["loras"]:
                lor[l["name"]] += 1
                if isinstance(l["strength"], (int, float)):
                    ls[l["name"]].append(float(l["strength"]))
        recipes.append({
            "name": pname, "images": pcount,
            "items": [{"name": k[1], "lib": lib_of(k[0]), "name_cn": item_cn.get(k, ""), "images": v}
                      for k, v in iset.most_common()],
            "loras": [{"name": n, "images": v, "avg": round(sum(ls[n]) / len(ls[n]), 2) if ls[n] else None}
                      for n, v in lor.most_common()],
        })

    lib_pairs = collections.Counter()
    for r in records:
        Ls = sorted({s["catalog"] for s in r["styles"]})
        for i in range(len(Ls)):
            for j in range(i + 1, len(Ls)):
                lib_pairs[(Ls[i], Ls[j])] += 1

    return {
        "overview": {
            "images": len(records),
            "completed": sum(1 for r in records if r["status"] == "completed"),
            "runs": len({r["run_id"] for r in records}),
            "style_presets_used": len({r["preset_name"] for r in records if r["preset_name"]}),
            "distinct_style_items": len(item_use),
            "distinct_libraries": len(lib_use),
            "distinct_loras": len(lora_use),
            "distinct_models": len({r["model"] for r in records if r["model"]}),
            "catalog_items_available": len(catalogs),
            "combination_size": dict(sorted(collections.Counter(len(r["styles"]) for r in records).items())),
        },
        "libraries": [{"catalog": c, "lib": lib_of(c), "images": v,
                       "distinct_items_used": len(lib_items[c]),
                       "distinct_items_available": sum(1 for (cc, _) in catalogs if cc == c),
                       "top_presets": dict(lib_presets[c].most_common(5))}
                      for c, v in lib_use.most_common()],
        "items": items,
        "recipes": recipes,
        "lib_pairs": [{"a": k[0], "a_lib": lib_of(k[0]), "b": k[1], "b_lib": lib_of(k[1]), "images": v}
                      for k, v in lib_pairs.most_common(30)],
        "loras": [{"name": n, "images": v,
                   "strength_min": round(min(lora_s[n]), 3) if lora_s[n] else None,
                   "strength_max": round(max(lora_s[n]), 3) if lora_s[n] else None,
                   "strength_avg": round(sum(lora_s[n]) / len(lora_s[n]), 3) if lora_s[n] else None,
                   "top_libs": [lib_of(c) for c, _ in lora_libs[n].most_common(4)]}
                  for n, v in lora_use.most_common()],
        "tech_used": [{"dim": d, "images": v, "items": len(tech_items[d]),
                       "top_items": [[k[1], item_cn.get(k, "")] for k in
                                     sorted(tech_items[d], key=lambda k: -item_use.get(k, 0))[:6]]}
                      for d, v in tech_imgs.most_common()],
    }


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="风格能力范围统计")
    ap.add_argument("--reviews", default=os.path.join(os.environ.get("LOCALAPPDATA", ""), "ComfyBatch-S", "reviews"))
    ap.add_argument("--images", default=r"C:/Users/wyj/Desktop/D")
    ap.add_argument("--styles", default=r"E:/ComfyUI-aki/ComfyUI-aki-v3/ComfyUI/custom_nodes/comfyui-easy-use/styles")
    ap.add_argument("--out-dir", default=here)
    a = ap.parse_args()

    if not os.path.isdir(a.reviews):
        sys.exit(f"审核记录目录不存在：{a.reviews}")
    if not os.path.isdir(a.styles):
        sys.exit(f"风格库目录不存在：{a.styles}")

    print(f"读取风格库 ... {a.styles}")
    catalogs = load_catalogs(a.styles)
    print(f"  可用词条 {len(catalogs)} 个 / 库文件 {len({c for c, _ in catalogs})} 个")

    print(f"读取生成记录 ... {a.reviews}")
    records = collect(a.reviews, a.images)
    print(f"  图片记录 {len(records)} 条 / 批次 {len({r['run_id'] for r in records})} 个")

    data = analyze(records, catalogs)
    os.makedirs(a.out_dir, exist_ok=True)
    jp = os.path.join(a.out_dir, "style_scope_deep.json")
    json.dump(data, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    o = data["overview"]
    print(f"\n覆盖率：{o['distinct_style_items']} / {o['catalog_items_available']} "
          f"= {o['distinct_style_items']/max(o['catalog_items_available'],1)*100:.2f}%")
    print(f"组合规模分布：{o['combination_size']}")
    print(f"已用词条 Top5：")
    for x in data["items"][:5]:
        print(f"  {x['images']:5d} 张 | {x['name'][:44]}")
    print(f"\n[ok] {jp}")
    print("提示：可视化报告由 tools/build_style_report.py 生成（复用同一份 JSON）。")


if __name__ == "__main__":
    main()
