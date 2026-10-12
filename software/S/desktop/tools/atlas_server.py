# -*- coding: utf-8 -*-
"""风格图鉴 · 本地服务版（推荐）

一条命令启动，浏览器打开即可用：
    python atlas_server.py
    python atlas_server.py --port 8793

相比静态 HTML 的优势：
  * 图片走本地 HTTP 服务，浏览器不会拦（file:// 图片在现代浏览器常被拦）
  * 「在文件夹中打开」按钮可用
  * 数据由后端提供，页面秒开

功能：多维度标签 + 叠加交集精确定位 + 统计视图 + 看图与打开文件夹
"""
from __future__ import annotations
import argparse, json, os, random, re, shutil, sys, threading, time, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_SRC = os.path.join(HERE, '..', 'src')
if os.path.isdir(_SRC):
    sys.path.insert(0, _SRC)
import comfybatch_lifecycle as UI
import style_atlas as A
import build_atlas as B
import atlas_sources as Sources
import atlas_management as Management
from atlas_resources import ResourceStore, Conflict, merge_marks, merge_grades

# 代码目录（HERE，放只读模板）与数据目录（DATA_DIR，放标记/评级/渲染产物）
# 是两回事。分界线的理由见 style_atlas.resolve_data_dir：打包后 __file__ 落在
# 解包临时目录，写进去的东西退出即消失。此处只做引用，不改口径 —— 单一出处。
DATA_DIR = A.DATA_DIR


def ensure_data_dir() -> str:
    """数据目录必须在任何一次写入之前存在（frozen 首次启动时它还不存在）。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    return DATA_DIR


def _resource(name: str) -> str:
    """只读模板文件的位置。

    源码运行 → 图鉴自己的目录；打包运行 → 解包临时目录（spec 的 datas 把
    compare_live.html 放了进去）。都没有时返回源码目录下的同名路径，
    调用方照旧拿到一个不存在的路径，各自给出自己的 500/404 文案。
    """
    for base in (HERE, getattr(sys, "_MEIPASS", "")):
        if base and os.path.isfile(os.path.join(base, name)):
            return os.path.join(base, name)
    return os.path.join(HERE, name)

# 缩略图缓存的位置取自 build_thumbs.py —— 单一出处，避免这里和生成器各写一份路径。
# 服务平时只端这批文件；/thumb?p= 未命中时会现场补生成（下方 make_thumb，
# 参数与 build_thumbs 完全一致）。旧注释说「解释器没有 Pillow」已过时：
# 当前解释器自带 Pillow 12.x，按需生成可用，失败仍有 404 软兜底。
try:
    import build_thumbs as _T
    THUMB_DIR = _T.DEFAULT_DIR
except Exception:
    THUMB_DIR = r"C:\Users\wyj\Desktop\_style_atlas_thumbs"
_HEX16 = re.compile(r"^[0-9a-f]{16}$")

_THUMB_W = 420
_THUMB_Q = 78

# 缩略图现场补建的并发闸门（见 /thumb 路由）。放在模块级是因为
# ThreadingHTTPServer 每个请求一个线程，闸门必须是跨线程共享的。
_thumb_sem = threading.Semaphore(3)


def thumb_hash_of(path: str) -> str:
    """路径 → 缩略图文件名。与 build_thumbs.thumb_hash 逐字节同源：
    sha1(str(path).strip().encode('utf-8')).hexdigest()[:16]。
    入参约定是「正斜杠规范形」—— 图鉴数据里的 path 就是这个形态，
    预生成缓存全部按它命名。调用前先用 normpath+replace 归一
    （见 /thumb 路由），反斜杠输入也能归到同一命名空间，不会裂出第二份缓存。"""
    import hashlib
    return hashlib.sha1(str(path).strip().encode("utf-8")).hexdigest()[:16]


def make_thumb(src: str, dst: str) -> bool:
    """现场生成一张缩略图（与 build_thumbs._work 同款参数：420px LANCZOS
    webp q78，RGBA 白底合成，tmp 原子替换）。失败返回 False —— 路由层
    回 404，前端 imgErr 三级兜底会退回原图，不会留死链。"""
    tmp = None
    try:
        from PIL import Image
        if os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
            return True
        im = Image.open(src)
        im.thumbnail((_THUMB_W, 10 ** 6), Image.LANCZOS)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        # tmp 带线程标识：并发请求同一张图时互不覆盖半成品
        tmp = "%s.%d.tmp" % (dst, threading.get_ident())
        im.save(tmp, "WEBP", quality=_THUMB_Q, method=4)
        os.replace(tmp, dst)
        return True
    except Exception:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        return False


def uid_path(u: str) -> str:
    """从标记标识里取出真实文件路径。

    标记存的是全局唯一 uid：`<绝对路径>#<批次>#<序号>`。
    直接拿它去 os.path.isfile() 必然为 False —— 后缀 `#run#index` 不是文件的一部分。
    必须先把这两段剥掉。

    实测：图鉴现有 6757 张图的路径**都不含** `#`，所以 rsplit 与 split 等价；
    这里用 rsplit('#', 2) 更防御 —— 万一将来文件名里出现 `#`，
    也只会切掉最后两段（正好是 run/index），不会把路径本体切坏。
    """
    if not isinstance(u, str):
        return ""
    return u.rsplit("#", 2)[0] if "#" in u else u


def uid_alive(u: str) -> bool:
    return os.path.isfile(uid_path(u))


def _same_path(a, b) -> bool:
    """判断两个路径是不是同一个文件：大小写、正反斜杠的差异都不算差异。

    这不是洁癖：图鉴数据里 path 一律是正斜杠形，而用户传进来的、从 Windows
    弹窗复制出来的往往是反斜杠；`_marks.json` 里两种形态历史上都出现过。
    直接字符串比较会让「同一个文件」认不出来，表现就是改名后收藏对不上。
    """
    if not a or not b:
        return False
    try:
        return os.path.normcase(os.path.normpath(str(a))) == \
               os.path.normcase(os.path.normpath(str(b)))
    except Exception:
        return False


# 新文件名里不允许出现的字符。
#   \\ / :   —— 路径分隔与盘符，带了就能借「改名」把文件搬出扫描根；
#   * ? " < > | —— Windows 文件名本身就不接受；
#   ' &      —— 页面里有大量 `onclick="f('${path}')"` 的内联写法（历史包袱），
#               引号会直接把那段处理程序的语法拆掉，`&` 会被当实体开头。
# 与其在别处补一堆转义，不如在这里一次说清楚：这几种字符不许进文件名。
_BAD_NAME_CHARS = set('\\/:*?"<>|&\'')


def _new_set_id() -> str:
    """生成标记夹 id。用时间戳+随机后缀，短且不冲突。"""
    return "s%d%04d" % (int(time.time() * 1000) % 10**9, random.randint(0, 9999))


# ---------------- 数据构建 + 自动刷新 ----------------
# 为什么需要这个：重建索引（build）要 8–10 秒、扫描上万张图。
# 若只在启动时跑一次，ComfyUI 新出的图就必须重启服务才看得到。
# 这里把它做成「后台线程定期重建 + 版本号递增」，前端轮询版本号自动刷新。
_state = {
    "data": None,
    "version": 0,          # 每次成功重建 +1，前端拿它判断要不要刷新
    "built_at": 0.0,
    "building": False,
    "last_ms": 0,
    "error": "",
    "bootstrap_done": False,   # 首次启动的同步构建是否完成
}
_state_lock = threading.Lock()
_stop = threading.Event()

# ---------- 单实例归一（focus / alive）----------
# 需求：重复启动图鉴时不开第二个窗口，已有页面自己置前。
#   _focus_seq  每次 POST /api/focus +1；页面心跳时发现 seq 变了就自聚焦
#   _alive_ts   最近一次页面心跳（POST /api/alive）的时间戳；
#               入口 open 前查它判断「图鉴窗口是否还开着」，
#               活着 → 只发 focus 通知、不开新标签（进程仍唯一，天然单实例）
_focus_seq = 0
_alive_ts = 0.0
ALIVE_WINDOW = 12.0   # 心跳间隔 5s ×2 + 余量；超过视为窗口已关

# ---------- 对比页选择会话（图鉴 → 对比 的交接） ----------
# 图鉴页把「当前筛选命中的图片」POST 成一个选择会话（id + 标签 + 图片明细），
# 对比页（/compare?sel=<id>）按 id 取回渲染。只存最近 N 条，进程内字典即可 ——
# 会话是短命的展示上下文，落盘反而制造垃圾状态。
_sel: dict = {}
_sel_seq = 0
_SEL_KEEP = 30
_sel_lock = threading.Lock()

# 标记文件的写锁。标记有两类写入方：图鉴页（整体覆盖 sets）和对比页
# （/api/marks/apply 局部读-改-写）。两边都是「读 → 改 → 写回」，
# 不加锁时并发交叉会丢标记：A 读、B 读、A 写、B 写 → A 的改动被盖掉。
# ThreadingHTTPServer 是真并发的，这个窗口确实存在。
_marks_lock = threading.Lock()


def system_grades(store):
    raw = store.load('_grades_system.json', {'grades': B.load_sys_grades()})
    mapped = {uid_path(store.resolve(k)).replace('\\', '/').casefold(): v
              for k, v in raw.get('grades', {}).items()}
    if mapped != raw.get('grades', {}):
        store.save('_grades_system.json', {'grades': mapped}, expected=raw['revision'])
    return mapped


def build_payload(reviews, styles_dir, max_thumb):
    """构建一份完整数据（含 lib_avail / techs / 缩略图映射 / 批次时间）。

    派生逻辑全在 build_atlas.enrich() 里 —— 原先此处抄了一份，
    与命令行版本各自演化；模板加占位符时服务端那段替换没跟上，
    前端就吃到未替换的字面量。现在两边共用同一个函数。
    """
    sources = Sources.load(DATA_DIR)
    data = B.enrich(A.build(reviews, styles_dir, verbose=False,
                           image_roots=sources['roots']), styles_dir,
                    verbose=False)
    data['image_sources'] = sources
    store = ResourceStore(DATA_DIR)
    store.index(data['images'])
    Management.decorate(data, store)
    data['resource_aliases'] = store.aliases()
    data['system_grades'] = system_grades(store)
    # Resolve renamed/moved references without dropping unlocated records.
    with _marks_lock:
        marks = store.load('_marks.json', {'sets': [{'id': 'default', 'name': '默认', 'paths': [], 'note': {}, 'color': 0}], 'active': 'default'})
        resolved = store.canonical_marks(marks)
        if resolved != marks:
            store.save('_marks.json', resolved, expected=marks['revision'])
        grades = store.load('_grades.json', {'grades': {}})
        resolved_g = {store.resolve(k): v for k, v in grades.get('grades', {}).items()}
        if resolved_g != grades.get('grades', {}):
            store.save('_grades.json', {'grades': resolved_g}, expected=grades['revision'])
    return data


# 桌面静态副本：用户会直接双击桌面那份 `风格图鉴.html`（走 file://）。
# 问题是它只在有人手动复制时才更新，久了就和数据脱节 —— 「版本对不上」的
# 一半原因在此。这里在每次重建后顺带同步一份，保证桌面那份的 build_id
# 永远等于服务端当前数据。设 ATLAS_NO_DESKTOP=1 可关闭。
DESKTOP_COPY = os.path.join(os.path.expanduser("~"), "Desktop", "风格图鉴.html")


def _sync_desktop_copy(src: str) -> None:
    if os.environ.get("ATLAS_NO_DESKTOP") == "1":
        return
    try:
        if not os.path.isfile(src):
            return
        tmp = DESKTOP_COPY + ".tmp"
        shutil.copyfile(src, tmp)
        os.replace(tmp, DESKTOP_COPY)
        print(f"[桌面副本] 已同步 → {DESKTOP_COPY}", flush=True)
    except Exception as e:
        # 同步失败不该拖垮重建 —— 服务端/hosted 版仍是最新，只是桌面那份旧了。
        print(f"[桌面副本] 同步失败（不影响服务）: {e}", flush=True)


def sync_desktop_copy_now() -> dict:
    """轻量同步：不重建索引，只把当前已渲染的 HTML 覆盖到桌面副本。

    给启动器「每次进入图鉴」用 —— 需求原话「每一次进入风格图鉴后出现，
    替换原文件」。重建一次要 20+ 秒，而副本只是文件的复制，
    <1 秒即可，所以单独开这个口子，避免每次进入都卡 20 秒。
    返回 {ok, bytes, mtime, skipped}。
    """
    src = os.path.join(DATA_DIR, "风格图鉴.html")
    if os.environ.get("ATLAS_NO_DESKTOP") == "1":
        return {"ok": False, "skipped": "ATLAS_NO_DESKTOP=1"}
    if not os.path.isfile(src):
        return {"ok": False, "error": "源文件不存在（还没构建过）"}
    _sync_desktop_copy(src)
    ok = os.path.isfile(DESKTOP_COPY)
    return {"ok": ok,
            "bytes": os.path.getsize(DESKTOP_COPY) if ok else 0,
            "mtime": os.path.getmtime(DESKTOP_COPY) if ok else 0,
            "path": DESKTOP_COPY}


# 后台建索引期间访问 / 时端出的等待页。用自动刷新，而不是让用户读一句
# 「请先运行 build_atlas.py」—— 内嵌进 S 之后用户什么都没做错，
# 这一步是他点开模块就该自动完成的。三秒一跳，建好即进。
_BOOTSTRAP_PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>风格图鉴 · 正在建立索引</title>
<style>
html,body{height:100%;margin:0}
body{font:14px/1.7 system-ui,"Microsoft YaHei",sans-serif;color:#1f2328;background:#f6f8fa;
     display:flex;align-items:center;justify-content:center}
.box{text-align:center;padding:28px 34px}
.sp{width:26px;height:26px;margin:0 auto 14px;border:3px solid #d0d7de;border-top-color:#0969da;
    border-radius:50%;animation:r 1s linear infinite}
@keyframes r{to{transform:rotate(360deg)}}
.t{font-size:16px;font-weight:600;margin-bottom:6px}
.s{color:#656d76;font-size:13px}
</style>
<div class="box"><div class="sp"></div>
<div class="t">风格图鉴正在建立索引</div>
<div class="s">首次启动需要扫描图片目录，约 30 秒；完成后本页自动进入。</div>
</div>
<script>setTimeout(function(){location.reload()},3000)</script>
</html>"""


