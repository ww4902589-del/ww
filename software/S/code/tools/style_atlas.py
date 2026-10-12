# -*- coding: utf-8 -*-
"""风格图鉴 · 统一数据引擎

一件事：把 ComfyBatch 生成记录 + 风格库 + 图片文件，构建成
「多维度标签 + 可叠加筛选」的统一数据集，供统计与看图共用。

标签维度（一张图同时属于多个类别）：
  preset   方案名（如 20260920-动漫服装-01-甜彩线描）
  theme    主题词（从方案名拆出，如 动漫服装 / 甜彩线描 / 手游 等）
  library  风格库（动漫风格合集 / 素描·线稿与钢笔 …）
  style    风格词条（精确到具体风格）
  tech     技法维度（线稿·描线 / 厚涂·可见笔触 …）
  lora     使用的 LoRA
  model    底模
  run      批次

筛选：多标签叠加 = 交集（AND）。选中「动漫服装」+「甜彩线描」即精确定位同时满足两者的图。
"""
from __future__ import annotations
import collections, glob, hashlib, json, os, re, sys, time, zlib
import atlas_management as Management


# ---------------- 状态落盘目录 ----------------
# 代码目录 ≠ 可写目录。图鉴被 S 内嵌后，代码跑在打包出来的 exe 里，
# __file__ 指向解包临时目录（_MEIPASS）—— 往那儿写标记和评级，退出即被删。
# 所以这里把「自己的状态文件放哪」独立出来，与代码位置解耦。
def resolve_data_dir() -> str:
    """标记 / 评级 / 索引缓存 / 渲染出的页面 的家。

    优先级：
      1. ``ATLAS_DATA_DIR`` —— 联调隔离用，也可把数据指到别处；
      2. 打包运行 —— ``%LOCALAPPDATA%\\ComfyBatch-S\\atlas``，
         与 S 自己的设置同处一地，exe 换位置数据也不丢；
      3. 源码运行 —— 图鉴自己的目录（原行为，命令行用法完全不变）。
    """
    env = os.environ.get("ATLAS_DATA_DIR")
    if env:
        return os.path.abspath(env)
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "ComfyBatch-S", "atlas")
    return os.path.dirname(os.path.abspath(__file__))


DATA_DIR = resolve_data_dir()


# ---------------- 派生结果缓存 ----------------
# build 的二十来秒几乎全花在「对同一张图反复算同一件事」上：判技法要跑几百万次
# 正则、判图片是不是正常要开一次文件头、读风格组合又要开一次。这些结果只取决于
# 那个文件本身 —— 文件没动，就没有理由重算。
#
# 失效依据是 (mtime_ns, size)：图被重新生成，mtime 就变，账自然作废。不需要谁
# 记得去清缓存，也不用按目录批量失效 —— 这一点很重要，靠人记得清的缓存等于没有。
#
# 两个 bucket 分开：files 的 key 带路径（不同目录的同名副本可能戳相同），
# text 的 key 是内容哈希（同样的正文算一次就够）。文本没有失效问题。
#
# 为什么不在这里做 GC：key 是「路径 + 戳」，一张图被重新生成只是换一个 key，
# 老 key 会留下。实测全库 7315 张的账约 4 MB，孤儿条目的增速是「被重新生成过的
# 图的张数」，量级很小，不值得为它引入更复杂的记账。
_CACHE_SCHEMA = 2
_cache_buckets = None
_cache_path = None
_cache_dirty = False
_MISS = object()

#: 当前风格库的内容指纹，由 build() 在读完风格库后写入。PNG 归因的结果依赖它，
#: 所以它必须进缓存的 key —— 否则改了词条还在吃旧结果，而且是静默地吃。
_STYLE_SIG = ""


def style_signature(styles) -> str:
    """风格库内容指纹：词条名 + prompt 正文。

    不用目录 mtime：风格库可能是多个文件、还可能被同名覆盖，内容指纹比时间戳
    更贴近「结果到底会不会变」这件事本身。
    """
    h = hashlib.sha1()
    for k in sorted(styles.keys()):
        h.update(("%s\x1f%s\x1e" % (k[0], k[1])).encode("utf-8"))
        h.update(str((styles[k] or {}).get("prompt") or "").encode("utf-8"))
    return h.hexdigest()[:12]


def _cache():
    global _cache_buckets, _cache_path
    if _cache_buckets is None:
        _cache_path = os.path.join(DATA_DIR, "_derive_cache.json")
        files, text = {}, {}
        try:
            with open(_cache_path, encoding="utf-8") as f:
                raw = json.load(f)
            if raw.get("schema") == _CACHE_SCHEMA:
                files = raw.get("files") or {}
                text = raw.get("text") or {}
        except Exception:
            pass          # 没有 / 读坏 / 版本不符 —— 都当空缓存，全量重算一遍就是
        _cache_buckets = (files, text)
    return _cache_buckets


def _stamp(path):
    """文件的失效戳；取不到（文件不在）返回空串，调用方就别去缓存了。"""
    try:
        st = os.stat(path)
        return "%d:%d" % (st.st_mtime_ns, st.st_size)
    except OSError:
        return ""


def _fkey(path, tag):
    """缓存 key：用途标签 + 路径 + 文件戳。

    **tag 不能省。** 同一个文件会被问好几件不同的事（尺寸、执行图原文、
    正面正文、风格归因），它们共用一个 bucket，只拿路径当 key 会让它们互相
    覆盖 —— 实测踩过：`image_dimensions` 读到 `png_prompt_text` 存下的 JSON
    字符串，拿前两个字符当宽高，`is_sane_image` 判失败，图鉴里少了三千多张图。
    """
    s = _stamp(path)
    return ("%s|%s|%s" % (tag, path, s)) if s else ""


def cache_flush():
    """把缓存落盘。build() 收尾时调一次 —— 攒着一起写，而不是每张图写一遍 4 MB。"""
    global _cache_dirty
    if not _cache_dirty or _cache_path is None:
        return
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = _cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"schema": _CACHE_SCHEMA,
                       "files": _cache_buckets[0], "text": _cache_buckets[1]},
                      f, ensure_ascii=False)
        os.replace(tmp, _cache_path)
        _cache_dirty = False
    except Exception:
        pass


def _cput(idx, key, value):
    """写缓存。idx：0 = files（按文件戳）1 = text（按内容哈希）。"""
    global _cache_dirty
    _cache()[idx][key] = value
    _cache_dirty = True


def cache_stats():
    files, text = _cache()
    return {"files": len(files), "text": len(text),
            "path": _cache_path, "dirty": _cache_dirty}


# ---------------- 技法维度词表 ----------------
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
# 注意：这里刻意保持「每维度一组独立正则」，**不要**合并成一条 (?:a|b|c)。
# 合并看起来能把 N 次 search 收成 1 次，实测反而慢 11%
# （1500 条真实风格 prompt：0.521 s → 0.577 s，重复 4 轮取最快）。
# 原因：单条 pattern 能吃到字面量前缀的快扫，每维度十几条分支塞进大交替后
# 这个优化就失效了。tech_of 的开销要靠结果缓存去省（见「派生结果缓存」一节），
# 不是靠合并正则。这条是被实测否掉的方案，别再试第二次。
_COMP = {k: [re.compile(p, re.I) for p in v] for k, v in DIMS.items()}

