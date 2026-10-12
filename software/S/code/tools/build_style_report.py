# -*- coding: utf-8 -*-
"""生成风格能力范围可视化报告（单文件 HTML，数据内嵌）。

用法：
    python tools/build_style_report.py --out-dir <目录>
读取 <目录>/style_scope_deep.json，产出 <目录>/风格能力范围统计报告.html
"""
import json, html, os, argparse

_ap = argparse.ArgumentParser()
_ap.add_argument("--out-dir", default=os.path.dirname(os.path.abspath(__file__)))
_a = _ap.parse_args()
_D = _a.out_dir

DEEP = json.load(open(os.path.join(_D, "style_scope_deep.json"), encoding="utf-8"))
OUT = os.path.join(_D, "风格能力范围统计报告.html")
DATA = json.dumps(DEEP, ensure_ascii=False)

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>风格能力范围统计 · Krea2 风格组合</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root{
  --bg:#f6f7f9; --panel:#ffffff; --ink:#1b1f24; --ink2:#5a6370; --ink3:#8b95a3;
  --line:#e4e8ee; --brand:#c8442f; --brand2:#e07a5f; --accent:#2f6fb5;
  --green:#3c8c5a; --amber:#c98a12;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);
  font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
  padding:28px 22px 60px;max-width:1400px;margin:0 auto}
h1{font-size:26px;font-weight:700;letter-spacing:-.4px}
h2{font-size:17px;font-weight:650;margin:0 0 4px}
.sub{color:var(--ink2);font-size:13px;margin-top:6px}
.head{border-bottom:2px solid var(--ink);padding-bottom:16px;margin-bottom:22px}
.stamp{display:flex;gap:18px;flex-wrap:wrap;margin-top:12px;font-size:12px;color:var(--ink3)}
.stamp b{color:var(--ink);font-weight:600}

