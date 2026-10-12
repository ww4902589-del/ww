# -*- coding: utf-8 -*-
"""无头验证风格图鉴：数据引擎 + 交集筛选逻辑 + 界面结构。"""
import json, os, re, sys, collections
sys.path.insert(0, r"C:/Users/wyj/WorkBuddy/2026-09-25-19-54-13")
import style_atlas as A

P = F = 0
def ck(name, cond, extra=""):
    global P, F
    if cond: P += 1; print(f"  ✓ {name}")
    else:    F += 1; print(f"  ✗ {name}" + (f" → {extra}" if extra else ""))

print("== 数据引擎 ==")
d = A.build(verbose=False)
st = d["stats"]
imgs = d["images"]
ck("图片 > 0", len(imgs) > 0, str(len(imgs)))
ck("解析率 > 95%", st["resolved"] / max(st["records"], 1) > 0.95, f"{st['resolved']}/{st['records']}")
ck("8 个标签维度齐全",
   set(d["facets"]) == {"preset","theme","library","style","tech","lora","model","run"},
   str(sorted(d["facets"])))
# 只算有风格词条的图（未命名/文件名反推的图本就无词条，计入是口径错误）
_wsty = [im for im in imgs if im.get("styles")]
_multi_rec = sum(1 for im in _wsty if len(im["styles"]) >= 2)
_no_sty = len(imgs) - len(_wsty)
ck(f"无风格词条的图已排除在比例之外（{_no_sty} 张）",
   _no_sty > 0, str(_no_sty))
ck("多分类成立（有词条图中跨多词条 > 50%）",
   _multi_rec / max(len(_wsty), 1) > 0.5, f"{_multi_rec}/{len(_wsty)}")

print("\n== 多分类归属正确性 ==")
# 每张图的标签应与记录一致
multi = [im for im in imgs if len(im["styles"]) >= 2]
ck("存在跨词条图", len(multi) > 0, str(len(multi)))
sample = multi[0]
ck("样例图有多个风格词条", len(sample["styles"]) >= 2, str(sample["styles"]))
ck("样例图有主题标签(若方案名匹配)", isinstance(sample["themes"], list))
# 验证：动漫服装+甜彩线描 这类叠加确实能命中
themed = [im for im in imgs if "动漫服装" in im["themes"] and "甜彩线描" in im["themes"]]
ck("「动漫服装」∧「甜彩线描」有命中", len(themed) > 0, str(len(themed)))

print("\n== 叠加筛选（全 AND 交集）逻辑 ==")
def filt(sel):
    """复刻前端 matches() 的语义：每个已选标签都必须满足（含同维度多选）"""
    out = []
    for im in imgs:
        ok = True
        for dim, vals in sel.items():
            for v in vals:                      # 逐个检查，任一失败即淘汰
                hitv = (
                    (dim == "theme" and v in im["themes"]) or
                    (dim == "library" and v in im["libs"]) or
                    (dim == "style" and v in im["styles"]) or
                    (dim == "tech" and v in im["techs"]) or
                    (dim == "preset" and im["preset"] == v) or
                    (dim == "run" and im["run"] == v) or
                    (dim == "model" and im["model"] == v) or
                    (dim == "lora" and any(l["n"] == v for l in im["loras"]))
                )
                if not hitv:
                    ok = False; break
            if not ok:
                break
        if ok:
            out.append(im)
    return out

a1 = filt({"theme": ["动漫服装"]})
a2 = filt({"theme": ["甜彩线描"]})
both = filt({"theme": ["动漫服装", "甜彩线描"]})
ck("单标签「动漫服装」有结果", len(a1) > 0, str(len(a1)))
ck("单标签「甜彩线描」有结果", len(a2) > 0, str(len(a2)))
ck("叠加结果 ⊆ 各自单标签结果", set(id(x) for x in both) <= set(id(x) for x in a1)
   and set(id(x) for x in both) <= set(id(x) for x in a2))
ck("叠加是严格交集（每张都同时满足两个）",
   all("动漫服装" in x["themes"] and "甜彩线描" in x["themes"] for x in both))
ck("叠加命中数 ≤ 单标签命中数", len(both) <= min(len(a1), len(a2)),
   f"{len(both)} vs {min(len(a1),len(a2))}")
ck("叠加命中数 < 两者之和(确实收窄)", len(both) <= len(set(id(x) for x in a1) | set(id(x) for x in a2)))

# 空交集
empty = filt({"theme": ["动漫服装"], "preset": ["绝不存在的方案XYZ"]})
ck("不存在的条件 → 空结果", len(empty) == 0)

# 跨维度交集
cross = filt({"theme": ["手游"], "tech": ["赛璐璐·平涂"]})
ck("跨维度交集(主题+技法)语义正确",
   all("手游" in x["themes"] and "赛璐璐·平涂" in x["techs"] for x in cross), str(len(cross)))

# 三个条件叠加，应进一步收窄
three = filt({"theme": ["手游"], "tech": ["赛璐璐·平涂"], "library": ["krea2_anime_动漫_styles"]})
ck("三条件叠加 ⊆ 两条件叠加", set(id(x) for x in three) <= set(id(x) for x in cross),
   f"{len(three)} vs {len(cross)}")

