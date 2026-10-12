# -*- coding: utf-8 -*-
"""风格图库浏览器 —— 选一个风格，直接看到用它生成的所有图片。

一条命令启动：
    python style_gallery_server.py
然后浏览器打开 http://127.0.0.1:8791/

特性：
  * 左侧风格词条列表（按使用量排序，可搜索）
  * 右侧该风格的图片缩略图墙，点开看大图
  * 每张图给「在文件夹中打开」按钮 + 完整本地路径
  * 图片走本地 HTTP，浏览器能直接显示（file:// 会被浏览器拦）
  * 只读：只读取图片，不修改任何文件

数据来源：ComfyBatch 审核记录 + 全盘图片索引（自动解析散落/归档的图片）。
"""
from __future__ import annotations
import argparse, collections, glob, json, os, re, sys, threading, time, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REVIEWS = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ComfyBatch-S", "reviews")
DEFAULT_IMG_ROOTS = [r"C:/Users/wyj/Desktop", r"D:/liu", r"E:/工作流"]
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")


# ---------------- 图片索引 ----------------
def build_index(roots, cache=os.path.join(HERE, "_img_index.json"), max_age=3600):
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
            dirs[:] = [d for d in dirs
                       if d.lower() not in ("node_modules", ".git", "$recycle.bin",
                                            "system volume information")]
            for f in files:
                if f.lower().endswith(IMG_EXT):
                    idx[f].append(os.path.join(root, f).replace("\\", "/"))
    try:
        json.dump(dict(idx), open(cache, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return idx


def load_records(reviews_dir):
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
            recs.append(it)
    return recs


def resolve_image(it, idx):
    cp = it.get("copied_to") or ""
    if not cp:
        return None
    cp = cp.replace("\\", "/")
    if os.path.exists(cp):
        return cp
    bn = os.path.basename(cp)
    cand = idx.get(bn, [])
    if not cand:
        return None
    if len(cand) == 1:
        return cand[0]
    rid = (it.get("_run") or "")[:8]
    if rid:
        hit = [p for p in cand if rid in p]
        if hit:
            # 优先 c/D/<run>/ 这种批次目录结构
            pref = [p for p in hit if f"/c/D/{rid}/" in p or f"/D/{rid}/" in p]
            return sorted(pref or hit)[0]
    par = os.path.basename(os.path.dirname(cp))
    h2 = [p for p in cand if f"/{par}/" in p]
    return sorted(h2)[0] if h2 else sorted(cand)[0]


def lib_of(c):
    if c.endswith("_styles"):
        c = c[:-len("_styles")]
    if c.startswith("krea2_"):
        c = c[len("krea2_"):]
    return c or "未知"


def build_gallery(reviews_dir, img_roots):
    print("建立图片索引 ...", flush=True)
    idx = build_index(img_roots)
    print(f"  索引 {sum(len(v) for v in idx.values())} 张图", flush=True)

    print("读取生成记录 ...", flush=True)
    recs = load_records(reviews_dir)

    # 词条 -> 图片
    by_item = collections.defaultdict(list)
    item_meta = {}
    resolved = unresolved = 0
    seen_dst = set()
    for it in recs:
        p = resolve_image(it, idx)
        if not p:
            unresolved += 1
            continue
        resolved += 1
        g = it.get("generation") or {}
        key = f"{it.get('_run','')[:8]}|{it.get('index')}"
        for s in g.get("styles") or []:
            cat, nm = str(s.get("catalog") or "").strip(), str(s.get("name") or "").strip()
            if not nm:
                continue
            kk = f"{cat}||{nm}"
            item_meta[kk] = {
                "catalog": cat, "name": nm, "lib": lib_of(cat),
                "name_cn": (s.get("name_cn") or s.get("display_name") or "").strip(),
                "prompt": (s.get("prompt") or "").strip(),
            }
            sig = (kk, p)
            if sig in seen_dst:
                continue
            seen_dst.add(sig)
            by_item[kk].append({
                "path": p,
                "name": os.path.basename(p),
                "run": it.get("_run", "")[:8],
                "title": it.get("title") or "",
                "preset": it.get("preset_name") or "",
                "index": it.get("index"),
                "size": os.path.getsize(p) if os.path.exists(p) else 0,
            })

    # 排序
    items = []
    for kk, imgs in by_item.items():
        meta = item_meta.get(kk, {})
        imgs.sort(key=lambda x: (x["run"], x["index"] or 0))
        items.append({**meta, "key": kk, "count": len(imgs), "images": imgs})
    items.sort(key=lambda x: -x["count"])

    print(f"  可解析 {resolved} | 未解析 {unresolved}", flush=True)
    print(f"  风格词条 {len(items)} 个，共 {sum(x['count'] for x in items)} 条风格-图关联", flush=True)
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "root": "",  # 运行时填充
        "items": items,
        "stats": {"records": len(recs), "resolved": resolved, "unresolved": unresolved,
                  "items": len(items), "images": sum(x["count"] for x in items)},
    }


PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>风格图库浏览器</title>
<style>
:root{--bg:#f5f6f8;--panel:#fff;--ink:#1b1f24;--ink2:#5a6370;--ink3:#8b95a3;
--line:#e3e7ed;--brand:#c8442f;--brand2:#e07a5f;--accent:#2f6fb5}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;height:100vh;display:flex;overflow:hidden}
#side{width:310px;flex:none;background:var(--panel);border-right:1px solid var(--line);
display:flex;flex-direction:column;height:100vh}
#side h1{font-size:16px;padding:16px 18px 10px;font-weight:700}
#side .meta{padding:0 18px 12px;color:var(--ink3);font-size:11.5px;line-height:1.7}
#q{margin:0 14px 10px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;
font-size:13px;font-family:inherit;outline:none}
#q:focus{border-color:var(--brand2)}
#list{flex:1;overflow-y:auto;padding:0 8px 20px}
.it{padding:9px 12px;border-radius:8px;cursor:pointer;margin-bottom:2px;transition:.12s}
.it:hover{background:#fafbfd}
.it.on{background:#fdf1ee}
.it .n{font-weight:600;font-size:13px;line-height:1.35}
.it .c{color:var(--ink3);font-size:11px;margin-top:2px;display:flex;
justify-content:space-between;gap:8px}
.it .badge{background:var(--brand);color:#fff;border-radius:10px;padding:0 7px;
font-size:10.5px;font-weight:600;flex:none}
#main{flex:1;overflow-y:auto;height:100vh;padding:22px 26px 60px}
#hd{border-bottom:2px solid var(--ink);padding-bottom:14px;margin-bottom:18px}
#hd h2{font-size:21px;font-weight:700;letter-spacing:-.3px}
#hd .sub{color:var(--ink2);font-size:12.5px;margin-top:5px}
#hd .lib{display:inline-block;background:#fdf1ee;color:#b8402c;border-radius:4px;
padding:1px 8px;font-size:11.5px;margin-left:6px}
#hd .prompt{color:var(--ink3);font-size:11.5px;margin-top:9px;line-height:1.65;
max-height:52px;overflow:hidden;font-style:italic}
#tools{display:flex;gap:10px;align-items:center;margin-bottom:14px;flex-wrap:wrap}
#tools button{background:#fff;border:1px solid var(--line);border-radius:7px;
padding:5px 12px;font-size:12.5px;cursor:pointer;font-family:inherit;color:var(--ink2)}
#tools button:hover{border-color:var(--brand2);color:var(--brand)}
#grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
overflow:hidden;transition:.15s}
.card:hover{border-color:var(--brand2);box-shadow:0 3px 14px rgba(200,68,47,.12)}
.card img{width:100%;aspect-ratio:1;object-fit:cover;display:block;
background:#f0f2f5;cursor:zoom-in}
.card .info{padding:8px 10px}
.card .fn{font-size:11.5px;font-weight:600;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap}
.card .sm{color:var(--ink3);font-size:10.5px;margin-top:2px;display:flex;
justify-content:space-between;gap:6px}
.card .act{display:flex;gap:6px;margin-top:7px}
.card .act button{flex:1;background:#fafbfd;border:1px solid var(--line);border-radius:5px;
padding:4px;font-size:10.5px;cursor:pointer;font-family:inherit;color:var(--ink2)}
.card .act button:hover{background:var(--brand);color:#fff;border-color:var(--brand)}
#empty{color:var(--ink3);text-align:center;padding:60px 20px;font-size:13px}
#viewer{position:fixed;inset:0;background:rgba(20,22,26,.93);display:none;
z-index:99;align-items:center;justify-content:center;flex-direction:column;padding:30px}
#viewer.on{display:flex}
#viewer img{max-width:92vw;max-height:82vh;border-radius:6px;box-shadow:0 10px 50px rgba(0,0,0,.5)}
#viewer .cap{color:#fff;margin-top:14px;font-size:13px;text-align:center;line-height:1.7}
#viewer .px{color:#9aa3ae;font-size:11.5px;word-break:break-all;max-width:80vw}
#viewer .close{position:absolute;top:20px;right:26px;color:#fff;font-size:28px;
cursor:pointer;line-height:1;opacity:.8}
#viewer .nav{position:absolute;top:50%;transform:translateY(-50%);color:#fff;
font-size:34px;cursor:pointer;padding:20px;opacity:.55;user-select:none}
#viewer .nav:hover{opacity:1}
#viewer .prev{left:12px}#viewer .next{right:12px}
</style></head><body>
<div id="side">
  <h1>风格图库浏览器</h1>
  <div class="meta" id="meta"></div>
  <input id="q" placeholder="搜索风格名 / 中文名 / 风格库 …">
  <div id="list"></div>