def _data_cache_path():
    return os.path.join(DATA_DIR, "_data_cache.json")


def _save_data_cache(data):
    """把这一轮的结果落盘，供下次启动秒开。

    为什么值得多存 6.7 MB 的 JSON：没有它，S 每启动一次就要重算一遍索引，
    用户在头二十秒里点开图鉴，看到的是「正在建立索引」。有了它，服务一上线
    就有数据可端，后台再慢慢核实 —— 那二十秒用户就不用付了。
    """
    try:
        ensure_data_dir()
        p = _data_cache_path()
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception as e:
        print(f"[缓存] 数据落盘失败（不影响服务）: {e}", flush=True)


def _write_page(data, max_thumb, page):
    """把数据渲染成页面并原子落盘（+ 同步桌面副本）。

    渲染只在这一个地方发生 —— 预载、重建、首页兜底三条路径共用。
    分散成三份的话，早晚有一份忘了带 RENDER_RULE，于是那份产物永远「过期」。
    """
    html = B.render_html(data, max_thumb)
    tmp = page + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp, page)
    _sync_desktop_copy(page)
    return html


def _preload(max_thumb, log):
    """把上一次的结果先端上来，别让人对着「正在建立索引」干等。

    返回是否载入成功。载入后 /api/data 与首页立即可用；后台的 rebuild 照常
    跑一遍做核实 —— 数据真变了，前端轮询到新 build_id 会自己刷过去。
    """
    with _state_lock:
        if _state["data"] is not None:
            return False
    try:
        with open(_data_cache_path(), encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    if not isinstance(data, dict) or not data.get("images"):
        return False
    # 派生规则（folder / bigfolder 的算法）升级过：旧缓存里的分类名是按旧规则
    # 算的，端上来会让用户在升级后最初那几秒看到「找不到那个文件夹」——
    # 正是这次要修的症状。版本不符就当没有缓存，老实重算。
    if data.get("folder_rule") != getattr(A, "FOLDER_RULE", None):
        log("[图鉴] 上次索引的分类规则已过时，改为重新构建")
        return False
    if data.get('image_sources') != Sources.load(DATA_DIR):
        log('[图鉴] 作品目录配置已变化，重新扫描')
        return False

    page = os.path.join(DATA_DIR, "风格图鉴.html")
    ResourceStore(DATA_DIR).attach_content_hashes(data['images'])
    if data.get('management_rule') != Management.RULE:
        # Old cached day values originated from mtime; never display them as production dates.
        for im in data['images']:
            im['produced_at'] = Management.produced_at(im)
        data['run_time'] = {}
    Management.decorate(data, ResourceStore(DATA_DIR))
    # 「页面在不在」不够 —— 换了 exe 之后，磁盘上那份旧模板渲染的页面依然在，
    # 只看存在性就会把它端给用户，升级看着像没生效。所以还要核版本戳。
    if os.path.isfile(page) and B.page_render_rule(page) != getattr(B, "RENDER_RULE", None):
        log("[图鉴] 磁盘上的页面是旧模板渲染的，重新渲染")
    if not os.path.isfile(page) or B.page_render_rule(page) != getattr(B, "RENDER_RULE", None) or data.get('management_rule'):
        # 数据在、页面不在或过期（换了数据目录、升级过来）：现场渲染一份。
        # render_html 实测 0.08 秒，比让首页走「等待页」再刷新划算得多。
        try:
            _write_page(data, max_thumb, page)
        except Exception as e:
            log(f"[图鉴] 预载渲染页面失败（数据仍可用）: {e}")

    with _state_lock:
        _state["data"] = data
        _state["built_at"] = time.time()
        _state["error"] = ""
    log(f"[图鉴] 已载入上次索引（{len(data['images'])} 张 · "
        f"{data.get('build_label') or data.get('build_id') or '?'}），后台再核实一遍")
    return True


def rebuild(reviews, styles_dir, max_thumb, why=""):
    """重建数据并落盘静态 HTML。返回 (成功?, 新版本号, 耗时秒)。"""
    with _state_lock:
        if _state["building"]:
            return False, _state["version"], 0.0
        _state["building"] = True
    t0 = time.time()
    try:
        ensure_data_dir()
        data = build_payload(reviews, styles_dir, max_thumb)
        with _state_lock:
            # Organization may have changed while the expensive scan ran outside the lock.
            if data.get('management_rule'):
                Management.decorate(data, ResourceStore(DATA_DIR))
            _state['data'] = data
            _write_page(data, max_thumb, os.path.join(DATA_DIR, "风格图鉴.html"))
    except Exception as e:
        with _state_lock:
            _state["error"] = str(e)
            _state["building"] = False
        print(f"[rebuild] 失败: {e}", flush=True)
        return False, _state["version"], time.time() - t0
    finally:
        # 成功路径里 build() 自己会 flush 一次（dirty 已清，这里空跑）；
        # 失败路径靠这一句 —— 已经算出来的那部分派生结果不该白费，
        # 下一次就能少算一些。扫描半路被中断是最常见的情形。
        A.cache_flush()

    ms = int((time.time() - t0) * 1000)
    with _state_lock:
        _state["version"] += 1
        _state["built_at"] = time.time()
        _state["last_ms"] = ms
        _state["building"] = False
        _state["error"] = ""
        ver = _state["version"]
        n = len(data["images"])
    _save_data_cache(data)     # 给下一次启动留一份现成的
    tag = f" ({why})" if why else ""
    print(f"[rebuild]{tag} v{ver} · {n} 张 · {ms/1000:.1f}s", flush=True)
    return True, ver, ms / 1000.0


def _watch(stop, reviews, styles_dir, max_thumb, interval):
    """后台线程：定期看图片目录有没有新文件，有就重建。

    用「目录 mtime + 数量」做轻量哨兵，避免每次都全量重建。
    （全量重建要 8–10 秒，出图过程中频繁重建会互相拖慢。）
    """
    def snapshot():
        """返回所有搜索根的 (目录数, 最新 mtime) 摘要。"""
        n = 0
        newest = 0.0
        for R in dict.fromkeys(A.DEFAULT_ROOTS + Sources.load(DATA_DIR)['roots']):
            if not os.path.isdir(R):
                continue
            for root, dirs, files in os.walk(R):
                dirs[:] = [d for d in dirs if not A._skip_dir(d)]
                n += len(files)
                try:
                    m = os.path.getmtime(root)
                    if m > newest:
                        newest = m
                except OSError:
                    pass
        return n, newest

    last = snapshot()
    while not stop.wait(interval):
        try:
            cur = snapshot()
            # 文件数变了，或某个目录 mtime 变新 → 可能出了新图
            if cur[0] != last[0] or cur[1] > last[1] + 1:
                print(f"[watch] 检测到变化 {last} → {cur}，重建中…", flush=True)
                rebuild(reviews, styles_dir, max_thumb, why="自动")
                last = snapshot()
        except Exception as e:
            print(f"[watch] 出错: {e}", flush=True)


def _bootstrap(reviews, styles, max_thumb, log, preload=False):
    """首次建索引。成功与否都不该让服务起不来 —— 数据是「有更好」，
    服务可用才是「必须有」：没有数据时 /api/version 也能应答，
    前端据此知道该等还是该报错。

    preload=True 时先把上次的结果端上来再重建：用户点开图鉴的瞬间就有图看，
    而不是先读一句「正在建立索引」等二十秒。
    """
    if preload:
        try:
            _preload(max_thumb, log)
        except Exception as e:
            log(f"[图鉴] 预载失败（不影响重建）: {e}")
    log("[图鉴] 构建索引 …")
    ok, ver, secs = rebuild(reviews, styles, max_thumb, why="启动")
    if not ok:
        log("[图鉴] 本次构建失败，服务仍会运行，可稍后调用 /api/rebuild")


def _bootstrap_async(reviews, styles, max_thumb, log, preload=False):
    try:
        _bootstrap(reviews, styles, max_thumb, log, preload=preload)
    finally:
        with _state_lock:
            _state["bootstrap_done"] = True


def serve(host="127.0.0.1", port=8793, reviews=None, styles=None, max_thumb=48,
          watch_interval=20.0, bootstrap="sync", log=print):
    """把图鉴服务跑起来，返回 (server, http_thread)。

    与 main() 的分工：main 是「命令行前台，一直开到 Ctrl+C」，
    serve 是「把服务交出去」。S 内嵌用后者 —— 它需要在**自己进程里**
    跑图鉴，还要能拿到句柄判断状态；fork 一个 Python 子进程做不到
    （exe 里没有解释器可指，且会多一份解包目录）。

    bootstrap="sync" 时先建完索引再返回（命令行：起来就有数据）；
    "background" 时立刻返回，索引在后台线程里建 —— 宿主启动不能被
    30 秒的扫盘挡住，而 /api/version 的 building 字段足以让前端知道该等。
    """
    reviews = reviews or A.DEFAULT_REVIEWS
    styles = styles or A.DEFAULT_STYLES
    ensure_data_dir()

    # 先占端口，再谈建索引。反过来的话，8793 被别的程序占着时，
    # 这边会先老老实实扫完整盘（二十多秒）才轮到 bind 报错 ——
    # 白烧一次全量扫描，换来的还是一句「起不来」。
    Handler.out_dir = DATA_DIR
    Handler.args = {"reviews": reviews, "styles": styles, "max_thumb": max_thumb}
    # 请求日志走宿主日志（console=False 的 exe 里 print 是黑洞，宿主会落文件）。
    Handler.log = staticmethod(log)
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True

    if bootstrap == "sync":
        _bootstrap(reviews, styles, max_thumb, log)
        with _state_lock:
            _state["bootstrap_done"] = True
    else:
        threading.Thread(target=_bootstrap_async,
                         args=(reviews, styles, max_thumb, log, True),
                         name="atlas-bootstrap", daemon=True).start()

    if watch_interval and watch_interval > 0:
        threading.Thread(
            target=_watch,
            args=(_stop, reviews, styles, max_thumb, watch_interval),
            name="atlas-watch", daemon=True).start()

    http = threading.Thread(target=srv.serve_forever, name="atlas-http", daemon=True)
    http.start()
    return srv, http


def main():
    ap = argparse.ArgumentParser(description="风格图鉴 · 本地服务版")
    ap.add_argument("--port", type=int, default=8793)
    ap.add_argument("--reviews", default=A.DEFAULT_REVIEWS)
    ap.add_argument("--styles", default=A.DEFAULT_STYLES)
    ap.add_argument("--max-thumb", type=int, default=48)   # 每页张数
    ap.add_argument("--watch-interval", type=float, default=20.0,
                    help="后台扫描新图的间隔（秒）；设 0 关闭自动刷新")
    a = ap.parse_args()

    srv, _http = serve(port=a.port, reviews=a.reviews, styles=a.styles,
                       max_thumb=a.max_thumb, watch_interval=a.watch_interval,
                       bootstrap="sync")

    st = (_state["data"] or {}).get("stats") or {}
    n = len((_state["data"] or {}).get("images") or [])
    print(f"\n  风格图鉴已启动 → http://127.0.0.1:{a.port}/")
    print(f"  数据目录：{DATA_DIR}")
    print(f"  {n} 张图 · {st.get('distinct_styles', 0)} 个风格词条 · "
          f"{st.get('distinct_themes', 0)} 个主题 · {st.get('distinct_techs', 0)} 个技法维度")
    if a.watch_interval > 0:
        print(f"  自动刷新：每 {a.watch_interval:g} 秒检查一次新图")
    print("  Ctrl+C 停止\n")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        _stop.set()
        srv.shutdown()


class Handler(BaseHTTPRequestHandler):
    data = None
    out_dir = HERE
    args = None          # 运行时参数（reviews/styles/max_thumb），供 /api/rebuild 用
    # serve() 会把它换成真正的 logger。用 staticmethod 绑定，否则赋值过来的
    # 普通函数会变成实例方法，调用时凭空多收一个 self。
    log = staticmethod(lambda *a: None)

    # 缩略图/原图每页几十个，全记下来只会把日志淹掉；报活每 5 秒一次同理。
    QUIET = ("/thumb", "/img", "/api/alive")

    def log_message(self, *a):
        pass

    def _note(self, extra=""):
        """把「有意义的请求」记进宿主日志。

        加这一条的起因是没有可观测性：S 前端的跨端口探测被 CORS 预检拦掉时，
        exe 无控制台、图鉴也不记请求，界面上只剩一句「正在连接图鉴服务…」，
        从外面完全看不出浏览器到底发了什么、发到没有。请求日志让下一次
        同类问题一眼可判。
        """
        try:
            path = self.path.split("?")[0]
            if path.startswith(self.QUIET):
                return
            origin = self.headers.get("Origin") or "-"
            self.log(f"[图鉴] {self.command} {path}{extra} · origin={origin}")
        except Exception:
            pass

    def do_OPTIONS(self):  # noqa: N802 - BaseHTTPRequestHandler 的命名约定
        """CORS 预检。

        踩过的坑：S 前端探测 8793 时写了 fetch(...,{cache:'no-store'})，
        浏览器会因此补上 Cache-Control / Pragma —— 这两个不在 CORS 安全头
        列表里，于是先发一个 OPTIONS 预检。本类原先没有 do_OPTIONS，
        BaseHTTPRequestHandler 只回 501 且不带任何 Access-Control-* 头，
        预检失败 → fetch reject → S 的图鉴门控永远停在「正在连接图鉴服务…」，
        用户看到的是「图鉴长时间加载」。前端已改成不带 no-store
        （服务端本来就发 Cache-Control: no-store，那个选项是多余的），
        这里再把预检补齐，任何跨端口调用都不会栽在这一步。
        """
        self._note("（CORS 预检）")
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, Cache-Control, Pragma")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------- 标记持久化 ----------
    @property
    def marks_file(self):
        return os.path.join(self.out_dir, "_marks.json")

    @property
    def grades_file(self):
        # 用户手动评级（三级：1=绿相似 2=黄半动画 0=红不相似）。
        # 与系统评级（构建时注入、只读）分开存，避免互相覆盖。
        return os.path.join(self.out_dir, "_grades.json")

    @property
    def resource_store(self):
        return ResourceStore(self.out_dir)

    def _load_marks(self):
        """读标记。返回统一的 v2 结构，旧格式自动迁移。

        v1: {"paths": [...], "note": {}}                  —— 单套标记
        v2: {"sets": [{id,name,paths,note}], "active": id} —— 多套具名标记夹
        读到 v1 就即时包成「默认」夹，避免用户已有标记丢失。
        """
        try:
            raw = self.resource_store.load('_marks.json', {'sets': [{'id': 'default', 'name': '默认', 'paths': [], 'note': {}, 'color': 0}], 'active': 'default'})
        except (OSError, ValueError) as e:
            raise RuntimeError('收藏数据读取失败，原数据已保留') from e
        if not isinstance(raw, dict):
            raw = {}
        if isinstance(raw.get("sets"), list) and raw["sets"]:
            sets = []
            for s in raw["sets"]:
                if not isinstance(s, dict):
                    continue
                item = {
                    "id": str(s.get("id") or _new_set_id()),
                    "name": str(s.get("name") or "未命名夹"),
                    "paths": [p for p in (s.get("paths") or []) if isinstance(p, str)],
                    "note": s.get("note") if isinstance(s.get("note"), dict) else {},
                }
                # color 是前端分配的固定色号，必须原样带回，否则重启后串色
                if isinstance(s.get("color"), int):
                    item["color"] = s["color"]
                sets.append(item)
            if not sets:
                sets = [{"id": _new_set_id(), "name": "默认", "paths": [], "note": {}}]
            active = str(raw.get("active") or sets[0]["id"])
            if active not in [s["id"] for s in sets]:
                active = sets[0]["id"]
            return {"sets": sets, "active": active, "revision": raw.get("revision", 0)}
        # v1 → v2 迁移
        return {"sets": [{
            "id": _new_set_id(),
            "name": "默认",
            "paths": [p for p in (raw.get("paths") or []) if isinstance(p, str)],
            "note": raw.get("note") if isinstance(raw.get("note"), dict) else {},
            "color": 0,
        }], "active": None}

    def _save_marks(self, obj):
        obj["schema_version"] = 2
        obj["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return self.resource_store.save('_marks.json', self.resource_store.canonical_marks(obj))

    # ---------- 用户评级持久化 ----------
    def _load_grades(self):
        """读用户评级。结构 {"grades": {uid: {"g": 0|1|2, "note": str}}}。"""
        try:
            raw = self.resource_store.load('_grades.json', {'grades': {}})
        except (OSError, ValueError) as e:
            raise RuntimeError('评级数据读取失败，原数据已保留') from e
        grades = {}
        if isinstance(raw, dict) and isinstance(raw.get("grades"), dict):
            for uid, it in raw["grades"].items():
                if isinstance(it, dict) and it.get("g") in (0, 1, 2):
                    item = {"g": int(it["g"])}
                    if isinstance(it.get("note"), str) and it["note"]:
                        item["note"] = it["note"]
                    grades[str(uid)] = item
        return grades

    def _save_grades(self, grades):
        obj = {
            "schema_version": 1,
            "grades": grades,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        return self.resource_store.save('_grades.json', obj)

    # ---------- 图片重命名 ----------
    def _rename_image(self, body):
        """在**原目录内**给一张图改名，并把所有「按路径认图」的引用一起搬过去。

        为什么引用必须一起搬：收藏（_marks.json 的 paths）和「我的评级」
        （_grades.json 的键）存的是 `<绝对路径>#<批次>#<序号>`。改了名而不同步，
        它们就全部指向一个不存在的路径 —— 用户看到的现象和「收藏的图不见了」
        一模一样，只不过这次的丢失是我们自己造成的。所以改名、改引用、改索引
        必须在同一次请求里做完。
        """
        src = str(body.get("path") or "").strip()
        raw = str(body.get("name") or "").strip()
        if not src or not raw:
            return {"ok": False, "error": "缺少 path 或 name"}
        src = os.path.normpath(src)
        if not os.path.isfile(src):
            return {"ok": False, "error": "原文件不存在（可能已被移动或删除）"}
        if any(ch in _BAD_NAME_CHARS for ch in raw):
            return {"ok": False, "error": "名字里不能有 \\ / : * ? \" < > | & 或引号"}
        if raw in (".", "..") or not raw.strip("."):
            return {"ok": False, "error": "新名字不合法"}
        d, old_name = os.path.split(src)
        old_ext = os.path.splitext(old_name)[1]
        new_ext = os.path.splitext(raw)[1]
        if not new_ext:
            raw += old_ext                      # 只输了主名 → 沿用原后缀
        elif new_ext.lower() != old_ext.lower():
            return {"ok": False, "error": "后缀不能改（原来的是 %s）" % old_ext}
        if raw == old_name:
            return {"ok": False, "error": "新名字和原名相同"}
        dst = os.path.join(d, raw)
        # 只改大小写时，大小写不敏感的文件系统认为 dst 就是 src 本身，
        # 直接 os.rename 会报「目标已存在」。这种情况绕一次中转名。
        case_only = os.path.normcase(dst) == os.path.normcase(src)
        if not case_only and os.path.exists(dst):
            return {"ok": False, "error": "同目录下已经有同名文件，换个名字"}
        try:
            if case_only:
                tmp = os.path.join(d, ".__rename_tmp__" + old_ext)
                os.rename(src, tmp)
                os.rename(tmp, dst)
            else:
                os.rename(src, dst)             # 目标存在时 Windows 直接报错，不覆盖
        except OSError as e:
            return {"ok": False, "error": "改名失败：%s" % e}

        new_path = dst.replace("\\", "/")
        ent = None
        with _state_lock:
            data = _state["data"] or {}
            images = data.get("images") or []
            for im in images:
                if _same_path(im.get("path"), src):
                    ent = im
                    break
            old_uid = (ent or {}).get("uid") or src.replace("\\", "/")
            tail = old_uid[len(uid_path(old_uid)):]      # `#批次#序号` 或 ''
            new_uid = new_path + tail
            if ent is not None:
                ent["path"] = new_path
                ent["uid"] = new_uid
                ent["name"] = raw
                # 缩略图的键（th）**故意不动**。它虽然是「路径的 sha1」，
                # 但文件内容就是这张图本身；继续用旧键，缓存照样命中。
                # 换键只会白扔一次已经生成好的缩略图。
        if _state.get("data"):
            _save_data_cache(_state["data"])    # 落盘，重启后不会又变回旧路径

        self.resource_store.relocate(src, new_path, new_uid)
        nmarks, ngrades = self._remap_refs(src, new_uid)
        if _state.get('data'):
            _state['data']['resource_aliases'] = self.resource_store.aliases()
            _state['data']['system_grades'] = system_grades(self.resource_store)
            _save_data_cache(_state['data'])
        return {"ok": True, "path": new_path, "name": raw, "uid": new_uid,
                "old_path": src.replace("\\", "/"),
                "th": (ent or {}).get("th") or "",
                "marks_updated": nmarks, "grades_updated": ngrades}

    def _remap_refs(self, old_path, new_uid):
        """把收藏与评级里指向 old_path 的引用搬到 new_uid。

        收藏在 _marks_lock 里读-改-写（和 /api/marks、/api/marks/apply 同一把锁，
        并发写不会互相盖掉）；顺手对同一个夹内的重复 uid 去重 ——
        迁移旧记录时经常出现「uid 一条 + 路径一条」指向同一张图，
        不去重的话夹里的数字会比真实张数多。
        """
        nmarks = 0
        with _marks_lock:
            store = self._load_marks()
            for s in store["sets"]:
                out, seen = [], set()
                for p in (s.get("paths") or []):
                    if _same_path(uid_path(p), old_path):
                        p = new_uid
                        nmarks += 1
                    if p in seen:
                        continue
                    seen.add(p)
                    out.append(p)
                s["paths"] = out
                s['note'] = {new_uid if _same_path(uid_path(k), old_path) else k: v
                             for k, v in s.get('note', {}).items()}
            self._save_marks({"sets": store["sets"], "active": store["active"]})

        with _marks_lock:
            ngrades = 0
            grades = self._load_grades()
            if grades:
                newg = {}
                for k, v in grades.items():
                    if _same_path(uid_path(k), old_path):
                        newg[new_uid] = v
                        ngrades += 1
                    else:
                        newg[k] = v
                if ngrades:
                    self._save_grades(newg)
        return nmarks, ngrades

    def _local_origin_ok(self):
        """只接受本机页面发来的写请求。

        /api/rename 会真的动磁盘上的文件，而 _send 对所有响应都加了
        `Access-Control-Allow-Origin: *`（那是给 S 前端跨端口探测用的）。
        两条凑在一起，任何网页都能在 S 开着的时候 POST 过来改用户文件名 ——
        这是我在加这个接口时引入的新风险，所以顺手堵上：
        有 Origin 头且 Host 不是本机的，一律拒掉。
        （不依赖 Referer，它可能被裁掉；Origin 在 POST 上一定带。）
        """
        org = self.headers.get("Origin")
        if not org:
            return True                          # 非浏览器调用（curl/本机脚本）
        if org in ("null", "file://"):
            return False
        try:
            host = urllib.parse.urlparse(org).hostname or ""
        except Exception:
            return False
        return host in ("127.0.0.1", "localhost", "::1")

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # S 前端（8790）会跨端口 fetch /api/version 探测图鉴是否在线，
        # 没有 CORS 头探测会被浏览器拦掉。本机工具，放开无妨。
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        self._note()
        u = urllib.parse.urlparse(self.path)
        if UI.handle_get(self, u.path, u.query):
            return
        if u.path in ("/", "/index.html"):
            p = os.path.join(DATA_DIR, "风格图鉴.html")
            stale = (bool((_state['data'] or {}).get('management_rule')) or not os.path.isfile(p)
                     or B.page_render_rule(p) != getattr(B, "RENDER_RULE", None))
            if stale:
                # 页面不在或过期：手里有数据就现渲一份端上去（0.08 秒），
                # 没数据才退到「正在建索引」页。**绝不把旧模板渲染的页面端出去** ——
                # 升级后看到旧界面，用户会以为改动没生效。
                with _state_lock:
                    data, building = _state["data"], _state["building"]
                if data:
                    try:
                        html = _write_page(data, (self.args or {}).get("max_thumb", 48), p)
                        self.log("[图鉴] 首页命中过期页面，已现场重渲")
                    except Exception as e:
                        self.log(f"[图鉴] 首页重渲失败：{e}")
                        html = None
                    if html is not None:
                        if "embed" in urllib.parse.parse_qs(u.query):
                            html = html.replace(
                                "</head>",
                                "<style id=\"embedstyle\">"
                                "#top h1,#top .st{display:none}"
                                "#top{padding:8px 14px}"
                                "</style></head>",
                                1)
                        return self._send(200, html, "text/html; charset=utf-8")
                if building:
                    return self._send(200, _BOOTSTRAP_PAGE, "text/html; charset=utf-8")
                if not os.path.isfile(p):
                    return self._send(500, "风格图鉴.html 未生成，请先运行 build_atlas.py",
                                      "text/plain; charset=utf-8")
                # 重渲失败且磁盘上有旧页面：端旧的，总比 500 好
            with open(p, encoding="utf-8") as f:
                html = f.read()
            # 图片地址由前端 imgSrc() 自适应决定（服务模式走 /img），
            # 这里不再做字符串替换 —— 那套替换依赖模板里的字面量写法，
            # 模板一改就静默失效，是「标记图不显示」的隐患之一。
            # embed=1（S 的「风格图鉴」模块 iframe 内嵌）：隐藏大标题与构建戳，
            # 只留 图库/收藏/统计/刷新 工具条并压缩 padding，看图区域最大化。
            # 页面自身的 syncTopbar() 在 body 末尾执行，会按压缩后的实际高度
            # 重写 --topbar，两栏布局自动对齐，无需改动 build_atlas.py。
            qs = urllib.parse.parse_qs(u.query)
            if "embed" in qs:
                html = html.replace(
                    "</head>",
                    "<style id=\"embedstyle\">"
                    "#top h1,#top .st{display:none}"
                    "#top{padding:8px 14px}"
                    "</style></head>",
                    1)
            return self._send(200, html, "text/html; charset=utf-8")

        if u.path == "/api/data":
            with _state_lock:
                d = _state["data"]
            return self._send(200, json.dumps(d, ensure_ascii=False))

        if u.path == '/api/related':
            try:
                q = urllib.parse.parse_qs(u.query, keep_blank_values=True)
                with _state_lock:
                    data = _state['data']
                result = Management.related(data, q.get('folder_id', [''])[0],
                    q.get('field_key', [''])[0], q.get('field_value', [''])[0],
                    q.get('date', [None])[0], q.get('page', [1])[0], q.get('page_size', [48])[0])
                return self._send(200, json.dumps(result, ensure_ascii=False))
            except (ValueError, TypeError, KeyError) as e:
                return self._send(400, json.dumps({'error': str(e)}, ensure_ascii=False))

        # 动态对比页：图鉴里「对比查看」创建选择会话后，这里按 sel=id 渲染
        # 该批图片（大图、可导入预设）。无 sel 时展示最近会话列表。
        if u.path == "/compare":
            p = _resource("compare_live.html")
            if not os.path.isfile(p):
                return self._send(500, "compare_live.html 缺失", "text/plain; charset=utf-8")
            with open(p, encoding="utf-8") as f:
                html = f.read()
            # embed=1（S 的「风格对比」模块 iframe 内嵌）：藏掉页面自己的大标题
            # 和「← 返回图鉴」按钮 —— 模块条由 S 提供，页面只留会话信息与工具。
            qs = urllib.parse.parse_qs(u.query)
            if "embed" in qs:
                html = html.replace(
                    "</head>",
                    "<style id=\"embedstyle\">"
                    "#top h1{display:none}"
                    "#top button{display:none}"
                    "#top{padding:8px 14px}"
                    "</style></head>",
                    1)
            return self._send(200, html, "text/html; charset=utf-8")

        # 237 张风格总对比审图台（静态快照，图片 base64 内嵌，源文件只读）。
        if u.path == "/compare237":
            p = r"C:\Users\wyj\Desktop\D\风格总对比_237张\总对比_审图台.html"
            if not os.path.isfile(p):
                return self._send(404, "对比审图台源文件不存在：" + p,
                                  "text/plain; charset=utf-8")
            with open(p, encoding="utf-8") as f:
                html = f.read()
            return self._send(200, html, "text/html; charset=utf-8")

        # 选择会话：GET 带 id 取单条，不带 id 列最近记录（对比页首屏用）。
        if u.path == "/api/selection":
            qs = urllib.parse.parse_qs(u.query)
            with _sel_lock:
                if "id" in qs:
                    try:
                        sid = int(qs["id"][0])
                    except ValueError:
                        return self._send(400, json.dumps(
                            {"ok": False, "error": "bad id"}, ensure_ascii=False))
                    item = _sel.get(sid)
                    if not item:
                        return self._send(404, json.dumps(
                            {"ok": False, "error": "会话不存在或已过期"}, ensure_ascii=False))
                    return self._send(200, json.dumps(
                        {"ok": True, "selection": item}, ensure_ascii=False))
                recent = [{"id": k, "label": v["label"], "count": len(v["items"]),
                           "created": v["created"]}
                          for k, v in sorted(_sel.items(), reverse=True)]
            return self._send(200, json.dumps(
                {"ok": True, "recent": recent}, ensure_ascii=False))

        # 轻量版本号：前端轮询它，变了就重新拉 /api/data。
        # 这样 ComfyUI 新出的图能自动出现在页面上，不用重启服务。
        if u.path == '/api/sources':
            with _state_lock:
                report = (_state['data'] or {}).get('source_scan')
            return self._send(200, json.dumps({**Sources.load(DATA_DIR), 'scan': report}, ensure_ascii=False))

        if u.path == "/api/version":
            with _state_lock:
                _d = _state["data"] or {}
                v = {
                    "version": _state["version"],
                    "built_at": _state["built_at"],
                    "building": _state["building"],
                    "last_ms": _state["last_ms"],
                    "images": len(_d.get("images") or []),
                    # build_id = 内容指纹。与页面顶部显示的是同一个值，
                    # 用来核对「这个页面 / 这份缓存 到底对应哪一份数据」。
                    "build_id": _d.get("build_id") or "",
                    "build_label": _d.get("build_label") or "",
                    "generated_at": _d.get("generated_at") or "",
                    "error": _state["error"],
                }
            return self._send(200, json.dumps(v, ensure_ascii=False))

        if u.path == "/api/marks":
            m = self._load_marks()
            # 这里**不再**把「文件已不在」的记录从返回里删掉。
            # 旧实现在这一行静默过滤，制造了一个用户说不清的后果：
            # 夹标签上的数字来自前端本地副本（合并时又把删掉的并回去），
            # 数字不变，而图渲染不出来 —— 症状就是「收藏的图片不在了，
            # 数量却没变」。更糟的是被过滤掉的那几条连提示都没有，
            # 用户既不知道少了哪几张，也没有任何入口去把它们找回来。
            # 现在如实上报：记录照常返回，哪些文件不在由 dead 单独说明。
            # 判存在前必须剥掉 `#批次#序号` 后缀，否则任何标记都会被误判。
            dead = []
            for s in m["sets"]:
                for p in (s.get("paths") or []):
                    if not uid_alive(p):
                        dead.append(p)
            if not m.get("active"):
                m["active"] = m["sets"][0]["id"]
            m["dead"] = dead
            m["dropped"] = len(dead)          # 兼容旧字段名（前端已改读 dead）
            return self._send(200, json.dumps(m, ensure_ascii=False))

        # 用户手动评级（与系统评级区分：系统评级在构建时注入页面，只读）
        if u.path == "/api/grades":
            grades = self._load_grades()
            raw = self.resource_store.load('_grades.json', {'grades': {}})
            dead = [uid for uid in grades if not uid_alive(uid)]
            return self._send(200, json.dumps(
                {'grades': grades, 'revision': raw['revision'], 'dead': dead, 'dropped': len(dead)},
                ensure_ascii=False))

        # 单实例归一：入口启动前探测「图鉴窗口是否还开着」（最近有无心跳）
        if u.path == "/api/focus":
            with _state_lock:
                alive = (time.time() - _alive_ts) < ALIVE_WINDOW
                seq = _focus_seq
            return self._send(200, json.dumps(
                {"seq": seq, "alive": alive}, ensure_ascii=False))

        if u.path == "/img":
            q = urllib.parse.parse_qs(u.query)
            p = os.path.normpath((q.get("p") or [""])[0])
            if not p or not os.path.isfile(p):
                return self._send(404, "not found", "text/plain")
            ext = os.path.splitext(p)[1].lower()
            ct = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".webp": "image/webp", ".gif": "image/gif",
                  ".bmp": "image/bmp"}.get(ext, "application/octet-stream")
            try:
                with open(p, "rb") as f:
                    return self._send(200, f.read(), ct)
            except Exception as e:
                return self._send(500, str(e), "text/plain")

        # 缩略图：前端网格只取这个，原图（单张 2-12MB）只在点开大图时读。
        # 名字是 build_thumbs.py 生成的 sha1 前 16 位；虽然是自家产物，
        # 仍按白名单字符校验 —— 这个值会被直接拼进文件路径。
        # ?p=<路径> 模式：对比页等只有 path 的调用方，按路径算 hash 查缓存，
        # 未命中且原图在盘上就现场生成（make_thumb，图鉴同款参数）。
        if u.path == "/thumb":
            q = urllib.parse.parse_qs(u.query)
            h = (q.get("h") or [""])[0]
            raw = (q.get("p") or [""])[0].strip()
            src = os.path.normpath(raw) if raw else ""
            if not h and src:
                if not os.path.isfile(src):
                    return self._send(404, "no such file", "text/plain")
                # hash 输入统一成正斜杠规范形（src 已 normpath）：图鉴数据与
                # build_thumbs 预生成缓存都用这一形态，调用方传反斜杠也归同一空间。
                h = thumb_hash_of(src.replace("\\", "/"))
            if not h or not _HEX16.match(h):
                return self._send(400, "bad hash", "text/plain")
            tp = os.path.join(THUMB_DIR, "t", h + ".webp")
            if not os.path.isfile(tp) and src and os.path.isfile(src):
                # 现场补建。前端现在会带 p 过来（见 build_atlas.thumbUrl），
                # 所以「缓存缺一张」不再等于「这个格子永远空着」。
                # 一页几十张同时缺缓存时，几十个请求会一起来生成 ——
                # 用信号量把它们压到 3 个并发：Pillow 解一张 3MB 的 PNG 要
                # 几十到几百毫秒，不限制会把 CPU 打满、把其它请求也拖慢。
                with _thumb_sem:
                    if not os.path.isfile(tp):     # 等锁期间可能已经生成好了
                        make_thumb(src, tp)
            if not os.path.isfile(tp):
                return self._send(404, "no thumb", "text/plain")
            try:
                with open(tp, "rb") as f:
                    return self._send(200, f.read(), "image/webp")
            except Exception as e:
                return self._send(500, str(e), "text/plain")

        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        self._note()
        u = urllib.parse.urlparse(self.path)
        if u.path in ('/api/ui/close','/api/ui/exit'):
            n = min(int(self.headers.get('Content-Length') or 0),4096)
            try:
                body = json.loads(self.rfile.read(n) or b'{}')
            except ValueError:
                body = {}
            UI.handle_close(self, u.path, body)
            return


        if u.path == '/api/sources':
            origin = self.headers.get('Origin')
            if origin and urllib.parse.urlparse(origin).hostname not in ('localhost', '127.0.0.1', '::1'):
                return self._send(403, json.dumps({'ok': False, 'error': '只允许本地页面修改目录'}, ensure_ascii=False))
            try:
                n = int(self.headers.get('Content-Length') or 0)
                if not 0 < n <= 32768:
                    raise ValueError('目录配置大小不正确')
                value = json.loads(self.rfile.read(n))
                sources = Sources.save(DATA_DIR, value.get('roots'))
            except (ValueError, TypeError, AttributeError, OSError) as e:
                return self._send(400, json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))
            return self._send(200, json.dumps({'ok': True, **sources}, ensure_ascii=False))

        # 创建选择会话：图鉴页把当前筛选命中的图片 POST 过来，
        # 服务端按 path 回填每张图的方案与配方（对比页/导入预设直接用）。
        if u.path == "/api/selection":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._send(400, json.dumps(
                    {"ok": False, "error": "bad json"}, ensure_ascii=False))
            label = str(body.get("label") or "未命名对比")
            paths = [str(p) for p in list(body.get("paths") or [])]
            if not paths:
                return self._send(400, json.dumps(
                    {"ok": False, "error": "没有图片"}, ensure_ascii=False))
            with _state_lock:
                data = _state["data"] or {}
            by_path = {im.get("path"): im for im in (data.get("images") or [])}
            items = []
            for p in paths:
                im = by_path.get(p) or {}
                items.append({
                    "path": p,
                    # uid（path#批次#序号）必须带上：对比页靠它去 /api/marks 里
                    # 认「这张图在不在哪个标记夹里」。只给 path 的话，跨批次的
                    # 同名图会互相认错（图鉴那边就是因为这个才做了 uid）。
                    "uid": im.get("uid") or "",
                    "preset": im.get("preset") or "",
                    "styles": list(im.get("styles") or []),
                    "loras": [l if isinstance(l, dict) else {"n": l, "s": None}
                              for l in (im.get("loras") or [])],
                })
            global _sel_seq
            with _sel_lock:
                _sel_seq += 1
                sid = _sel_seq
                _sel[sid] = {"label": label, "items": items,
                             "created": time.strftime("%m-%d %H:%M")}
                while len(_sel) > _SEL_KEEP:
                    _sel.pop(min(_sel), None)
            return self._send(200, json.dumps(
                {"ok": True, "id": sid, "count": len(items), "label": label},
                ensure_ascii=False))

        # 手动触发重建（出图完成后点一下「刷新」，立刻看到新图）
        if u.path == "/api/rebuild":
            a = self.args or {}
            ok, ver, secs = rebuild(
                a.get("reviews", A.DEFAULT_REVIEWS),
                a.get("styles", A.DEFAULT_STYLES),
                a.get("max_thumb", 48), why="手动")
            return self._send(200, json.dumps(
                {"ok": ok, "version": ver, "seconds": round(secs, 1)},
                ensure_ascii=False))

        # 轻量同步桌面副本（不重建索引，<1s）。启动器「每次进入图鉴」调它。
        # 只接受 POST —— GET 会被浏览器/预取误触，白复制一遍。
        if u.path == "/api/sync_desktop":
            if self.command != "POST":
                return self._send(405, json.dumps(
                    {"ok": False, "error": "use POST"}, ensure_ascii=False))
            return self._send(200, json.dumps(
                sync_desktop_copy_now(), ensure_ascii=False))

        # 局部标记：只动「这几张图 × 这一个夹」，其余夹/其余图原样不动。
        # 为什么需要它 —— 对比页也要能标记，但不能走「整体覆盖 sets」：
        #   整体覆盖的前提是「调用方手里有一份最新的全量副本」，图鉴页满足
        #   （它就是权威），对比页不满足：它只在加载时读过一次 /api/marks，
        #   期间你在图鉴里标的图它一无所知。让它整体覆盖 = 把新标的全抹掉。
        # 所以这里按「服务端现读现改」做读-改-写，两端并发也只影响各自的图。
        if u.path == '/api/organization':
            if not self._local_origin_ok():
                self.rfile.read(int(self.headers.get('Content-Length') or 0))
                return self._send(403, json.dumps({'error': '只接受本机页面的保存请求'}))
            try:
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
                with _state_lock:
                    data = _state['data']
                    fid = Management.organize(data, self.resource_store, body)
                    Management.decorate(data, self.resource_store)
                    _state['version'] += 1
                    result = {'ok': True, 'folder_id': fid, 'data': data}
                return self._send(200, json.dumps(result, ensure_ascii=False))
            except (ValueError, TypeError, KeyError) as e:
                return self._send(400, json.dumps({'error': str(e)}, ensure_ascii=False))

        if u.path == "/api/marks/apply":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._send(400, json.dumps(
                    {"ok": False, "error": "bad json"}, ensure_ascii=False))

            def _norm(p):
                return str(p or "").replace("\\", "/").lower()

            store = self._load_marks()
            mk = _marks_lock
            with mk:
                store = self._load_marks()     # 锁内重读，避免和并发写交叉
                ops = body.get("ops") if isinstance(body.get("ops"), list) else []
                changed = 0
                for op in ops:
                    if not isinstance(op, dict):
                        continue
                    sid = str(op.get("set") or "")
                    uids = [u for u in (op.get("uids") or []) if isinstance(u, str)]
                    # drop=True：整夹删除（不做增删图，直接摘掉这个夹）。
                    # 对比页的「清空判定」用它收掉自己派生的空夹 ——
                    # 派生夹是自动产物，留着空壳只会让夹列表越堆越长。
                    # 只看 data 的对比页不受影响，图本身更不会被动。
                    #
                    # 服务端护栏：**只允许删带 @ 的派生夹**。
                    # 无后缀的 verdict-keep / verdict-reject 是升级前所有判定
                    # 共用的夹（用户的历史数据），谁都不许通过这个接口删掉它。
                    # 这条护栏不依赖前端自觉 —— 前端写错也不该能毁掉用户数据。
                    if bool(op.get("drop")):
                        if "@" not in sid:
                            continue          # 老夹不可删，静默跳过（不是错误）
                        before_n = len(store["sets"])
                        store["sets"] = [s for s in store["sets"] if s["id"] != sid]
                        if len(store["sets"]) != before_n:
                            changed += 1
                            if store.get("active") == sid:
                                store["active"] = (store["sets"][0]["id"]
                                                   if store["sets"] else "")
                        continue
                    if not uids:
                        continue
                    target = None
                    for s in store["sets"]:
                        if s["id"] == sid:
                            target = s
                            break
                    # 夹不存在（图鉴里被删了）→ 直接建一个同名新夹。
                    # 不静默丢弃：用户点了「标进红夹」就该有个红夹。
                    if target is None:
                        target = {"id": sid or _new_set_id(),
                                  "name": str(op.get("name") or "未命名夹"),
                                  "paths": [], "note": {}}
                        if isinstance(op.get("color"), int):
                            target["color"] = op["color"]
                        store["sets"].append(target)
                    if bool(op.get("remove")):
                        drop = set(uids)
                        drop_paths = {_norm(uid_path(u)) for u in uids}
                        keep = []
                        for x in target["paths"]:
                            if x in drop or _norm(uid_path(x)) in drop_paths:
                                target["note"].pop(x, None)
                                changed += 1
                            else:
                                keep.append(x)
                        target["paths"] = keep
                    else:
                        # 同一张图在目标夹里的旧记录（uid 漂移残留）先清掉再写，
                        # 与图鉴 toggleMark 同一口径，避免重复计数。
                        drop_paths = {_norm(uid_path(u)) for u in uids}
                        target["paths"] = [
                            x for x in target["paths"]
                            if x not in set(uids)
                            and _norm(uid_path(x)) not in drop_paths]
                        for u in uids:
                            target["paths"].append(u)
                            changed += 1
                act = str(body.get("active") or "")
                if act and act in [s["id"] for s in store["sets"]]:
                    store["active"] = act
                elif not store.get("active"):
                    store["active"] = store["sets"][0]["id"]
                self._save_marks({"sets": store["sets"], "active": store["active"]})
            total = sum(len(s["paths"]) for s in store["sets"])
            return self._send(200, json.dumps(
                {"ok": True, "changed": changed, "n": total,
                 "sets": len(store["sets"]), "file": self.marks_file},
                ensure_ascii=False))

        # 图片重命名。这是**唯一**会动用户磁盘文件的接口，所以额外要求
        # 请求确实来自本机页面（见 _local_origin_ok）。
        if u.path == "/api/rename":
            if not self._local_origin_ok():
                # Consume this request before closing, so Windows returns the 403
                # instead of resetting a socket with unread body bytes.
                self.rfile.read(int(self.headers.get('Content-Length') or 0))
                return self._send(403, json.dumps(
                    {"ok": False, "error": "只接受本机页面发来的改名请求"}, ensure_ascii=False))
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._send(400, json.dumps(
                    {"ok": False, "error": "bad json"}, ensure_ascii=False))
            try:
                res = self._rename_image(body)
            except Exception as e:
                res = {"ok": False, "error": "内部错误：%s" % e}
            return self._send(200 if res.get("ok") else 400,
                              json.dumps(res, ensure_ascii=False))

        if u.path in ('/api/marks/sync', '/api/grades/sync'):
            if not self._local_origin_ok():
                return self._send(403, json.dumps({'error': '只接受本机页面的保存请求'}))
            n = int(self.headers.get('Content-Length') or 0)
            try:
                body = json.loads(self.rfile.read(n) or b'{}')
                baseline, desired = body['baseline'], body['desired']
                if not isinstance(baseline, dict) or not isinstance(desired, dict):
                    raise ValueError('保存数据格式错误')
                marks = u.path == '/api/marks/sync'
                if not isinstance(desired.get('sets' if marks else 'grades'), list if marks else dict):
                    raise ValueError('保存数据格式错误')
                if marks:
                    if not desired['sets'] or any(not isinstance(x, dict) or not isinstance(x.get('id'), str)
                        or not isinstance(x.get('paths'), list) or not all(isinstance(u, str) for u in x['paths'])
                        or not isinstance(x.get('note', {}), dict) for x in desired['sets']):
                        raise ValueError('收藏夹格式错误，至少保留一个收藏夹')
                elif any(not isinstance(k, str) or not isinstance(v, dict) or v.get('g') not in (0,1,2)
                         for k, v in desired['grades'].items()):
                    raise ValueError('评级格式错误')
                store = self.resource_store
                if marks:
                    baseline, desired = store.canonical_marks(baseline), store.canonical_marks(desired)
                else:
                    for doc in (baseline, desired):
                        doc['grades'] = {store.resolve(k): v for k, v in doc.get('grades', {}).items()}
                with _marks_lock:
                    result = store.sync('_marks.json' if marks else '_grades.json', baseline, desired,
                                        merge_marks if marks else merge_grades)
                return self._send(200, json.dumps(result, ensure_ascii=False))
            except Conflict as e:
                return self._send(409, json.dumps({'error': str(e)}, ensure_ascii=False))
            except (ValueError, KeyError, TypeError) as e:
                return self._send(400, json.dumps({'error': str(e)}, ensure_ascii=False))

        if u.path in ('/api/marks', '/api/grades'):
            # Unversioned whole-document replacements are unsafe even with a lock:
            # the old browser snapshot can erase a newer page's additions.
            self.rfile.read(int(self.headers.get('Content-Length') or 0))
            return self._send(409, json.dumps(
                {'error': '页面已过期，请刷新后保存；现有数据未被覆盖'}, ensure_ascii=False))

        # ---------- 单实例归一 ----------
        # 页面心跳：每 5s 一次，顺带回读 focus seq —— 页面发现 seq 变了
        # （有别的入口请求打开图鉴）就把自己置前。比单独做两条链路省事。
        if u.path == "/api/alive":
            global _alive_ts, _focus_seq
            with _state_lock:
                _alive_ts = time.time()
                seq = _focus_seq
            return self._send(200, json.dumps({"ok": True, "seq": seq},
                                              ensure_ascii=False))

        # 入口（风格图鉴.py open）在决定「已有窗口」后调它，通知页面自聚焦
        # （global 声明是函数级的，/api/alive 分支里已声明过 _focus_seq）
        if u.path == "/api/focus":
            with _state_lock:
                _focus_seq += 1
                seq = _focus_seq
            return self._send(200, json.dumps({"ok": True, "seq": seq},
                                              ensure_ascii=False))

        # 把标记图导出（复制）到一个文件夹，便于按这些样本重做风格设定
        if u.path == "/api/export_marks":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                body = {}
            dst_dir = (body.get("dst") or "").strip()
            paths = [p for p in (body.get("paths") or []) if isinstance(p, str)]
            if not dst_dir:
                return self._send(400, json.dumps({"ok": False, "err": "未指定目录"}),
                                  "application/json")
            done = skipped = failed = 0
            errs = []
            for u in paths:
                p = uid_path(u)          # 标记是 uid，先还原成真实路径
                if not os.path.isfile(p):
                    failed += 1
                    continue
                dst = os.path.join(dst_dir, os.path.basename(p))
                if os.path.exists(dst):
                    skipped += 1
                    continue
                try:
                    os.makedirs(dst_dir, exist_ok=True)
                    shutil.copy2(p, dst)
                    if os.path.getsize(p) != os.path.getsize(dst):
                        raise IOError("体积不一致")
                    done += 1
                except Exception as e:
                    failed += 1
                    errs.append(f"{os.path.basename(p)}: {e}")
            return self._send(200, json.dumps(
                {"ok": True, "done": done, "skipped": skipped, "failed": failed,
                 "dst": dst_dir, "errs": errs[:5]}, ensure_ascii=False))

        if u.path == "/api/open":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                body = {}
            p = uid_path(body.get("path") or "")
            if p:
                target = p if os.path.isdir(p) else os.path.dirname(p)
                if os.path.isdir(target):
                    try:
                        if sys.platform == "win32":
                            os.startfile(target)                     # noqa
                        elif sys.platform == "darwin":
                            os.system(f'open "{target}"')
                        else:
                            os.system(f'xdg-open "{target}"')
                        return self._send(200, json.dumps({"ok": True}))
                    except Exception as e:
                        return self._send(500, json.dumps({"ok": False, "err": str(e)}))
            return self._send(404, json.dumps({"ok": False, "err": "路径不存在"}), "application/json")
        return self._send(404, "not found", "text/plain")


if __name__ == "__main__":
    main()