# ---------------- 技法维度 · 中文词表 ----------------
# 原 DIMS 全是英文正则，对**中文提示词**几乎失效：一批手写中文提示的迭代稿
# （`一件水彩风格的洛丽塔连衣裙…`）技法维度全空。这里补一份中文关键词表，
# 与英文表合并后一起匹配。单独放一张表，方便日后只动中文部分、不碰英文表。
DIMS_CN: dict[str, list[str]] = {
    "线稿·描线": ["墨线", "线稿", "描线", "勾线", "线条", "排线", "钢笔线"],
    "手绘·铅笔炭笔": ["铅笔", "炭笔", "素描", "速写", "手绘稿"],
    "厚涂·可见笔触": ["厚涂", "笔触", "笔刷感", "油画笔触", "刮刀"],
    "赛璐璐·平涂": ["赛璐璐", "平涂", "硬边", "色块", "硬边阴影"],
    "水彩·晕染": ["水彩", "晕染", "湿画", "水墨", "透明罩染", "水彩风格"],
    "油画·古典": ["油画", "古典油画", "古典", "明暗对照", "巴洛克"],
    "半调·印刷": ["半调", "网点", "丝网", "印刷", "漫画网点"],
    "矢量·扁平": ["矢量", "扁平", "几何", "极简设计", "扁平化"],
    "3D·渲染": ["3d", "三维", "渲染", "次表面", "低模", "雕塑"],
    "胶片·颗粒": ["胶片", "颗粒", "噪点滤镜", "复古胶片", "漏光"],
    "霓虹·辉光": ["霓虹", "辉光", "发光", "赛博", "光晕", "荧光"],
    "噪点·故障": ["故障", "扫描线", "像素化", "失真", "故障艺术"],
    "纹理·肌理": ["肌理", "质感纹理", "拼贴", "纸纹"],
    "光影·氛围": ["光影", "氛围光", "电影感光影", "逆光", "体积光", "丁达尔",
                "金色时刻", "柔光", "明暗"],
    "超现实·梦幻": ["超现实", "梦幻", "空灵", "奇想", "异世界"],
    "极简·留白": ["留白", "极简", "简约", "克制", "负空间"],
    "装饰·图案": ["装饰", "花纹", "图案", "新艺术", "花卉纹样", "植物纹样"],
    "质感·材质": ["材质", "光泽", "哑光", "金属感", "半透明", "高光", "喷笔"],
    "摄影·写实": ["摄影", "写实", "人像摄影", "景深", "棚拍", "商业摄影", "写实感"],
    "构图·景别": ["构图", "景别", "全身", "半身", "特写", "取景", "剪影", "三分法"],
}
# 合并进技法匹配表：中文表在前，英文表在后，互不覆盖
for _k, _v in DIMS_CN.items():
    _COMP.setdefault(_k, [])
    _COMP[_k] = [re.compile(p, re.I) for p in _v] + _COMP[_k]

# ---------------- 主题词表（把方案名拆成可检索主题） ----------------
# 每个主题：显示名 -> 匹配方案名/preset 的正则
THEMES: dict[str, str] = {
    "动漫服装": r"动漫服装|服装",
    "甜彩线描": r"甜彩|线描",
    "联名海报": r"联名|海报",
    "汉服": r"汉服",
    "女仆": r"女仆",
    "巫女": r"巫女",
    "JK": r"JK",
    "复古": r"复古",
    "空灵": r"空灵",
    "暮色": r"暮色|暗色",
    "哥特": r"哥特|Gothic",
    "手游": r"手游|抽卡|Gacha",
    "漫画": r"漫画|Manga",
    "数字绘画": r"数字|Digital",
    "赛璐璐": r"赛璐璐|Cel",
    "海报封面": r"海报|封面|Poster|Cover",
    "舞台": r"舞台",
    "彩色图形": r"彩漫|全彩|图形",
    "未命名": r"^\(未命名\)$",      # 无记录图片的统一标签，可单独筛选
}
_THEME_RE = {k: re.compile(v, re.I) for k, v in THEMES.items()}

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
UNNAMED = "(未命名)"          # 无记录图片统一归入的方案名

# ---------------- 风格归因修复：从 PNG 内嵌元数据反推风格组合 ----------------
# 背景：大量早期批次的生成记录（reviews / report）里 `generation.styles` 为空，
# 风格定义组合彻底丢了 —— 页面上一排「未命名」，风格库/词条筛选全落空。
# 但 ComfyUI 保存 PNG 时会把整张执行图写进 tEXt 的 `prompt` 块，里面有
# `easy stylesSelector` 节点，记着这次实际用的 `styles`（风格库）与
# `select_styles`（词条，可能逗号分隔多个）。这是**最权威的现场证据**，
# 比事后记录更可靠。全部用标准库解析文件头，不解码像素。
_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_STYLE_NODE_HINTS = ("stylesselector", "stylesselector")


def png_text_chunks(path, keys=("prompt",)):
    """读取 PNG 的 tEXt / iTXt / zTXt 文本块，返回 {关键字: 文本}。

    只扫到第一个 IDAT 为止（文本块按规范都在像素数据之前），
    所以对大图也很快，不会把整个文件读一遍。
    """
    out = {}
    want = {k.lower() for k in keys}
    try:
        with open(path, "rb") as f:
            if f.read(8) != _PNG_SIG:
                return out
            while True:
                lb = f.read(4)
                if len(lb) < 4:
                    break
                ln = int.from_bytes(lb, "big")
                typ = f.read(4)
                if typ not in (b"tEXt", b"iTXt", b"zTXt"):
                    if typ == b"IDAT":
                        break
                    if ln > 100_000_000:       # 异常长度，防跑飞
                        break
                    f.seek(ln + 4, 1)
                    continue
                data = f.read(ln)
                f.read(4)                      # CRC
                try:
                    if typ == b"tEXt":
                        k, v = data.split(b"\x00", 1)
                        k, v = k.decode("latin1"), v
                    elif typ == b"iTXt":
                        parts = data.split(b"\x00", 5)
                        if len(parts) < 6:
                            continue
                        k, v = parts[0].decode("latin1"), parts[-1]
                    else:                      # zTXt：关键字 + 压缩方法 + zlib 数据
                        k, rest = data.split(b"\x00", 1)
                        k, v = k.decode("latin1"), zlib.decompress(rest[1:])
                except Exception:
                    continue
                if k.lower() in want:
                    out[k.lower()] = v.decode("utf-8", "replace")
    except Exception:
        return out
    return out


def png_prompt_text(path):
    """PNG 里那段执行图（"prompt" 文本块）原文；取不到返回空串。

    单独抽出来，是因为原先有两个地方各读一次同一个文件：
    styles_from_png 读它找风格节点，png_positive_text 又读它找正面正文。
    一张图开两次文件，几千张就是几千次白开的系统调用 —— 实测这一项
    在 7315 张的库上占到 6.4 秒（13,457 次 open）。
    """
    key = _fkey(path, "prompt")
    if key:
        got = _cache()[0].get(key, _MISS)
        if got is not _MISS:
            return got
    val = png_text_chunks(path, ("prompt",)).get("prompt") or ""
    if key:
        _cput(0, key, val)
    return val


def styles_from_png(path):
    """从 PNG 内嵌执行图里取出风格组合。

    返回 [(catalog, style_name), ...]；取不到返回 []。
    `styles` 字段可能一种多选（逗号分隔），全部拆开。
    """
    raw = png_prompt_text(path)
    if not raw:
        return []
    try:
        g = json.loads(raw)
    except Exception:
        return []
    if not isinstance(g, dict):
        return []
    for node in g.values():
        if not isinstance(node, dict):
            continue
        ct = str(node.get("class_type") or "").lower()
        if not any(h in ct for h in _STYLE_NODE_HINTS):
            continue
        inp = node.get("inputs") or {}
        catalog = str(inp.get("styles") or "").strip()
        sel = str(inp.get("select_styles") or "").strip()
        # 有些批次把整个风格组合序列化成 JSON 数组塞进 select_styles：
        #   [{"catalog": "...", "name": "..."}, ...]
        # 直接按逗号拆会把它撕成碎片（曾把 styles 存成一堆字符串片段）。
        if sel.startswith("["):
            try:
                arr = json.loads(sel)
            except Exception:
                arr = None
            if isinstance(arr, list):
                out = []
                for s in arr:
                    if isinstance(s, dict):
                        c = str(s.get("catalog") or s.get("library") or "").strip()
                        n = str(s.get("name") or "").strip()
                        if n:
                            out.append((c, n))
                    elif isinstance(s, str) and s.strip():
                        out.append((catalog, s.strip()))
                if out:
                    return out
        names = [x.strip() for x in re.split(r"[,;]", sel) if x.strip()]
        if not names and not catalog:
            continue
        if not names:
            return [(catalog, "")]
        return [(catalog, n) for n in names]
    return []
DEFAULT_REVIEWS = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ComfyBatch-S", "reviews")
DEFAULT_STYLES = r"E:/ComfyUI-aki/ComfyUI-aki-v3/ComfyUI/custom_nodes/comfyui-easy-use/styles"
DEFAULT_ROOTS = [r"C:/Users/wyj/Desktop", r"D:/liu", r"E:/工作流"]
# 兜底（未命名）只在这些目录里找，避免把无关素材全拖进图鉴
ORPHAN_ROOTS = [r"C:/Users/wyj/Desktop/c", r"C:/Users/wyj/Desktop/D"]
CACHE = os.path.join(DATA_DIR, "_img_index.json")

# 标准 ComfyBatch 落盘命名：`NNN-提示词_NNN--<方案名>_<序号>_.png`
# 存在记录残缺（report 中断、reviews 被清）但命名完好的批次，靠它反推方案名。
_NAMED_RE = re.compile(r"^(\d{3}-提示词_\d+)--(.+?)_\d+_\.png$")