</div>
<div id="main">
  <div id="hd"><h2>请从左侧选择一个风格</h2>
    <div class="sub">点选后显示该风格生成的全部图片</div></div>
  <div id="tools" style="display:none">
    <span id="cnt" style="font-size:12.5px;color:var(--ink2)"></span>
    <button onclick="openFirstFolder()">在文件夹中打开首张</button>
    <button onclick="copyAll()">复制全部路径</button>
  </div>
  <div id="grid"></div>
  <div id="empty">← 从左侧选择风格</div>
</div>
<div id="viewer">
  <span class="close" onclick="closeV()">×</span>
  <span class="nav prev" onclick="step(-1)">‹</span>
  <span class="nav next" onclick="step(1)">›</span>
  <img id="vimg" src="">
  <div class="cap" id="vcap"></div>
  <div class="px" id="vpx"></div>
</div>
<script>
let DATA=null, cur=null, vi=0;
const $=s=>document.querySelector(s);

fetch('/api/data').then(r=>r.json()).then(d=>{
  DATA=d;
  $('#meta').innerHTML=`共 <b>${d.stats.items}</b> 个风格 · <b>${d.stats.images}</b> 条关联<br>`+
    `可解析图片 <b>${d.stats.resolved}</b> / ${d.stats.records}<br>索引于 ${d.generated_at}`;
  renderList(d.items);
});

