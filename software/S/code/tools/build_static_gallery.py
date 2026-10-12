# -*- coding: utf-8 -*-
"""生成静态版风格图库 HTML —— 双击即可打开，不依赖本地服务。

图片用相对/绝对文件路径引用。注意：部分浏览器会拦截 file:// 下的本地图片，
若图片不显示，请改用 style_gallery_server.py（推荐）。

用法：
    python build_static_gallery.py --out 风格图库.html --img-root "C:/Users/wyj/Desktop/c/D"
"""
import argparse, html, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import style_gallery_server as S


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="风格图库.html")
    ap.add_argument("--reviews", default=S.DEFAULT_REVIEWS)
    ap.add_argument("--roots", default=",".join(S.DEFAULT_IMG_ROOTS))
    ap.add_argument("--max-per-item", type=int, default=60, help="每个风格最多内嵌多少张（防文件过大）")
    a = ap.parse_args()

    data = S.build_gallery(a.reviews, [r.strip() for r in a.roots.split(",") if r.strip()])
    items = []
    for it in data["items"]:
        imgs = it["images"][:a.max_per_item]
        items.append({
            "name": it["name"], "name_cn": it["name_cn"], "lib": it["lib"],
            "count": it["count"], "shown": len(imgs), "prompt": it["prompt"][:400],
            "images": [{"p": im["path"].replace("\\", "/"), "n": im["name"],
                        "r": im["run"], "s": im["size"]} for im in imgs],
        })

    payload = json.dumps({"items": items, "stats": data["stats"],
                          "generated": data["generated_at"]}, ensure_ascii=False)

    tpl = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>风格图库 · 静态版</title>