def _skip_dir(name: str) -> bool:
    n = name.lower()
    if n in ("node_modules", ".git", "$recycle.bin",
             "system volume information", "__pycache__", ".workbuddy"):
        return True
    # 软件自测产物目录：`_build_vXXX/upgrade_check/...` 里是 ComfyBatch 审核界面的
    # 缩略图缓存（几百上千张 jpg），不是用户的生成作品，不该进图鉴。
    if n.startswith("_build_") or n == "upgrade_check":
        return True
    # 备份目录（`_backup_*`）是同一批图的副本，进图鉴会让同一张图出现两次，
    # 统计和筛选都会翻倍。一律跳过。
    if n.startswith("_backup"):
        return True
    return n.startswith(".")


def _run_of_path(p: str) -> str:
    """从路径里推断批次号（8 位 hex 目录名）；推断不出给 _UNNAMED。"""
    for seg in reversed(p.split("/")):
        if re.fullmatch(r"[0-9a-f]{8}", seg):
            return seg
    return "_UNNAMED"


# ---------------- 文件夹分类（按磁盘目录归类，不看预设名） ----------------
# 图鉴的路径层级很杂（Desktop/D/、Desktop/c/D/、Desktop/c/2/…），直接取父目录
# 会碎成 1400+ 个类，筛选没法用。所以这里要回答的是：「这张图在磁盘上的哪个
# 文件夹里」—— 但要短到能当筛选按钮用。
#
# **旧实现在这里猜错了**。它从图片所在目录往上找第一个「看起来像批次目录」的
# 段（8 位 hex / 含「轮批套组」/ 含中文 / 带日期），只回这一段 + 根前缀。问题是
# 中间的目录名会被**吞掉**：
#     Desktop/D/T/926266f5/x.png
#   `926266f5` 命中「8 位 hex」→ 直接返回它，`T` 不命中任何规则就被跳过，
#   分类记成 `D/926266f5`。用户在磁盘上看到的是 `D/T/…`，按 `T` 去分类里找
#   —— 界面上根本没有 `T` 这一项。这是「文件夹分类里找不到某天的图」的直接
#   原因：不是图没进来（时间分类里看得到），而是分类名跟磁盘对不上号。
#
# 新实现不再猜哪一段「像批次」，而是取「扫描根之后、文件名之前」的**完整相对
# 路径**。根由 _ROOT_HINTS 认（Desktop / 工作流 / liu）：
#     Desktop/D/T/926266f5/x.png  → `D/T/926266f5`
#     Desktop/c/D/x.png           → `c/D`      （与旧值一致）
#     E:/工作流/xxx/y.png          → `xxx`
# 代价是标签变长、种类变多（实测 199 → 241 种），换来的正是「界面上的名字 =
# 磁盘上的路径」这件事。界面按第一段分组（bigfolder）做两级展示，第一段仍然
# 只有 D / c / E / F / 2 … 这十几个，不会失控。
_ROOT_HINTS = ("Desktop", "工作流", "liu")
#: 不在任何已知扫描根下时的兜底：最多保留末几段（正常扫描走不到这条分支）。
_LABEL_MAX_SEG = 4
#: 「文件夹分类」这条派生规则的版本号。
#: folder 是**派生列**，它变了 build_id 不会变（build_id 只认图集内容），
#: 于是两件事会静默出错：① 磁盘上的 _data_cache.json 被当成新数据端上来，
#: 用户看到的是旧分类；② 页面轮询 build_id 发现没变就不刷新。
#: 把它喂进指纹、并让预载校验版本，两个洞一起堵上。改过 folder_of 就 +1。
FOLDER_RULE = 1


def folder_of(p: str) -> str:
    """把图片路径映射成一个「文件夹分类」标签 —— 磁盘上的真实相对路径。

    返回形如 `D/T/926266f5`、`c/D`、`第12轮` 的标签；
    跨根目录时带上根之后的第一级（Desktop/c → c），保证不同根下的同名目录
    不会撞在一起（磁盘上确有 22 组同名目录，如 D/34393175 与 c/34393175）。
    """
    s = [x for x in str(p or "").replace("\\", "/").split("/") if x]
    if len(s) < 2:
        return "其他"
    parent = s[-2]
    # 找到「扫描根」的位置，取它之后到文件名之前的**全部**段。
    # 取第一个命中（与旧实现一致）：`Desktop/c/...` 里最后一个 `c` 才是根之下
    # 的第一级，而 `Desktop` 是最靠上的根，认它才能把 `c` 保留在标签里。
    for i, seg in enumerate(s[:-1]):
        if seg in _ROOT_HINTS:
            rel = s[i + 1:-1]
            return "/".join(rel) if rel else parent
    # 不在任何已知根下：保守取末几段 —— 至少父目录可见，也不至于把标签拉爆。
    rel = s[max(0, len(s) - 1 - _LABEL_MAX_SEG):-1]
    return "/".join(rel) if rel else (parent or "其他")


def _iter_orphan_images(roots, seen_paths):
    """遍历搜索根下的图片，产出未被 seen_paths 覆盖的那些。"""
    for R in roots:
        if not os.path.isdir(R):
            continue
        for root, dirs, files in os.walk(R):
            dirs[:] = [d for d in dirs if not _skip_dir(d)]
            for f in files:
                if not f.lower().endswith(IMG_EXT):
                    continue
                p = os.path.join(root, f).replace("\\", "/")
                if p not in seen_paths:
                    yield p


def lib_of(c: str) -> str:
    if c.endswith("_styles"):
        c = c[:-len("_styles")]
    if c.startswith("krea2_"):
        c = c[len("krea2_"):]
    return c or "未知"