function renderList(items){
  $('#list').innerHTML=items.map((x,i)=>`
    <div class="it" data-i="${i}">
      <div class="n">${esc(x.name_cn||x.name)}</div>
      <div class="c"><span>${esc(x.lib)}</span><span class="badge">${x.count}</span></div>
    </div>`).join('');
  $('#list').onclick=e=>{
    const el=e.target.closest('.it'); if(!el) return;
    document.querySelectorAll('.it').forEach(t=>t.classList.remove('on'));
    el.classList.add('on'); select(+el.dataset.i);
  };
}
function esc(s){return String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function fsize(b){return b>1048576?(b/1048576).toFixed(1)+'MB':(b/1024).toFixed(0)+'KB';}

function select(i){
  cur=DATA.items[i]; vi=0;
  $('#hd').innerHTML=`<h2>${esc(cur.name_cn||cur.name)}<span class="lib">${esc(cur.lib)}</span></h2>
    <div class="sub">${esc(cur.name)} · ${cur.count} 张图</div>
    ${cur.prompt?`<div class="prompt">${esc(cur.prompt)}</div>`:''}`;
  $('#tools').style.display='flex';
  $('#cnt').textContent=`共 ${cur.count} 张`;
  $('#empty').style.display='none';
  $('#grid').innerHTML=cur.images.map((im,k)=>`
    <div class="card">
      <img loading="lazy" src="/img?p=${encodeURIComponent(im.path)}" onclick="openV(${k})">
      <div class="info">
        <div class="fn" title="${esc(im.name)}">${esc(im.name)}</div>
        <div class="sm"><span>${esc(im.run)}</span><span>${im.size?fsize(im.size):''}</span></div>
        <div class="act">
          <button onclick="openFolder(${k})">打开文件夹</button>
          <button onclick="copyPath(${k})">复制路径</button>
        </div>
      </div>
    </div>`).join('');
}
function openV(k){vi=k;$('#viewer').classList.add('on');syncV();}
function syncV(){
  const im=cur.images[vi];
  $('#vimg').src='/img?p='+encodeURIComponent(im.path);
  $('#vcap').textContent=`${vi+1} / ${cur.count}　${im.name}`;
  $('#vpx').textContent=im.path;
}
function step(d){vi=(vi+d+cur.count)%cur.count;syncV();}
function closeV(){$('#viewer').classList.remove('on');}
document.addEventListener('keydown',e=>{
  if(!$('#viewer').classList.contains('on'))return;
  if(e.key==='Escape')closeV(); if(e.key==='ArrowLeft')step(-1); if(e.key==='ArrowRight')step(1);
});
function openFolder(k){post(cur.images[k].path);}
function openFirstFolder(){if(cur)post(cur.images[0].path);}
function copyPath(k){navigator.clipboard.writeText(cur.images[k].path);}
function copyAll(){navigator.clipboard.writeText(cur.images.map(x=>x.path).join('\n'));}
function post(p){fetch('/api/open',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({path:p})});}