<style>
:root{--bg:#f5f6f8;--panel:#fff;--ink:#1b1f24;--ink2:#5a6370;--ink3:#8b95a3;
--line:#e3e7ed;--brand:#c8442f;--brand2:#e07a5f}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
display:flex;height:100vh;overflow:hidden}
#side{width:300px;flex:none;background:var(--panel);border-right:1px solid var(--line);
display:flex;flex-direction:column}
#side h1{font-size:16px;padding:16px 18px 8px;font-weight:700}
#side .meta{padding:0 18px 10px;color:var(--ink3);font-size:11.5px;line-height:1.7}
#q{margin:0 14px 10px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;
font-size:13px;font-family:inherit;outline:none}
#list{flex:1;overflow-y:auto;padding:0 8px 20px}
.it{padding:9px 12px;border-radius:8px;cursor:pointer}
.it:hover{background:#fafbfd}.it.on{background:#fdf1ee}
.it .n{font-weight:600;font-size:13px}
.it .c{color:var(--ink3);font-size:11px;display:flex;justify-content:space-between;margin-top:2px}
.it .b{background:var(--brand);color:#fff;border-radius:10px;padding:0 7px;font-size:10.5px}
#main{flex:1;overflow-y:auto;padding:22px 26px 60px}
#hd{border-bottom:2px solid var(--ink);padding-bottom:14px;margin-bottom:16px}
#hd h2{font-size:20px}#hd .s{color:var(--ink2);font-size:12.5px;margin-top:4px}
#hd .lib{background:#fdf1ee;color:#b8402c;border-radius:4px;padding:1px 8px;font-size:11.5px;margin-left:6px}
#hd .p{color:var(--ink3);font-size:11.5px;margin-top:8px;font-style:italic}
#grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(185px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.card:hover{border-color:var(--brand2)}
.card img{width:100%;aspect-ratio:1;object-fit:cover;display:block;background:#eef0f3}
.card .i{padding:7px 9px}
.card .f{font-size:11.5px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card .m{color:var(--ink3);font-size:10.5px;display:flex;justify-content:space-between;margin-top:2px}
.card .p2{color:#9aa3ae;font-size:9.5px;word-break:break-all;margin-top:4px;max-height:26px;overflow:hidden}
#viewer{position:fixed;inset:0;background:rgba(20,22,26,.93);display:none;z-index:9;
align-items:center;justify-content:center;flex-direction:column;padding:30px}
#viewer.on{display:flex}
#viewer img{max-width:92vw;max-height:82vh;border-radius:6px}
#viewer .c{color:#fff;margin-top:12px;font-size:13px;text-align:center}
#viewer .x{position:absolute;top:18px;right:24px;color:#fff;font-size:28px;cursor:pointer}
#viewer .n{position:absolute;top:50%;transform:translateY(-50%);color:#fff;font-size:32px;
cursor:pointer;padding:18px;opacity:.6}
</style></head><body>
<div id="side"><h1>风格图库</h1><div class="meta" id="meta"></div>
<input id="q" placeholder="搜索风格 …"><div id="list"></div></div>
<div id="main"><div id="hd"><h2>← 从左侧选择风格</h2></div><div id="grid"></div></div>
<div id="viewer"><span class="x" onclick="close_()">×</span>
<span class="n" style="left:10px" onclick="st(-1)">‹</span>
<span class="n" style="right:10px" onclick="st(1)">›</span>
<img id="vi"><div class="c" id="vc"></div></div>
<script>
const D=__DATA__;
document.getElementById('meta').innerHTML=
  `共 <b>${D.stats.items}</b> 个风格 · <b>${D.stats.images}</b> 条关联<br>生成于 ${D.generated}`;
let cur=null,k=0;
function esc(s){return String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function fs(b){return b>1048576?(b/1048576).toFixed(1)+'MB':(b/1024).toFixed(0)+'KB';}
function render(list){
  document.getElementById('list').innerHTML=list.map((x,i)=>
    `<div class="it" data-i="${i}"><div class="n">${esc(x.name_cn||x.name)}</div>
     <div class="c"><span>${esc(x.lib)}</span><span class="b">${x.count}</span></div></div>`).join('');
  document.getElementById('list').onclick=e=>{
    const el=e.target.closest('.it'); if(!el)return;
    document.querySelectorAll('.it').forEach(t=>t.classList.remove('on'));
    el.classList.add('on'); sel(+el.dataset.i);
  };
}
function sel(i){
  cur=D.items[i];k=0;
  document.getElementById('hd').innerHTML=
    `<h2>${esc(cur.name_cn||cur.name)}<span class="lib">${esc(cur.lib)}</span></h2>
     <div class="s">${esc(cur.name)} · ${cur.count} 张${cur.shown<cur.count?`（显示前 ${cur.shown} 张）`:''}</div>
     ${cur.prompt?`<div class="p">${esc(cur.prompt)}</div>`:''}`;
  document.getElementById('grid').innerHTML=cur.images.map((im,j)=>
    `<div class="card"><img loading="lazy" src="file:///${im.p}" onclick="op(${j})"
      onerror="this.style.opacity=.25;this.alt='需用本地服务打开'">
     <div class="i"><div class="f" title="${esc(im.n)}">${esc(im.n)}</div>
     <div class="m"><span>${esc(im.r)}</span><span>${im.s?fs(im.s):''}</span></div>
     <div class="p2">${esc(im.p)}</div></div></div>`).join('');
}
function op(j){k=j;document.getElementById('viewer').classList.add('on');sy();}
function sy(){const im=cur.images[k];
  document.getElementById('vi').src='file:///'+im.p;
  document.getElementById('vc').textContent=`${k+1} / ${cur.images.length}　${im.n}`;}
function st(d){k=(k+d+cur.images.length)%cur.images.length;sy();}
function close_(){document.getElementById('viewer').classList.remove('on');}
document.addEventListener('keydown',e=>{
  if(!document.getElementById('viewer').classList.contains('on'))return;
  if(e.key==='Escape')close_(); if(e.key==='ArrowLeft')st(-1); if(e.key==='ArrowRight')st(1);});
document.getElementById('q').oninput=e=>{
  const s=e.target.value.trim().toLowerCase();
  render(D.items.filter(x=>(x.name+x.name_cn+x.lib).toLowerCase().includes(s)));};
render(D.items);
</script></body></html>"""

    open(a.out, "w", encoding="utf-8").write(tpl.replace("__DATA__", payload))
    print(f"[ok] {a.out}  ({os.path.getsize(a.out):,} bytes)")
    print(f"     风格 {data['stats']['items']} 个 · 关联 {data['stats']['images']} 条")
    print("     提示：若图片不显示，请改用 style_gallery_server.py 启动本地服务版")


if __name__ == "__main__":
    main()