def build_index(roots, cache=CACHE, max_age=1800):
    if os.path.exists(cache) and time.time() - os.path.getmtime(cache) < max_age:
        try:
            return collections.defaultdict(list, json.load(open(cache, encoding="utf-8")))
        except Exception:
            pass
    idx = collections.defaultdict(list)
    for R in roots:
        if not os.path.isdir(R):
            continue
        for root, dirs, files in os.walk(R):
            dirs[:] = [d for d in dirs if not _skip_dir(d)]
            for f in files:
                if f.lower().endswith(IMG_EXT):
                    idx[f].append(os.path.join(root, f).replace("\\", "/"))
    try:
        json.dump(dict(idx), open(cache, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return idx


def _size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return -1


def _manifest(runs_dir=None):
    """从 reviews/report 里收集归因线索。

    返回 (keys, names, dirname_pairs)：
      keys   = {(文件名, 字节数)}      精确内容指纹
      names  = {文件名}                弱线索
      dirname_pairs = {(父目录名, 文件名)}   ← 关键！

    为什么需要 dirname_pairs：用户把 `D/`、`E/`、`F/`、`G/` 这些批次目录**整体搬进
    `c/` 下**，但 report/review 里存的还是旧路径。旧路径已不存在，拿不到字节数，
    可**旧路径的父目录名（`F`、`E`…）仍保留在磁盘新路径里**。用它 + 文件名
    就能把「搬家后目录名没变」的图找回来。
    """
    global _MANIFEST
    if _MANIFEST is not None:
        return _MANIFEST
    keys = set()          # (name, size)
    names = set()         # name
    dnp = set()           # (parent_dir_name, name)

    def feed(cp):
        cp = (cp or "").replace("\\", "/")
        if not cp:
            return
        bn = os.path.basename(cp)
        names.add(bn)
        dnp.add((os.path.basename(os.path.dirname(cp)), bn))
        if os.path.exists(cp):
            keys.add((bn, _size(cp)))

    rev_dir = runs_dir or DEFAULT_REVIEWS
    for fp in glob.glob(os.path.join(rev_dir, "*.json")):
        try:
            j = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        for it in (j.get("items") or {}).values():
            feed(it.get("copied_to"))
    for rp in _glob_reports(DEFAULT_ROOTS):
        try:
            j = json.load(open(rp, encoding="utf-8"))
        except Exception:
            continue
        for r in (j.get("results") or []):
            feed(r.get("copied_to"))
    _MANIFEST = (keys, names, dnp)
    return _MANIFEST


_MANIFEST = None


def load_styles(styles_dir):
    """风格库 -> 词条元信息（name_cn / prompt）。"""
    out = {}
    if not os.path.isdir(styles_dir):
        return out
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
                out[(cid, nm)] = {
                    "name_cn": str(it.get("name_cn") or "").strip(),
                    "prompt": str(it.get("prompt") or ""),
                }
    return out


def tech_of(prompt: str):
    """正文命中的技法维度。

    这是 build 里最重的一项：实测 14814 次调用、418 万次 re.search、7.2 秒。
    而输入高度重复 —— 风格库那 3946 条词条的 prompt 被反复算了一万多遍。
    所以按正文内容缓存（不是按文件）：同样的文字算一次就够，文本没有失效问题。

    注意：不要试图用「合并成一条大正则」来省这一步，实测那样更慢，见 _COMP 处的注释。
    """
    if not prompt:
        return []
    key = hashlib.sha1(prompt.encode("utf-8", "replace")).hexdigest()[:16]
    got = _cache()[1].get(key, _MISS)
    if got is not _MISS:
        return list(got)
    val = [d for d, pats in _COMP.items() if any(p.search(prompt) for p in pats)]
    _cput(1, key, val)
    return val


def build_name_index(styles):
    """{词条名 -> [(库, 元信息), ...]}，用于把 PNG 里的风格名反查回风格库。

    词条名跨库重名极少（实测 3946 条里只有 1 对），所以按名反查基本唯一。
    """
    by_name = collections.defaultdict(list)
    for (cat, nm), meta in styles.items():
        by_name[nm].append((cat, meta))
    return by_name


def build_prompt_index(styles, min_len=40):
    """[(风格 prompt 主体小写, 库, 词条名)]，用于「工作流没挂 selector」时的兜底。

    很多迭代稿/手调图的工作流是直接把手写的风格文本塞进 CLIPTextEncode，
    没有 `easy stylesSelector` 节点可读。但那串文本往往**就是风格库词条的
    prompt 原文**，拿它去比对就能认出用的是哪个词条。

    风格 prompt 的规范写法是 `...正文... Subject:{prompt}`，正文才是风格指纹，
    所以只取 Subject 之前的部分，并要求长度 >= min_len 以防误命中短词。
    """
    out = []
    for (cat, nm), meta in styles.items():
        p = (meta.get("prompt") or "").strip()
        if not p:
            continue
        body = p.split("Subject:")[0].strip().rstrip(".").strip()
        if len(body) >= min_len:
            out.append((body.lower(), cat, nm))
    # 长的优先，避免短指纹抢先命中
    out.sort(key=lambda x: -len(x[0]))
    return out


def styles_by_prompt_text(text, prompt_index, limit=3):
    """在提示词正文里找风格词条原文，返回 [(catalog, name), ...]。"""
    if not text or not prompt_index:
        return []
    low = text.lower()
    out = []
    for body, cat, nm in prompt_index:
        if body in low:
            if (cat, nm) not in out:
                out.append((cat, nm))
            if len(out) >= limit:
                break
    return out


def png_positive_text(path):
    """取 PNG 执行图里最长的 CLIPTextEncode 正面文本（用于文本兜底匹配）。

    解析执行图那段 JSON 也不便宜（几千到几万字符），所以连结果一起按文件戳缓存。
    """
    key = _fkey(path, "pos")
    if key:
        got = _cache()[0].get(key, _MISS)
        if got is not _MISS:
            return got
    val = _png_positive_text(path)
    if key:
        _cput(0, key, val)
    return val


def _png_positive_text(path):
    raw = png_prompt_text(path)
    if not raw:
        return ""
    try:
        g = json.loads(raw)
    except Exception:
        return ""
    if not isinstance(g, dict):
        return ""
    best = ""
    for node in g.values():
        if not isinstance(node, dict):
            continue
        if "CLIPTextEncode" not in str(node.get("class_type") or ""):
            continue
        t = (node.get("inputs") or {}).get("text")
        if isinstance(t, str) and len(t) > len(best):
            best = t
    return best


def resolve_png_styles(path, styles, by_name, prompt_index=None):
    """按「路径 + 文件戳 + 风格库指纹」缓存，再交给 _resolve_png_styles 算。

    归因结果同时取决于那个 PNG 和当前风格库：图变了、或词条/prompt 变了，
    结果都可能变。两者都进 key，所以不需要谁记得清缓存。
    """
    key = _fkey(path, "pngstyle")
    if key:
        ck = "%s|%s" % (key, _STYLE_SIG)
        got = _cache()[0].get(ck, _MISS)
        if got is not _MISS:
            return list(got[0]), list(got[1]), list(got[2]), dict(got[3])
    libs, names, techs, cn = _resolve_png_styles(path, styles, by_name, prompt_index)
    if key:
        _cput(0, "%s|%s" % (key, _STYLE_SIG), [libs, names, techs, cn])
    return libs, names, techs, cn


def _resolve_png_styles(path, styles, by_name, prompt_index=None):
    """把 PNG 里的 (catalog, name) 组合解析成最终入库的 libs / styles / techs。

    - PNG 有 catalog：直接采信（现场字段最准），再用风格库补 name_cn / prompt
    - 只有 name：跨库按名反查；命中唯一库就采信，多半命中则取首个并保留
    - 两者都拿不到元信息：仍把名字入 styles（筛选仍可用），库留空
    - 都没命中（没挂 selector 的工作流）：拿提示词正文比对风格库 prompt 原文
    返回 (libs, style_names, techs, name_cn_map)。
    """
    libs, names, techs, cn = [], [], [], {}
    pairs = styles_from_png(path)
    if not pairs and prompt_index:
        pairs = styles_by_prompt_text(png_positive_text(path), prompt_index)
    for catalog, name in pairs:
        if not name:
            if catalog and catalog not in libs:
                libs.append(catalog)
            continue
        cats = by_name.get(name) or []
        lib = catalog
        meta = {}
        if catalog and (catalog, name) in styles:
            meta = styles[(catalog, name)]
        elif len(cats) == 1:
            lib = lib or cats[0][0]
            meta = cats[0][1]
        elif cats:
            lib = lib or cats[0][0]
        if not lib and cats:
            lib = cats[0][0]
        if name not in names:
            names.append(name)
        if lib and lib not in libs:
            libs.append(lib)
        if meta.get("name_cn"):
            cn[name] = meta["name_cn"]
        for t in tech_of(meta.get("prompt") or ""):
            if t not in techs:
                techs.append(t)
    return libs, names, techs, cn


# ---------------- 风格归因修复：从「轮次 manifest」反推风格组合 ----------------
# 用户做过多轮（R6~R19）服饰/场景迭代，每轮会留一个 manifest.json 记录这批图的
# 完整设计参数。其中 `styles` 是**标准的结构化风格组合**：
#     [{"catalog": "krea2_anime_动漫_styles", "name": "Yoshitaka Amano Style"}, ...]
# 与图鉴内部的 libs / styles 字段完全同构，命中即等于精确还原。
# 另有 `style_combo`（如 `水彩绘画 × 大气透视场景 × 半写实角色插画`）、
# `brush`（笔触说明）、`style_id`（如 F10+S7）等人类可读的组合描述。
_MANIFEST_GLOBS = ("**/*manifest*.json",)


def load_manifests(roots):
    """扫描所有轮次 manifest，返回 {文件名小写: 条目} 与 {id: 条目}。

    条目里可能有 styles（结构化组合）或 style_combo（文字组合），两者都留。
    """
    by_file, by_id = {}, {}
    for R in roots:
        if not os.path.isdir(R):
            continue
        paths = []
        for g in _MANIFEST_GLOBS:
            paths.extend(glob.glob(os.path.join(R, g), recursive=True))
        for fp in sorted(set(paths)):
            if _skip_dir(os.path.basename(os.path.dirname(fp))):
                continue
            try:
                data = json.load(open(fp, encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict):
                data = data.get("items") or data.get("data") or []
            if not isinstance(data, list):
                continue
            for it in data:
                if not isinstance(it, dict):
                    continue
                # 必须有风格线索才收，避免把无关 manifest 拖进来
                if not (it.get("styles") or it.get("style_combo")):
                    continue
                for key in ("file", "final_file", "base_file"):
                    v = it.get(key)
                    if isinstance(v, str) and v.strip():
                        by_file[os.path.basename(v.replace("\\", "/")).lower()] = it
                v = it.get("id")
                if isinstance(v, str) and v.strip():
                    by_id.setdefault(v.strip().lower(), it)
    return by_file, by_id


def styles_from_manifest_entry(entry):
    """把 manifest 条目里的风格线索归一成 [(catalog, name), ...]（可能为空）。

    - styles[] 优先，结构最准
    - 没有就用 style_combo 按 `×` 拆成多个「组合项」，catalog 留空，
      靠 build 阶段按名反查补库；查不到也仍然作为词条入档（可筛选）
    """
    out = []
    for s in (entry.get("styles") or []):
        if isinstance(s, dict):
            cat = str(s.get("catalog") or s.get("library") or "").strip()
            nm = str(s.get("name") or "").strip()
            if nm:
                out.append((cat, nm))
        elif isinstance(s, str) and s.strip():
            out.append(("", s.strip()))
    if out:
        return out
    combo = str(entry.get("style_combo") or "").strip()
    if combo:
        for part in re.split(r"\s*[×xX]\s*", combo):
            part = part.strip()
            if part:
                out.append(("", part))
    return out


def resolve_manifest_styles(entry, styles, by_name):
    """把 manifest 条目的风格组合解析成入库字段（与 resolve_png_styles 同形）。"""
    libs, names, techs, cn = [], [], [], {}
    for catalog, name in styles_from_manifest_entry(entry):
        cats = by_name.get(name) or []
        lib = catalog
        meta = {}
        if catalog and (catalog, name) in styles:
            meta = styles[(catalog, name)]
        elif len(cats) == 1:
            lib = lib or cats[0][0]
            meta = cats[0][1]
        elif cats:
            lib = lib or cats[0][0]
        if not lib and cats:
            lib = cats[0][0]
        if name not in names:
            names.append(name)
        if lib and lib not in libs:
            libs.append(lib)
        if meta.get("name_cn"):
            cn[name] = meta["name_cn"]
        for t in tech_of(meta.get("prompt") or ""):
            if t not in techs:
                techs.append(t)
    # brush 描述里也藏技法线索，顺手补 techs
    for t in tech_of(str(entry.get("brush") or "")):
        if t not in techs:
            techs.append(t)
    return libs, names, techs, cn


def resolve_image(it, idx):
    """把记录解析到磁盘上的真实文件 —— 返回**全部内容一致的副本**（列表）。

    一张图常在磁盘上留有多份拷贝（批次目录被整体搬家／复制），例如
    `001-提示词_001--手游+数字_00001_.png` 同存于 `c/D/7155c6f7/`、`D/7155c6f7/`，
    它们是同一张图的副本。旧实现只挑一个，其余副本被丢进「未命名」——
    这是「未联系」比例虚高的主因。

    但 **同名 ≠ 同图**：批次 A 与批次 B 都可能有 `001-提示词_001_00001_.png`。
    所以用 `(文件名, 字节数)` 内容指纹做白名单，并且**批次优先**：
      1) 只认领「路径含本批次号 /{rid}/」且通过指纹的副本（最准）
      2) 没有本批次副本时，才认领其它通过指纹的副本（搬家后旧目录没了的情况）
      3) 指纹全不命中 → 保底取「路径含本批次号」的那一个
    """
    cp = (it.get("copied_to") or "").replace("\\", "/")
    bn = os.path.basename(cp)
    keys, _names, dnp = _manifest()

    # 无 copied_to 的记录（report 中断、未落盘路径）→ 靠 title 反查文件名。
    # ComfyBatch 落盘命名会把标题拼进文件名：`NNN-<title>_<序号>_.png`。
    if not bn:
        t = (it.get("title") or "").strip()
        if not t:
            return []
        return sorted(_title_index().get(t, []))

    old_par = os.path.basename(os.path.dirname(cp))

    cand = []
    if cp and os.path.exists(cp):
        cand.append(cp)
    for p in idx.get(bn, []):
        if p not in cand:
            cand.append(p)
    if not cand:
        return []

    # 通过内容指纹的候选
    hits = [p for p in cand if (bn, _size(p)) in keys]

    # 指纹不足时补上「旧父目录名 + 文件名」线索（目录被整体搬家的情况）
    if (old_par, bn) in dnp:
        par_hits = [p for p in cand if f"/{old_par}/" in p and p not in hits]
        hits.extend(par_hits)

    if not hits:
        # 全不命中 → 保底：本批次目录下的那一个
        rid = (it.get("_run") or "")[:8]
        if rid:
            same_run = [p for p in cand if f"/{rid}/" in p]
            if same_run:
                return sorted(same_run)[0:1]
        return [cand[0]] if os.path.exists(cand[0]) else []

    # 批次优先：本批次的副本优先认领
    rid = (it.get("_run") or "")[:8]
    if rid:
        own = [p for p in hits if f"/{rid}/" in p]
        if own:
            return sorted(own)
        return sorted(hits)

    return sorted(hits)


_RUN_SEG = re.compile(r"/[0-9a-f]{8}/")


_IDX_ALL = None
_TITLE_IDX = None


def idx_all():
    """扁平的全部索引路径（供 title 反查用）。"""
    global _IDX_ALL
    if _IDX_ALL is None:
        _IDX_ALL = [p for v in build_index(DEFAULT_ROOTS).values() for p in v]
    return _IDX_ALL


def _title_index():
    """{标题 -> [路径]}，从磁盘文件名 `NNN-<标题>_<序号>_.png` 反向建索引。

    用于补救 `copied_to` 缺失的 report：它们的 title 仍在，可与文件名对上。
    """
    global _TITLE_IDX
    if _TITLE_IDX is not None:
        return _TITLE_IDX
    out = collections.defaultdict(list)
    for p in idx_all():
        m = re.match(r"^\d{3}-(.+?)_\d+_\.[a-z]+$", os.path.basename(p), re.I)
        if m:
            out[m.group(1)].append(p)
    _TITLE_IDX = out
    return out


def _has_run_seg(p: str) -> bool:
    return bool(_RUN_SEG.search(p))


def _glob_reports(roots):
    """找出所有批次报告（包括藏在各处目录里的）。"""
    out = []
    for R in roots:
        if not os.path.isdir(R):
            continue
        out.extend(glob.glob(os.path.join(R, "**", "comfybatch-v2-*-report.json"),
                             recursive=True))
    return sorted(set(out))


def _norm_report_result(r, rid):
    """把 report.json 的一条 result 规范化成与 reviews item 同形的记录。"""
    g = r.get("generation") or {}
    styles = []
    for s in (g.get("styles") or []):
        nm = str(s.get("name") or "").strip()
        if not nm:
            continue
        styles.append({
            # report 里字段叫 library；reviews 里叫 catalog —— 统一成 catalog
            "catalog": str(s.get("catalog") or s.get("library") or "").strip(),
            "name": nm,
            "name_cn": str(s.get("name_cn") or "").strip(),
            "display_name": str(s.get("display_name") or "").strip(),
            "prompt": str(s.get("prompt") or ""),
        })
    # 保留条件：有风格词条 / 有落盘路径 / **有标题**（标题可用于反查文件名）
    if not styles and not (r.get("copied_to") or "") and not (r.get("title") or ""):
        return None
    return {
        "index": r.get("index"),
        "title": r.get("title") or "",
        "preset_name": r.get("preset_name") or "",
        "copied_to": r.get("copied_to") or "",
        "produced_at": Management.produced_at(r),
        "style_fields": r.get("style_fields") or g.get("style_fields") or [],
        "image": r.get("image") or {},
        "generation": {
            "model": g.get("model") or "",
            "styles": styles,
            "loras": [{"name": l.get("name", ""), "strength": l.get("strength")}
                      for l in (g.get("loras") or []) if l.get("name")],
        },
        "_run": (rid or "")[:8],
        "_src": "report",
    }


# ---------------------------------------------------------------- 图片头校验
# 纯标准库读文件头拿宽高，**不解码**。这里绝不能用 PIL：
# 启动器用的解释器（.workbuddy/…/3.13.12）没装 Pillow，一旦引入服务端
# rebuild 会直接崩在 import 上。

_IMG_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8", b"GIF87a", b"GIF89a", b"BM")


def image_dimensions(path):
    """读文件头返回 (宽, 高)；读不出来（含文件不在）返回 None，不抛异常。

    按「路径 + 文件戳」缓存 —— 图没被重新生成，尺寸就不会变。这一项在
    7315 张的库上要开 7000 多个文件头，实测 3.8 秒。

    为什么吞掉 OSError：扫盘过程中文件被删、被别的程序独占是常态
    （用户一边整理图一边看），那不是异常情况，是「这张读不出尺寸」。
    """
    key = _fkey(path, "dim")
    if key:
        got = _cache()[0].get(key, _MISS)
        if got is not _MISS:
            return (got[0], got[1]) if got else None
    try:
        val = _image_dimensions(path)
    except OSError:
        return None
    if key:
        _cput(0, key, [val[0], val[1]] if val else None)
    return val


def _image_dimensions(path):
    """（无缓存的原实现）读文件头返回 (宽, 高)；认不出来返回 None。"""
    with open(path, "rb") as f:
        head = f.read(32)
        if len(head) < 10:
            return None

        # PNG：8 字节签名 + 4 字节长度 + "IHDR" + 宽度 + 高度（大端）
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            if head[12:16] != b"IHDR":
                return None
            f.seek(16)
            b = f.read(8)
            if len(b) < 8:
                return None
            return (int.from_bytes(b[0:4], "big"), int.from_bytes(b[4:8], "big"))

        # GIF：宽高是小端，各两字节
        if head.startswith((b"GIF87a", b"GIF89a")):
            return (int.from_bytes(head[6:8], "little"),
                    int.from_bytes(head[8:10], "little"))

        # BMP：宽高在 18/22 字节处，小端
        if head.startswith(b"BM"):
            f.seek(18)
            b = f.read(8)
            if len(b) < 8:
                return None
            return (int.from_bytes(b[0:4], "little"),
                    abs(int.from_bytes(b[4:8], "little", signed=True)))

        # WebP：RIFF....WEBP，再往下分 VP8 / VP8L / VP8X 三种头
        if head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
            f.seek(12)
            b = f.read(30)
            if len(b) < 14:
                return None
            tag = b[0:4]
            if tag == b"VP8X":                      # 扩展：宽高各 3 字节，减一
                return (int.from_bytes(b[8:11], "little") + 1,
                        int.from_bytes(b[11:14], "little") + 1)
            if tag == b"VP8 ":                      # 有损：帧头里 14 位宽高
                i = b.find(b"\x9d\x01\x2a")
                if i < 0:
                    return None
                return (int.from_bytes(b[i + 3:i + 5], "little") & 0x3FFF,
                        int.from_bytes(b[i + 5:i + 7], "little") & 0x3FFF)
            if tag == b"VP8L":                      # 无损：位打包
                if len(b) < 25 or b[9] != 0x2F:
                    return None
                bits = int.from_bytes(b[10:14], "little")
                return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
            return None

        # JPEG：从 FF D8 开始逐段扫，遇到 SOF 段取宽高
        if head.startswith(b"\xff\xd8"):
            f.seek(2)
            while True:
                m = f.read(1)
                while m and m != b"\xff":           # 对齐到下一个标记
                    m = f.read(1)
                if not m:
                    return None
                mk = f.read(1)
                while mk == b"\xff":
                    mk = f.read(1)
                if not mk:
                    return None
                k = mk[0]
                if k in (0xD8, 0x01) or 0xD0 <= k <= 0xD7:
                    continue                        # 无载荷的标记
                ln = f.read(2)
                if len(ln) < 2:
                    return None
                seg = int.from_bytes(ln, "big")
                # SOF0-3 / SOF5-7 / SOF9-11 / SOF13-15 带帧头；SOF4/8/12 是别的表
                if k in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                         0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    b = f.read(5)
                    if len(b) < 5:
                        return None
                    return (int.from_bytes(b[3:5], "big"),
                            int.from_bytes(b[1:3], "big"))
                if seg < 2:
                    return None
                f.seek(seg - 2, 1)                  # 跳过本段载荷继续找
        return None


def is_sane_image(path, _cache=None):
    """路径存在、且文件头能读出正数宽高，才算一张真图。

    为什么需要：图鉴里混进了集成测试留下的假文件 ——
    `fake-comfy/…/abcd1234/001-探针1_00001_.png` 其实是 100 字节的 "xxxx…"，
    另一个是 PNG 签名后面接 90 万个 0 字节。它们路径存在、也有记录引用，
    但永远渲染成裂图 —— 这正是「有些图片显示不出来」里最刺眼的那一类。
    缩略图生成器同时报了 8 个这类失败，两边互相印证。
    """
    if _cache is not None and path in _cache:
        return _cache[path]
    ok = False
    try:
        if os.path.isfile(path):
            wh = image_dimensions(path)
            ok = bool(wh) and wh[0] > 0 and wh[1] > 0
    except Exception:
        ok = False
    if _cache is not None:
        _cache[path] = ok
    return ok


def build(reviews_dir=DEFAULT_REVIEWS, styles_dir=DEFAULT_STYLES, roots=None, verbose=True, image_roots=None):
    roots = roots or DEFAULT_ROOTS
    global _MANIFEST, _IDX_ALL, _TITLE_IDX, _STYLE_SIG
    _MANIFEST = None          # 每次构建都重新生成内容指纹
    _IDX_ALL = None
    _TITLE_IDX = None
    def log(*a):
        if verbose:
            print(*a, flush=True)

    log("索引图片 ...")
    idx = build_index(roots)
    log(f"  {sum(len(v) for v in idx.values())} 张")
    log("建立内容指纹（文件名+体积）...")
    _mk, _mn, _md = _manifest(reviews_dir)
    log(f"  {len(_mk)} 个指纹 / {len(_mn)} 个唯一文件名 / {len(_md)} 个目录名对")

    log("读取风格库 ...")
    styles = load_styles(styles_dir)
    log(f"  {len(styles)} 个词条")
    # 风格库指纹要在任何 PNG 归因之前定下来 —— 它是那部分缓存的 key 之一。
    _STYLE_SIG = style_signature(styles)
    _by_name = build_name_index(styles)
    _prompt_idx = build_prompt_index(styles)
    _png_cache = {}          # path -> (libs, names, techs, cn)  避免同一图重复读
    _name_cn_cache = {}      # 词条名 -> 中文名（PNG 反查补出来的）
    log("读取轮次 manifest（结构化风格组合）...")
    _mf_by_file, _mf_by_id = load_manifests(roots)
    log(f"  {len(_mf_by_file)} 个文件名 / {len(_mf_by_id)} 个 id 带风格组合")

    log("读取生成记录 ...")
    recs = []
    for fp in glob.glob(os.path.join(reviews_dir, "*.json")):
        if fp.endswith(".tmp"):
            continue
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        rid = d.get("run_id") or ""
        for k, it in (d.get("items") or {}).items():
            it["_run"] = rid
            it["_src"] = "review"
            recs.append(it)

    # ---- 补充数据源：批次报告 report.json ----
    # 很多早期批次的审核记录已不在 reviews 里，但 report.json 保留在图片旁边。
    # 不读它们会漏掉成百上千张「本应被联系上」的图。
    seen_bn = set(os.path.basename((it.get("copied_to") or "").replace("\\", "/"))
                  for it in recs if it.get("copied_to"))
    rep_extra = 0
    for fp in _glob_reports(roots):
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        rid = (d.get("run_id") or os.path.basename(fp).split("comfybatch-v2-")[-1]
               .split("-report")[0]) or ""
        for r in (d.get("results") or []):
            st = (r.get("status") or "").lower()
            cp = (r.get("copied_to") or "").replace("\\", "/")
            title = (r.get("title") or "").strip()
            # 状态过滤：只保留「已完成」的。
            # 例外：虽然状态是 error/submitted，但**标题能在磁盘上找到对应文件**时
            # 也要保留 —— ComfyBatch 有先落盘后判错的情况，图其实是有效的。
            keep = st in ("", "completed", "success")
            if not keep and title:
                if _title_index().get(title):
                    keep = True
            if not keep:
                continue
            bn = os.path.basename(cp)
            if bn and bn in seen_bn:
                continue                      # reviews 已有，避免重复
            it = _norm_report_result(r, rid)
            if it:
                if bn:
                    seen_bn.add(bn)
                recs.append(it)
                rep_extra += 1
    log(f"  报告补充 {rep_extra} 条")

    images = []          # 每张图一条
    resolved = unresolved = 0
    png_recovered = 0    # 靠 PNG 内嵌元数据补回风格组合的张数
    manifest_recovered = 0   # 靠轮次 manifest 补回风格组合的张数
    claimed = {}         # path -> 已认领它的记录 uid（一条路径只归一条记录）
    for it in recs:
        paths = resolve_image(it, idx)
        if not paths:
            unresolved += 1
            continue
        resolved += 1
        g = it.get("generation") or {}
        preset = (it.get("preset_name") or "").strip()

        libs, style_names, techs = [], [], []
        for s in g.get("styles") or []:
            cat = str(s.get("catalog") or "").strip()
            nm = str(s.get("name") or "").strip()
            if not nm:
                continue
            libs.append(cat)
            style_names.append(nm)
            meta = styles.get((cat, nm)) or {}
            pr = meta.get("prompt") or s.get("prompt") or ""
            for t in tech_of(pr):
                if t not in techs:
                    techs.append(t)

        # 单风格批次的历史写法：styles[] 为空，词条写在顶层
        # style_library / style_name。不补这条会漏掉这些图的风格归属。
        if not style_names:
            _sl = str(g.get("style_library") or "").strip()
            _sn = str(g.get("style_name") or "").strip()
            # style_name 可能是 "A — B" 这种复合写法，按破折号拆开各算一个词条
            _names = [x.strip() for x in re.split(r"\s+[—–-]\s+", _sn) if x.strip()] if _sn else []
            for nm in _names:
                style_names.append(nm)
                if _sl:
                    libs.append(_sl)
                meta = styles.get((_sl, nm)) or {}
                for t in tech_of(meta.get("prompt") or ""):
                    if t not in techs:
                        techs.append(t)
            if _names:
                log(f"    顶层风格字段补录 {it.get('_run','')[:8]}#{it.get('index')}: {_names}")

        # ---- 修复一：轮次 manifest 里的结构化风格组合（最准，优先） ----
        if not style_names:
            _mf = None
            for _p in paths:
                _mf = _mf_by_file.get(os.path.basename(_p).lower())
                if _mf:
                    break
            if not _mf:
                _mf = _mf_by_id.get(str(it.get("title") or "").strip().lower())
            if _mf:
                _l, _n, _t, _cn = resolve_manifest_styles(_mf, styles, _by_name)
                for nm in _n:
                    if nm not in style_names:
                        style_names.append(nm)
                for lb in _l:
                    if lb not in libs:
                        libs.append(lb)
                for t in _t:
                    if t not in techs:
                        techs.append(t)
                _name_cn_cache.update(_cn)
                if _n:
                    manifest_recovered += 1

        # ---- 修复主线：记录里风格丢了，就从 PNG 内嵌执行图现场取回 ----
        # 记录残缺（styles=[] / 只有库没有词条）时，PNG 里的 `easy stylesSelector`
        # 节点才是真实发生过的事。这是「风格定义组合丧失」的主要修法。
        if not style_names:
            _png_hit = None
            for _p in paths:
                if _p in _png_cache:
                    _png_hit = _png_cache[_p]
                else:
                    _png_hit = resolve_png_styles(_p, styles, _by_name, _prompt_idx)
                    _png_cache[_p] = _png_hit
                if _png_hit[1]:
                    break
            if _png_hit and _png_hit[1]:
                _l, _n, _t, _cn = _png_hit
                for nm in _n:
                    if nm not in style_names:
                        style_names.append(nm)
                for lb in _l:
                    if lb not in libs:
                        libs.append(lb)
                for t in _t:
                    if t not in techs:
                        techs.append(t)
                _name_cn_cache.update(_cn)
                png_recovered += 1

        # ---- 修复二：风格组合认不出时，至少把技法维度从现场提示词抽出来 ----
        # 记录里没有风格词条，PNG 也没有 selector 节点时，正文本往往还是写着
        # 「全彩漫画 / cel shading / 墨线」这类工艺描述。丢了风格归属不该
        # 连技法维度也一起丢 —— 那会让统计页的技法分布严重偏空。
        if not techs:
            for _p in paths:
                _t = tech_of(png_positive_text(_p))
                if _t:
                    for t in _t:
                        if t not in techs:
                            techs.append(t)
                    break

        themes = [t for t, rx in _THEME_RE.items() if rx.search(preset)]

        _base = {
            "produced_at": Management.produced_at(it),
            "style_fields": Management.fields(it, style_names),
            "run": (it.get("_run") or "")[:8],
            "index": it.get("index"),
            "title": it.get("title") or "",
            "preset": preset or "(未命名)",
            "model": g.get("model") or "",
            "libs": sorted(set(libs)),
            "styles": sorted(set(style_names)),
            "techs": sorted(set(techs)),
            "themes": sorted(set(themes)),
            "loras": [{"n": l.get("name", ""), "s": l.get("strength")}
                      for l in (g.get("loras") or []) if l.get("name")],
            "src": it.get("_src") or "review",     # 数据来源：review / report
        }
        # 一条记录可能对应多个磁盘副本（目录被搬家/复制过）→ 每个副本都是一条
        # 但同一路径只归最先认领它的记录，避免同名跨批次记录互相抢文件。
        for p in paths:
            if p in claimed:
                continue
            claimed[p] = True
            im = dict(_base)
            im["path"] = p
            im["name"] = os.path.basename(p)
            im["size"] = os.path.getsize(p) if os.path.exists(p) else 0
            im["folder"] = folder_of(p)          # 文件夹分类（按磁盘目录）
            images.append(im)

    # ---- 兜底：未被记录覆盖的图片 ----
    # 分两类处理：
    #   ① 文件名是标准 ComfyBatch 命名 `NNN-提示词_NNN--<方案名>_<序号>_.png`
    #      → 命名本身就带方案名，**可从文件名反推方案/主题**（记录残缺时的补救）
    #   ② 其余（迭代稿 / 实验批 / 素材拼版）→ 统一归「未命名」
    seen_paths = set(im["path"] for im in images)
    orphan = named_recover = 0
    png_orphan = 0
    manifest_orphan = 0

    def _png_style_cols(p):
        """给无记录图片取风格组合：manifest 优先，退到 PNG 内嵌元数据。

        即便风格库/词条都认不出来，也仍从提示词正文抽**技法维度** ——
        正文里写着的「水彩 / 赛璐璐 / 墨线」是真实存在的工艺信息，
        不该因为认不出具体风格词条就整条丢掉。
        返回值第二项是来源标记（manifest / png / ''），供计数用。
        """
        bn = os.path.basename(p).lower()
        mf = _mf_by_file.get(bn)
        if mf:
            _l, _n, _t, _cn = resolve_manifest_styles(mf, styles, _by_name)
            if _n:
                _name_cn_cache.update(_cn)
                return _l, _n, _t, "manifest"
        hit = _png_cache.get(p)
        if hit is None:
            hit = resolve_png_styles(p, styles, _by_name, _prompt_idx)
            _png_cache[p] = hit
        _l, _n, _t, _cn = hit
        _name_cn_cache.update(_cn)
        if not _t:
            # 认不出风格组合，至少从正文补技法维度
            _t = tech_of(png_positive_text(p))
        return _l, _n, _t, ("png" if _n else "")

    source_report = None
    if image_roots is None:
        orphan_paths = _iter_orphan_images(ORPHAN_ROOTS, seen_paths)
    else:
        import atlas_sources
        orphan_paths, source_report = atlas_sources.scan(
            image_roots, seen_paths, _skip_dir, IMG_EXT, lambda p: is_sane_image(p, {}))
    for p in orphan_paths:
        bn = os.path.basename(p)
        m = _NAMED_RE.match(bn)
        if m:
            preset = m.group(2).strip()
            themes = [t for t, rx in _THEME_RE.items() if rx.search(preset)]
            _l, _s, _t, _how = _png_style_cols(p)    # 命名之外，再尽力补风格
            if _s:
                png_orphan += 1
                if _how == "manifest":
                    manifest_orphan += 1
            images.append({
                "path": p, "name": bn, "run": _run_of_path(p), "index": None,
                "title": m.group(1), "preset": preset, "model": "",
                "libs": _l, "styles": _s, "techs": _t,
                "themes": sorted(set(themes)) or ["未命名"],
                "loras": [], "size": os.path.getsize(p) if os.path.exists(p) else 0,
                "src": "named",           # 来源：文件名反推
                "style_fields": Management.fields(_mf_by_file.get(bn.lower()) or
                    {'generation': {'styles': [{'catalog': c, 'name': n} for c, n in styles_from_png(p)]}}, _s),
                "folder": folder_of(p),
            })
            named_recover += 1
            continue
        _l, _s, _t, _how = _png_style_cols(p)
        if _s:
            png_orphan += 1
            if _how == "manifest":
                manifest_orphan += 1
        images.append({
            "path": p, "name": bn, "run": _run_of_path(p), "index": None,
            "title": "", "preset": UNNAMED, "model": "",
            "libs": _l, "styles": _s, "techs": _t,
            "themes": ["未命名"] if not _s else [],
            "loras": [], "size": os.path.getsize(p) if os.path.exists(p) else 0,
            # src：manifest = 轮次清单还原 · png = 内嵌元数据救回 · orphan = 什么都没认出来
            "src": ("manifest" if _how == "manifest" else
                    ("png" if _how == "png" else "orphan")),
            "style_fields": Management.fields(_mf_by_file.get(bn.lower()) or
                {'generation': {'styles': [{'catalog': c, 'name': n} for c, n in styles_from_png(p)]}}, _s),
            "folder": folder_of(p),
        })
        orphan += 1
    log(f"  文件名反推方案 {named_recover} 张 · 未命名兜底 {orphan} 张"
        f"（其中 {png_orphan} 张补回风格组合）")
    log(f"  归因修复：轮次清单补 {manifest_recovered + manifest_orphan} 张 · "
        f"PNG 元数据补 {png_recovered + (png_orphan - manifest_orphan)} 张")

    # ---- 剔除不是真图的条目 ----
    # 只读文件头判定，不解码。被剔掉的是集成测试留下的假 PNG（见 is_sane_image）。
    # 不剔的话它们在页面上永远是裂图，而用户看到的解释只能是「图挂了」。
    _cache = {}
    bad = [im["path"] for im in images if not is_sane_image(im["path"], _cache)]
    if bad:
        images = [im for im in images if is_sane_image(im["path"], _cache)]
        log(f"  剔除 {len(bad)} 条非图片文件（测试残留/占位文件），"
            f"如 {os.path.basename(bad[0])}")

    # 标记键：路径可能因同名跨批次重复，用 (path#run#index) 保证全局唯一
    for im in images:
        im["uid"] = f"{im['path']}#{im['run']}#{im['index']}"

    log(f"  记录 {len(recs)} 条，可解析 {resolved}，未解析 {unresolved}")

    # ---- 汇总统计 ----
    def bump(counter, vals):
        for v in set(vals):
            counter[v] += 1

    c_preset, c_theme, c_lib, c_style, c_tech, c_lora, c_model, c_run = (collections.Counter() for _ in range(8))
    c_folder = collections.Counter()
    # 风格库 → 其下实际启用的风格词条（两级的「子选项」）
    lib_children: dict[str, set] = {}
    for im in images:
        c_preset[im["preset"]] += 1
        c_run[im["run"]] += 1
        if im.get("folder"):
            c_folder[im["folder"]] += 1
        if im["model"]:
            c_model[im["model"]] += 1
        bump(c_theme, im["themes"])
        bump(c_lib, im["libs"])
        bump(c_style, im["styles"])
        bump(c_tech, im["techs"])
        bump(c_lora, [l["n"] for l in im["loras"]])
        for lb in im["libs"]:
            lib_children.setdefault(lb, set()).update(im["styles"])

    style_meta = {}
    for im in images:
        for s in im["styles"]:
            k = s
            if k not in style_meta:
                style_meta[k] = {"lib": "", "name_cn": "", "prompt": ""}
    # 回填词条元信息
    for (cat, nm), meta in styles.items():
        if nm in style_meta:
            style_meta[nm]["lib"] = lib_of(cat)
            style_meta[nm]["name_cn"] = meta.get("name_cn") or ""
            style_meta[nm]["prompt"] = meta.get("prompt") or ""
    # 记录里带的中文名兜底
    for fp in glob.glob(os.path.join(reviews_dir, "*.json")):
        if fp.endswith(".tmp"):
            continue
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        for k, it in (d.get("items") or {}).items():
            for s in (it.get("generation") or {}).get("styles") or []:
                nm = str(s.get("name") or "").strip()
                if nm in style_meta and not style_meta[nm]["name_cn"]:
                    style_meta[nm]["name_cn"] = (s.get("name_cn") or s.get("display_name") or "").strip()

    # PNG 反查出来的中文名（记录里完全没有的词条靠它补）
    for nm, cn in _name_cn_cache.items():
        if nm in style_meta and cn and not style_meta[nm]["name_cn"]:
            style_meta[nm]["name_cn"] = cn

    # 共现：词条对、主题对
    def pairs_counter(key):
        c = collections.Counter()
        for im in images:
            vs = sorted(set(im[key]))
            for i in range(len(vs)):
                for j in range(i + 1, len(vs)):
                    c[(vs[i], vs[j])] += 1
        return c

    # ---- 内容指纹（稳定版本号）----
    # 需求：「版本要能对应上数据，不然每次版本都对不上」。
    # 原先只有 generated_at（每次重建都变的时钟串），页面又可能是静态快照，
    # 打开后无从判断「我看的这版到底对应哪份数据」。
    # 这里算一个**由内容决定**的短指纹：同样的图片集合 → 同样的指纹；
    # 图片增删改 → 指纹必变。前端把它显示在页头，用户一眼可核对。
    # 用 sha1 取前 10 位十六进制，够短且碰撞可忽略。
    # 注意：对 uid 排序后再喂，保证「同样的图集 → 同样的指纹」不依赖 glob 顺序。
    _fp = hashlib.sha1()
    _fp.update(str(len(images)).encode())
    # 派生规则也进指纹：folder 的算法换了，同一个图集在页面上就是另一份视图，
    # build_id 必须跟着变 —— 否则前端轮询不到「数据变了」，会一直显示旧的分类。
    _fp.update(("folder_rule=%d" % FOLDER_RULE).encode())
    for _u, _sz, _mt in sorted(
            (str(im.get("uid") or im.get("path") or ""),
             int(im.get("size") or 0), float(im.get("mtime") or 0))
            for im in images):
        _fp.update(b"\x00")
        _fp.update(_u.encode("utf-8", "replace"))
        _fp.update(b"|")
        _fp.update(str(_sz).encode())
        _fp.update(b"|")
        _fp.update(str(_mt).encode())
    build_id = _fp.hexdigest()[:10]
    n_images = len(images)
    # 人类可读的一句话版本：好念、好核对
    build_label = "图鉴 %s · %d 张" % (build_id, n_images)

    data = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "build_id": build_id,          # 稳定内容指纹（10 位十六进制）
        "build_label": build_label,    # 给人看的版本说明
        "images": images,
        "source_scan": source_report,
        "style_meta": style_meta,
        # 派生规则版本：服务端预载 _data_cache.json 时校验它，版本不符就丢弃重算
        # （否则升级后最初那几秒端上来的是按旧规则分的文件夹）。
        "folder_rule": FOLDER_RULE,
        "facets": {
            "folder": c_folder.most_common(),
            "library": c_lib.most_common(),
            "style": c_style.most_common(),
            "tech": c_tech.most_common(),
            "lora": c_lora.most_common(),
            "model": c_model.most_common(),
            "run": c_run.most_common(),
            "theme": c_theme.most_common(),
            "preset": c_preset.most_common(),
        },
        # 风格库 → 其下启用的风格词条（供「风格库 → 子选项」两级筛选）
        "lib_children": {k: sorted(v) for k, v in lib_children.items()},
        "pairs": {
            "style": [[list(k), v] for k, v in pairs_counter("styles").most_common(60)],
            "theme": [[list(k), v] for k, v in pairs_counter("themes").most_common(40)],
            "library": [[list(k), v] for k, v in pairs_counter("libs").most_common(40)],
        },
        "stats": {
            "records": len(recs), "resolved": resolved, "unresolved": unresolved,
            "distinct_styles": len(c_style), "distinct_libs": len(c_lib),
            "distinct_techs": len(c_tech), "distinct_themes": len(c_theme),
            "distinct_presets": len(c_preset), "distinct_loras": len(c_lora),
            "distinct_folders": len(c_folder),
            "catalog_available": len(styles),
            "multi_lib_images": sum(1 for im in images if len(im["libs"]) > 1),
            "multi_style_images": sum(1 for im in images if len(im["styles"]) > 1),
            # 风格归因修复情况 —— 供前端显示「已修复」进度
            "png_recovered": png_recovered,        # 靠 PNG 元数据救回（有记录的图）
            "png_orphan_recovered": png_orphan - manifest_orphan,   # PNG 救回（无记录的图）
            "manifest_recovered": manifest_recovered + manifest_orphan,  # 靠轮次 manifest 救回
            "manifest_orphan_recovered": manifest_orphan,
            "styled_images": sum(1 for im in images if im["styles"] or im["libs"]),
            "unstyled_images": sum(1 for im in images if not im["styles"] and not im["libs"]),
            "recovered_images": sum(1 for im in images if im.get("src") in ("png", "manifest")),
        },
    }
    cache_flush()      # 这一轮攒下的派生结果，收尾一次落盘
    return data


if __name__ == "__main__":
    d = build()
    st = d["stats"]
    print("\n=== 汇总 ===")
    for k, v in st.items():
        print(f"  {k}: {v}")
    print(f"\n图片 {len(d['images'])} 张，标签维度：")
    for f, rows in d["facets"].items():
        print(f"  {f:8s} {len(rows)} 个取值")