$('#q').oninput=e=>{
  const s=e.target.value.trim().toLowerCase();
  renderList(DATA.items.filter(x=>
    (x.name+x.name_cn+x.lib+x.catalog).toLowerCase().includes(s)));
};
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    data = None

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if u.path == "/api/data":
            return self._send(200, json.dumps(self.data, ensure_ascii=False))
        if u.path == "/img":
            q = urllib.parse.parse_qs(u.query)
            p = (q.get("p") or [""])[0]
            p = os.path.normpath(p)
            if not p or not os.path.isfile(p):
                return self._send(404, "not found", "text/plain")
            ext = os.path.splitext(p)[1].lower()
            ct = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}.get(ext, "application/octet-stream")
            try:
                with open(p, "rb") as f:
                    return self._send(200, f.read(), ct)
            except Exception as e:
                return self._send(500, str(e), "text/plain")
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/open":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                body = {}
            p = body.get("path") or ""
            if p and os.path.isfile(p):
                try:
                    if sys.platform == "win32":
                        os.startfile(os.path.dirname(p))       # noqa
                    elif sys.platform == "darwin":
                        os.system(f'open "{os.path.dirname(p)}"')
                    else:
                        os.system(f'xdg-open "{os.path.dirname(p)}"')
                    return self._send(200, json.dumps({"ok": True}))
                except Exception as e:
                    return self._send(500, json.dumps({"ok": False, "err": str(e)}))
            return self._send(404, json.dumps({"ok": False}))
        return self._send(404, "not found", "text/plain")


def main():
    ap = argparse.ArgumentParser(description="风格图库浏览器")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--reviews", default=DEFAULT_REVIEWS)
    ap.add_argument("--roots", default=",".join(DEFAULT_IMG_ROOTS),
                    help="图片搜索根目录，逗号分隔")
    a = ap.parse_args()

    roots = [r.strip() for r in a.roots.split(",") if r.strip()]
    data = build_gallery(a.reviews, roots)

    Handler.data = data
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"\n  风格图库浏览器已启动 → http://127.0.0.1:{a.port}/")
    print(f"  风格 {data['stats']['items']} 个 · 关联 {data['stats']['images']} 条")
    print("  Ctrl+C 停止\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