.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:26px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi .v{font-size:26px;font-weight:700;letter-spacing:-1px;line-height:1.15}
.kpi .v small{font-size:13px;font-weight:500;color:var(--ink3);margin-left:2px}
.kpi .l{font-size:12px;color:var(--ink2);margin-top:3px}
.kpi.hi{background:linear-gradient(140deg,#fdf1ee,#fff);border-color:#f0cfc6}
.kpi.hi .v{color:var(--brand)}

.alert{background:#fff8ec;border:1px solid #f0dcb4;border-left:4px solid var(--amber);
  border-radius:8px;padding:13px 16px;margin-bottom:24px;font-size:13px;color:#6b5320}
.alert b{color:#8a5f00}

section{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:20px 22px;margin-bottom:18px}
.sec-head{display:flex;justify-content:space-between;align-items:flex-end;
  gap:14px;flex-wrap:wrap;margin-bottom:16px}
.sec-head p{color:var(--ink2);font-size:12.5px;margin-top:3px}

.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}
.tab{padding:5px 12px;border-radius:20px;border:1px solid var(--line);background:#fff;
  color:var(--ink2);font-size:12.5px;cursor:pointer;transition:.15s}
.tab:hover{border-color:var(--brand2);color:var(--brand)}
.tab.on{background:var(--ink);border-color:var(--ink);color:#fff}

table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-weight:600;color:var(--ink2);font-size:11.5px;
  text-transform:uppercase;letter-spacing:.4px;padding:8px 10px;border-bottom:1px solid var(--line);
  white-space:nowrap;cursor:pointer;user-select:none}
th:hover{color:var(--brand)}
td{padding:9px 10px;border-bottom:1px solid #f1f4f8;vertical-align:top}
tr:hover td{background:#fafbfd}
.num{text-align:right;font-variant-numeric:tabular-nums;font-weight:600;white-space:nowrap}
.bar{height:5px;border-radius:3px;background:var(--brand);opacity:.75;min-width:2px}

.tag{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11px;
  margin:1px 3px 1px 0;white-space:nowrap}
.t-tech{background:#eef3fa;color:#2f6fb5}
.t-cn{background:#f2f4f7;color:#5a6370}
.t-lib{background:#fdf1ee;color:#b8402c}
.t-ok{background:#eaf5ee;color:#2f7048}
.t-solo{background:#f5eefa;color:#7a4a9e}

.chip{display:inline-block;background:#f2f4f7;border:1px solid var(--line);
  border-radius:6px;padding:4px 9px;font-size:12px;margin:0 6px 6px 0}
.chip b{font-weight:600}
.chip .s{color:var(--ink3);font-size:11px;margin-left:4px}

.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.box{background:#fafbfd;border:1px solid var(--line);border-radius:9px;padding:14px}
.box h3{font-size:13px;font-weight:650;margin-bottom:10px;color:var(--ink)}
.rank{display:flex;align-items:center;gap:9px;margin-bottom:7px;font-size:12.5px}
.rank .idx{width:18px;text-align:right;color:var(--ink3);font-size:11px;flex:none}
.rank .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rank .bar{width:90px;flex:none}
.rank .vv{width:38px;text-align:right;font-variant-numeric:tabular-nums;
  color:var(--ink2);font-size:11.5px;flex:none}

.badge{display:inline-flex;align-items:center;gap:4px;font-size:11px;
  padding:2px 8px;border-radius:20px;background:#eef3fa;color:#2f6fb5;font-weight:600}
.badge.solo{background:#f5eefa;color:#7a4a9e}
.recipe{border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:12px}
.recipe-top{display:flex;justify-content:space-between;align-items:baseline;
  gap:12px;margin-bottom:9px;flex-wrap:wrap}
.recipe-top .rn{font-weight:650;font-size:14.5px}
.recipe-top .ri{color:var(--ink3);font-size:12px}
.lo{display:inline-block;background:#fff;border:1px dashed var(--line);border-radius:5px;
  padding:2px 8px;font-size:11.5px;margin:2px 5px 2px 0;color:var(--ink2)}
.lo b{color:var(--brand);font-variant-numeric:tabular-nums}

#search{width:100%;padding:9px 13px;border:1px solid var(--line);border-radius:8px;
  font-size:13.5px;font-family:inherit;margin-bottom:12px;outline:none}
#search:focus{border-color:var(--brand2)}
.foot{color:var(--ink3);font-size:11.5px;text-align:center;margin-top:26px;line-height:1.9}
canvas{max-height:290px}
</style>
</head>
<body>
<div class="head">
  <h1>风格能力范围统计</h1>
  <div class="sub">基于 Krea2 风格组合的真实生成记录 —— 各风格划定能力边界、笔触画像与搭配配方</div>
  <div class="stamp" id="stamp"></div>
</div>

<div class="kpis" id="kpis"></div>
<div class="alert" id="alert"></div>

<section>
  <div class="sec-head">
    <div><h2>一、风格词条能力画像</h2>
    <p>每个词条实际调用次数、技法维度、常共现搭档。<b>组合</b>=与其他风格同批使用；<b>独奏</b>=单独使用。</p></div>
    <div class="tabs" id="techTabs"></div>
  </div>
  <input id="search" placeholder="搜索词条名 / 中文名 / 风格库 …">
  <div style="overflow-x:auto"><table id="tbl">
    <thead><tr>
      <th data-k="images">调用图数 ▾</th><th data-k="name">风格词条</th>
      <th data-k="lib">所属风格库</th><th>技法维度</th><th data-k="role">使用角色</th>
      <th>常共现搭档</th>
    </tr></thead><tbody></tbody>
  </table></div>
</section>

<section>
  <div class="sec-head"><div><h2>二、风格库覆盖面</h2>
  <p>你从每个库里实际启用了多少词条 —— 分母是该库可用词条总数。</p></div></div>
  <div class="grid2">
    <div class="box"><h3>各风格库启用词条数 vs 可用总数</h3><canvas id="c-lib"></canvas></div>
    <div><table id="tbl-lib"><thead><tr>
      <th data-k="images">调用图数 ▾</th><th>风格库</th><th>启用/可用</th><th>覆盖率</th><th>主力方案</th>
    </tr></thead><tbody></tbody></table></div>
  </div>
</section>

<section>
  <div class="sec-head"><div><h2>三、技法维度分布</h2>
  <p>把 27 个词条的 prompt 按笔触/媒介/渲染措辞归类，看你的风格组合整体偏向哪些表现手法。</p></div></div>
  <div class="grid2">
    <div class="box"><h3>技法维度 → 覆盖图数</h3><canvas id="c-tech"></canvas></div>
    <div class="box"><h3>维度词条归属（代表性词条）</h3><div id="tech-list"></div></div>
  </div>
</section>

<section>
  <div class="sec-head"><div><h2>四、组合配方库</h2>
  <p>按预设方案还原的真实组合：用了哪些词条、配了哪些 LoRA、强度多少。可直接复用。</p></div></div>
  <div id="recipes"></div>
</section>

<section>
  <div class="sec-head"><div><h2>五、LoRA 搭配统计</h2>
  <p>每个 LoRA 出现在多少张图、强度区间与常用搭配库。</p></div></div>
  <table id="tbl-lora"><thead><tr>
    <th data-k="images">调用图数 ▾</th><th>LoRA</th><th>强度区间</th><th>均值</th><th>常搭配风格库</th>
  </tr></thead><tbody></tbody></table>
</section>

<div class="foot" id="foot"></div>

<script>
const D = __DATA__;

/* ---------- 头部信息 ---------- */
const O = D.overview;
document.getElementById('stamp').innerHTML =
  `数据源 · ComfyBatch-S 审核记录 + 批次报告　|　统计时间 <b>${new Date().toLocaleDateString('zh-CN')}</b>` +
  `　|　共 <b>${O.runs}</b> 个批次、<b>${O.images}</b> 张图`;

document.getElementById('kpis').innerHTML = [
  ['图片记录', O.images, '张（成功 '+O.completed+'）', ''],
  ['启用的风格词条', O.distinct_style_items, '个', 'hi'],
  ['覆盖风格库', O.distinct_libraries, '个', ''],
  ['组合方案', O.style_presets_used, '套', ''],
  ['搭配 LoRA', O.distinct_loras, '个', ''],
  ['库中可用词条', O.catalog_items_available.toLocaleString(), '个', ''],
].map(([l,v,u,c])=>`<div class="kpi ${c}"><div class="v">${v}<small>${u}</small></div><div class="l">${l}</div></div>`).join('');

const cover = (O.distinct_style_items / O.catalog_items_available * 100).toFixed(2);
const cs = O.combination_size;
const multi = ((cs['2']||0)+(cs['3']||0));
document.getElementById('alert').innerHTML =
  `<b>核心结论</b>：${O.images} 张图只用到 <b>${O.distinct_style_items}</b> 个风格词条，` +
  `而风格库里有 <b>${O.catalog_items_available.toLocaleString()}</b> 个 —— 覆盖率仅 <b>${cover}%</b>。` +
  `其中 <b>${multi}</b> 张（${(multi/O.images*100).toFixed(0)}%）是 2~3 个词条组合生成，` +
  `说明你的风格能力实际由少数几个「主力词条」撑起来，拓展空间极大。`;

/* ---------- 表格工具 ---------- */
function sortRows(tbody, key, dir, getter){
  const rows=[...tbody.children];
  rows.sort((a,b)=>{
    const va=getter(a.dataset), vb=getter(b.dataset);
    if(typeof va==='number') return dir*(va-vb);
    return dir*String(va).localeCompare(String(vb),'zh');
  });
  rows.forEach(r=>tbody.appendChild(r));
}

/* ---------- 一、词条表 ---------- */
const ICH = D.items;
const ALLTECH = [...new Set(ICH.flatMap(x=>x.tech))];
document.getElementById('techTabs').innerHTML =
  `<span class="tab on" data-t="">全部</span>` +
  ALLTECH.map(t=>`<span class="tab" data-t="${t}">${t}</span>`).join('');

let curTech='', curQ='', sortKey='images', sortDir=-1;
function renderItems(){
  const tb=document.querySelector('#tbl tbody');
  let rows=ICH.filter(x=>{
    if(curTech && !x.tech.includes(curTech)) return false;
    if(curQ){
      const s=(x.name+' '+x.name_cn+' '+x.lib+' '+x.catalog).toLowerCase();
      if(!s.includes(curQ)) return false;
    }
    return true;
  });
  const max=Math.max(...rows.map(r=>r.images),1);
  rows.sort((a,b)=>{
    const va=a[sortKey], vb=b[sortKey];
    if(typeof va==='number') return sortDir*(va-vb);
    return sortDir*String(va).localeCompare(String(vb),'zh');
  });
  tb.innerHTML=rows.map(x=>{
    const role = (x.role['独奏']?`<span class="tag t-solo">独奏 ${x.role['独奏']}</span>`:'')+
                 (x.role['组合']?`<span class="tag t-ok">组合 ${x.role['组合']}</span>`:'');
    const mates = x.mates.slice(0,3).map(m=>
      `<span class="chip" style="padding:2px 7px;margin:0 4px 4px 0">${m.name.length>26?m.name.slice(0,26)+'…':m.name}<span class="s">${m.images}</span></span>`).join('');
    return `<tr data-k='${JSON.stringify({images:x.images,name:x.name,lib:x.lib,role:x.role['组合']||0})}'>
      <td class="num"><div>${x.images}</div>
        <div class="bar" style="width:${Math.max(3,x.images/max*90)}px;margin-left:auto"></div></td>
      <td><div style="font-weight:600">${x.name}</div>
        ${x.name_cn?`<div style="color:var(--ink3);font-size:11.5px">${x.name_cn}</div>`:''}</td>
      <td><span class="tag t-lib">${x.lib}</span>
        ${x.influences.length?`<div style="color:var(--ink3);font-size:11px;margin-top:3px">师承：${x.influences.slice(0,2).join('、').slice(0,40)}</div>`:''}</td>
      <td>${x.tech.map(t=>`<span class="tag t-tech">${t}</span>`).join('')}</td>
      <td>${role}</td>
      <td style="max-width:250px">${mates}</td>
    </tr>`;
  }).join('');
}
function bindSort(tableId, getter){
  document.querySelectorAll('#'+tableId+' th[data-k]').forEach(th=>{
    th.onclick=()=>{
      const k=th.dataset.k;
      sortDir = (k===sortKey)? -sortDir : -1; sortKey=k;
      document.querySelectorAll('#'+tableId+' th[data-k]').forEach(t=>{
        t.textContent=t.textContent.replace(' ▾','').replace(' ▴','');
      });
      th.textContent=th.textContent.split(' ')[0]+(sortDir<0?' ▾':' ▴');
      renderItems();
    };
  });
  document.querySelectorAll('#'+tableId+' th[data-k]').forEach(th=>{
    if(th.dataset.k===sortKey) th.textContent=th.textContent+(sortDir<0?' ▾':' ▴');
  });
}
document.getElementById('techTabs').onclick=e=>{
  if(!e.target.classList.contains('tab'))return;
  document.querySelectorAll('#techTabs .tab').forEach(t=>t.classList.remove('on'));
  e.target.classList.add('on'); curTech=e.target.dataset.t; renderItems();
};
document.getElementById('search').oninput=e=>{curQ=e.target.value.trim().toLowerCase();renderItems();};
renderItems(); bindSort('tbl');

/* ---------- 二、风格库 ---------- */
const LIB=D.libraries;
new Chart(document.getElementById('c-lib'),{
  type:'bar',
  data:{labels:LIB.map(x=>x.lib.length>22?x.lib.slice(0,22)+'…':x.lib),
    datasets:[
      {label:'已启用词条',data:LIB.map(x=>x.distinct_items_used),backgroundColor:'#c8442f'},
      {label:'库中可用',data:LIB.map(x=>x.distinct_items_available),backgroundColor:'#dfe4ea'},
    ]},
  options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,
    plugins:{legend:{position:'bottom',labels:{boxWidth:11,font:{size:11}}}},
    scales:{x:{type:'logarithmic',ticks:{font:{size:10}}},y:{ticks:{font:{size:11}}}}}
});
document.querySelector('#tbl-lib tbody').innerHTML=LIB.map(x=>{
  const cov=x.distinct_items_available?(x.distinct_items_used/x.distinct_items_available*100):0;
  return `<tr>
    <td class="num">${x.images}</td>
    <td>${x.lib}</td>
    <td class="num" style="color:var(--ink2)">${x.distinct_items_used} / ${x.distinct_items_available}</td>
    <td class="num" style="color:${cov>5?'#3c8c5a':'#8b95a3'}">${cov.toFixed(1)}%</td>
    <td>${Object.keys(x.top_presets).slice(0,2).map(p=>`<span class="tag t-cn">${p}</span>`).join('')}</td>
  </tr>`;
}).join('');

/* ---------- 三、技法 ---------- */
const T=D.tech_used.filter(t=>!['纹理·拼贴','霓虹·辉光','装饰·图案','厚涂·笔触感','半调·印刷','超现实·梦幻','矢量·扁平','油画·古典','光影·氛围','胶片·颗粒','极简·留白','水彩·晕染','噪点·故障'].includes(t.dim));
new Chart(document.getElementById('c-tech'),{
  type:'bar',
  data:{labels:T.map(t=>t.dim),
    datasets:[{label:'覆盖图数',data:T.map(t=>t.images),backgroundColor:'#2f6fb5',borderRadius:4}]},
  options:{responsive:true,maintainAspectRatio:false,
    plugins:{legend:{display:false}},
    scales:{y:{beginAtZero:true,ticks:{font:{size:11}}},x:{ticks:{font:{size:10.5}}}}}
});
document.getElementById('tech-list').innerHTML=D.tech_used.map(t=>`
  <div style="margin-bottom:11px">
    <div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:4px">
      <b>${t.dim}</b><span style="color:var(--ink3)">${t.images} 张 · ${t.items} 个词条</span></div>
    <div>${t.top_items.slice(0,3).map(i=>`<span class="chip" style="padding:2px 7px">${(i[0]||'').slice(0,30)}</span>`).join('')}</div>
  </div>`).join('');

/* ---------- 四、配方 ---------- */
document.getElementById('recipes').innerHTML=D.recipes.map(r=>`
  <div class="recipe">
    <div class="recipe-top">
      <span class="rn">${r.name}</span>
      <span class="ri">${r.images} 张 · ${r.items.length} 个风格词条${r.loras.length?' · '+r.loras.length+' 个 LoRA':''}</span>
    </div>
    <div style="margin-bottom:8px">
      ${r.items.map(i=>`<span class="chip"><b>${i.name_cn||i.name}</b>${i.name_cn?`<span class="s">${i.name.length>24?i.name.slice(0,24)+'…':i.name}</span>`:''}<span class="s">${i.images}张</span><span class="tag t-lib" style="margin-left:5px">${i.lib}</span></span>`).join('')}
    </div>
    ${r.loras.length?`<div>${r.loras.map(l=>`<span class="lo">${l.name.replace('.safetensors','').slice(0,42)} <b>${l.avg??'—'}</b></span>`).join('')}</div>`:''}
  </div>`).join('');

/* ---------- 五、LoRA ---------- */
document.querySelector('#tbl-lora tbody').innerHTML=D.loras.map(l=>`
  <tr>
    <td class="num">${l.images}</td>
    <td style="font-weight:600;font-size:12.5px">${l.name}</td>
    <td class="num" style="color:var(--ink2)">${l.strength_min} ~ ${l.strength_max}</td>
    <td class="num" style="color:var(--brand)">${l.strength_avg}</td>
    <td>${l.top_libs.map(x=>`<span class="tag t-lib">${x}</span>`).join('')}</td>
  </tr>`).join('');

document.getElementById('foot').innerHTML =
  `统计口径：以 ComfyBatch-S 的审核记录（reviews/*.json）为准，同一 run 的批次报告不重复计数。<br>` +
  `技法维度由风格词条自身 prompt 文本归类，非人工标注；「师承」取自词条 prompt 的 Influenced by 字段。`;
</script>
</body>
</html>
"""

open(OUT, "w", encoding="utf-8").write(HTML.replace("__DATA__", DATA))
print("[ok] ->", OUT)