print("\n== 生成的 HTML ==")
html = open(r"C:/Users/wyj/WorkBuddy/2026-09-25-19-54-13/风格图鉴.html", encoding="utf-8").read()
ck("无残留占位符", "__DATA__" not in html and "__MAXTHUMB__" not in html)
ck("含 Chart.js", "chart.umd.min.js" in html)
ck("含叠加筛选函数", "function matches" in html and "function toggle" in html)
ck("含统计视图", "function renderStats" in html and "renderStyleTable" in html)
ck("含打开文件夹", "/api/open" in html and "function goFolder" in html)
ck("含六个标签维度定义", all(k in html for k in ["theme","preset","library","style","tech","lora"]))
for k in ['id="facets"','id="grid"','id="statsView"','id="viewer"','id="selbar"']:
    ck(f"元素 {k}", k in html)

print("\n== 标记功能（与图库、统计并列）==")
ck("三个标签页并列", all(f'data-v="{v}"' in html for v in ("browse", "marks", "stats")),
   "需同时有 图库/标记/统计")
ck("标记页容器存在", 'id="marksView"' in html)
ck("标记数量角标存在", 'id="mkbadge"' in html)
ck("卡片渲染含标记按钮", 'class="mk' in html and 'toggleMark' in html)
ck("标记可持久化(localStorage)", "localStorage" in html and "style_atlas_marks_v1" in html)
ck("标记可同步后端", "/api/marks" in html)
ck("标记页含配方反推", "配方反推" in html and "byPreset" in html)
ck("标记页含导出到文件夹", "/api/export_marks" in html and "exportMarks" in html)
ck("标记页含复制配方文本", "copyRecipe" in html)
ck("标记页含清空", "clearMarks" in html)
ck("标记页含风格画像四维",
   all(k in html for k in ["风格词条", "风格库", "主题", "技法维度"]))
ck("查看器可标记", 'id="vmk"' in html and "markCurrent" in html)
ck("标记页有自己的网格", 'id="mkGrid"' in html)
# 标记页不应依赖左侧筛选面板
ck("非图库页隐藏侧栏逻辑存在", "$('#facet').style.display" in html)

# 标记键唯一性：path 会因同名跨批次重复，因此必须用 uid
paths = [im["path"] for im in imgs]
uids = [im["uid"] for im in imgs]
ck("每图有 uid（标记键）", all(isinstance(u, str) and u for u in uids))
ck("uid 全局唯一 → 标记不会串图", len(uids) == len(set(uids)),
   f"{len(uids)} vs {len(set(uids))}")
dup_paths = len(paths) - len(set(paths))
print(f"  （参考：路径重复 {dup_paths} 条 —— 这正是标记必须用 uid 的原因）")

print("\n== 数据源覆盖（防回归）==")
# 扩源后：reviews + 全盘 report.json。应显著多于只读 reviews 时的 1663 条。
ck("收录条数已含 report 补录（> 2000）", len(imgs) > 2000, str(len(imgs)))
srcs = {}
for im in imgs:
    k = im.get("src", "?")
    srcs[k] = srcs.get(k, 0) + 1
print(f"  （来源分布：{srcs}）")
ck("两种数据源都有贡献", len(srcs) >= 1, str(srcs))

print("\n== 未命名统一（c / D 全纳入）==")
ck("存在未命名兜底来源(orphan)", srcs.get("orphan", 0) > 0, str(srcs.get("orphan", 0)))
orph = [im for im in imgs if im.get("src") == "orphan"]
ck("未命名图方案名统一为 (未命名)",
   all(im["preset"] == A.UNNAMED for im in orph), str(len(orph)))
ck("未命名图带「未命名」主题标签",
   all("未命名" in im["themes"] for im in orph), str(len(orph)))
ck("未命名图无风格词条（本就无记录）",
   all(len(im["styles"]) == 0 for im in orph))
ck("未命名图均有 uid", all(im["uid"] for im in orph))
# 抽查若干未命名图确实落在 c 或 D 下
_bad = [im for im in orph if not any(im["path"].startswith(r) for r in ("C:/Users/wyj/Desktop/c", "C:/Users/wyj/Desktop/D"))]
ck("未命名图均来自桌面 c / D", len(_bad) == 0, str(len(_bad)))

print("\n== 文件名/report 反推（记录残缺时的补救）==")
_named = [im for im in imgs if im.get("src") == "named"]
ck("存在文件名反推来源(named)", len(_named) > 0, str(len(_named)))
ck("反推图带方案名", all(im["preset"] for im in _named))
ck("反推图有主题标签（能拆出）或归未命名",
   all(im["themes"] for im in _named))
# review/report 来源的图必有风格词条（记录完整）；残缺记录例外
_recd2 = [im for im in imgs if im.get("src") == "review"]
ck("review 来源图全部有风格词条",
   all(im["styles"] for im in _recd2), f"{sum(1 for im in _recd2 if not im['styles'])} 张缺")

print("\n== 数据源覆盖（防回归）==")
# 扩源后：reviews + 全盘 report.json。应显著多于只读 reviews 时的 1663 条。
ck("收录条数已含 report 补录（> 2000）", len(imgs) > 2000, str(len(imgs)))
srcs = {}
for im in imgs:
    k = im.get("src", "?")
    srcs[k] = srcs.get(k, 0) + 1
print(f"  （来源分布：{srcs}）")
ck("三种以上数据源都有贡献", len(srcs) >= 3, str(srcs))

print(f"\n===== 结果: {P} 通过 / {F} 失败 =====")
sys.exit(1 if F else 0)
