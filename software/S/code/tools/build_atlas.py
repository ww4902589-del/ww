# -*- coding: utf-8 -*-
"""风格图鉴 —— 生成合并版界面（统计报告 + 多标签图库 + 叠加筛选）。

产出单文件 HTML，双击即用：
    python build_atlas.py
    python build_atlas.py --out 风格图鉴.html --max-thumb 50

功能：
  * 标签面板：方案 / 主题 / 风格库 / 风格词条 / 技法 / LoRA 六维
  * 点单个标签 → 该类别全部图片
  * 叠加多个标签 → 交集精确定位（如「动漫服装」+「甜彩线描」）
  * 统计视图：覆盖率、维度分布、共现排行、配方
  * 每张图带「打开文件夹」+「复制路径」
"""
import argparse, collections, json, os, re, shutil, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import style_atlas as A
import atlas_management as Management

#: 渲染产物的版本号。**改了 HTML 模板 / CSS / 前端 JS 就 +1。**
#:
#: 为什么需要它：`渲染出的 风格图鉴.html` 是一份派生文件，会落盘、会被服务端
#: 直接端给浏览器。换了 exe（里面是新的 build_atlas.py）之后，磁盘上那份旧页面
#: 依然存在，服务端只看「文件在不在」就原样返回 —— 于是**升级后用户看到的还是
#: 旧界面**，会以为改动没生效。这类静默不生效只能靠版本戳抓。
#:
#: 与 `style_atlas.FOLDER_RULE` 是同一类护栏，但管的是**模板**：
#: FOLDER_RULE 管「数据里派生列怎么算」，RENDER_RULE 管「页面长什么样」。
#: 1 → 2：V2.34 查看器改成左右分栏 + 信息栏、收藏夹加「待定位」区。
#: 2 → 3：V2.34 收尾 —— 前端加 uid 规范化（canonUid/canonSets），
#:        掐掉「升级前打开的旧标签页把陈旧 uid 并回来」这一类回潮。
RENDER_RULE = 10

#: 渲染时写进 <head> 的锚点，服务端用它判断磁盘上那份页面是不是本版本渲的。
RENDER_META = "atlas-render"

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="__RENDER_META__" content="__RENDER_RULE__">
<title>风格图鉴 · Krea2 风格组合</title>
<style>
:root{--bg:#f5f6f8;--panel:#fff;--ink:#1b1f24;--ink2:#5a6370;--ink3:#8b95a3;
--line:#e3e7ed;--brand:#c8442f;--brand2:#e07a5f;--accent:#2f6fb5;--ok:#3c8c5a}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
#top{background:var(--panel);border-bottom:1px solid var(--line);padding:14px 22px;
position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:18px;flex-wrap:wrap}
#top h1{font-size:19px;font-weight:700;letter-spacing:-.3px}
#top .st{color:var(--ink3);font-size:11.5px;line-height:1.6}
/* 内容指纹：做成可点选复制的小胶囊，方便用户拿去核对 */
#top .bid{display:inline-block;padding:1px 7px;border-radius:9px;font-family:ui-monospace,Consolas,monospace;
  font-size:11px;font-weight:600;color:var(--brand);background:rgba(200,68,47,.09);
  border:1px solid rgba(200,68,47,.22);cursor:help;user-select:all}
/* 标记张数芯片：和版本并排，方便一起核对 */
#top .bidm{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;font-weight:600;
  color:#3a6ea5;background:rgba(58,110,165,.09);border:1px solid rgba(58,110,165,.22);cursor:help}
/* 风格归因修复进度条 —— 修复张数是「这轮到底救回了多少」的直接证据 */
#top .fixbar{display:inline-block;margin-top:3px;padding:2px 8px;border-radius:9px;
  background:#eef7ee;color:#2e6b3a;font-size:11px;line-height:1.7}
#top .fixbar b{color:#1f5c2c}
.tabs{display:flex;gap:4px;margin-left:auto}
.tb{padding:6px 15px;border-radius:20px;border:1px solid var(--line);background:#fff;
color:var(--ink2);font-size:13px;cursor:pointer;font-family:inherit}
.tb.on{background:var(--ink);border-color:var(--ink);color:#fff}
.tb:hover{border-color:var(--brand2)}
/* 左右两栏各自独立滚动 —— 原来整页一起滚，选筛选条件时往下翻，
   右边的图会一起被推走；翻图片翻到底，左边的筛选又跑没了。
   现在两栏各滚各的：左边怎么翻，右边都一直有图；右边怎么翻，左边条件都在。
   高度都是「视口减掉顶部工具条」：--topbar 由脚本按 #top 的实际高度写进去
   （工具条会换行，写死 68px 的话换行后就对不齐）。 */
.wrap{display:flex;height:calc(100vh - var(--topbar,68px));overflow:hidden}
/* 左侧标签面板 */
#facet{width:285px;flex:none;background:var(--panel);border-right:1px solid var(--line);
padding:0 0 24px;overflow-y:auto;overscroll-behavior:contain}
.fg{padding:0 14px 12px}
.fg h3{font-size:12px;font-weight:650;color:var(--ink2);margin:12px 0 7px;
display:flex;justify-content:space-between;align-items:center;
text-transform:uppercase;letter-spacing:.4px}
.fg h3 span{font-weight:400;color:var(--ink3);font-size:10.5px}
.fg .chips{display:flex;flex-wrap:wrap;gap:5px;max-height:165px;overflow-y:auto}
.fg .chips.hide{display:none}
.ck{background:#f4f6f9;border:1px solid transparent;border-radius:6px;
padding:3px 8px;font-size:11.5px;cursor:pointer;color:var(--ink2);transition:.12s;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.ck:hover{border-color:var(--brand2);color:var(--brand)}
.ck.on{background:var(--brand);color:#fff;border-color:var(--brand)}
.ck .n{color:var(--ink3);font-size:10px;margin-left:3px}
.ck.on .n{color:#f5d5ce}
.more{font-size:10.5px;color:var(--accent);cursor:pointer;margin-top:4px;display:inline-block}
/* 主分类：文件夹 / 批次（时间）—— 缩略式按钮。
   这两维是「图从哪来、什么时候做的」，是翻图鉴时最先要用的定位手段，
   所以给缩略图（一眼认出是哪一批的什么图），并排在最上面。
   风格 / 技法等语义维度降为次要，收在下面。 */
.sec2{display:flex;align-items:center;gap:7px;padding:0 14px;margin:16px 0 0}
.sec2 .ln{flex:1;height:1px;background:var(--line)}
.sec2 .tt{font-size:11px;font-weight:650;color:var(--ink2);letter-spacing:.4px;
  white-space:nowrap}
.sec2 .tt em{font-style:normal;color:var(--ink3);font-weight:400}
.pgrid{display:flex;flex-wrap:wrap;gap:6px}
.pgrid.cap{max-height:322px;overflow-y:auto;padding-right:2px}
/* 子层级（大文件夹下的子文件夹 / 当天各批次）：左侧竖线 + 浅底，
   一眼看出它是「上一层选中之后才出现」的第二级，而不是又一排同级按钮。 */
.subhead{display:flex;align-items:baseline;gap:8px;margin:11px 0 6px;
  padding-left:8px;border-left:2px solid var(--brand2);font-size:11px;color:var(--ink2)}
.subhead b{color:var(--brand)}
.subhead .clr{margin-left:auto;color:var(--accent);cursor:pointer;font-size:10.5px}
.subhead .clr:hover{text-decoration:underline}
.pgrid.sub{margin-left:8px;padding-left:9px;border-left:2px solid var(--line);
  max-height:300px;overflow-y:auto;padding-right:2px}
.pb{width:58px;flex:none;border:1px solid var(--line);border-radius:9px;padding:3px;
  background:#fff;cursor:pointer;transition:.12s;position:relative;font-family:inherit}
.pb:hover{border-color:var(--brand2);box-shadow:0 2px 8px rgba(200,68,47,.12)}
.pb.on{border-color:var(--brand);background:#fdf1ee;box-shadow:0 0 0 2px rgba(200,68,47,.16)}
.pb img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:6px;display:block;
  background:linear-gradient(#eef0f3,#e4e8ee)}
.pb .lb{font-size:9px;line-height:11px;color:var(--ink2);text-align:center;display:block;
  margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pb.on .lb{color:var(--brand);font-weight:600}
.pb .ct{position:absolute;top:5px;right:5px;background:rgba(12,16,22,.62);color:#fff;
  font-size:9px;line-height:13px;border-radius:7px;padding:0 4px;font-weight:600}
.pb.on .ct{background:var(--brand)}
.pb .noimg{width:100%;aspect-ratio:1;border-radius:6px;display:flex;align-items:center;
  justify-content:center;color:var(--ink3);font-size:9px;
  background:linear-gradient(#eef0f3,#e4e8ee)}
/* 统计页：数据只在点「更新统计」时重算，条件变了先在这里提示 */
.statsbar{position:sticky;top:0;z-index:9;display:flex;align-items:center;gap:12px;
  background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:9px 14px;margin-bottom:14px;transition:.15s}
.statsbar.on{background:#fff7f4;border-color:var(--brand2)}
.statsbar .sbtext{flex:1;font-size:12.5px;color:var(--ink2)}
.statsbar.on .sbtext{color:#a5381f}
.sbupd{background:var(--ink);color:#fff;border:none;border-radius:7px;padding:6px 14px;
  font-size:12.5px;font-weight:600;cursor:pointer;font-family:inherit;flex:none}
.sbupd:hover{background:var(--brand)}
/* 风格库 → 子选项（两级联动） */
.fg h3 .hint{font-style:normal;font-weight:400;font-size:10px;color:var(--ink3);
  border:1px solid var(--line);border-radius:5px;padding:1px 5px}
.librow{margin-bottom:2px}
.libhead{display:flex;gap:4px;align-items:center}
.tw{font-size:10px;color:var(--accent);cursor:pointer;padding:2px 5px;
  border:1px solid var(--line);border-radius:5px;white-space:nowrap;flex:none}
.tw:hover{background:#f4f6f9}
.subchips{display:flex;flex-wrap:wrap;gap:4px;margin:4px 0 7px 10px;
  padding-left:8px;border-left:2px solid var(--line);max-height:150px;overflow-y:auto}
.ck.sm{font-size:10.5px;padding:2px 6px;background:#fafbfc}
/* 次要维度折叠块 */
.weakdim{border:1px dashed var(--line);border-radius:10px;padding:10px 14px;background:#fcfcfd}
.weakdim summary{cursor:pointer;font-weight:650;color:var(--ink2);font-size:13.5px;outline:none}
.weakdim[open] summary{margin-bottom:8px}
.weakdim p{color:var(--ink3);font-size:12px;margin:0 0 10px}
/* 多标记夹：夹切换条 / 图上的夹色条 / 右键菜单 */
.setbar{flex-wrap:wrap;gap:6px}
.settab{display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border-radius:16px;
  border:1px solid var(--line);cursor:pointer;font-size:12.5px;color:var(--ink2);
  background:#fff;transition:.12s;white-space:nowrap}
.settab:hover{border-color:var(--sc)}
.settab.on{background:var(--sc);color:#fff;border-color:var(--sc);font-weight:600}
.settab b{font-weight:600;opacity:.85;font-size:11.5px}
.settab.on b{opacity:.95}
.settab .dot,.mi .dot{width:8px;height:8px;border-radius:50%;background:var(--sc);
  flex:none;display:inline-block}
.settab.on .dot{background:#fff}
.settags{display:flex;flex-wrap:wrap;gap:3px;padding:4px 6px 0}
.stg{font-size:10px;padding:1px 6px;border-radius:9px;cursor:pointer;
  background:var(--sc);color:#fff;opacity:.88;white-space:nowrap;max-width:100%;
  overflow:hidden;text-overflow:ellipsis}
.stg:hover{opacity:1}
#mkmenu .mi{display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:6px;
  cursor:pointer;color:var(--ink2)}
#mkmenu .mi:hover{background:#f4f6f9}
/* 主区 */
#main{flex:1;padding:18px 22px 60px;overflow-y:auto;overflow-x:hidden;
  overscroll-behavior:contain}
#selbar{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:11px 14px;margin-bottom:15px;display:none}
#selbar.on{display:block}
#selbar .row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
#selbar .lbl{font-size:11.5px;color:var(--ink3);flex:none}
.stag{background:#fdf1ee;color:#b8402c;border-radius:6px;padding:3px 9px;font-size:12px;
display:inline-flex;align-items:center;gap:6px;border:1px solid #f2d5cd}
.stag i{cursor:pointer;font-style:normal;opacity:.6;font-weight:700}
.stag i:hover{opacity:1}
/* 评级条件条：实等级色底（--sc），与普通标签的浅红底区分 */
.stag.gtag{background:var(--sc);color:#fff;border-color:var(--sc)}
.stag.gtag i{opacity:.85}
#selbar .res{margin-left:auto;font-size:12.5px;color:var(--ink2)}
#selbar .res b{color:var(--brand);font-size:15px}
#selbar button{background:#fff;border:1px solid var(--line);border-radius:6px;
padding:4px 11px;font-size:12px;cursor:pointer;font-family:inherit;color:var(--ink2)}
#selbar button:hover{border-color:var(--brand2);color:var(--brand)}
/* 图库与标记页共用同一套网格。写成 `#grid,.grid` 是因为原来只有 #grid 这个
   ID 规则，标记页用的是 class="grid" —— 少了这条，标记页的图片会塌成单列
   （卡片被拉到整行宽、一张 1190px）。两处必须同一条规则，改一处就一起改。 */
#grid,.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(178px,1fr));gap:11px}
#pager{display:none;align-items:center;justify-content:center;gap:5px;flex-wrap:wrap;
  margin:16px 0 4px;padding-top:12px;border-top:1px solid var(--line)}
#pager .pginfo{color:var(--ink3);font-size:12px;margin-right:8px}
#pager .pginfo b{color:var(--ink);font-weight:600}
#pager .pg{background:var(--panel);border:1px solid var(--line);border-radius:6px;
  min-width:30px;padding:4px 9px;font-size:12.5px;font-family:inherit;color:var(--ink);
  cursor:pointer;transition:.12s}
#pager .pg:hover:not(:disabled){border-color:var(--brand2);color:var(--brand2)}
#pager .pg.cur{background:var(--brand);border-color:var(--brand);color:#fff;font-weight:600}
#pager .pg:disabled{opacity:.38;cursor:default}
#pager .pgdots{color:var(--ink3);padding:0 2px;font-size:12.5px}
#offbar{display:none;position:fixed;left:50%;transform:translateX(-50%);top:12px;z-index:999;
  align-items:center;gap:12px;background:#7c2d12;color:#fff;border-radius:8px;
  padding:9px 14px;font-size:13px;box-shadow:0 6px 22px rgba(0,0,0,.28);max-width:92vw}
#offbar .obtxt{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#offbar .obbtn{background:#fff;color:#7c2d12;border:none;border-radius:6px;
  padding:5px 11px;font-size:12.5px;font-weight:600;cursor:pointer;font-family:inherit;flex:none}
#offbar .obbtn:hover{background:#ffe4d6}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;
transition:.14s;position:relative}
.card:hover{border-color:var(--brand2);box-shadow:0 3px 14px rgba(200,68,47,.13)}
.card img{width:100%;aspect-ratio:1;object-fit:cover;display:block;background:#eef0f3;cursor:zoom-in}
.card .acts{position:absolute;top:6px;right:6px;display:flex;gap:4px;opacity:0;transition:.14s}
.card:hover .acts{opacity:1}
.card .acts button{background:rgba(255,255,255,.95);border:none;border-radius:5px;
padding:3px 7px;font-size:10.5px;cursor:pointer;font-family:inherit;color:var(--ink);font-weight:600}
.card .acts button:hover{background:var(--brand);color:#fff}
.card .tags{display:flex;gap:3px;flex-wrap:wrap;margin-top:4px}
.tg{font-size:9.5px;padding:1px 5px;border-radius:3px;background:#f0f2f5;color:#5a6370;white-space:nowrap}
.tg.th{background:#eef7f1;color:#2f7048}
.tg.lb{background:#fdf1ee;color:#b8402c}
.tg.te{background:#f0f2fa;color:#4a5a9e}
/* 标记按钮：常驻可见，一眼看出标记状态 */
.mk{position:absolute;top:6px;left:6px;z-index:3;width:26px;height:26px;border-radius:50%;
border:none;cursor:pointer;font-size:13px;line-height:1;padding:0;
background:rgba(255,255,255,.92);color:#98a2b0;display:flex;align-items:center;justify-content:center;
box-shadow:0 1px 5px rgba(0,0,0,.16);transition:.13s}
.mk:hover{background:#fff;color:var(--brand);transform:scale(1.1)}
.mk.on{background:var(--brand);color:#fff;box-shadow:0 2px 8px rgba(200,68,47,.42)}
.card.marked{border-color:var(--brand);box-shadow:0 0 0 2px rgba(200,68,47,.16)}
/* ---------- 风格评级（三级）----------
   系统判定与用户标记必须一眼可分：
   · 系统 = 白底 + 彩色虚线描边（暗示"只读、参考"），不可点
   · 用户 = 实色角签（实心=我说了算），可点击循环 绿→黄→红→清除
   · 分歧 = 白底红字 ≠ 角标（系统与我的判定不一致时）
   布局走流内（与 .settags 同款）：卡片下部有无 .settags 高度不定，
   绝对定位贴图片底边会漂移。 */
.gchips{display:flex;gap:3px;align-items:center;flex-wrap:wrap;padding:4px 6px 0}
.gsig,.gusr{font-size:10px;font-weight:650;line-height:1;padding:3px 7px;border-radius:9px;
  display:inline-flex;align-items:center;gap:2px}
.gsig{background:rgba(255,255,255,.9);border:1px dashed var(--gc);color:var(--gc)}
.gusr{background:var(--gc);border:1px solid var(--gc);color:#fff;cursor:pointer}
.gusr:hover{filter:brightness(1.12)}
.gdiff{font-size:10px;font-weight:800;padding:2px 5px;border-radius:8px;background:#fff;
  border:1px solid #de3737;color:#de3737}
/* 用户评级过的卡片：整卡实色描边（与 .card.marked 的红星描边并存，评级色优先） */
.card.ug1{border-color:#1aad5c;box-shadow:0 0 0 2px rgba(26,173,92,.25)}
.card.ug2{border-color:#e8a33d;box-shadow:0 0 0 2px rgba(232,163,61,.25)}
.card.ug0{border-color:#de3737;box-shadow:0 0 0 2px rgba(222,55,55,.25)}
/* 看图器评级栏 */
.vg{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin:2px 0}
.vg .vgl{font-size:12px;font-weight:700;color:var(--ink2)}
.vg .vgn{font-size:11.5px;color:var(--ink3)}
.vgb{background:#fff;border:1.5px solid var(--gc);color:var(--gc);border-radius:7px;
  padding:4px 12px;font-size:12px;font-weight:700;cursor:pointer;font-family:inherit}
.vgb:hover{background:var(--gc);color:#fff}
.vgb.on{background:var(--gc);color:#fff}
.vgx{background:#fff;border:1px solid var(--line);color:var(--ink2);border-radius:7px;
  padding:4px 10px;font-size:11.5px;cursor:pointer;font-family:inherit}
.vgx:hover{border-color:#de3737;color:#de3737}
/* 侧栏评级 chips：复用 .ck 骨架，选中态用各自等级色（--gc）而不是品牌红 */
.ck.gon{background:var(--gc,var(--brand));border-color:var(--gc,var(--brand));color:#fff}
.ck.gon .n{color:rgba(255,255,255,.82)}
.card .i{padding:7px 9px}
.card .f{font-size:11.5px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card .m{color:var(--ink3);font-size:10px;display:flex;justify-content:space-between;margin-top:1px}
/* 统计视图 */
.sec{background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:18px 20px;margin-bottom:16px}
.sec h2{font-size:16px;font-weight:650;margin-bottom:3px}
.sec p{color:var(--ink2);font-size:12.5px;margin-bottom:14px}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(133px,1fr));gap:11px}
.k{background:#fafbfd;border:1px solid var(--line);border-radius:9px;padding:12px 14px}
.k .v{font-size:23px;font-weight:700;letter-spacing:-1px;line-height:1.15}
.k .v small{font-size:12px;font-weight:500;color:var(--ink3)}
.k .l{font-size:11.5px;color:var(--ink2);margin-top:2px}
.k.hi{background:linear-gradient(140deg,#fdf1ee,#fff);border-color:#f0cfc6}
.k.hi .v{color:var(--brand)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:15px;align-items:start}
@media(max-width:1000px){.grid2{grid-template-columns:1fr}}
/* 各维度分布：三列等高对齐。
   align-items:start 是关键 —— 默认 grid 会把同一行的盒子拉到等高，
   短的那个就在底部留一大片空白（原先「技法维度」画布 208px 被拉到 440px，
   空出 232px，看起来就是「内容空白还占地方」）。 */
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:15px;align-items:start}
@media(max-width:1200px){.grid3{grid-template-columns:1fr 1fr}}
@media(max-width:800px){.grid3{grid-template-columns:1fr}}
.box{background:#fafbfd;border:1px solid var(--line);border-radius:9px;padding:14px}
.box h3{font-size:13px;font-weight:650;margin-bottom:9px}
.rank{display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:12px}
.rank .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rank .bar{width:80px;height:5px;border-radius:3px;background:var(--brand);opacity:.7;flex:none}
.rank .vv{width:42px;text-align:right;color:var(--ink2);font-size:11px;flex:none}
/* 统计页里的排名行可点：点一下加入筛选条件（数字要再点「更新统计」才动） */
.rank.clickable{cursor:pointer;border-radius:5px;padding:1px 5px;margin-left:-5px;
  transition:.12s}
.rank.clickable:hover{background:#f4f6f9}
.rank.clickable.on{background:#fdf1ee}
.rank.clickable.on .nm{color:var(--brand);font-weight:600}
.rank.clickable.on .bar{opacity:1}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;font-weight:600;color:var(--ink2);font-size:11px;padding:7px 9px;
border-bottom:1px solid var(--line);white-space:nowrap;text-transform:uppercase;letter-spacing:.3px}
td{padding:7px 9px;border-bottom:1px solid #f1f4f8}
.num{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.warn{background:#fff8ec;border:1px solid #f0dcb4;border-left:4px solid #c98a12;
border-radius:8px;padding:12px 15px;font-size:12.5px;color:#6b5320;margin-bottom:16px}
.warn b{color:#8a5f00}
/* 查看器：左边图、右边信息。
   原先是一列居中把图和信息摞在一起（justify-content:center）。那个写法有个
   说不清的后果：只要这一列高于视口，居中就会把溢出**均分到上下两侧**，而
   #viewer 又不是滚动容器 —— 上面那截图片落在哪儿，就没有任何一个滚动条
   能到达。切图时图片高度每换一张就变一次、信息块长短也跟着变，整列的重心
   于是每次都在动：用户看到的是「显示的区域一直在往上跑、回不来」。
   现在把图放进一个尺寸只由视口决定的舞台（与信息长短无关），信息挪到右侧
   自己滚 —— 图不会再被信息挤走，任何多余内容都滚得到。 */
#viewer{position:fixed;inset:0;background:rgba(20,22,26,.94);display:none;z-index:99;
padding:22px 24px;flex-direction:row;align-items:stretch;gap:18px;overflow:hidden}
#viewer.on{display:flex}
/* 舞台：flex:1 + min-width/height:0 —— 图片怎么大都撑不破它，
   max-width/height 又都是 100%，于是图片永远完整落在视口里，不会被裁。 */
.vstage{position:relative;flex:1 1 auto;min-width:0;min-height:0;
display:flex;align-items:center;justify-content:center}
#viewer img{max-width:100%;max-height:100%;border-radius:6px;box-shadow:0 10px 50px rgba(0,0,0,.5)}
/* 信息栏：独立滚动，切图时回到顶部（否则上一张滚到一半的位置会带到下一张） */
.vside{flex:0 0 330px;min-width:0;max-height:100%;overflow-y:auto;overscroll-behavior:contain;
align-self:stretch;display:flex;flex-direction:column;align-items:flex-start;
text-align:left;padding:2px 6px 10px 2px}
#viewer .c{color:#fff;font-size:13px;line-height:1.7}
#viewer .px{color:#9aa3ae;font-size:11px;word-break:break-all;margin-top:3px}
#viewer .kv{width:100%;border-collapse:collapse;font-size:11.5px;margin-top:9px}
#viewer .kv td{padding:2px 0;border:0;color:#cfd6de;vertical-align:top}
#viewer .kv td.k{color:#8b95a3;white-space:nowrap;padding-right:9px;width:52px}
#viewer .kv td .sel{user-select:all}
/* 大图里的「配方」：风格 + LoRA（带强度）。这是要照抄回软件的东西，
   所以单独一段、可选中复制，而不是混在路径那行灰字里。 */
#viewer .rec{color:#cfd6de;font-size:11.5px;margin-top:9px;line-height:1.75}
#viewer .rec b{color:#fff;font-weight:600}
#viewer .rec .rs{color:#8fd0a8}
#viewer .rec .rl{color:#f0b98a}
#viewer .rec .rn{color:#7d8794}
#viewer .x{position:absolute;top:14px;right:20px;color:#fff;font-size:29px;cursor:pointer;opacity:.8;z-index:3}
#viewer .nv{position:absolute;top:50%;transform:translateY(-50%);color:#fff;font-size:33px;
cursor:pointer;padding:18px;opacity:.55;user-select:none;z-index:2}
#viewer .nv:hover{opacity:1}
#viewer .nv.l{left:2px}
#viewer .nv.r{right:2px}
#viewer .do{display:flex;flex-wrap:wrap;gap:7px;margin-top:12px;width:100%}
#viewer .do button{background:rgba(255,255,255,.14);color:#fff;border:1px solid rgba(255,255,255,.28);
border-radius:6px;padding:5px 12px;font-size:12px;cursor:pointer;font-family:inherit}
#viewer .do button:hover{background:rgba(255,255,255,.26)}
/* 大图打不开时，在信息栏里给一句能读的话 + 一个重试按钮，
   而不是让用户对着一个灰块猜发生了什么。 */
#viewer .verr{display:none;width:100%;margin-top:10px;gap:8px;align-items:center;
background:rgba(200,68,47,.22);border:1px solid rgba(240,150,130,.45);
border-radius:7px;padding:7px 10px;font-size:11.5px;color:#ffd9d0;line-height:1.5}
#viewer .verr.on{display:flex}
#viewer .verr .t{flex:1;word-break:break-all}
#viewer .verr button{flex:none;background:rgba(255,255,255,.18);color:#fff;
border:1px solid rgba(255,255,255,.3);border-radius:5px;padding:3px 10px;
font-size:11.5px;cursor:pointer;font-family:inherit}
/* 窄窗口：改成上下排。这里**不能**再用居中 —— 一列 + 居中 + 溢出就是上面
   那个老问题；所以明确 flex-start，并让整层自己滚。 */
@media (max-width:900px){
  #viewer{flex-direction:column;overflow-y:auto;justify-content:flex-start;padding:16px 14px}
  .vstage{flex:0 0 auto;width:100%;height:auto}
  #viewer img{max-height:58vh}
  .vside{flex:0 0 auto;width:100%;max-height:none;overflow:visible}
}
.hidden{display:none}
#empty{color:var(--ink3);text-align:center;padding:56px 20px;font-size:13px}
/* 标记页 */
.mkbar{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:12px 15px;margin-bottom:15px;display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.mkbar .big{font-size:19px;font-weight:700;color:var(--brand)}
.mkbar .lbl{font-size:12px;color:var(--ink2)}
.mkbar button{background:#fff;border:1px solid var(--line);border-radius:6px;
padding:5px 12px;font-size:12px;cursor:pointer;font-family:inherit;color:var(--ink2)}
.mkbar button:hover{border-color:var(--brand2);color:var(--brand)}
.mkbar button.pri{background:var(--brand);border-color:var(--brand);color:#fff}
.mkbar button.pri:hover{background:#b03a26;color:#fff}
.mkbar .sp{margin-left:auto}
/* 夹标签上的「待定位」小签。存在的理由：夹标签原来只写记录条数，
   记录还在、图却打不开时这个数字一点不变，用户完全看不出来（这次的症状）。 */
.settab .pd{font-style:normal;font-weight:600;font-size:10px;color:#b3453a;
background:rgba(200,68,47,.12);border-radius:8px;padding:0 5px;margin-left:4px}
/* 待定位条目：一条记录一行，写清楚它原本指向哪、能归到哪 */
.pdcard{border:1px solid var(--line);border-left:3px solid #d9a24b;border-radius:8px;
padding:8px 11px;margin:7px 0;background:#fffdf7}
.pdpath{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:#5a6370;
word-break:break-all;line-height:1.55}
.pdcand{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.pdpick{background:#eef4fb;border:1px solid #cfe0f2;color:#2f6fb5;border-radius:6px;
padding:3px 9px;font-size:11px;cursor:pointer;font-family:inherit;max-width:100%;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pdpick:hover{background:#e0ebf8;border-color:#a9c8e8}
.pdnone{font-size:11px;color:var(--ink3)}
.pdact{margin-top:6px}
.pdact button{background:#fff;border:1px solid var(--line);border-radius:6px;
padding:2px 9px;font-size:11px;cursor:pointer;font-family:inherit;color:var(--ink2)}
.pdact button:hover{border-color:#d9a24b;color:#a1741f}
.mklib{background:#fafbfd;border:1px solid var(--line);border-radius:9px;padding:14px;margin-bottom:14px}
.mklib h3{font-size:13px;font-weight:650;margin-bottom:9px;display:flex;gap:7px;align-items:center}
.mknote{display:flex;gap:7px;align-items:center;margin-bottom:7px}
.mknote .n{font-size:12px;font-weight:600;width:190px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mknote .bar{height:7px;border-radius:4px;background:var(--brand);opacity:.68}
.mknote .v{font-size:11.5px;color:var(--ink2);flex:none}
.mknote .cnt{font-size:11px;color:var(--ink3);flex:none;width:44px;text-align:right}
.recipe{background:#fff;border:1px dashed #d9dee6;border-radius:8px;padding:10px 12px;
margin-bottom:7px;font-size:12px;line-height:1.75}
.recipe b{color:var(--brand)}
.recipe code{background:#f4f6f9;border-radius:3px;padding:1px 5px;font-size:11.5px;
font-family:Consolas,Monaco,monospace;color:#3c4450}
.recipe .bar{display:flex;gap:5px;flex-wrap:wrap;margin-top:6px}
/* ---------- 标记页：压文字 UI 的高度，图片尺寸不动 ----------
   这一页信息密度本来就高（夹切换 + 风格画像 + 配方 + 图片四段），
   文字块占掉的高度直接从图片可见面积里扣。实测改前整页 4138px，
   其中两段文字区（画像 786 + 配方 1641）占了一大半，而配方里每张
   缩略图只有 52px —— 比例是反的。
   只动字号/间距/内边距，不动任何图片尺寸（卡片反而从 158px 提到 178px，
   和图库一致）。 */
#marksView .sec{padding:12px 14px;margin-bottom:10px}
#marksView .sec h2{font-size:14px;margin-bottom:2px}
#marksView .sec p{font-size:11.5px;margin-bottom:8px}
#marksView .mklib{padding:9px 11px;margin-bottom:0}
#marksView .mklib h3{font-size:12px;margin-bottom:5px}
#marksView .mknote{margin-bottom:2px;gap:6px}
#marksView .mknote .n{font-size:11.5px;width:150px}
#marksView .mknote .bar{height:5px}
#marksView .mknote .v,#marksView .mknote .cnt{font-size:10.5px}
#marksView .recipe{padding:6px 9px;margin-bottom:5px;font-size:11.5px;line-height:1.5}
#marksView .recipe code{font-size:10.5px;padding:0 4px}
/* 配方的三行文字并成一行内联：每行一个 `<div>` 时，8 条配方就是 16 行文字，
   而每条配方的缩略图才 52px —— 文字挤掉的高度远多于图。 */
#marksView .recipe .rc{display:inline;color:var(--ink3);margin-right:8px;font-size:10.5px}
#marksView .recipe .rc code{margin-right:2px;display:inline-block}
#marksView .recipe .bar{margin-top:4px}
#marksView .mkbar{padding:7px 11px;gap:7px}
#marksView .mkbar button{padding:3px 9px;font-size:11.5px}
#marksView .settab{padding:3px 8px;font-size:11.5px}
/* 可折叠的分析区：<details class="mkcoll">。
   summary 一行装下标题 + 折叠时的摘要，展开箭头由 details 自带。
   .sec 的 padding 靠 summary/内容自己带，避免折叠时还留一圈空白。 */
#marksView .mkcoll{padding:0;overflow:hidden}
#marksView .mkcoll>summary{padding:10px 14px;cursor:pointer;list-style:none;
  display:flex;align-items:baseline;gap:9px;flex-wrap:wrap;transition:.12s}
#marksView .mkcoll>summary::-webkit-details-marker{display:none}
#marksView .mkcoll>summary::before{content:'▸';color:var(--ink3);font-size:11px;flex:none}
#marksView .mkcoll[open]>summary::before{content:'▾'}
#marksView .mkcoll>summary:hover{background:#fafbfd}
#marksView .mkcoll>summary h2{margin:0}
#marksView .mkcoll .msum{color:var(--ink3);font-size:11.5px}
#marksView .mkcoll[open] .msum{display:none}
#marksView .mkcoll>*:not(summary){margin-left:14px;margin-right:14px}
#marksView .mkcoll>*:last-child{margin-bottom:12px}
.mgtools label{display:block;margin:8px 0}
.mgtools select{display:block;width:100%;padding:7px;border:1px solid var(--line);border-radius:6px}
.mgtools button,.mgfield,#relatedPanel button,#relatedPanel select{font:inherit;padding:6px 9px;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--ink);cursor:pointer}
.mgtools p{font-size:11px;color:var(--ink2);margin:8px 0}
.mgdialog{margin:auto;padding:22px;border:1px solid var(--line);border-radius:10px;max-width:90vw}
.mgdialog p{margin-bottom:16px}.mgdialog button{padding:8px 14px;margin:6px;font:inherit}.mgdialog input{display:block;padding:8px;margin:8px 0}
.mgfield{margin:3px;color:var(--accent)}
.mgselect{position:absolute;left:48px;top:8px;z-index:6;background:#fff;padding:3px 6px;border-radius:6px}
#relatedPanel{display:none;position:fixed;right:0;top:var(--topbar,68px);bottom:0;width:calc((100vw - 285px)/2);z-index:110;overflow:auto;background:var(--panel);padding:16px;border-left:1px solid var(--line);box-shadow:-6px 0 24px #0002}
#relatedPanel.on{display:block}
#relatedPanel header{display:flex;align-items:center;justify-content:space-between;gap:10px}
#relatedPanel p{margin:8px 0}
#relatedGrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:12px}
.relatedCard{border:1px solid var(--line);border-radius:8px;padding:8px;overflow:hidden}
.relatedCard p{font-size:11px;overflow-wrap:anywhere}
.relatedImage{width:100%;margin-top:5px}.relatedImage img{width:100%;height:150px;object-fit:contain}
#relatedPreview img{width:100%;max-height:40vh;object-fit:contain;background:#eee}
#relatedPanel footer{position:sticky;bottom:-16px;background:#fff;padding:12px 0;border-top:1px solid var(--line)}
#relatedPanel footer button{margin:3px}#relatedPager{padding:12px 0}
@media(max-width:800px){#relatedPanel{width:100%}}
</style></head><body>
<div id="top">
  <div><h1>风格图鉴</h1>
  <div class="st" id="stamp"></div></div>
  <div class="tabs">
    <button class="tb on" data-v="browse">图库</button>
    <button class="tb" data-v="marks">收藏 <span id="mkbadge" style="display:none">0</span></button>
    <button class="tb" data-v="stats">统计</button>
    <button class="tb" onclick="refreshNow()" title="重新扫描目录，载入新生成的图">↻ 刷新</button>
    <button class="tb" onclick="openSources()">作品目录</button>
  </div>
</div>
<div class="wrap">
  <div id="facet">
    <div class="fg" style="padding-top:4px">
      <h3>已选条件 <span id="selcnt">0</span></h3>
      <div id="selfull" style="font-size:11.5px;color:var(--ink3)">未选——显示全部图片</div>
    </div>
    <div id="gradefacet"></div>
    <div id="primary"></div>
    <div id="facets"></div>
  </div>
  <div id="main">
    <div id="selbar">
      <div class="row">
        <span class="lbl">叠加条件 →</span>
        <span id="stagbox"></span>
        <button onclick="clearAll()">清空</button>
        <button onclick="openInCompare()" title="把当前命中的图片送到「风格对比」模块查看">⊞ 对比查看</button>
        <span class="res">命中 <b id="hitn">0</b> 张</span>
      </div>
    </div>
    <div id="browseView">
      <div id="grid"></div>
      <div id="pager"></div>
      <div id="empty">← 左侧点选标签开始浏览</div>
    </div>
    <div id="marksView" class="hidden"></div>
    <div id="statsView" class="hidden"></div>
  </div>
</div>
<div id="viewer">
  <span class="x" onclick="closeV()">×</span>
  <div class="vstage">
    <span class="nv l" onclick="step(-1)">‹</span>
    <span class="nv r" onclick="step(1)">›</span>
    <img id="vimg" onload="imgOk(this)" onerror="imgErr(this)">
  </div>
  <aside class="vside" id="vside">
    <div class="c" id="vc"></div>
    <div class="verr" id="verr"><span class="t"></span><button onclick="retryVImage()">重试</button></div>
    <div class="vg" id="vgrade"></div>
    <table class="kv" id="vinfo"></table>
    <div class="rec" id="vrec"></div>
    <div class="px" id="vpx"></div>
    <div class="do"><button onclick="openFolder()">在文件夹中打开</button>
    <button onclick="copyPath()">复制路径</button>
    <button onclick="renameCurrent()" title="在当前文件夹内给这张图改名（收藏与评级会跟着走）">✎ 重命名</button>
    <button id="vmk" onclick="markCurrent()">☆ 收藏</button>
    <button id="vcp" onclick="copyRecipe()" title="复制成软件「风格／LoRA 预设」能直接用的 JSON">复制配方 JSON</button>
    <button id="vimp" onclick="importToS()" title="把这张图的风格+LoRA 直接存成工作台预设（可自定义命名）">⇢ 导入工作台</button>
    </div>
  </aside>
</div>
<script src="/ui_lifecycle.js"></script>
<script>
let D=__DATA__;
/* 筛选面板分两级 ——
   主分类：文件夹 / 批次（时间）。这两维回答「图是从哪来的、哪一批做的」，
   是翻这个图鉴时最先要用的定位手段，所以给缩略图按钮、排在最上面。
   两维都是**两级**的：
     文件夹：先选大文件夹（c / D / E / F …）→ 列出它下面各子文件夹的
             真实相对路径（如 D 下的 T/926266f5）
     批次：  先选日期（17 天）→ 列出当天各批次
   平铺的话是 200+ 个文件夹 + 99 个批次，第一屏根本看不完，等于没分类。
   次要维度：风格库 / 风格词条 / 技法 / LoRA / 底模，语义分类，收在下面。
   「主题」是旧版按预设名正则匹配出来的弱维度，默认收起放最后。 */
const SECONDARY_DEF=[
  ['library','风格库'],['style','风格词条'],
  ['tech','技法维度'],['lora','LoRA'],['model','底模'],
  ['theme','主题（旧·弱）'],['preset','方案']
];
const LABEL={folder:'文件夹',library:'风格库',style:'风格词条',
tech:'技法',lora:'LoRA',model:'底模',run:'批次',
theme:'主题',preset:'方案',bigfolder:'大文件夹',day:'日期'};
/* 默认收起的维度：主题/方案是次要标签，避免挤占主分类视觉位置 */
const COLLAPSED_DEF=new Set(['theme','preset']);
let sel={};   // {dim:Set(values)}
let hit=[], vi=0, expanded={};
// 标记页大图的翻页范围：登记该夹的 uid 列表。openVByUid 用它限制 ‹ › 的跨度，
// 避免在标记页点开后翻到全量图鉴里去（历史 bug）。离开标记页时清空。
let _scopeUids=[], _scopeSetId='';
let pgOpen={};   // {dim:true} —— 主分类（文件夹/批次）是否展开全部缩略按钮
// 二维展开状态：{lib:Set(库名)} Shape=null 时为对象；简化为 plain object
let openLib={};   // {libName: true} —— 风格库是否展开了子选项
/* 标记页两段分析区的展开状态。默认折叠：这两段是"想看才看"的参考信息，
   摊开时占掉的高度比图片区还多（实测画像 587px + 配方 1095px，而图片区才 1968px）。
   renderMarks 每次都重建 innerHTML，所以状态得存在这里，不能靠 DOM 记住。 */
let mkOpen={style:false, recipe:false};
function mkToggle(key,el){ mkOpen[key]=!!el.open; }

/* 把数据渲染到页头（刷新数据后要重写） */
function applyData(){
  const st=D.stats||{};
  const el=document.getElementById('stamp');
  if(el){
    const total=(D.images||[]).length||1;
    const styled=st.styled_images??0;
    const pct=(styled/total*100).toFixed(1);
    el.innerHTML=
      // 显示图库里真实的图片数 —— 原先写的是 stats.resolved（3796 条生成记录），
      // 比图库实际能翻的 7018 张少一半，看的人会以为图丢了。
      `图库 <b>${total}</b> 张 · ${st.distinct_styles??0} 个风格词条 · `+
      `${st.distinct_libs??0} 个风格库 · `+
      // 内容指纹：稳定、可核对。原先只写「生成于 <时间>」，那个时钟串每次重建
      // 都变、静态快照里更永远停在旧时刻 —— 用户无从确认「我看的这版对应哪份
      // 数据」，就是「每次版本都对不上」的来源。指纹由图片集合算出，同数据同码。
      `<span class="bid" title="内容指纹：由当前图片集合算出。数据不变则此码不变；图片增删即变——用它核对页面与数据是否对得上">版 ${D.build_id||'—'}</span> · `+
      `生成于 ${D.generated_at||''}`;
    // 归因修复进度 —— 让「风格定义组合」的恢复情况可见，别只写在日志里
    const fixed=(st.png_recovered??0)+(st.png_orphan_recovered??0)+(st.manifest_recovered??0);
    if(fixed>0){
      el.innerHTML+=
        `<br><span class="fixbar">风格归因已修复 <b>${fixed}</b> 张`+
        `（PNG 现场 ${(st.png_recovered??0)+(st.png_orphan_recovered??0)} · 轮次清单 ${st.manifest_recovered??0}）`+
        ` · 有风格组合 <b>${styled}</b>/${total} = <b>${pct}%</b></span>`;
    }
    updateStampMarks();
  }
}
/* 页头的「标记 N 张」芯片。
   为什么要放在版本旁边：用户会拿「标记张数」和「版本」一起核对
   「这一版对应的是不是我当前这堆标记」。这里显示的是**真正能在图鉴里
   看到、能翻到的标记张数**（去重后的图片数），而不是原始路径条数 ——
   后者可能因 uid/path 双写而虚高（历史上出现过 42 条其实只有 21 张的情况），
   正是「数量对不上」的来源之一。 */
function updateStampMarks(){
  const el=document.getElementById('stamp');
  if(!el) return;
  // applyData() 在顶层就会先跑一次，那时 SETS 还没声明（let 的 TDZ）——
  // 连 typeof SETS 都会抛 ReferenceError，只能 try/catch 兜住。
  let sets;
  try{ sets=SETS; }catch(e){ return; }
  if(!sets||!sets.length) return;
  let chip=document.getElementById('stampMarks');
  if(!chip){
    chip=document.createElement('span');
    chip.id='stampMarks';
    chip.className='bidm';
    el.appendChild(document.createTextNode(' · '));
    el.appendChild(chip);
  }
  const seen=new Set();          // 去重：同一张图即使多处登记也只算一次
  let raw=0, dead=0;
  sets.forEach(s=>{
    raw+=s.paths.size;
    const r=resolveSet(s);
    r.live.forEach(im=>seen.add(im.uid));
    dead+=r.dead.length;
  });
  const n=seen.size;
  // 「登记条数 - 可显示张数」里混着两种东西：同一张图的重复登记，和原图已
  // 不在记录路径上的（待定位）。分开报 —— 把后者说成「重复登记」是在
  // 用一句话盖掉一条真实的数据丢失。
  const dup=Math.max(0, raw-n-dead);
  chip.textContent=`收藏 ${n} 张`+(dead>0?` + ${dead} 待定位`:'')+
    (dup>0?`（含重复登记 ${dup}）`:'')+(sets.length>1?` · ${sets.length} 个夹`:'');
  chip.title=`可显示 ${n} 张（去重）`+
    (dead>0?`，另有 ${dead} 条记录的原图已不在记录的路径上（收藏页「待定位」可逐条归位）`:'')+
    `。原始登记条数 ${raw} 条`+
    (dup>0?`，其中 ${dup} 条是同一张图的重复登记（不同夹或 uid/path 双写）。`:'。');
}
applyData();

const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fsize=b=>b>1048576?(b/1048576).toFixed(1)+'MB':(b/1048576*1024).toFixed(0)+'KB';

/* ---------- 标签面板 ---------- */
/* 通用：一行 chips（可展开） */
function chipRow(dim, rows, limit){
  const exp=expanded[dim];
  const show=exp?rows:rows.slice(0,limit||14);
  return `<div class="chips">${show.map(([v,n])=>
    `<span class="ck${isOn(dim,v)?' on':''}" onclick="toggle('${dim}',${JSON.stringify(String(v)).replace(/"/g,'&quot;')})"
      title="${esc(v)}">${esc(short(v))}<span class="n">${n}</span></span>`).join('')}</div>`+
    (rows.length>(limit||14)?`<span class="more" onclick="exp('${dim}')">${exp?'收起':'展开全部 '+rows.length+' 类'}</span>`:'');
}

/* ---------- 主分类：文件夹 / 批次 → 缩略式按钮 ----------
   用缩略图而不是纯文字 chip，是因为这两维的值本身不表意：
   「c/E」「7155c6f7」看名字不知道是什么，看一眼封面图就认出来了。
   批次按钮额外带日期（取自该批图片文件的修改时间）——
   数据里没有生成时间戳，文件 mtime 是最接近的可用代理。 */
/* ---------- 主分类：两级缩略按钮 ----------
   ① 文件夹：先选大文件夹（c / D / 幻想画风… / T），右边立刻是它的全部图；
      选中后下面才列出它下面的子文件夹，再点一层才是精确到某一个目录。
   ② 批次：先选日期（17 天），选中后下面才列出当天各批次。

   为什么改成两级：平铺是 185 个文件夹 + 99 个批次，第一屏根本看不完 ——
   那时候的「分类」只是把数据排成一片，没有帮人缩小范围。
   两级之后第一层各 4 个和 17 个，一眼能看完。
   按钮一律带缩略图：c / D / 09-29 这些值本身不表意，看一眼图就认出来了。 */
function selectedOne(dim){
  const s=sel[dim];
  if(!s||s.size!==1) return null;
  return [...s][0];
}
function renderPrimary(){
  const el=$('#primary');
  if(!el) return;
  el.innerHTML = mgPrimary();
}
/* ① 文件夹：大文件夹 + （选中后）它的子文件夹 */
function bigFolderBlock(){
  const rows=D.facets.bigfolder||[];
  if(!rows.length) return '';
  const cur=selectedOne('bigfolder');
  let sub='';
  if(cur){
    // 列出该大文件夹下的全部子文件夹（不受当前筛选结果影响 —— 这是"要选什么"，
    // 不是"筛出来什么"，用 hit 过滤会让选项自己越点越少）。
    const kids=(D.facets.folder||[]).filter(([v])=>v===cur||v.startsWith(cur+'/'));
    const subOn=sel.folder?sel.folder.size:0;
    sub=`<div class="subhead">
        <span>「${esc(cur)}」下的子文件夹 <b>${kids.length}</b> 个</span>
        ${subOn?`<span class="clr" onclick="clearDim('folder')">清空子项</span>`:''}
      </div>
      <div class="pgrid sub">${kids.map(([v,n])=>pbHtml('folder',v,n)).join('')}</div>`;
  }
  return `<div class="fg">
    <h3>① 文件夹 <span>${rows.length} 个大类</span></h3>
    <div style="color:var(--ink3);font-size:10.5px;margin:-5px 0 7px">
      先选大类；选中后列出它下面的目录（磁盘上的真实层级）</div>
    <div class="pgrid">${rows.map(([v,n])=>pbHtml('bigfolder',v,n)).join('')}</div>
    ${sub}
  </div>`;
}
/* ② 批次：日期 + （选中后）当天的各批次 */
function dayBlock(){
  const rows=D.facets.day||[];
  if(!rows.length) return '';
  const cur=selectedOne('day');
  let sub='';
  if(cur){
    const kids=(D.facets.run||[]).filter(([v])=>(D.run_time||{})[v]===cur);
    const subOn=sel.run?sel.run.size:0;
    sub=`<div class="subhead">
        <span>「${esc(cur)}」当天 <b>${kids.length}</b> 个批次</span>
        ${subOn?`<span class="clr" onclick="clearDim('run')">清空批次</span>`:''}
      </div>
      <div class="pgrid sub">${kids.map(([v,n])=>pbHtml('run',v,n)).join('')}</div>`;
  }
  return `<div class="fg">
    <h3>② 批次 · 时间 <span>${rows.length} 天</span></h3>
    <div style="color:var(--ink3);font-size:10.5px;margin:-5px 0 7px">
      按天归并（原来 99 个批次平铺）；选中后下面出现当天各批次</div>
    <div class="pgrid">${rows.map(([v,n])=>pbHtml('day',v,n)).join('')}</div>
    ${sub}
  </div>`;
}
function clearDim(dim){
  delete sel[dim];
  apply();
}
function pbHtml(dim,v,n){
  const on=isOn(dim,v);
  const src=coverSrc(coverOf(dim,v));
  const lab=pbLabel(dim,v);
  return `<button class="pb${on?' on':''}"
    title="${esc(v||'（无日期）')}&#10;${n} 张"
    onclick="toggle('${dim}',${JSON.stringify(String(v)).replace(/"/g,'&quot;')})">
    ${src?`<img loading="lazy" src="${src}" alt="">`
         :`<span class="noimg">无缩略图</span>`}
    <span class="ct">${n}</span>
    <span class="lb">${esc(lab)}</span></button>`;
}
function pbLabel(dim,v){
  if(dim==='bigfolder') return String(v);
  if(dim==='day') return v?String(v):'无日期';
  if(dim==='run') return runLabel(v);
  return folderLabel(v);
}
function folderLabel(v){
  const s=String(v);
  const i=s.indexOf('/');
  /* 二级列表只在选中的大文件夹之下出现，第一段（D / c / E…）已经由上面那排
     按钮表达过了。这里再重复一遍会把仅有的 58px 宽度吃掉 —— 剥掉它，
     让「T/926266f5」这种真正用来认路的段露出来。
     走的也是磁盘上的真实层级：用户是照着资源管理器找图的。
     过长时保留头尾（中间省略）：头认根，尾认批次；完整值在 title 里。 */
  const rel=i<0?s:s.slice(i+1);
  if(rel.length<=14) return rel;
  return rel.slice(0,6)+'…'+rel.slice(-6);
}
function runLabel(v){
  const s=String(v);
  if(s==='_UNNAMED'||!s) return '未命名';
  const t=(D.run_time||{})[s]||'';
  return t?t:s.slice(0,8);
}
/* 批次在列表里要「日期 + 短 id」：同一天常有好几批，
   只给日期的话几行长得一模一样，分不出点的是哪一个。 */
function runRowLabel(v){
  const s=String(v);
  if(s==='_UNNAMED'||!s) return '未命名（无批次号）';
  const t=(D.run_time||{})[s]||'';
  return (t?t+' · ':'')+s.slice(0,8);
}

function renderFacets(){
  renderGradeFacet();   // 评级面板（#gradefacet，独立写自己的容器）
  renderPrimary();
  $('#facets').innerHTML=
    `<div class="sec2"><span class="tt">次要维度 <em>风格 / 技法 / 模型</em></span>`
    +`<span class="ln"></span></div>`
    +SECONDARY_DEF.map(([dim,title])=>{
    const rows=D.facets[dim]||[];
    if(!rows.length) return '';

    // ---- 风格库：两级联动。库本身可点选；展开后显示它下面的风格词条子选项 ----
    if(dim==='library'){
      const exp=expanded.library;
      const show=exp?rows:rows.slice(0,10);
      return `<div class="fg"><h3>${title} <span>${rows.length} 类</span>
        <i class="hint" title="风格词条是风格库的子选项：展开库可直接选词条">子选项 ▾</i></h3>
        ${show.map(([lib,n])=>{
          const kids=(D.lib_children&&D.lib_children[lib])||[];
          const open=!!openLib[lib];
          const onLib=isOn('library',lib);
          const kidsHtml=(open&&kids.length)?
            `<div class="subchips">${kids.map(s=>
              `<span class="ck sm${isOn('style',s)?' on':''}"
                onclick="event.stopPropagation();toggle('style',${JSON.stringify(String(s)).replace(/"/g,'&quot;')})"
                title="${esc(s)}">${esc(short(s))}</span>`).join('')}</div>`:'';
          return `<div class="librow">
            <div class="libhead">
              <span class="ck${onLib?' on':''}" style="flex:1"
                onclick="toggle('library',${JSON.stringify(String(lib)).replace(/"/g,'&quot;')})"
                title="${esc(lib)}">${esc(short(lib))}<span class="n">${n}</span></span>
              ${kids.length?`<span class="tw" onclick="event.stopPropagation();toggleLibOpen(${JSON.stringify(lib).replace(/"/g,'&quot;')})"
                title="展开/收起该风格库下的风格词条子选项">${open?'▾':'▸'}${kids.length}</span>`:''}
            </div>${kidsHtml}</div>`;
        }).join('')}
        ${rows.length>10?`<span class="more" onclick="exp('library')">${exp?'收起':'展开全部 '+rows.length+' 类'}</span>`:''}
      </div>`;
    }

    // ---- 其余维度：默认 14 条；主题/方案默认收起且只给 6 条 ----
    const collapsed=COLLAPSED_DEF.has(dim)&&!expanded[dim];
    const lim=collapsed?6:14;
    return `<div class="fg"><h3>${title} <span>${rows.length} 类</span></h3>
      ${chipRow(dim,rows,lim)}</div>`;
  }).join('');
}
function toggleLibOpen(lib){openLib[lib]=!openLib[lib];renderFacets();}
function short(v){
  const s=String(v);
  // 方案名压缩：20260920-动漫服装-01-甜彩线描 → 动漫服装-01-甜彩线描
  let t=s.replace(/^\d{8}-/,'').replace(/^ComfyBatch-V2[\\/]/,'');
  return t.length>22?t.slice(0,21)+'…':t;
}
function isOn(dim,v){return sel[dim]&&sel[dim].has(String(v));}
function exp(dim){expanded[dim]=!expanded[dim];renderFacets();}
function toggle(dim,v){
  v=String(v);
  sel[dim]=sel[dim]||new Set();
  if(sel[dim].has(v)) sel[dim].delete(v); else sel[dim].add(v);
  if(!sel[dim].size) delete sel[dim];
  apply();
}

/* ---------- 标记（收藏）系统 · 多套具名标记夹 ----------
   双写：localStorage 即时生效 + 后端 _marks.json 落盘（跨浏览器不丢）。

   数据模型（v2）：一条图可同时属于**多个**标记夹，用来应对不同需求
   （如「待锐化」「精选」「待重做」各一个夹，互不干扰、可分别导出）。

     SETS = [{id, name, paths:Set<uid>, note:{uid:备注}}, ...]
     ACTIVE = 当前激活夹 id（新标记默认落进它）

   后端 _marks.json 存 {sets:[{id,name,paths:[],note:{}}], active}，
   旧版单套格式 {paths,note} 在后端自动迁移成名为「默认」的夹。

   标识用 uid = `<绝对路径>#<批次>#<序号>`。为什么要这么麻烦：
   同一张图在磁盘上可能有多个副本（批次目录被整体搬家/复制过），
   光靠路径会把跨批次的同名图混成一张。但 uid 有个副作用 ——
   图鉴一旦重建，run/index 推断结果可能变化（记录补齐、目录改名），
   uid 随之漂移，用户的标记就会「凭空消失」。
   所以匹配做成双通道：uid 精确命中 + path 兜底命中。 */
const DEFAULT_SET_NAME='默认';
let SETS=[];                // [{id,name,paths:Set,note:{},color:int}]
let ACTIVE='';              // 当前夹 id
let _colorSeq=0;            // 颜色分配游标（按创建顺序）
let serverOK=true;          // 后端不可用（如直开静态 HTML）时降级为纯本地
const LS_KEY='style_atlas_marks_v2';
const LS_KEY_OLD='style_atlas_marks_v1';
const COLORS=['#e05a4f','#2f7fd4','#17a673','#c98a12','#8b5cf6','#0f9bb5','#d9488b'];

/* 图片地址：走服务时用 /img（浏览器不拦本地图）；直开静态 HTML 时退回 file:///。
   协议是 file: 说明没经过 HTTP 服务，此时 /img 必然 404。 */
let useServer=true;

/* 把磁盘绝对路径转成可用的 <img src> */
function imgSrc(p){
  return useServer ? `/img?p=${encodeURIComponent(p)}` : 'file:///'+p;
}

/* ---------- 缩略图 ----------
   网格原先直接引原图。原图是 ComfyUI 直出的 PNG，单张 2-12MB，全库
   7026 张合计 15GB；一页几十张同时解码时浏览器会放弃一部分，表现就是
   「有些图显示不出来」（灰块）。缩略图把每张压到宽 420px 的 webp
   （约 26KB，缩小约 85 倍），网格只取缩略图；点开大图仍读原图，
   清晰度不受影响。

   缩略图由 tools/build_thumbs.py 预生成，哈希是路径的 sha1 前 16 位，
   与 Python 端 thumb_hash() 必须一致 —— 改了那边就要重建。
   没有缩略图时（未生成 / 单张生成失败）自动退回原图，页面不会因此变空。 */
const THUMBDIR=String(__THUMBDIR__).replace(/\\/g,'/');   // 统一正斜杠，见 render_html()
const THUMB_OK=__THUMB_OK__;
/* 缩略图 URL。**必须带上 p=<原图路径>**：
   服务端 /thumb 命中不了缓存时会拿 p 现场补建一张（make_thumb）。
   旧的写法只给 h，服务端无从知道原图在哪，只能回 404 ——
   这就是「一部分图一直不显示」的直接原因（实测 4725 张缺缓存）。
   p 是长参数，换来的是「缓存缺了也能自己长回来」，值。 */
function thumbUrl(h,path,size){
  if(!THUMB_OK||!h) return '';
  const s=size||'t';
  if(useServer) return `/thumb?h=${h}&s=${s}`+(path?'&p='+encodeURIComponent(path):'');
  return 'file:///'+THUMBDIR+'/'+s+'/'+h+'.webp';
}
function thumbSrc(im){
  if(!THUMB_OK||!im||!im.th) return imgSrc(im.path);
  return thumbUrl(im.th, im.path, 't');
}
/* 文件夹 / 批次缩略按钮的封面图：直接复用该组某张图已有的网格缩略图，
   不额外生成文件（按钮只显示 58px，420px 的图缩着用足够）。 */
function coverSrc(h){
  if(!THUMB_OK||!h) return '';
  return thumbUrl(h,'','t');
}
function coverOf(dim,v){
  const c=(D.thumbs&&D.thumbs.covers&&D.thumbs.covers[dim])||{};
  return c[v]||'';
}

/* 图片加载兜底，三级：
   ① 缩略图拿不到 → 换原图。缩略图是缓存，可能还没生成或单个失败；
      原图一定在磁盘上，先让用户看见，别留灰块。
   ② 原图也拿不到 → 换另一条取图通道（服务挂了时 /img 全 404，
      而 location.protocol 仍是 http:，useServer 依然为 true）。
   ③ 两条通道都失败 → 标灰并把原因写进 title，
      让「这张图打不开」变成一句能读的话，而不是一个说不清的灰块。

   这里的失败状态（dataset.fb / style.opacity / title）是**挂在 img 元素自己
   身上**的。切图时不清掉，上一张的失败就会跟着下一张走 —— 实测（真浏览器）
   把 fb 设成 3、opacity 设成 .22 再切下一张，两者原样留着；于是下一张只要
   任意一次加载失败，就直接跳到第 ③ 级、被压成 22% 透明度，再也回不来。
   两头都修：sync() 每次切图清空状态（见 sync），imgOk 在任何一次成功加载
   之后也把状态复原 —— 后者是硬兜底，不指望每个调用方都记得清。 */
function imgErr(el){
  if(!el) return;
  const p=el.dataset.p;
  if(!p) return;
  const st=el.dataset.fb||'0';
  if(st==='0'&&el.dataset.th==='1'){
    el.dataset.fb='1';
    el.src=imgSrc(p);
    return;
  }
  if(st!=='2'){
    const alt=useServer ? 'file:///'+p : `/img?p=${encodeURIComponent(p)}`;
    el.dataset.fb='2';
    if(el.src!==alt){ el.src=alt; return; }
  }
  el.dataset.fb='3';
  el.style.opacity='.22';
  if(!el.title||el.title.indexOf('图片打不开')<0){
    el.title=(el.title?el.title+'\n':'')+'图片打不开：'+p;
  }
  // 走到第 ③ 级说明两条通道都试过了。这时再补一次「按路径现场建缩略图」：
  // 绝大多数失败是缓存未命中（不是图坏了），补建之后就能恢复。
  requestThumbBuild(p, el);
  if(el.id==='vimg') showVImageError(p);
}
/* 成功加载：把元素上的失败状态复原。
   这是「正常图恢复不透明状态」的硬保证 —— 即使哪天又有人漏了切图清理，
   只要图能加载出来，显示就一定是对的。 */
function imgOk(el){
  if(!el) return;
  el.style.opacity='';
  el.dataset.fb='0';
  if(el.title&&el.title.indexOf('图片打不开')>=0){
    el.title=el.title.replace(/图片打不开：[^\n]*/g,'').trim();
  }
  if(el.id==='vimg'){
    clearVImageError();
    const s=document.getElementById('vsize');
    if(s&&el.naturalWidth) s.textContent=el.naturalWidth+' × '+el.naturalHeight;
  }
}
/* 缩略图补建：直接请求 /thumb?h=&p=，服务端现场生成。成功后把 img 换回缩略图，
   省掉原图那几 MB 的下载解码。同一路径只补一次，避免坏图反复打接口。 */
const _thumbAsk=new Set();
function requestThumbBuild(path, el){
  if(!useServer||!THUMB_OK||!path||_thumbAsk.has(path)) return;
  _thumbAsk.add(path);
  fetch('/thumb?h='+encodeURIComponent((el&&el.dataset.h)||'')+'&s=t&p='+encodeURIComponent(path),
        {cache:'no-store'})
    .then(r=>{
      if(!r.ok){ _thumbAsk.delete(path); return; }   // 真建不出来，留原图，别再试
      if(el&&el.dataset.h){
        el.dataset.fb='0'; el.dataset.th='1';
        el.style.opacity='';
        if(el.title&&el.title.indexOf('图片打不开')>=0) el.title='';
        el.src=thumbUrl(el.dataset.h, path, 't');
      }
    })
    .catch(()=>_thumbAsk.delete(path));
}

/* uid = `<绝对路径>#<批次>#<序号>`：剥掉最后两段拿到真实路径。
   用 lastIndexOf 连切两刀，与后端 rsplit('#',2)[0] 保持同一口径。 */
function uidPath(u){
  const i=u.lastIndexOf('#');
  if(i<0) return u;
  const j=u.lastIndexOf('#',i-1);
  return j<0?u.slice(0,i):u.slice(0,j);
}
function newSetId(){return 's'+Date.now().toString(36)+Math.random().toString(36).slice(2,6);}
function getSet(id){return SETS.find(s=>s.id===id)||null;}
function activeSet(){return getSet(ACTIVE)||SETS[0]||null;}
/* 颜色按夹的「创建顺序」固定分配（存进夹对象），这样删掉别的夹时
   现有夹不会串色 —— 用 findIndex 的话，删一个夹会让后面全部变色。 */
function _nextColor(){ return (_colorSeq++)%COLORS.length; }
function setColor(id){
  const s=getSet(id);
  if(!s) return COLORS[0];
  if(s.color==null) s.color=_nextColor();
  return COLORS[s.color%COLORS.length];
}
/* 某夹里的 path 集合（uid 漂移兜底用） */
function setPaths(set){const r=new Set();set.paths.forEach(u=>r.add(uidPath(u)));return r;}

// A page submits its own delta against its last acknowledged snapshot.
// The server merges unrelated changes; old snapshots cannot overwrite new records.
let mkBase=null, mkAck=null, mkBlocked=false, mkQueue=Promise.resolve(), mkEdit=0;
const cloneDoc=v=>JSON.parse(JSON.stringify(v));
function marksDoc(){return {sets:SETS.map(s=>({id:s.id,name:s.name,paths:[...s.paths],note:s.note,color:s.color})),active:ACTIVE};}
function acceptMarks(m){
  SETS=m.sets.map(s=>({...s,paths:new Set(s.paths||[])}));
  ACTIVE=m.active||SETS[0].id;
  _colorSeq=1+Math.max(-1,...SETS.map(s=>typeof s.color==='number'?s.color:-1));
}
function mkInit(){
  if(location.protocol==='file:'){location.replace('http://127.0.0.1:8793/');return;}
  useServer=location.protocol!=='file:';
  try{const cached=JSON.parse(localStorage.getItem(LS_KEY)||'null');if(cached&&cached.sets&&cached.sets.length)acceptMarks(cached);}catch(e){}
  if(!SETS.length)acceptMarks({sets:[{id:'default',name:DEFAULT_SET_NAME,paths:[],note:{},color:0}],active:'default'});
  if(!useServer)return;
  fetch('/api/marks').then(r=>{if(!r.ok)throw Error('读取失败');return r.json();}).then(m=>{
    // Keep old browser cache as a recovery copy, never union it into authoritative data.
    const old=localStorage.getItem(LS_KEY);if(old)localStorage.setItem(LS_KEY+'_before_v235',old);
    mkBase=cloneDoc(m); mkAck=cloneDoc(m); acceptMarks(m); refreshMarks();
    const pending=JSON.parse(localStorage.getItem(LS_KEY+'_pending')||'null');
    if(pending&&pending.baseline&&pending.desired){
      mkBase=pending.baseline; mkAck=cloneDoc(pending.baseline); acceptMarks(pending.desired); mkPersist(); refreshMarks();
    }
    if(m.dead&&m.dead.length)toast(`有 ${m.dead.length} 条收藏待定位，记录已保留`);
  }).catch(()=>{toast('收藏尚未连上本地服务；本页修改会保存在待同步记录中');});
}
function mkPersist(){
  const desired=cloneDoc(marksDoc()), serial=++mkEdit;
  try{localStorage.setItem(LS_KEY,JSON.stringify(desired));}catch(e){}
  if(location.protocol==='file:')return;
  if(!mkBase){toast('收藏尚未读取完成，请连接后再保存');return;}
  // Queue snapshots, using the last submitted local snapshot as each delta baseline.
  const baseline=cloneDoc(mkBase);mkBase=cloneDoc(desired);
  const pending={baseline,desired};
  try{localStorage.setItem(LS_KEY+'_pending',JSON.stringify({baseline:mkAck,desired}));}catch(e){}
  mkQueue=mkQueue.then(async()=>{
    if(mkBlocked)return;
    const r=await fetch('/api/marks/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending)});
    const m=await r.json();if(!r.ok)throw Error(m.error||'保存失败');
    mkAck=cloneDoc(desired);
    if(serial===mkEdit){mkAck=cloneDoc(m);mkBase=cloneDoc(m);acceptMarks(m);localStorage.removeItem(LS_KEY+'_pending');localStorage.setItem(LS_KEY,JSON.stringify(m));refreshMarks();}
    else localStorage.setItem(LS_KEY+'_pending',JSON.stringify({baseline:mkAck,desired:marksDoc()}));
  }).catch(e=>{mkBlocked=true;toast('收藏未同步：'+e.message+'；本地待同步记录已保留');});
}
/* ---------- 风格评级（三级：1=绿·相似 2=黄·半动画 0=红·不相似） ----------
   与上面的「标记夹」是两套独立体系，来源和生命周期都不同：
   · 系统判定 SYS_GRADES —— 构建时由 _grades_system.json 注入（path 键），
     来自「风格总对比 237 张」批次的人工比对。只读，页面上不可改。
   · 我的标记 UG —— 用户手动点出来的（uid 键 + path 兜底），localStorage
     即存即显，服务在时再 POST 到 /api/grades 落 _grades.json。
   两边不一致时卡片上出 ≠ 角标；筛选可按「系统 / 我的 / 分歧」三个轴独立过滤。 */
let SYS_GRADES=__SYSGRADES__;       // {normpath:{g,note,grp}} —— 只读
let UG={};                           // {uid:{g,note}}
let UGPATH={};                       // {normpath:{g}} —— uid 漂移兜底索引
let gsel={sys:new Set(),user:new Set(),diff:false};   // 评级筛选（维度内 OR）
const LS_GKEY='style_atlas_grades_v1';
const GNAMES={1:'相似',2:'半动画',0:'不相似'};
const GCOLORS={1:'#1aad5c',2:'#e8a33d',0:'#de3737'};
function gnorm(p){return String(p||'').replace(/\\/g,'/').toLowerCase();}
function sysGradeOf(im){return SYS_GRADES[gnorm(im.path)]||null;}
function ugIndex(){
  UGPATH={};
  Object.entries(UG).forEach(([u,it])=>{UGPATH[gnorm(uidPath(u))]=it;});
}
function usrGradeOf(im){return UG[im.uid]||UGPATH[gnorm(im.path)]||null;}
let gBase=null,gAck=null,gBlocked=false,gQueue=Promise.resolve(),gEdit=0;
function gInit(){
  try{const m=JSON.parse(localStorage.getItem(LS_GKEY)||'null');if(m&&m.grades)UG=m.grades;}catch(e){}
  ugIndex();renderGradeFacet();
  if(location.protocol==='file:')return;
  fetch('/api/grades').then(r=>{if(!r.ok)throw 0;return r.json();}).then(m=>{
    const old=localStorage.getItem(LS_GKEY);if(old)localStorage.setItem(LS_GKEY+'_before_v235',old);
    gBase=cloneDoc(m);gAck=cloneDoc(m);UG=m.grades||{};ugIndex();renderGradeFacet();
    const pending=JSON.parse(localStorage.getItem(LS_GKEY+'_pending')||'null');
    if(pending){gBase=pending.baseline;gAck=cloneDoc(pending.baseline);UG=pending.desired.grades;gPersist();ugIndex();renderGradeFacet();}
  }).catch(()=>{});
}
function gPersist(){
  const desired=cloneDoc({grades:UG}),serial=++gEdit;
  try{localStorage.setItem(LS_GKEY,JSON.stringify(desired));}catch(e){}
  if(location.protocol==='file:')return;
  if(!gBase){toast('评级尚未读取完成，请连接后再保存');return;}
  const pending={baseline:cloneDoc(gBase),desired};gBase=cloneDoc(desired);
  localStorage.setItem(LS_GKEY+'_pending',JSON.stringify({baseline:gAck,desired}));
  gQueue=gQueue.then(async()=>{
    if(gBlocked)return;
    const r=await fetch('/api/grades/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending)});
    const m=await r.json();if(!r.ok)throw Error(m.error||'保存失败');
    gAck=cloneDoc(desired);
    if(serial===gEdit){gAck=cloneDoc(m);gBase=cloneDoc(m);gAck=cloneDoc(m);UG=m.grades;localStorage.removeItem(LS_GKEY+'_pending');localStorage.setItem(LS_GKEY,JSON.stringify(m));ugIndex();refreshGradeUI();}
    else localStorage.setItem(LS_GKEY+'_pending',JSON.stringify({baseline:gAck,desired:{grades:UG}}));
  }).catch(e=>{gBlocked=true;toast('评级未同步：'+e.message+'；本地待同步记录已保留');});
}
/* 写入用户评级（g=null 清除）。清掉指向同一 path 的漂移残留（多个旧 uid）。 */
function _setUG(u,g){
  const p=gnorm(uidPath(u));
  if(g==null){ delete UG[u]; }
  else UG[u]={g:g};
  Object.keys(UG).forEach(x=>{if(x!==u&&gnorm(uidPath(x))===p)delete UG[x];});
  ugIndex(); gPersist();
}
/* 卡片上的评级签（流内，与 settags 同层）。系统签只读；用户签点击循环 绿→黄→红→清除。 */
function gradeChipsHTML(im){
  const sg=sysGradeOf(im), ug=usrGradeOf(im);
  if(!sg&&!ug) return '';
  const diff=sg&&ug&&sg.g!==ug.g;
  const st=`style="--gc:${GCOLORS[sg?sg.g:0]}"`;
  return `<div class="gchips">`
    +(sg?`<span class="gsig" ${st} title="系统判定：${GNAMES[sg.g]}（只读 · 风格总对比 237 张批次）${sg.note?' · '+esc(sg.note):''}">${GNAMES[sg.g]}</span>`:'')
    +(ug?`<span class="gusr" style="--gc:${GCOLORS[ug.g]}" title="我的评级：${GNAMES[ug.g]}（点击循环切换，长按无效，再点回清除）"
        onclick="event.stopPropagation();cycleByUid('${esc(im.uid)}')">${GNAMES[ug.g]}</span>`:'')
    +(diff?'<span class="gdiff" title="系统判定与我的评级不一致">≠</span>':'')
    +`</div>`;
}
/* 用户签点击：循环 绿(1) → 黄(2) → 红(0) → 清除 */
function cycleByUid(u){
  const cur=UG[u]||UGPATH[gnorm(uidPath(u))]||null;
  const next=(!cur)?1:(cur.g===1?2:(cur.g===2?0:null));
  _setUG(u,next);
  refreshGradeUI();
}
/* 看图器评级栏：系统判定 + 绿/黄/红三按钮 + 清除 + 分歧提示 */
function vgradeHTML(im){
  const sg=sysGradeOf(im), ug=usrGradeOf(im);
  return `<span class="vgl">评级</span>`
    +(sg?`<span class="gsig" style="--gc:${GCOLORS[sg.g]}" title="系统判定（只读）${sg.note?' · '+esc(sg.note):''}">系统：${GNAMES[sg.g]}</span>`
        :'<span class="vgn">无系统判定</span>')
    +[1,2,0].map(g=>`<button class="vgb${ug&&ug.g===g?' on':''}" style="--gc:${GCOLORS[g]}"
        onclick="setGrade('${esc(im.uid)}',${g})">${GNAMES[g]}</button>`).join('')
    +(ug?`<button class="vgx" onclick="setGrade('${esc(im.uid)}',null)">清除我的</button>`:'')
    +(sg&&ug&&sg.g!==ug.g?'<span class="gdiff" title="系统判定与我的评级不一致">≠ 分歧</span>':'');
}
function setGrade(u,g){ _setUG(u,g); refreshGradeUI(); }
function syncVGrade(){
  const im=cur()[vi];
  const vg=$('#vgrade');
  if(vg) vg.innerHTML=im?vgradeHTML(im):'';
}
/* 评级变了之后的局部刷新：当前页网格 / 标记页 / 看图器评级栏 / 侧栏计数。
   不走 apply() —— 那会 firstPage() 把页码拽回第 1 页，翻到第 3 页点个签被甩回去，很难受。 */
function refreshGradeUI(){
  renderGrid();
  if(!$('#marksView').classList.contains('hidden')) renderMarks();
  syncVGrade();
  renderGradeFacet();
}
/* 侧栏「风格评级」面板：系统 / 我的 / 分歧 三个筛选轴 + 计数 */
function renderGradeFacet(){
  const el=$('#gradefacet');
  if(!el) return;
  const sysCnt={1:0,2:0,0:0}, usrCnt={1:0,2:0,0:0};
  Object.values(SYS_GRADES).forEach(it=>{ if(sysCnt[it.g]!=null) sysCnt[it.g]++; });
  Object.values(UG).forEach(it=>{ if(usrCnt[it.g]!=null) usrCnt[it.g]++; });
  const gchips=(cnt,selSet,kind)=>[1,2,0].map(g=>
    `<span class="ck${selSet.has(g)?' gon':''}" style="--gc:${GCOLORS[g]}"
      onclick="tgSel('${kind}',${g})" title="${kind==='sys'?'系统判定（只读）':'我的评级'}：${GNAMES[g]}">${GNAMES[g]}<span class="n">${cnt[g]}</span></span>`).join('');
  const nsel=gsel.sys.size+gsel.user.size+(gsel.diff?1:0);
  el.innerHTML=`<div class="fg">
    <h3>风格评级 <span id="gselcnt">${nsel}</span></h3>
    <div class="chips">${gchips(sysCnt,gsel.sys,'sys')}</div>
    <div style="font-size:11px;color:var(--ink3);margin:1px 0 6px">系统判定 · 对照参考图自动比对（只读，随图鉴构建注入）</div>
    <div class="chips">${gchips(usrCnt,gsel.user,'user')}
      <span class="ck${gsel.diff?' gon':''}" style="--gc:#de3737"
        onclick="tgSel('diff')" title="系统判定与我的评级不一致的图">只看分歧<span class="n">≠</span></span>
    </div>
    <div style="font-size:11px;color:var(--ink3);margin-top:1px">我的评级 · 点卡片角签或大图里 1/2/3/0 键</div>
    ${Object.keys(UG).length?`<div class="chips" style="margin-top:4px">
      <span class="ck" onclick="uiClearGrades()" title="删除全部「我的评级」">清空我的评级<span class="n">${Object.keys(UG).length}</span></span></div>`:''}
  </div>`;
}
function tgSel(kind,g){
  if(kind==='diff') gsel.diff=!gsel.diff;
  else{ const s=gsel[kind]; s.has(g)?s.delete(g):s.add(g); }
  apply();
}
function uiClearGrades(){
  if(!Object.keys(UG).length) return;
  if(!confirm(`清空全部「我的评级」（${Object.keys(UG).length} 条）？系统判定不受影响。`)) return;
  UG={}; ugIndex(); gPersist(); apply(); toast('已清空我的评级');
}

/* 这张图是否在**任一**夹里 */
function isMarked(u,path){
  return setsOf(u,path).length>0;
}
/* 这张图落在哪些夹（返回夹对象数组）—— 支持一图多夹 */
function setsOf(u,path){
  const out=[];
  SETS.forEach(s=>{
    if(s.paths.has(u)){out.push(s);return;}
    if(path&&setPaths(s).has(path)) out.push(s);
  });
  return out;
}
/* 切换（加/取消）某张图在某个夹里的状态；不传 setId 用 ACTIVE */
function toggleMark(u,path,setId){
  const s=getSet(setId||ACTIVE)||activeSet();
  if(!s) return;
  const p=path||uidPath(u);
  // 该夹里清掉所有指向这张图的记录（uid 漂移后可能残留多条）
  let had=false;
  [...s.paths].forEach(x=>{if(x===u||uidPath(x)===p){s.paths.delete(x);had=true;}});
  if(!had) s.paths.add(u);
  else delete s.note[u];
  mkPersist(); refreshMarks();
}
/* 把一张图加入/移出指定夹（用于右键菜单的「加入夹 X」） */
function addToSet(u,setId){
  const s=getSet(setId);
  if(!s) return;
  if(s.paths.has(u)){toast(`已在「${s.name}」中`);return;}
  s.paths.add(u); mkPersist(); refreshMarks();
  toast(`已加入「${s.name}」`);
}
function removeFromSet(u,setId){
  const s=getSet(setId);
  if(!s) return;
  const p=uidPath(u);
  [...s.paths].forEach(x=>{if(x===u||uidPath(x)===p)s.paths.delete(x);});
  delete s.note[u];
  mkPersist(); refreshMarks();
}
function newSet(name){
  const s={id:newSetId(),name:name||('夹 '+(SETS.length+1)),paths:new Set(),note:{},
           color:_nextColor()};
  SETS.push(s); ACTIVE=s.id;
  mkPersist(); refreshMarks();
  return s;
}
function renameSet(id,name){
  const s=getSet(id); if(!s) return;
  s.name=name||s.name; mkPersist(); refreshMarks();
}
function deleteSet(id){
  if(SETS.length<=1){toast('至少保留一个收藏夹');return;}
  const i=SETS.findIndex(s=>s.id===id); if(i<0) return;
  SETS.splice(i,1);
  if(ACTIVE===id) ACTIVE=SETS[0].id;
  mkPersist(); refreshMarks();
}
function clearSet(id){
  const s=getSet(id); if(!s) return;
  if(!confirm(`确定清空「${s.name}」里的 ${s.paths.size} 个收藏？`)) return;
  s.paths.clear(); s.note={};
  mkPersist(); refreshMarks(); toast(`已清空「${s.name}」`);
}
/* 索引反查表：uid / 规范化路径 → 记录。
   D.images 近 8000 条，收藏页每重绘一次都要按记录反查，现建现用会明显卡；
   缓存一份，数据换了（build_id 变或条数变）再重建。 */
let _IX=null;
function ix(){
  if(_IX&&_IX.n===D.images.length&&_IX.bid===D.build_id) return _IX;
  const byUid=new Map(), byPath=new Map();
  D.images.forEach(im=>{ byUid.set(im.uid,im);if(im.resource_id)byUid.set(im.resource_id,im);byPath.set(gnorm(im.path),im); });
  Object.entries(D.resource_aliases||{}).forEach(([old,u])=>{const im=byUid.get(u);if(im){byUid.set(old,im);byPath.set(gnorm(uidPath(old)),im);}});
  _IX={n:D.images.length,bid:D.build_id,byUid,byPath};
  return _IX;
}
function findImage(u){
  const I=ix();
  return I.byUid.get(u)||I.byPath.get(gnorm(uidPath(u)))||null;
}
/* 把一个夹的记录拆成「能显示的」和「待定位的」两段。
   旧实现是 `D.images.filter(...)` 一把梭：记录找不到对应的图，整条就消失了，
   而夹标签上的数字（s.paths.size）**一点没变**。
   用户看到的是「数量没变、收藏的图不见了」，而且没有任何入口能知道
   少的是哪几张、它们去了哪、还能不能找回来。
   现在能显示的正常显示，不能显示的显式留成「待定位」条目（见 pendingSectionHTML）。 */
function resolveSet(set){
  const live=[], dead=[], seen=new Set();
  if(!set) return {live,dead};
  [...set.paths].forEach(u=>{
    const im=findImage(u);
    if(im){
      if(seen.has(im.uid)) return;      // 同一张图被 uid + 路径各记一次 → 去重
      seen.add(im.uid); live.push(im);
    }else if(dead.indexOf(u)<0){
      dead.push(u);
    }
  });
  return {live:uniqueImages(live),dead};
}
/* 某夹里的全部图（按图鉴顺序） */
function setImages(set){ return resolveSet(set).live; }
/* 把记录里的 uid 规范成当前索引里的 uid。
   uid 是 `<路径>#<批次>#<序号>`；路径没变、但批次/序号的推断变过（重新扫描、
   换扫描根、改名后重建），同一条收藏就会以两个不同 uid 存在 ——
   用户看到的是「一个夹里同一张图两份记录」，取消收藏只摘掉一份。

   为什么必须在这里做，而不是只在迁移脚本里做一次：
   图鉴页的写协议是「前端整体覆盖 sets」，而前端手里的 sets 是它自己那份
   localStorage 副本。一个**升级前打开的旧标签页**会把它的旧 uid 并进服务端，
   我刚归位好的记录又被旧 uid 覆盖回来。
   实测发生过：迁移后 46/36，旧标签页一次写回变成 63/51 —— 多出的 32 条
   正好是这次迁移改动过的那 32 条（30 条死引用 + 2 条 uid 漂移）。

   规范化之后，同一个文件在两边算出同一个 uid，并集变**幂等**，
   这类回潮就没法再发生。
   注意：文件确实不在索引里时**原样保留** —— 那是「待定位」，要留给用户归位，
   不能趁规范化把它抹掉。 */
function canonUid(u){ const im=findImage(u); return im?im.uid:u; }
function canonSets(list){
  let changed=false;
  const out=list.map(s=>{
    const seen=new Set(), paths=[];
    [...s.paths].forEach(u=>{
      const n=canonUid(u);
      if(n!==u) changed=true;
      const k=gnorm(n);
      if(seen.has(k)){ changed=true; return; }
      seen.add(k); paths.push(n);
    });
    if(!changed) return s;
    return Object.assign({},s,{paths:(s.paths instanceof Set)?new Set(paths):paths});
  });
  return changed?out:list;
}
function refreshMarks(){
  // 角标：能显示的张数 + （有的话）待定位条数。
  // 旧写法取 s.paths.size，和页面里真正渲染出的张数不是一回事 ——
  // 「数量没变但图不见了」正是这两个数字不同源造成的。
  const b=$('#mkbadge');
  let live=0, dead=0;
  SETS.forEach(s=>{const r=resolveSet(s); live+=r.live.length; dead+=r.dead.length;});
  if(b){
    b.style.display=(live+dead)?'inline-block':'none';
    b.textContent=dead?`${live}+${dead}`:String(live);
    b.title=dead?`可显示 ${live} 张，另有 ${dead} 条记录待定位`:`可显示 ${live} 张`;
  }
  updateStampMarks();   // 页头「标记 N 张」跟着一起刷新
  // 网格内已渲染卡片的按钮状态：显示这张图落在哪几个夹
  document.querySelectorAll('.mk').forEach(el=>{
    const hit=setsOf(el.dataset.u,el.dataset.p);
    const on=hit.length>0;
    el.classList.toggle('on',on);
    el.textContent=on?(hit.length>1?`★${hit.length}`:'★'):'☆';
    if(on) el.style.color=setColor(hit[0].id); else el.style.color='';
    el.closest('.card')?.classList.toggle('marked',on);
  });
  // 若停在标记页，实时刷新
  if(!$('#marksView').classList.contains('hidden')) renderMarks();
}
/* 兼容旧调用 */
function markedImages(){return setImages(activeSet());}

/* ---------- 标记页：多夹管控 + 配方反推 ---------- */
function renderMarks(){
  const set=activeSet();
  // 每个夹的两个数：能显示的张数 + 待定位的条数。
  // 夹标签上必须同时暴露这两个数 —— 只显示 s.paths.size 的话，
  // 「记录还在、图不见了」这件事在界面上根本看不出来（这次的症状）。
  const cnts={};
  SETS.forEach(s=>{const r=resolveSet(s);cnts[s.id]={live:r.live.length,dead:r.dead.length,total:s.paths.size};});
  const tabOf=s=>{
    const c=cnts[s.id];
    const pd=c.dead
      ? `<em class="pd" title="${c.dead} 条记录的原图已不在记录的路径上 —— 见下方「待定位」">+${c.dead} 待定位</em>`
      : '';
    return `<span class="settab${s.id===ACTIVE?' on':''}" style="--sc:${setColor(s.id)}"
        onclick="switchSet('${s.id}')"
        title="切换到「${esc(s.name)}」：可显示 ${c.live} 张 · 记录 ${c.total} 条${c.dead?` · 待定位 ${c.dead} 条`:''}">
        <i class="dot"></i>${esc(s.name)}<b>${c.live}</b>${pd}
      </span>`;
  };
  // 一条工具栏装下夹切换 + 全部动作。原先是两条（夹切换一条、统计与导出另一条），
  // 两条都是整行背景条，白占 110px 高度；统计文字挪进下面小节的副标题即可。
  const bar=`<div class="mkbar setbar">
    <span class="lbl">收藏夹</span>
    ${SETS.map(tabOf).join('')}
    <span class="sp"></span>
    <button onclick="uiNewSet()">＋ 新建夹</button>
    <button onclick="uiRenameSet()">重命名</button>
    <button onclick="uiDeleteSet()">删除夹</button>
    ${set?`<button class="pri" onclick="exportSet('${set.id}')">导出本夹</button>
    <button onclick="exportAllSets()">导出全部夹</button>
    <button onclick="copyRecipe()">复制配方文本</button>
    <button onclick="clearSet('${set.id}')">清空本夹</button>
    ${serverOK?'':'<span class="lbl" style="color:#c98a12">（后端未连接，仅本地保存）</span>'}`:''}
  </div>`;

  if(!set){$('#marksView').innerHTML=bar;return;}
  const _res=resolveSet(set);
  const list=_res.live, missing=_res.dead;
  // 大图翻页范围：在标记页点开图后，按 ‹ › 应在本夹内走。
  // 用 uid 数组登记，onclick 里传索引给 openVByUid。
  _scopeSetId=set.id;
  _scopeUids=list.map(im=>im.uid);
  const cnt=(key,f)=>list.reduce((a,im)=>a+((im[key]||[]).filter(f).length?1:0),0);
  const top=(key,limit)=>{
    const c={};
    list.forEach(im=>{
      const v=im[key];
      const arr=Array.isArray(v)?v:(v==null?[]:[v]);   // 兼容字符串字段
      arr.forEach(x=>c[x]=(c[x]||0)+1);
    });
    return Object.entries(c).sort((a,b)=>b[1]-a[1]).slice(0,limit||12);
  };
  const tSty=top('styles'), tLib=top('libs'), tThe=top('themes'), tTec=top('techs');
  const tPre=top('preset',10);
  const maxSty=Math.max(...tSty.map(x=>x[1]),1);

  // 配方：按「方案」聚合标记图，给出可直接照抄的组合
  const byPreset={};
  list.forEach(im=>{(byPreset[im.preset]=byPreset[im.preset]||[]).push(im);});
  const recipes=Object.entries(byPreset).sort((a,b)=>b[1].length-a[1].length).slice(0,8)
    .map(([p,ims])=>{
      const s=[...new Set(ims.flatMap(i=>i.styles))];
      const l=[...new Set(ims.flatMap(i=>i.libs))];
      const t=[...new Set(ims.flatMap(i=>i.techs))];
      return `<div class="recipe">
        <b>${esc(short(p))}</b> · ${ims.length} 张
        <span class="rc">词条 ${s.map(x=>`<code>${esc(x)}</code>`).join('')||'—'}</span>
        <span class="rc">库 ${l.map(x=>`<code>${esc(short(x))}</code>`).join('')||'—'}</span>
        <span class="rc">技法 ${t.map(x=>`<code>${esc(x)}</code>`).join('')||'—'}</span>
        <div class="bar">${ims.slice(0,10).map(i=>
          `<img src="${thumbSrc(i)}" title="${esc(i.name)}"
            data-p="${esc(i.path)}" data-h="${esc(i.th||'')}"
            data-th="${(THUMB_OK&&i.th)?1:0}" onload="imgOk(this)" onerror="imgErr(this)"
            style="width:52px;height:52px;object-fit:cover;border-radius:5px;cursor:zoom-in"
            onclick="openVByUid('${esc(i.uid)}')">`).join('')}</div>
      </div>`;
    }).join('');

  const bars=(rows,max,clickable)=>rows.map(([v,n])=>
    `<div class="mknote"><span class="n">${esc(short(v))}</span>
      <span class="bar" style="width:${Math.max(3,n/max*190)}px"></span>
      <span class="v">${n} 张</span>
      <span class="cnt">${(n/list.length*100).toFixed(0)}%</span></div>`).join('')
    ||'<div style="color:var(--ink3);font-size:12px">无</div>';

  if(!list.length){
    $('#marksView').innerHTML=bar+(missing.length
      ? `<div class="sec"><h2>「${esc(set.name)}」</h2>
      <p>没有能直接显示的图片，但下面还有 ${missing.length} 条待定位记录 —— 点开看看它们的原图去哪了。</p></div>`
      : `<div class="sec"><h2>「${esc(set.name)}」</h2>
      <p>这个夹还是空的。回到「图库」点图片左上角 ☆ 即可加入；想换夹就先在下面点一下目标夹。</p>
      <div style="color:var(--ink3);font-size:12.5px;padding:26px 0;text-align:center">
        一图可同时属于多个夹，方便按不同需求分别导出。</div></div>`)
      +(missing.length?pendingSectionHTML(set,missing):'');
    return;
  }

  $('#marksView').innerHTML=bar+`
  <details class="sec mkcoll" ${mkOpen.style?'open':''} ontoggle="mkToggle('style',this)">
    <summary><h2>收藏样本的风格画像</h2>
      <span class="msum">${list.length} 张 · ${new Set(list.flatMap(i=>i.styles)).size} 个风格词条 ·
        ${new Set(list.flatMap(i=>i.techs)).size} 个技法 —— 点击展开</span></summary>
    <p>「<b style="color:${setColor(set.id)}">${esc(set.name)}</b>」${list.length} 张 ·
      涉及 ${new Set(list.map(i=>i.preset)).size} 个方案 ·
      ${new Set(list.flatMap(i=>i.styles)).size} 个风格词条 —— 这就是你想复刻的那套风格</p>
    <div class="grid2">
      <div class="mklib"><h3>风格词条</h3>${bars(tSty,maxSty)}</div>
      <div class="mklib"><h3>风格库</h3>${bars(tLib,Math.max(...tLib.map(x=>x[1]),1))}</div>
      <div class="mklib"><h3>主题</h3>${bars(tThe,Math.max(...tThe.map(x=>x[1]),1))}</div>
      <div class="mklib"><h3>技法维度</h3>${bars(tTec,Math.max(...tTec.map(x=>x[1]),1))}</div>
    </div>
  </details>

  <details class="sec mkcoll" ${mkOpen.recipe?'open':''} ontoggle="mkToggle('recipe',this)">
    <summary><h2>配方反推</h2>
      <span class="msum">${Object.keys(byPreset).length} 个方案 · 可直接照抄的组合 —— 点击展开</span></summary>
    <p>按方案聚合 —— 直接照抄这些组合，就能重做同样的风格与笔触</p>
    ${recipes}
    <div class="mklib" style="margin-top:9px"><h3>方案分布</h3>${bars(tPre,Math.max(...tPre.map(x=>x[1]),1))}</div>
  </details>

  <div class="sec"><h2>「${esc(set.name)}」的图片</h2>
    <p>点 ★ 选择收藏到哪个夹；点图片可看大图</p>
    <div id="mkGrid" class="grid">
      ${list.map(im=>{
        const inSets=setsOf(im.uid,im.path);
        const ug=usrGradeOf(im);
        return `
        <div class="card marked${ug?' ug'+ug.g:''}">
          <button class="mk on" data-u="${esc(im.uid)}" data-p="${esc(im.path)}"
            style="color:${setColor(inSets[0].id)}"
            title="点击选择要移出的夹"
            onclick="askMark('${esc(im.uid)}','${esc(im.path)}',event)">★</button>
          <img loading="lazy" src="${thumbSrc(im)}"
            data-p="${esc(im.path)}" data-h="${esc(im.th||'')}"
            data-th="${(THUMB_OK&&im.th)?1:0}" onload="imgOk(this)" onerror="imgErr(this)"
            onclick="openVByUid('${esc(im.uid)}')">
          ${gradeChipsHTML(im)}
          <div class="i"><div class="f" title="${esc(im.preset)}">${esc(short(im.preset))}</div>
          <div class="m"><span>${esc(im.run)}</span><span>${fsize(im.size)}</span></div></div>
          <div class="settags">${inSets.map(s=>
            `<span class="stg" style="--sc:${setColor(s.id)}" title="点击移出「${esc(s.name)}」"
              onclick="event.stopPropagation();removeFromSet('${esc(im.uid)}','${s.id}')">${esc(s.name)}</span>`
          ).join('')}</div>
        </div>`;}).join('')}
    </div>
  </div>
  ${missing.length?pendingSectionHTML(set,missing):''}`;
}
/* ---------- 待定位 ----------
   夹里记着、但原图已经不在那条路径上的记录。
   这些记录**不能删也不能藏**：原图多半只是被搬走或改了名，
   删掉就真找不回来了。所以逐条列出来，按文件名在索引里找同名候选，
   让用户一键归位；确认不要了再自己移除（只删记录，不动磁盘文件）。 */
function pendingSectionHTML(set, missing){
  const rows=missing.map(u=>{
    const p=uidPath(u);
    const bn=baseName(p).toLowerCase();
    const same=D.images.filter(im=>baseName(im.path).toLowerCase()===bn).slice(0,4);
    const pick=same.length
      ? same.map(im=>`<button class="pdpick" title="${esc(im.path)}"
          onclick="relocateMark('${esc(u)}','${esc(im.uid)}','${set.id}')">归位到 ${esc(shortDir(im.path)||'.')}</button>`).join('')
      : '<span class="pdnone">索引里没有同名文件</span>';
    return `<div class="pdcard">
      <div class="pdpath" title="${esc(p)}">${esc(p)}</div>
      <div class="pdcand">${pick}</div>
      <div class="pdact"><button onclick="dropDeadMark('${esc(u)}','${set.id}')"
        title="只删掉这条收藏记录，磁盘上的文件一个都不动">移除记录</button></div>
    </div>`;
  }).join('');
  return `<details class="sec mkcoll" open>
    <summary><h2>待定位（${missing.length}）</h2>
      <span class="msum">记录还在，原图不在那条路径上了 —— 点开处理</span></summary>
    <p>这些记录的原图可能被搬到了别的目录、或被改过名。软件不会替你删掉它们：
    下面按文件名列出索引里能找到的同名候选，选一个就归位；确实不要了再点「移除记录」。</p>
    ${rows}</details>`;
}
function relocateMark(oldUid, newUid, setId){
  let moved=0;
  SETS.forEach(s=>{
    if(setId&&s.id!==setId) return;
    if(s.paths.has(oldUid)){
      s.paths.delete(oldUid);
      if(!s.paths.has(newUid)) s.paths.add(newUid);
      moved++;
    }
  });
  if(!moved){ toast('这条记录已经不在了'); renderMarks(); return; }
  mkPersist(); renderMarks();
  toast('已归位到 '+baseName(uidPath(newUid)));
}
function dropDeadMark(uid, setId){
  const s=getSet(setId);
  if(!s) return;
  if(!confirm('只删除这条收藏记录，磁盘上的文件一个都不动。继续？')) return;
  s.paths.delete(uid);
  mkPersist(); renderMarks();
  toast('已移除这条待定位记录');
}
function switchSet(id){ ACTIVE=id; mkPersist(); renderMarks(); }
function uiNewSet(){
  const n=prompt('新收藏夹的名称：','夹 '+(SETS.length+1));
  if(!n) return; newSet(n.trim()); renderMarks(); toast(`已新建「${n.trim()}」`);
}
function uiRenameSet(){
  const s=activeSet(); if(!s) return;
  const n=prompt('重命名收藏夹：',s.name);
  if(!n||!n.trim()) return;
  renameSet(s.id,n.trim()); renderMarks();
}
function uiDeleteSet(){
  const s=activeSet(); if(!s) return;
  if(SETS.length<=1){toast('至少保留一个收藏夹');return;}
  if(!confirm(`确定删除收藏夹「${s.name}」？（夹内 ${s.paths.size} 个收藏会一并删除，图不会被删）`)) return;
  deleteSet(s.id); renderMarks(); toast(`已删除「${s.name}」`);
}
/* 导出指定夹；dst 目录可在弹框里改 */
function exportSet(id,ask){
  const s=getSet(id); if(!s) return;
  if(!serverOK){toast('后端未连接，无法导出');return;}
  const imgs=setImages(s);
  if(!imgs.length){toast(`「${s.name}」是空的`);return;}
  const def='C:\\\\Users\\\\wyj\\\\Desktop\\\\c\\\\标记导出\\\\'+s.name.replace(/[\\\\/:*?"<>|]/g,'_');
  const dst=ask===false?def:prompt(`把「${s.name}」的 ${imgs.length} 张导出到哪？（不存在会自动创建）`,def);
  if(!dst) return;
  fetch('/api/export_marks',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({dst:dst.replace(/\\\\/g,'/'),paths:imgs.map(im=>im.path)})
  }).then(r=>r.json()).then(j=>{
    toast(`「${s.name}」导出完成：复制 ${j.done} · 跳过 ${j.skipped} · 失败 ${j.failed}`);
  }).catch(()=>toast('导出失败'));
}
/* 一次性导出全部夹，各自建同名子目录 */
function exportAllSets(){
  if(!serverOK){toast('后端未连接，无法导出');return;}
  const base='C:\\\\Users\\\\wyj\\\\Desktop\\\\c\\\\标记导出';
  if(!confirm(`把所有收藏夹分别导出到：\n${base.replace(/\\\\/g,'\\')}\\<夹名>\\\n\n继续？`)) return;
  let i=0;
  const next=()=>{
    if(i>=SETS.length){toast('全部收藏夹导出完成');return;}
    const s=SETS[i++];
    if(!s.paths.size){next();return;}
    const imgs=setImages(s);
    fetch('/api/export_marks',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({dst:(base+'\\\\'+s.name.replace(/[\\\\/:*?"<>|]/g,'_')).replace(/\\\\/g,'/'),
                           paths:imgs.map(im=>im.path)})
    }).then(()=>next()).catch(()=>next());
  };
  next();
}
/* 兼容旧按钮 */
function exportMarks(){ exportSet(ACTIVE); }
function clearMarks(){ clearSet(ACTIVE); }
function copyRecipe(){
  const set=activeSet();
  const list=set?setImages(set):[];
  const byPreset={};
  list.forEach(im=>{(byPreset[im.preset]=byPreset[im.preset]||[]).push(im);});
  let txt=`风格收藏配方 · 「${set?set.name:''}」（${list.length} 张样本，导出于 ${new Date().toLocaleString('zh-CN')}）\n`;
  txt+='='.repeat(56)+'\n\n';
  Object.entries(byPreset).sort((a,b)=>b[1].length-a[1].length).forEach(([p,ims],i)=>{
    txt+=`【${i+1}】${p}  (${ims.length} 张)\n`;
    txt+=`  风格词条：${[...new Set(ims.flatMap(x=>x.styles))].join(' | ')||'—'}\n`;
    txt+=`  风格库  ：${[...new Set(ims.flatMap(x=>x.libs))].join(' | ')||'—'}\n`;
    const tech=[...new Set(ims.flatMap(x=>x.techs))];
    if(tech.length) txt+=`  技法维度：${tech.join(' | ')}\n`;
    const lo=[...new Set(ims.flatMap(x=>x.loras.map(l=>l.n+(l.s!=null?`@${l.s}`:''))))];
    if(lo.length) txt+=`  LoRA    ：${lo.join(' | ')}\n`;
    txt+=`  文件名  ：${ims.map(x=>x.name).join(', ')}\n\n`;
  });
  navigator.clipboard.writeText(txt).then(
    ()=>toast('配方已复制到剪贴板'),
    ()=>toast('复制失败，请手动导出'));
}
let toastT=null;
function toast(msg){
  let el=$('#toast');
  if(!el){ el=document.createElement('div'); el.id='toast';
    el.style.cssText='position:fixed;bottom:26px;left:50%;transform:translateX(-50%);'+
      'background:#1b1f24;color:#fff;padding:9px 20px;border-radius:22px;font-size:12.5px;'+
      'z-index:200;box-shadow:0 4px 20px rgba(0,0,0,.3);transition:.2s;opacity:0';
    document.body.appendChild(el); }
  el.textContent=msg; el.style.opacity='1';
  clearTimeout(toastT); toastT=setTimeout(()=>el.style.opacity='0',2200);
}
/* 按 uid 打开大图。
   关键：翻页范围（window._shown）必须锁定在「你点进来时看到的那批图」，
   而不是永远锁全量。历史 bug —— 这里原先无条件 window._shown=D.images，
   于是在「标记页」点开某张标记图后按「下一张」，会跳到全量图鉴里的原图
   下一张，而不是标记集里的下一张 —— 用户反馈的正是这个。
   规则：若当前有 scope（标记页登记的 _scopeUids）且该 uid 在其中，就在 scope
   内翻页；否则退回图库当前的筛选结果 hit（默认行为），最后才是全量。 */
function openVByUid(u){
  let list=null, i=-1;
  if(_scopeUids.length){
    const byUid=new Map(D.images.map(im=>[im.uid,im]));
    list=_scopeUids.map(x=>byUid.get(x)).filter(Boolean);
    i=list.findIndex(im=>im.uid===u);
    if(i<0) list=null;       // 不在 scope 里 → 放弃 scope
  }
  if(!list){
    i=hit.findIndex(im=>im.uid===u);
    list=(i>=0)?hit:null;
  }
  if(!list){
    i=D.images.findIndex(im=>im.uid===u);
    list=D.images;
  }
  if(i<0) return;
  window._shown=list; vi=i; $('#viewer').classList.add('on'); sync();
}

/* ---------- 叠加筛选（全 AND 交集） ----------
   需求：「动漫服装 + 甜彩线描」要精确命中同时具备两者的图。
   因此同维度内多选也是 AND —— 每个已选标签都必须被图片满足。 */
function matches(im){
  if(!mgMatches(im))return false;
  // 评级筛选先判（O(1) 查表，比标签维度更便宜）。注意这里**维度内是 OR**：
  // 同时勾「相似+半动画」的语义是「命中其中任一即可」，和标签维度的 AND 不同 ——
  // 若混进下面的 sel 循环，绿+黄会变成「既要绿又要黄」= 恒空。
  if(gsel.sys.size){
    const sg=sysGradeOf(im);
    if(!sg||!gsel.sys.has(sg.g)) return false;
  }
  if(gsel.user.size){
    const ug=usrGradeOf(im);
    if(!ug||!gsel.user.has(ug.g)) return false;
  }
  if(gsel.diff){
    const sg=sysGradeOf(im), ug=usrGradeOf(im);
    if(!(sg&&ug&&sg.g!==ug.g)) return false;
  }
  for(const [dim,vals] of Object.entries(sel)){
    for(const v of vals){
      let ok=false;
      if(dim==='folder'){ ok=(im.folder||'')===v; }
      else if(dim==='library'){ ok=im.libs.includes(v); }
      else if(dim==='style'){ ok=im.styles.includes(v); }
      else if(dim==='tech'){ ok=im.techs.includes(v); }
      else if(dim==='theme'){ ok=im.themes.includes(v); }
      else if(dim==='lora'){ ok=im.loras.some(l=>l.n===v); }
      else if(dim==='preset'){ ok=im.preset===v; }
      else if(dim==='model'){ ok=im.model===v; }
      else if(dim==='run'){ ok=im.run===v; }
      else if(dim==='bigfolder'){ ok=(im.bigfolder||'')===v; }
      else if(dim==='day'){ ok=(im.day||'')===v; }
      if(!ok) return false;   // 任一已选标签不满足 → 整图淘汰
    }
  }
  return true;
}
/* Deduplicate only after filtering: all paths and their saved state remain available. */
function uniqueImages(images){
  const groups=new Map();
  const priority=im=>(isMarked(im.uid,im.path)?2:0)+(usrGradeOf(im)?1:0);
  for(const im of images){
    const key=im.content_hash?'sha256:'+im.content_hash:'uid:'+im.uid;
    const prior=groups.get(key);
    if(!prior || priority(im)>priority(prior)) groups.set(key,im);
  }
  return [...groups.values()];
}
function apply(){
  hit=uniqueImages(D.images.filter(matches));
  mgOrder(hit);
  firstPage();          // 换条件就回第 1 页，否则会停在旧页码上看到空白
  const gnsel=gsel.sys.size+gsel.user.size+(gsel.diff?1:0);
  const nsel=Object.values(sel).reduce((a,s)=>a+s.size,0)+gnsel;
  $('#selcnt').textContent=nsel;
  if(nsel){
    const parts=[];
    // 评级条件条（放在标签条件前面，点击 × 摘掉对应评级轴）
    gsel.sys.forEach(g=>parts.push(`<span class="stag gtag" style="--sc:${GCOLORS[g]}">系统: ${GNAMES[g]}<i onclick="tgSel('sys',${g})">×</i></span>`));
    gsel.user.forEach(g=>parts.push(`<span class="stag gtag" style="--sc:${GCOLORS[g]}">我的: ${GNAMES[g]}<i onclick="tgSel('user',${g})">×</i></span>`));
    if(gsel.diff) parts.push(`<span class="stag gtag" style="--sc:#de3737">只看分歧<i onclick="tgSel('diff')">×</i></span>`);
    for(const [dim,vals] of Object.entries(sel))
      for(const v of vals) parts.push(`<span class="stag">${LABEL[dim]}: ${esc(short(v))}<i onclick="toggle('${dim}',${JSON.stringify(v).replace(/"/g,'&quot;')})">×</i></span>`);
    $('#stagbox').innerHTML=parts.join('');
    $('#selbar').classList.add('on');
    $('#selfull').innerHTML=`${nsel} 个条件叠加`;
  }else{
    $('#selbar').classList.remove('on');
    $('#selfull').textContent='未选——显示全部图片';
  }
  $('#hitn').textContent=hit.length;
  renderGrid(); renderFacets();
  // 统计页不跟着筛选实时重算。统计是「全量聚合 + 画图」，条件一变就重绘，
  // 数字会在眼皮底下跳，也没法对比改条件前后 —— 改成标脏，由用户点「更新统计」。
  if(statsVisible()) markStatsDirty();
}
function clearAll(){
  sel={};
  gsel={sys:new Set(),user:new Set(),diff:false};
  renderGradeFacet();
  apply();
}

/* ---------- 图片网格 ---------- */
let shown=0;

/* ---------- 分页 ---------- */
/* 每页张数。原来只有「仅显示前 N 张」一句提示，翻不到后面 —— 现补上真正的翻页。
   切筛选条件时自动回到第 1 页（否则会停在一个空的页码上）。 */
let PAGE=1;
const PER=__MAXTHUMB__;
function pageCount(){return Math.max(1,Math.ceil(hit.length/PER));}
function gotoPage(n){
  const pc=pageCount();
  n=Math.min(Math.max(1,n|0),pc);
  if(n===PAGE) return;
  PAGE=n;
  renderGrid();
  // 翻页后把图片区滚回顶部。现在 #main 自己是滚动容器（两栏独立滚动），
  // window.scrollTo 已经不管用了 —— 要滚的是 #main。
  const m=$('#main');
  if(m) m.scrollTo({top:0,behavior:'smooth'});
}
function firstPage(){PAGE=1;}

/* 翻页条：首页/上页/页码输入/下页/末页 + 「第 x / y 页 · 共 n 张」 */
function renderPager(){
  const el=$('#pager');
  if(!el) return;
  const pc=pageCount(), n=hit.length;
  if(n<=PER){ el.innerHTML=''; el.style.display='none'; return; }
  el.style.display='flex';
  const btn=(label,page,dis,title)=>
    `<button class="pg" ${dis?'disabled':''} title="${title||''}"
      onclick="gotoPage(${page})">${label}</button>`;
  const nums=[];
  // 页码窗口：当前页 ±2，首页和末页始终露出
  const lo=Math.max(1,PAGE-2), hi=Math.min(pc,PAGE+2);
  if(lo>1){ nums.push(1); if(lo>2) nums.push('…'); }
  for(let i=lo;i<=hi;i++) nums.push(i);
  if(hi<pc){ if(hi<pc-1) nums.push('…'); nums.push(pc); }
  el.innerHTML=
    `<span class="pginfo">共 <b>${n}</b> 张 · 第 <b>${PAGE}</b> / ${pc} 页</span>`+
    btn('‹ 上页',PAGE-1,PAGE<=1,'上一页')+
    nums.map(x=>x==='…'
      ? `<span class="pgdots">…</span>`
      : `<button class="pg${x===PAGE?' cur':''}" onclick="gotoPage(${x})">${x}</button>`
    ).join('')+
    btn('下页 ›',PAGE+1,PAGE>=pc,'下一页')+
    btn('末页 »',pc,PAGE>=pc,'跳到末页');
}

function renderGrid(){
  const pc=pageCount();
  if(PAGE>pc) PAGE=pc;                 // 条件变少时别停在空页
  const list=hit.slice((PAGE-1)*PER,PAGE*PER);
  shown=list.length;
  $('#empty').style.display=hit.length?'none':'block';
  $('#empty').textContent=hit.length?'':'没有同时满足这些条件的图片，试试减少条件';
  $('#grid').innerHTML=list.map((im,i)=>{
    const inSets=setsOf(im.uid,im.path);
    const on=inSets.length>0;
    const ug=usrGradeOf(im);
    const gi=(PAGE-1)*PER+i;           // 全局序号：看图翻页要用它
    return `
    <div class="card${on?' marked':''}${ug?' ug'+ug.g:''}" oncontextmenu="mkMenu(event,'${esc(im.uid)}','${esc(im.path)}')">
      <label class="mgselect"><input type="checkbox" aria-label="组织选定 ${esc(im.name||baseName(im.path))}" ${MG.selected.has(im.image_uid||im.uid)?'checked':''}
        onchange="mgSelect(${JSON.stringify(im.image_uid||im.uid).replace(/"/g,'&quot;')},this.checked)">组织</label>
      <button class="mk${on?' on':''}"
        data-u="${esc(im.uid)}" data-p="${esc(im.path)}"
        style="${on?'color:'+setColor(inSets[0].id):''}"
        title="${on?'点击选择要移出的夹（右键同）':'点击选择要收藏到哪个夹（右键同）'}"
        onclick="askMark('${esc(im.uid)}','${esc(im.path)}',event)"
        >${on?(inSets.length>1?'★'+inSets.length:'★'):'☆'}</button>
      <img loading="lazy" src="${thumbSrc(im)}" onclick="openV(${gi})"
        data-p="${esc(im.path)}" data-h="${esc(im.th||'')}"
        data-th="${(THUMB_OK&&im.th)?1:0}" onload="imgOk(this)" onerror="imgErr(this)">
      ${gradeChipsHTML(im)}
      <div class="acts">
        <button onclick="event.stopPropagation();goFolder(${gi})" title="在文件夹中打开">📂</button>
        <button onclick="event.stopPropagation();cpPath(${gi})" title="复制路径">⧉</button>
        <button onclick="event.stopPropagation();renameAt(${gi})" title="重命名这张图">✎</button>
      </div>
      ${on?`<div class="settags">${inSets.map(s=>
        `<span class="stg" style="--sc:${setColor(s.id)}" title="点击移出「${esc(s.name)}」"
          onclick="event.stopPropagation();removeFromSet('${esc(im.uid)}','${s.id}')">${esc(s.name)}</span>`
      ).join('')}</div>`:''}
      <div class="i">
        <div>${mgFieldHTML(im)}</div>
        <div class="f" title="${esc(im.preset)}">${esc(short(im.preset))}</div>
        <div class="m"><span>${esc(im.run)} #${im.index??''}</span><span>${fsize(im.size)}</span></div>
        <div class="tags">
          ${im.themes.slice(0,2).map(t=>`<span class="tg th">${esc(t)}</span>`).join('')}
          ${im.libs.slice(0,1).map(l=>`<span class="tg lb">${esc(short(l))}</span>`).join('')}
          ${im.techs.slice(0,2).map(t=>`<span class="tg te">${esc(t)}</span>`).join('')}
        </div>
      </div>
    </div>`;}).join('');
  renderPager();
  // 查看器索引是**全局序号**（gi = (PAGE-1)*PER + 本页序号），所以这里必须整份 hit，
  // 不能只放本页的 list —— 放 list 的话第 2 页起点开第一张就是 list[48]，undefined，
  // sync() 在 im.path 上抛异常，查看器停在上一张图上，看起来就是「点开的不是这张」。
  // 全量放进去还有个好处：大图里按 ‹ › 能一路翻完整个筛选结果，不用关掉重开。
  window._shown=hit;
}

/* ---------- 右键：加入/移出某个夹 ---------- */
/* 选夹菜单：点 ★ 或右键都走这里，问「标记到哪个夹」。
   原来只有右键能选夹，点 ★ 直接进当前激活夹 —— 但激活夹是上一次操作留下的，
   和当下想标的那类图未必一致，标错了还得再找回来改。
   现在两种入口都弹这一个菜单：列出全部夹、显示每张图在不在里面，
   点一下加/移出。点「＋ 新建夹并加入」可以直接建一个新夹。
   at = {x,y} 屏幕坐标（右键用鼠标位置，点 ★ 用按钮位置）。 */
function openSetPicker(u,path,at){
  closeMkMenu();
  const inSets=setsOf(u,path);
  const inIds=new Set(inSets.map(s=>s.id));
  const el=document.createElement('div');
  el.id='mkmenu';
  // 贴边翻转：菜单默认向右下展开，靠近视口右/下边缘时改成向左/上，
  // 否则在最后一行最后一张图上点 ★，菜单会有一半在屏幕外。
  const W=210, H=54+SETS.length*26;
  const x=Math.min(at.x, window.innerWidth-W-10);
  const y=(at.y+H>window.innerHeight) ? Math.max(8,at.y-H) : at.y;
  el.style.cssText=`position:fixed;left:${Math.max(8,x)}px;top:${y}px;z-index:300;
    background:#fff;border:1px solid var(--line);border-radius:9px;padding:6px;
    box-shadow:0 8px 28px rgba(0,0,0,.16);min-width:${W}px;font-size:12.5px`;
  const act=ACTIVE;
  el.innerHTML=`<div style="color:var(--ink3);font-size:11px;padding:3px 8px 6px">
      收藏到哪个夹？（点击加入／再点移出）</div>`
    + SETS.map(s=>{
        const has=inIds.has(s.id);
        const cur=s.id===act;
        return `<div class="mi" onclick="event.stopPropagation();${
          has?`removeFromSet('${esc(u)}','${s.id}')`:`addToSet('${esc(u)}','${s.id}')`
        };closeMkMenu()">
          <i class="dot" style="background:${setColor(s.id)}"></i>
          <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
            ${esc(s.name)}${cur?'<span style="color:var(--ink3);font-size:10px"> ·当前</span>':''}</span>
          <span style="color:${has?'var(--brand)':'var(--ink3)'};font-weight:600">${has?'✓':s.paths.size}</span></div>`;
      }).join('')
    + `<div class="mi" style="border-top:1px solid var(--line);margin-top:4px;padding-top:6px"
        onclick="event.stopPropagation();closeMkMenu();uiNewSetFromMenu('${esc(u)}')">
        ＋ 新建夹并加入</div>`;
  document.body.appendChild(el);
  setTimeout(()=>document.addEventListener('click',closeMkMenu,{once:true}),0);
}
function mkMenu(ev,u,path){
  ev.preventDefault();
  openSetPicker(u,path,{x:ev.clientX,y:ev.clientY});
}
/* 卡片上的 ★：在按钮下方弹选夹菜单 */
function askMark(u,path,ev){
  if(ev) ev.stopPropagation();
  const b=ev&&ev.currentTarget&&ev.currentTarget.getBoundingClientRect
    ? ev.currentTarget.getBoundingClientRect() : {left:60,top:60,bottom:88};
  openSetPicker(u,path,{x:b.left,y:b.bottom+4});
}
function closeMkMenu(){const e=document.getElementById('mkmenu');if(e)e.remove();}
function uiNewSetFromMenu(u){
  const n=prompt('新收藏夹的名称：','夹 '+(SETS.length+1));
  if(!n||!n.trim()) return;
  const s=newSet(n.trim());
  addToSet(u,s.id);
  renderMarks();
}
function cur(){return window._shown||hit;}
function openV(i){vi=i;$('#viewer').classList.add('on');sync();}
/* 切图。这里必须做三件「复位」的事，少一件就有可见后果：
   ① 清掉 img 元素上挂的失败状态（fb / fbDone / opacity / title）——
      上一张失败留下的 fb=3 会让下一张跳过两级兜底、直接被压成 22% 透明；
   ② 信息栏与舞台滚回顶部 —— 否则上一张滚到一半的位置会带到下一张；
   ③ 清掉上一张的报错提示条。
   （图片尺寸不再影响布局：图住在尺寸固定的 .vstage 里，信息在右侧独立栏。） */
function sync(){
  const list=cur();
  const im=list[vi];
  // 越界时明确报出来，不要什么都不做。原先这里直接读 im.path，
  // 序号一错就抛异常，大图停在上一张上 —— 用户看到的是「点开的不是这张」，
  // 而不是任何错误，因此这个 bug 一直像是「图对不上」而不是「代码有 bug」。
  if(!im){
    $('#vc').textContent=`序号越界（${vi+1} / ${list.length}）—— 请关闭后重新点一张`;
    $('#vpx').textContent='';
    const _pe=$('#vinfo'); if(_pe) _pe.innerHTML='';
    return;
  }
  const _vi=$('#vimg');
  _vi.dataset.p=im.path;
  delete _vi.dataset.fb; delete _vi.dataset.fbDone;   // 复位兜底状态
  _vi.dataset.th=(THUMB_OK&&im.th)?'1':'0';
  _vi.dataset.h=im.th||'';
  _vi.style.opacity='';                                // 复位灰化
  if(_vi.title&&_vi.title.indexOf('图片打不开')>=0) _vi.title='';
  clearVImageError();
  _vi.src=imgSrc(im.path);
  const v=$('#viewer'); if(v) v.scrollTop=0;
  const side=$('#vside'); if(side) side.scrollTop=0;
  $('#vc').textContent=`${vi+1} / ${list.length}　${im.preset} #${im.index??''}　${im.run}`;
  $('#vinfo').innerHTML=viewerInfoHTML(im);
  $('#vinfo').innerHTML+=`<tr><td class="k">风格字段</td><td>${mgFieldHTML(im)}</td></tr>`;
  $('#vrec').innerHTML=recipeText(im);
  syncVGrade();
  $('#vpx').textContent=im.path;
  const mk=$('#vmk');
  if(mk){ const hit=setsOf(im.uid,im.path);
    if(hit.length){
      mk.textContent=hit.length>1?`★ 已收藏（${hit.length} 个夹）· 点击改`:'★ 已收藏 · 点击改';
      mk.style.background='rgba(200,68,47,.55)';
    }else{
      mk.textContent='☆ 选择收藏夹';
      mk.style.background='';
    } }
}
/* ---------- 大图右侧的信息栏 ----------
   用户的要求：「点开图片详细查看时，图片的信息需要显示在旁侧」。
   原先信息挤在图下面那条居中的一列里，既会把图挤出视口，也没地方放更多字段。
   这里给一张紧凑的键值表：文件名 / 所在文件夹 / 批次 / 序号 / 尺寸 / 大小 / 完整路径。
   全部可选中复制 —— 排查「这张图在哪」时要用。 */
function baseName(p){return String(p||'').split(/[\\/]/).pop();}
function dirName(p){const s=String(p||'').replace(/\\/g,'/');const i=s.lastIndexOf('/');return i<0?'':s.slice(0,i);}
function shortDir(p){const d=dirName(p);return d.length>34?'…'+d.slice(-33):d;}
function viewerInfoHTML(im){
  const rows=[
    ['文件名', esc(baseName(im.path))],
    ['所在文件夹', esc(shortDir(im.path))],
    ['批次', esc(im.run||'—')],
    ['生产日期', esc(im.day||'日期未知')],
    ['序号', esc(im.index==null?'—':String(im.index))],
    // 尺寸不在索引里（构建时没记），等大图解码完由 imgOk 回填真实像素。
    ['尺寸', '<span id="vsize">—</span>'],
    ['大小', esc(fsize(im.size))],
  ];
  return rows.map(([k,v])=>
    `<tr><td class="k">${esc(k)}</td><td><span class="sel">${v}</span></td></tr>`
  ).join('');
}
function showVImageError(p){
  const el=$('#verr'); if(!el) return;
  const t=el.querySelector('.t'); if(t) t.textContent='这张图打不开：'+p;
  el.classList.add('on');
}
function clearVImageError(){const el=$('#verr'); if(el) el.classList.remove('on');}
/* 重试：把兜底状态清零，从缩略图重新走一遍三级链。 */
function retryVImage(){
  const im=cur()[vi]; if(!im) return;
  const img=$('#vimg'); if(!img) return;
  delete img.dataset.fb; img.style.opacity=''; img.title=''; clearVImageError();
  _thumbAsk.delete(im.path);
  img.dataset.th=(THUMB_OK&&im.th)?'1':'0';
  img.dataset.h=im.th||'';
  img.src=thumbSrc(im);
}
/* ---------- 配方：这张图生成时用的风格 + LoRA ----------
   图鉴记录里的 loras 有两种形状：{n,s} 对象（带强度）和纯字符串（早期记录没记强度）。
   统一成软件方案能直接吃的形状，顺便把顺序留住 —— LoRA 是串行链，顺序有语义。 */
// 风格 → 它所属的风格库（完整键）。用 D.lib_children 反查，而不是拿 libs 和 styles
// 按下标硬配对：一条记录可以挂多个库、多个词条，两者并不是一一对应的。
const STYLE_LIB=(()=>{
  const out={};
  Object.entries(D.lib_children||{}).forEach(([lib,kids])=>{
    (kids||[]).forEach(name=>{ if(!(name in out)) out[name]=lib; });
  });
  return out;
})();
function recipeOf(im){
  // 没有库归属的风格（例如直接写进工作流、不在目录里的那两条）留空 catalog，
  // 导出的 JSON 里它们会被标出来，不硬塞一个错库名。
  const styles=(im.styles||[]).map(name=>({catalog:STYLE_LIB[name]||'',name}));
  const loras=(im.loras||[]).map(l=>typeof l==='string'
    ? {name:l,strength:null,use_triggers:true}
    : {name:l.n,strength:(l.s==null?null:l.s),use_triggers:true}).filter(l=>l.name);
  return {styles,loras};
}
function recipeText(im){
  const r=recipeOf(im);
  const s=r.styles.length
    ? r.styles.map(x=>`<span class="rs">${esc(x.name)}</span>`).join(' + ')
    : '<span class="rn">没有风格记录</span>';
  const l=r.loras.length
    ? r.loras.map(x=>`<span class="rl">${esc(x.name.replace(/\.safetensors$/,''))}</span>`
        +(x.strength==null?'':`<span class="rn">@${x.strength}</span>`)).join(' + ')
    : '<span class="rn">没有 LoRA 记录</span>';
  return `<b>风格</b> ${s}<br><b>LoRA</b> ${l}`;
}
/* 复制成软件「风格／LoRA 预设」能直接用的 JSON。
   字段名与 comfybatch_recipe / style_lora_presets 对齐，粘进去就能存。 */
function copyRecipe(){
  const im=cur()[vi];
  if(!im) return;
  const r=recipeOf(im);
  const payload={name:(im.preset&&im.preset!=='(未命名)')?im.preset:`配方 ${im.run}`,
                 styles:r.styles, loras:r.loras};
  const text=JSON.stringify(payload,null,2);
  navigator.clipboard.writeText(text).then(
    ()=>toast(`已复制配方 JSON（${r.styles.length} 风格 / ${r.loras.length} LoRA）`),
    ()=>toast('复制失败，浏览器拒绝了剪贴板访问'));
}
/* ---------- ⇢ 工作台 / 对比页联动 ----------
   导入工作台：把这张图的配方 POST 给 S（8790）直接存成「风格/LoRA 预设」，
   名字弹窗自定义。跨源请求（8793 → 8790），S 侧已开 CORS（ACAO + OPTIONS 预检）。
   对比查看：把当前筛选命中的图建成一个选择会话（POST /api/selection 拿 id），
   嵌在工作台 iframe 里就 postMessage 让父页切到「风格对比」模块；
   独立打开图鉴时开新窗口兜底。 */
const S_BASE='http://127.0.0.1:8790';
/* 对比页已按每页 48 张分页展示（一行 6 张 × 8 行），上限只防「全库一把梭」：
   240 张 = 5 页，日常批次/分类远够；超过才确认。 */
const COMPARE_CAP=240;
function importToS(){
  const im=cur()[vi];
  if(!im) return;
  const r=recipeOf(im);
  if(!r.styles.length && !r.loras.length){
    toast('这张图没有风格/LoRA 记录，无预设可导入'); return;
  }
  const def=(im.preset&&im.preset!=='(未命名)')?im.preset:`配方 ${im.run}`;
  const name=prompt('导入为工作台预设名：', def+' 导入');
  if(name==null) return;
  const nm=name.trim()||def;
  fetch(S_BASE+'/api/save-style-lora-preset',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:nm,styles:r.styles,loras:r.loras})})
  .then(x=>x.json())
  .then(j=>{
    if(j&&j.ok) toast(`已导入工作台预设「${nm}」（${r.styles.length} 风格 / ${r.loras.length} LoRA）`);
    else toast('导入失败：'+((j&&j.error)||'未知错误'));
  })
  .catch(()=>toast('导入失败：工作台(8790)未连接'));
}
function openInCompare(){
  const paths=hit.map(im=>im.path);
  if(!paths.length){ toast('当前没有命中的图片'); return; }
  if(paths.length>COMPARE_CAP &&
     !confirm(`当前命中 ${paths.length} 张，超过单次上限 ${COMPARE_CAP} 张（每页 48 张分页展示），只取前 ${COMPARE_CAP} 张，继续？`)) return;
  const nsel=Object.values(sel).reduce((a,s)=>a+s.size,0);
  const label=(nsel?`${nsel} 个条件叠加`:'全部图片')+' · '+Math.min(paths.length,COMPARE_CAP)+' 张';
  fetch('/api/selection',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({label:label,paths:paths.slice(0,COMPARE_CAP)})})
  .then(x=>x.json())
  .then(j=>{
    if(!j||!j.ok){ toast('创建对比会话失败'); return; }
    if(window.parent&&window.parent!==window){
      window.parent.postMessage({type:'atlas-open-compare',sel:j.id,label:label},'*');
      toast('已发送到「风格对比」');
    }else{
      window.open('/compare?sel='+j.id,'_blank');
    }
  })
  .catch(()=>toast('对比会话创建失败'));
}
function markCurrent(){
  const im=cur()[vi];
  if(!im) return;
  // 大图里按 M（或点「☆ 标记到…」）同样弹选夹菜单，锚在按钮下方。
  // 这里不调 sync() —— toggleMark 内部会 refreshMarks 重绘，sync 会把菜单底下的
  // 计数器刷成旧值，反而看不出刚标进了哪个夹。
  const mk=$('#vmk');
  const r=mk&&mk.getBoundingClientRect?mk.getBoundingClientRect():{left:40,top:40,bottom:70};
  openSetPicker(im.uid,im.path,{x:r.left,y:r.bottom+4});
}
function step(d){vi=(vi+d+cur().length)%cur().length;sync();}
function closeV(){$('#viewer').classList.remove('on');}
function goFolder(i){post(cur()[i].path);}
function cpPath(i){navigator.clipboard.writeText(cur()[i].path);}
function openFolder(){post(cur()[vi].path);}
function copyPath(){navigator.clipboard.writeText(cur()[vi].path);}
function post(p){fetch('/api/open',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({path:p})}).catch(()=>{});}

/* ---------- 图片重命名 ----------
   需求：「给予图片重命名的功能」。
   只在**原目录内**改名（不许借这个入口搬目录），后缀不能改 ——
   一是避免误操作把图片搬出扫描根，二是后缀参与「这是不是一张图」的判断。
   改名之后最容易出事的地方不是文件本身，而是**引用**：收藏（paths 里存
   `<路径>#<批次>#<序号>`）和「我的评级」（uid 为键）全部按路径认图，
   改了名它们就一起指空 —— 表现和「收藏的图不见了」一模一样。
   所以服务端在同一次请求里把这两份引用一起搬过去（见 atlas_server /api/rename），
   前端拿到新 uid 后再把内存里的 D.images / SETS / UG 一起对齐。 */
function renameCurrent(){
  const im=cur()[vi];
  if(!im) return;
  renameImage(im, baseName(im.path));
}
function renameAt(i){ const im=cur()[i]; if(im) renameImage(im, baseName(im.path)); }
function renameImage(im, oldName){
  if(!useServer){ toast('静态页（file://）不能改名，请从 S 里打开图鉴'); return; }
  const nn=prompt('重命名这张图片（在原文件夹内改名，后缀保持不变）\n\n'+
                  '文件夹：'+dirName(im.path)+'\n原名：'+oldName, oldName);
  if(nn==null) return;
  const t=nn.trim();
  if(!t||t===oldName) return;
  // 页面里大量 onclick 是把路径拼进字符串的（历史写法），文件名里带引号会
  // 把那些处理程序的语法拆掉。与其在这里埋一颗雷，不如直接说清楚。
  if(/["'<>&]/.test(t)){ toast('名字里不能有引号或 < > & 这些符号'); return; }
  fetch('/api/rename',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({path:im.path,name:t})})
  .then(r=>r.json())
  .then(j=>{
    if(!j||!j.ok){ toast('重命名失败：'+((j&&j.error)||'未知错误')); return; }
    applyRename(im,j);
    toast('已重命名为 '+baseName(j.path));
  })
  .catch(()=>toast('重命名失败：图鉴服务未连接'));
}
/* 把改名结果落到前端的内存模型上。四处都要动，少一处就是一处「看起来丢了」：
   D.images 里那条记录、收藏夹的 paths、我的评级的键、以及当前视图的重绘。 */
function applyRename(im, j){
  const oldUid=im.uid, oldPath=im.path;
  const newUid=j.uid||j.path, newPath=j.path;
  im.path=newPath; im.uid=newUid;
  if(j.name) im.name=j.name;
  if(j.th!=null) im.th=j.th;
  SETS.forEach(s=>{
    const p=s.paths;
    if(p.has(oldUid)){ p.delete(oldUid); p.add(newUid); }
    if(p.has(oldPath)){ p.delete(oldPath); p.add(newPath); }
  });
  if(UG[oldUid]){ UG[newUid]=UG[oldUid]; delete UG[oldUid]; ugIndex(); }
  _IX=null;          // 索引缓存作废（uid/path → 记录 的反查表要重建）
  mkPersist();
  refreshMarks();    // 角标 + 卡片上的 ★ + （若正停在收藏页）整页重绘
  gPersist();
  if($('#viewer').classList.contains('on')) sync();
}
document.addEventListener('keydown',e=>{
  if(!$('#viewer').classList.contains('on'))return;
  if(e.key==='Escape')closeV(); if(e.key==='ArrowLeft')step(-1); if(e.key==='ArrowRight')step(1);
  if(e.key==='m'||e.key==='M')markCurrent();
  // 评级快捷键：1=相似 2=半动画 3=不相似 0=清除我的。设完顺带翻到下一张，
  // 连续审图时手指不用离开键盘（1-1-1-1 一路标下去）。
  const KEY2G={1:1,2:2,3:0};
  if(KEY2G[e.key]!=null||e.key==='0'){
    const im=cur()[vi];
    if(!im) return;
    setGrade(im.uid, KEY2G[e.key]!=null?KEY2G[e.key]:null);
    step(1);
  }});

/* ---------- 统计视图 ----------
   只在用户「点」的时候重算：进入统计页签算一次点击，之后改筛选条件
   只是标脏 + 顶部提示，数字不动，直到再点「更新统计」。
   为什么不让它跟着筛选实时变：统计是全量聚合 + 画图，条件一变就重绘，
   数字在眼皮底下跳，也没法对照「改条件前 / 改条件后」。 */
let STATS_DIRTY=false;
function statsVisible(){const v=$('#statsView');return v&&!v.classList.contains('hidden');}
function markStatsDirty(){
  STATS_DIRTY=true;
  const bar=$('#statsbar');
  if(!bar) return;
  bar.classList.add('on');
  const t=bar.querySelector('.sbtext');
  if(t) t.innerHTML=`筛选条件已变化（现在命中 <b>${hit.length}</b> 张）`
    +` —— 下方数字仍是上次统计的结果，点右侧按钮重算`;
}
function statsBarHtml(){
  return `<div class="statsbar${STATS_DIRTY?' on':''}" id="statsbar">
    <span class="sbtext">${STATS_DIRTY
      ? `筛选条件已变化（现在命中 <b>${hit.length}</b> 张） —— 下方数字仍是上次统计的结果，点右侧按钮重算`
      : `本次统计对应：<b>${Object.keys(sel).length?'当前筛选范围':'全部'}</b> · 命中 <b>${hit.length}</b> 张`}</span>
    <button class="sbupd" onclick="renderStats()"
      title="按当前筛选条件重新统计一遍">↻ 更新统计</button></div>`;
}

function renderStats(){
  STATS_DIRTY=false;
  const S=D.stats;
  const scope=hit.length, scopeLabel=Object.keys(sel).length?'当前筛选范围':'全部';
  const cnt=(dim,v,im)=>countIn(im||hit,dim,v);
  const libRows=D.facets.library.map(([v,n])=>({v,n:n0(v)}));
  function n0(v){return hit.reduce((a,im)=>a+(im.libs.includes(v)?1:0),0);}
  const techRows=D.facets.tech.map(([v])=>({v,n:countIn(hit,'techs',v)}));
  const folderRows=D.facets.folder.map(([v])=>({v,n:countIn(hit,'folder',v)}))
    .sort((a,b)=>b.n-a.n);
  const runRows=D.facets.run.map(([v])=>({v,n:countIn(hit,'run',v)}));   // 已按时间倒序
  const themeRows=D.facets.theme.map(([v,n])=>({v,n:n2(v)}));
  function n2(v){return hit.reduce((a,im)=>a+(im.themes.includes(v)?1:0),0);}
  // 覆盖率的分母只算「本图鉴里真的出现过的风格库」。
  // 原先用 S.catalog_available（3946），那是整个风格库目录，包含图鉴里
  // 一张图都没有的库 —— 拿它当分母，覆盖率被算成 5.22%，比实际（16.9%）
  // 低了三倍，读起来像是白干了。口径写清楚比数字好看重要。
  const availScope=(()=>{let s=0;
    (D.facets.library||[]).forEach(([lib])=>{
      const v=(D.lib_avail||{})[lib];
      if(typeof v==='number') s+=v;});
    return s;})();
  const cov=availScope?(S.distinct_styles/availScope*100):0;
  const nShown=(D.images||[]).length;
  const multiLib=hit.filter(im=>im.libs.length>1).length;
  const multiSty=hit.filter(im=>im.styles.length>1).length;

  $('#statsView').innerHTML=statsBarHtml()+`
  <div class="sec">
    <h2>概览 · ${scopeLabel}</h2>
    <p>命中 ${scope} 张</p>
    <div class="kpi">
      <div class="k hi"><div class="v">${scope}<small>张</small></div><div class="l">命中图片</div></div>
      <div class="k"><div class="v">${new Set(hit.flatMap(i=>i.styles)).size}<small>个</small></div><div class="l">涉及风格词条</div></div>
      <div class="k"><div class="v">${new Set(hit.flatMap(i=>i.libs)).size}<small>个</small></div><div class="l">涉及风格库</div></div>
      <div class="k"><div class="v">${new Set(hit.flatMap(i=>i.preset)).size}<small>套</small></div><div class="l">方案</div></div>
      <div class="k"><div class="v">${multiSty}<small>张</small></div><div class="l">跨多风格词条</div></div>
      <div class="k"><div class="v">${multiLib}<small>张</small></div><div class="l">跨多风格库</div></div>
    </div>
  </div>

  ${Object.keys(sel).length?'':`
  <div class="warn"><b>覆盖率提示</b>：图库 ${nShown} 张图用到 <b>${S.distinct_styles}</b> 个风格词条，
  而图上出现的 ${(D.facets.library||[]).length} 个风格库合计登记了 <b>${availScope.toLocaleString()}</b> 个
  —— 覆盖率 <b>${cov.toFixed(1)}%</b>，还有 ${(availScope-S.distinct_styles).toLocaleString()} 个词条没试过。</div>`}

  <div class="sec"><h2>各维度分布</h2><p>点任意一行即可把它加入筛选条件（数字要再点「更新统计」才变）</p>
    <div class="grid3">
      <div class="box"><h3>文件夹分类 → 覆盖图数</h3><div id="folderlist"></div></div>
      <div class="box"><h3>批次 · 时间 → 覆盖图数</h3><div id="runlist"></div></div>
      <div class="box"><h3>技法维度 → 覆盖图数</h3><div id="techlist"></div></div>
    </div>
  </div>

  <div class="sec"><h2>风格库 → 子选项分布</h2>
    <p>每个风格库下实际启用的风格词条（风格词条的「父级」就是风格库）</p>
    <table><thead><tr><th>风格库</th><th>命中图数</th><th>子选项数</th><th>启用/可用</th>
      <th>覆盖率</th><th>主力方案</th></tr></thead><tbody>${renderLibTable()}</tbody></table>
  </div>

  <div class="sec"><h2>共现排行</h2><p>哪两个风格经常一起出现（可用于复现组合）</p>
    <div class="grid2">
      <div class="box"><h3>风格词条 × 词条</h3><div id="pairstyle"></div></div>
      <div class="box"><h3>风格库 × 风格库</h3><div id="pairlib"></div></div>
    </div>
  </div>

  <div class="sec"><h2>风格词条明细</h2>
    <table><thead><tr><th>风格词条</th><th>风格库</th><th>命中</th><th>技法维度</th>
      <th>常共现搭档</th></tr></thead><tbody>${renderStyleTable()}</tbody></table>
  </div>

  <div class="sec"><h2>LoRA 使用</h2>
    <table><thead><tr><th>LoRA</th><th>命中图数</th><th>强度区间</th><th>均值</th></tr></thead>
    <tbody>${renderLoraTable()}</tbody></table>
  </div>

  <details class="sec weakdim"><summary>次要维度 · 主题（按预设名匹配，仅供参考）</summary>
    <p>主题是从「方案名」正则匹配出来的旧维度，不参与主分类，仅作辅助检索。</p>
    <div class="box"><h3>主题 → 覆盖图数</h3><div id="themelist"></div></div>
    <div class="box" style="margin-top:12px"><h3>主题 × 主题</h3><div id="pairtheme"></div></div>
  </details>`;

  // 这些排名行可点：点一下等于在筛选里选中该值（页面本来就写着「点标签可直接跳转筛选」，
  // 原先只是文案，行本身没有 onclick）。点完只标脏，数字要再点「更新统计」才动。
  const rankRow=(dim,x,max)=>`<div class="rank clickable${isOn(dim,x.v)?' on':''}"
    title="点一下加入筛选条件：${LABEL[dim]||dim} = ${esc(x.v)}${dim==='run'&&(D.run_time||{})[x.v]?'（'+D.run_time[x.v]+'）':''}"
    onclick="toggle('${dim}',${JSON.stringify(String(x.v)).replace(/"/g,'&quot;')})">
    <span class="nm">${esc(dim==='run'?runRowLabel(x.v):short(x.v))}</span>
    <span class="bar" style="width:${barW(x.n,max)}px"></span>
    <span class="vv">${x.n}</span></div>`;
  const rankList=(dim,rows,lim,label)=>{
    const shown=rows.filter(x=>x.n>0).slice(0,lim);
    const body=shown.map(x=>rankRow(dim,x,rows)).join('');
    const rest=rows.filter(x=>x.n>0).length-shown.length;
    return (body||'<div style="color:var(--ink3);font-size:12px">无</div>')
      +(rest>0?`<div style="color:var(--ink3);font-size:11px;margin-top:7px">
        另有 ${rest} 个${label||''}未列出</div>`:'');
  };
  $('#folderlist').innerHTML=rankList('folder',folderRows,12,'文件夹');
  // 批次用日期当标签，光看 hex 认不出是哪天
  $('#runlist').innerHTML=rankList('run',runRows,12,'批次');
  $('#techlist').innerHTML=rankList('tech',techRows,12);
  $('#themelist').innerHTML=rankList('theme',themeRows,12,'主题');
  $('#pairstyle').innerHTML=pairList(D.pairs.style,'style');
  $('#pairlib').innerHTML=pairList(D.pairs.library,'library');
  $('#pairtheme').innerHTML=pairList(D.pairs.theme,'theme');
}

function barW(n,rows){const m=Math.max(...rows.map(r=>r.n),1);return Math.max(3,n/m*80);}
/* 某维度取某值的图数。
   必须区分数组字段和字符串字段：libs/techs/styles 是数组，用 includes 判成员；
   但 folder 是**字符串**，对字符串调 includes 是子串匹配 ——
   "c/E".includes("c") 为真，于是「文件夹 c」会被算成所有 c/* 的总和（6282 张，
   真实值 0），而 c/1 会把 c/11、c/1x 一并算进去。数字看着像真的，其实全错。 */
function countIn(list,key,v){
  return list.reduce((a,im)=>{
    const val=im[key];
    if(Array.isArray(val)) return a+(val.includes(v)?1:0);
    return a+(val===v?1:0);
  },0);
}
function renderLibTable(){
  return D.facets.library.map(([lib,total])=>{
    const n=countIn(hit,'libs',lib);
    const kids=(D.lib_children&&D.lib_children[lib])||[];
    const used=new Set(hit.filter(im=>im.libs.includes(lib)).flatMap(i=>i.styles)).size;
    const avail=D.lib_avail[lib]||'—';
    const cov=typeof avail==='number'&&avail?((used/avail)*100).toFixed(1)+'%':'—';
    const presets=topPreset(lib);
    return `<tr><td>${esc(lib)}</td><td class="num">${n}</td>
      <td class="num">${kids.length}</td>
      <td class="num" style="color:var(--ink2)">${used} / ${avail}</td>
      <td class="num">${cov}</td><td>${presets}</td></tr>`;
  }).join('');
}
function topPreset(lib){
  const c={};
  hit.forEach(im=>{if(im.libs.includes(lib))c[im.preset]=(c[im.preset]||0)+1;});
  return Object.entries(c).sort((a,b)=>b[1]-a[1]).slice(0,2)
    .map(([p])=>`<span class="tg">${esc(short(p))}</span>`).join('');
}
function renderStyleTable(){
  const rows=D.facets.style.map(([s])=>({s,n:countIn(hit,'styles',s)}))
    .filter(x=>x.n>0).sort((a,b)=>b.n-a.n).slice(0,40);
  return rows.map(({s,n})=>{
    const meta=D.style_meta[s]||{};
    const mates=topMates(s);
    return `<tr><td style="font-weight:600">${esc(meta.name_cn||s)}</td>
      <td><span class="tg lb">${esc(short(meta.lib||''))}</span></td>
      <td class="num">${n}</td>
      <td>${(meta.techs||[]).map(t=>`<span class="tg te">${esc(t)}</span>`).join('')}</td>
      <td>${mates}</td></tr>`;
  }).join('')||'<tr><td colspan="5" style="color:var(--ink3)">无</td></tr>';
}
function topMates(s){
  const c={};
  hit.forEach(im=>{if(im.styles.includes(s))im.styles.forEach(o=>{if(o!==s)c[o]=(c[o]||0)+1;});});
  return Object.entries(c).sort((a,b)=>b[1]-a[1]).slice(0,2)
    .map(([m,n])=>`<span class="tg">${esc(short(m))} ${n}</span>`).join('')||'—';
}
function renderLoraTable(){
  const rows=D.facets.lora.map(([l])=>({l,n:countIn(hit,'loras','') })).filter(x=>x.n>0);
  return D.facets.lora.map(([l,total])=>{
    const n=hit.filter(im=>im.loras.some(x=>x.n===l)).length;
    const ss=hit.flatMap(im=>im.loras.filter(x=>x.n===l).map(x=>x.s)).filter(x=>typeof x==='number');
    const avg=ss.length?(ss.reduce((a,b)=>a+b,0)/ss.length).toFixed(2):'—';
    return `<tr><td style="font-weight:600;font-size:12px">${esc(l)}</td>
      <td class="num">${n}</td>
      <td class="num" style="color:var(--ink2)">${ss.length?Math.min(...ss).toFixed(2)+' ~ '+Math.max(...ss).toFixed(2):'—'}</td>
      <td class="num" style="color:var(--brand)">${avg}</td></tr>`;
  }).join('');
}
function pairList(pairs,kind){
  // 每个 kind 对应自己的字段：style→styles、theme→themes、library→libs。
  // 原先只有「是 style 就用 styles，否则一律用 themes」这一支，
  // 于是 library 去 im.themes 里找风格库名，永远找不到 ——
  // 「风格库 × 风格库」恒显示「无」，却照样占着一整块高度。
  const FIELD={style:'styles',theme:'themes',library:'libs'};
  const key=FIELD[kind]||'themes';
  const rows=pairs.filter(p=>{
    const [a,b]=p[0];
    if(!hit.length)return false;
    return hit.some(im=>(im[key]||[]).includes(a)) || hit.some(im=>(im[key]||[]).includes(b));
  }).slice(0,11);
  return rows.map(([pr,n])=>`<div class="rank"><span class="nm">${esc(short(pr[0]))} + ${esc(short(pr[1]))}</span>
    <span class="vv">${n}</span></div>`).join('')||'<div style="color:var(--ink3);font-size:12px">无</div>';
}

/* 技法维度原先画在这张 canvas 上（Chart.js）。换成和文件夹一致的排行条列表，原因有三：
   ① 画布是固定宽高比，被 grid 拉伸后底部空出一大块 —— 就是「内容空白，占地方」；
   ② Chart.js 走 cdn.jsdelivr.net，断网或被墙时 `new Chart` 抛异常，而它在 renderStats
      里排在前面，后面的文件夹/主题/共现就全都不渲染了 —— 一个图表挂掉带走整页；
   ③ 排行条能点（加了 onclick），图表不能。现在统计页已经没有外部依赖，断网可用。 */

/* ---------- 视图切换 ---------- */
document.querySelectorAll('.tb[data-v]').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.tb[data-v]').forEach(t=>t.classList.remove('on'));
  b.classList.add('on');
  const v=b.dataset.v;
  $('#browseView').classList.toggle('hidden',v!=='browse');
  $('#marksView').classList.toggle('hidden',v!=='marks');
  $('#statsView').classList.toggle('hidden',v!=='stats');
  $('#facet').style.display=(v==='browse')?'':'none';   // 标记/统计页不需要左侧筛选
  // 离开标记页就撤销翻页范围，否则回图库点图也会被锁在标记集里
  if(v!=='marks'){ _scopeUids=[]; _scopeSetId=''; }
  if(v==='stats') renderStats();
  if(v==='marks') renderMarks();
});

/* ---------- 自动刷新 ----------
   后端每 N 秒扫一次图片目录，有新图就重建数据。
   页面定时问一次，**变了就重新拉 /api/data 并整体重绘** —— 这样 ComfyUI
   新出的图会自动出现，不用重启服务、不用手动刷新页面。
   变更判据用 build_id（内容指纹）而不是内存里的 version 计数：
   version 每次重建都 +1（哪怕内容一张没变，例如服务重启后重新扫一遍），
   拿它当判据会导致「明明没新图也整页重绘」。build_id 只在内容真变时才变。
   仅在走 HTTP 服务时启用（file:// 直开没有后端，轮询没意义）。 */
let _ver=null, _bid=D.build_id||null, _pollT=null, _offline=false;
function applyRemoteData(nd){
  const previousPage=PAGE, previousScroll=$('#main')?.scrollTop||0;
  const active=cur()[vi]?.uid, viewerOpen=$('#viewer')?.classList.contains('on');
  D=nd;
  if(nd.system_grades)SYS_GRADES=nd.system_grades;
  // 新数据（或重新连上）就是「索引又变了」的信号，这时候把记录里的 uid 对齐一次：
  // 旧标签页并回来的陈旧 uid 会在这一步塌缩成当前 uid，然后写回，变成幂等。
  const nx=canonSets(SETS);
  if(nx!==SETS){ SETS=nx; refreshMarks(); }
  applyData();
  renderFacets(); apply();
  PAGE=Math.min(previousPage,pageCount());renderGrid();
  if($('#main'))$('#main').scrollTop=previousScroll;
  if(viewerOpen&&active){const index=cur().findIndex(im=>im.uid===active);if(index>=0){vi=index;sync();}}
  if(MG.open)mgQuery();
  if(!$('#marksView').classList.contains('hidden')) renderMarks();
  if(statsVisible()) markStatsDirty();   // 新数据到了也等用户点一下再重算
}
/* 断连提示条：服务没了要让用户看得见，而不是默默变成一片灰图。
   起因 —— 8793 服务中途停掉时，页面上每张图的 /img 都失败、塌成浅灰空块，
   用户看到「图片全空白」却不知道为什么。这里做显式状态提示 + 一键重连。 */
function setOffline(off, msg){
  let bar=$('#offbar');
  if(off){
    if(!bar){
      bar=document.createElement('div');
      bar.id='offbar';
      bar.innerHTML='<span class="obtxt"></span>'+
        '<button class="obbtn" onclick="reconnect()">重新连接</button>';
      document.body.appendChild(bar);
    }
    bar.querySelector('.obtxt').textContent=msg||'与图鉴服务的连接已断开（图片可能无法显示）';
    bar.style.display='flex';
  } else if(bar){
    bar.style.display='none';
  }
}
function reconnect(){
  toast('正在重新连接…');
  fetch('/api/version').then(r=>r.json()).then(v=>{
    _ver=v.version; _bid=v.build_id||_bid; setOffline(false);
    return fetch('/api/data').then(r=>r.json()).then(nd=>{
      applyRemoteData(nd); toast('已重新连接');
    });
  }).catch(()=>toast('仍连不上，请双击桌面「风格图鉴」重新启动'));
}
function pollVersion(){
  fetch('/api/version').then(r=>r.json()).then(v=>{
    if(_offline){_offline=false; setOffline(false);}   // 恢复了
    const cur=v.build_id||null;
    if(_bid===null){ _bid=cur; _ver=v.version; return; }
    // 只有内容指纹变了才重绘；指纹相同则什么都不做。
    if(cur && cur!==_bid){
      _bid=cur; _ver=v.version;
      return fetch('/api/data').then(r=>r.json()).then(nd=>{
        applyRemoteData(nd);
        toast('已载入新数据（版 '+cur+'）');
      });
    }
  }).catch(()=>{
    if(!_offline){ _offline=true; setOffline(true); }  // 只在状态翻转时提示一次
  });
}
function startPolling(){
  if(!useServer) return;                 // file:// 直开，无后端
  pollVersion();
  _pollT=setInterval(pollVersion, 15000); // 15 秒问一次，很轻
}

/* ---------- 单实例归一：心跳 + 自聚焦 ----------
   需求：重复启动图鉴不开第二个窗口 —— 入口（风格图鉴.py open）启动前
   先探测「页面是否活着」（最近有无心跳），活着就只 POST /api/focus 通知
   已有页面置前，不再开新标签；窗口真关了才开新的。
   本页每 5s POST /api/alive 报活，顺带回读 focus seq；seq 变了（有别处
   请求打开图鉴）就 window.focus() + 标题闪烁 + toast 提醒。
   说明：浏览器安全策略下脚本 window.focus() 不保证能把后台标签拉到前台，
   所以配了标题闪烁和 toast 兜底 —— 至少用户知道窗口就在任务栏里。
   file:// 直开没有后端，不参与（各开各的静态页，本来也无服务冲突）。 */
let _focusSeen=null, _aliveT=null;
function onFocusPing(){
  try{ window.focus(); }catch(e){}
  const t=document.title;
  try{ document.title='▶ 风格图鉴 —— 已在这个窗口'; setTimeout(()=>{document.title=t;},4000); }catch(e){}
  toast('图鉴已在别处被请求打开 —— 就是当前这个窗口');
}
function aliveTick(){
  if(!useServer) return;
  fetch('/api/alive',{method:'POST'}).then(r=>r.json()).then(j=>{
    if(_focusSeen===null){ _focusSeen=j.seq||0; return; }   // 首次只对齐基线，不误报
    if(j.seq!==_focusSeen){ _focusSeen=j.seq; onFocusPing(); }
  }).catch(()=>{});
}
function startAlive(){
  if(!useServer) return;
  aliveTick();
  _aliveT=setInterval(aliveTick, 5000);
}
/* 手动刷新按钮：立刻让后端重建，再拉最新数据 */
async function openSources(){
  if(!useServer){toast('请在 S 软件中设置作品目录');return;}
  try{
    const response=await fetch('/api/sources');
    if(!response.ok)throw Error('目录读取失败');
    const config=await response.json();
    const dialog=document.createElement('dialog');
    dialog.style.cssText='width:min(700px,90vw);background:#19202b;color:#eee;border:1px solid #666;border-radius:12px;padding:24px';
    dialog.innerHTML='<h3>作品目录</h3><p>每行一个完整目录。有效图片直接收录，生成记录用于补充风格标签。</p><textarea aria-label="作品目录路径" style="width:100%;height:180px"></textarea><pre style="white-space:pre-wrap;font-size:12px"></pre><p role="status"></p><button type="button">保存并扫描</button> <button type="button">关闭</button>';
    dialog.querySelector('textarea').value=config.roots.join('\n');
    dialog.querySelector('pre').textContent=(config.scan||[]).map(s=>s.path+'\n'+(s.exists?`图片文件 ${s.images} · 直接收录 ${s.added} · 记录已收录 ${s.already_indexed} · 无效 ${s.invalid} · 跳过目录 ${s.excluded_directories} · 不支持格式 ${s.unsupported}`:'目录不存在')+(s.errors.length?' · 读取失败 '+s.errors.length:'')).join('\n\n');
    const buttons=dialog.querySelectorAll('button');
    buttons[1].onclick=()=>{dialog.close();dialog.remove();};
    buttons[0].onclick=async()=>{
      buttons[0].disabled=true;
      const status=dialog.querySelector('[role=status]');status.textContent='正在保存并扫描…';
      try{
        const roots=dialog.querySelector('textarea').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
        const r=await fetch('/api/sources',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({roots})});
        const saved=await r.json();if(!r.ok||!saved.ok)throw Error(saved.error||'保存失败');
        const rebuilt=await fetch('/api/rebuild',{method:'POST'}).then(r=>r.json());
        if(!rebuilt.ok)throw Error('目录已保存，扫描未完成；请稍后点击刷新');
        const nd=await fetch('/api/data').then(r=>r.json());applyRemoteData(nd);
        dialog.close();dialog.remove();toast('目录已保存：共 '+nd.images.length+' 张图片');
      }catch(e){status.textContent=e.message;buttons[0].disabled=false;}
    };
    document.body.append(dialog);dialog.showModal();
  }catch(e){toast(e.message||'目录读取失败');}
}
function refreshNow(){
  if(!useServer){ location.reload(); return; }
  toast('正在刷新索引…');
  fetch('/api/rebuild',{method:'POST',headers:{'Content-Type':'application/json'},
    body:'{}'}).then(r=>r.json()).then(j=>{
      return fetch('/api/data').then(r=>r.json()).then(nd=>{
        _ver=j.version; _bid=nd.build_id||_bid; applyRemoteData(nd);
        toast(`已刷新：${nd.images.length} 张 · 版 ${nd.build_id||'—'}（${j.seconds}s）`);
      });
    }).catch(()=>toast('刷新失败，后端未连接'));
}

__MANAGEMENT_JS__
/* ---------- 启动 ---------- */
/* 顶部工具条会随窗口宽度换行（标签多的时候变两行），所以两栏的高度
   不能写死 68px —— 量一次实际高度写进 --topbar，换行后也对得齐。 */
function syncTopbar(){
  const t=$('#top');
  if(t) document.documentElement.style.setProperty('--topbar', t.offsetHeight+'px');
}
window.addEventListener('resize', syncTopbar);
syncTopbar();
renderFacets(); apply(); mkInit(); gInit(); startPolling(); startAlive();
</script></body></html>
"""


def enrich(data, styles_dir, verbose=True):
    """把派生信息补进 data：lib_avail / 每词条技法 / 缩略图映射 / 批次时间。

    原地修改并返回 data。**服务端的 rebuild 必须走这里** ——
    原先 atlas_server.py 自己抄了一份装配逻辑，两边各写各的替换，
    模板一加占位符，服务端那份就会留下未替换的字面量，前端直接语法错误。
    这次的 __THUMBDIR__ / __THUMB_OK__ 正好是这种漂移。
    """
    def log(*a):
        if verbose:
            print(*a, flush=True)

    # lib_avail：每个风格库在 catalog 里有多少条可用词条（覆盖率的分母）
    lib_avail = {}
    cat_cnt = {}
    for (cat, nm) in A.load_styles(styles_dir):
        cat_cnt[cat] = cat_cnt.get(cat, 0) + 1
    for lib, _ in data["facets"]["library"]:
        lib_avail[lib] = cat_cnt.get(lib, 0)         # facets 里存的是 catalog 原始串
        lib_avail[A.lib_of(lib)] = cat_cnt.get(lib, 0)

    # 每词条技法：技法维度是由风格词条的 prompt 派生的，不是独立轴
    for nm, meta in data["style_meta"].items():
        meta["techs"] = A.tech_of(meta.get("prompt") or "")

    data["lib_avail"] = lib_avail

    # ---- 缩略图：给每张图挂 hash + 文件夹/批次封面 ----
    # 缓存由 build_thumbs.py 预生成。这里只做映射，不生成文件。
    # 清单缺失时整块降级：网格退回原图，页面照常能开。
    thumb_dir, thumb_ok, covers, n_have = "", False, {}, 0
    try:
        import build_thumbs as T
        thumb_dir = T.DEFAULT_DIR
        tdir = os.path.join(thumb_dir, "t")
        for im in data["images"]:
            im["th"] = T.thumb_hash(im["path"])
        # 逐张确认缩略图真的在磁盘上 —— 只信清单会让没生成的图变成灰块
        n_have = sum(1 for im in data["images"]
                     if os.path.isfile(os.path.join(tdir, im["th"] + ".webp")))
        mf = os.path.join(thumb_dir, "manifest.json")
        if os.path.isfile(mf):
            covers = (json.load(open(mf, encoding="utf-8")).get("covers") or {})
        thumb_ok = n_have > 0
    except Exception as e:
        log(f"[warn] 缩略图不可用（{e}）→ 网格将回退到原图")
    data["thumbs"] = {"dir": thumb_dir, "ok": thumb_ok, "covers": covers,
                      "have": n_have}

    # Production dates come only from generation records. mtime is not production time.
    run_max = {}
    for im in data["images"]:
        im['produced_at'] = Management.produced_at(im)
        im['style_fields'] = Management.fields(im, im.get('styles') or [])
        r = im.get("run") or ""
        if r == "_UNNAMED":
            continue
        t = im['produced_at'][:10]
        if t > run_max.get(r, ''):
            run_max[r] = t
    data["run_time"] = dict(run_max)

    # 批次按时间倒序（新的在前）—— 最近的批次最常要翻
    named = [x for x in data["facets"].get("run") or [] if x[0] != "_UNNAMED"]
    unnamed = [x for x in data["facets"].get("run") or [] if x[0] == "_UNNAMED"]
    named.sort(key=lambda kv: (run_max.get(kv[0], ''), kv[0]), reverse=True)
    data["facets"]["run"] = named + unnamed

    # ---- 派生出两个「粗一档」的维度：大文件夹 与 日期 ----
    #
    # 为什么要在数据里派生，而不是前端算：
    #   · 筛选是按字段相等判断的（matches()），前端现算等于每次匹配都拆一次字符串；
    #   · 封面缩略图要按这两个维度挑代表图，那是在构建时挑最省事；
    #   · 99 个批次按钮、185 个文件夹按钮平铺出来就是「零碎」本身 —— 归成
    #     17 天 / 4 个大文件夹之后，第一屏就能看完。
    #
    # 两个都是**派生列**，不是新的真相来源：folder / run 才是原始值。
    def first_seg(value):
        s = str(value or "")
        i = s.find("/")
        return s[:i] if i >= 0 else s

    c_big = collections.Counter()
    c_day = collections.Counter()
    for im in data["images"]:
        im["bigfolder"] = first_seg(im.get("folder"))
        im["day"] = im['produced_at'][:10]
        c_big[im["bigfolder"]] += 1
        c_day[im["day"]] += 1

    # 大文件夹按张数倒序；日期按倒序（新的在前），「无日期」排最后
    data["facets"]["bigfolder"] = c_big.most_common()
    data["facets"]["day"] = (
        sorted(((d, n) for d, n in c_day.items() if d), key=lambda kv: kv[0], reverse=True)
        + ([(("", c_day[""]))] if c_day.get("") else [])
    )

    # ---- 封面：每个分组挑一张代表图 ----
    # 选「体积最大」的那张（同组里体积大通常画得最满、最有代表性），
    # 用 (-size, path) 排序保证确定性 —— 重跑不换封面，否则按钮会闪。
    # 直接复用该图已有的网格缩略图，不额外生成文件（按钮只有 58px）。
    def pick_covers(groups_of):
        best = {}
        for im in data["images"]:
            th = im.get("th")
            if not th:
                continue
            key = (-(im.get("size") or 0), im.get("path") or "")
            for g in groups_of(im):
                cur = best.get(g)
                if cur is None or key < cur[0]:
                    best[g] = (key, th)
        return {g: v[1] for g, v in best.items()}

    covers = {
        "folder": pick_covers(lambda im: [im.get("folder") or ""]),
        "bigfolder": pick_covers(lambda im: [im.get("bigfolder") or ""]),
        "run": pick_covers(lambda im: [im.get("run") or ""]),
        "day": pick_covers(lambda im: [im.get("day") or ""]),
    }
    data["thumbs"]["covers"] = covers
    return data


def load_sys_grades(path=None):
    """读系统评级文件（_grades_system.json，path 键）。

    由「风格总对比_237张」批次判定生成，构建时注入页面成为只读的系统判定。
    文件缺失/损坏一律降级为 {} —— 页面只是没有系统签，不能白屏。
    """
    p = path or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_grades_system.json")
    try:
        j = json.load(open(p, encoding="utf-8"))
        g = j.get("grades") if isinstance(j, dict) else None
        if isinstance(g, dict):
            return g
    except Exception:
        pass
    return {}


def _management_script():
    for base in (os.path.dirname(__file__), getattr(sys, '_MEIPASS', '')):
        path = os.path.join(base, 'atlas_management.js')
        if os.path.isfile(path):
            with open(path, encoding='utf8') as f:
                return f.read()
    raise FileNotFoundError('atlas_management.js')


def render_html(data, max_thumb=48):
    """模板 + 数据 → 最终单文件 HTML。

    所有占位符替换只在这一个地方发生，命令行和服务端共用。
    新增占位符时记得同时改这里 —— 漏一个，浏览器里就是一句 JS 语法错误。
    """
    th = data.get("thumbs") or {}
    # 目录注入成 URL 用的正斜杠 —— Windows 路径是反斜杠，直接拼进 file:/// 会得到
    # `file:///C:\Users\...\_style_atlas_thumbs/t/x.webp` 这种混用分隔符的东西。
    # 原图路径没这个问题，是因为数据里存的本来就是正斜杠。
    tdir = (th.get("dir") or "").replace("\\", "/")
    return (HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False))
                .replace("__MAXTHUMB__", str(max_thumb))
                .replace("__MANAGEMENT_JS__", _management_script())
                .replace("__THUMBDIR__", json.dumps(tdir, ensure_ascii=False))
                .replace("__THUMB_OK__", "true" if th.get("ok") else "false")
                .replace("__RENDER_META__", RENDER_META)
                .replace("__RENDER_RULE__", str(RENDER_RULE))
                .replace("__SYSGRADES__",
                         json.dumps(data.get("system_grades", load_sys_grades()), ensure_ascii=False)))


def page_render_rule(path):
    """读一份已渲染页面里的 RENDER_RULE。读不到（旧版页面 / 文件坏了）返回 None。

    只读开头 2KB —— 页面 6~7 MB，全文读一遍是浪费；meta 一定在 head 里。
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(2048)
    except OSError:
        return None
    m = re.search(r'<meta name="%s" content="(\d+)">' % re.escape(RENDER_META), head)
    return int(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="风格图鉴.html")
    ap.add_argument("--max-thumb", type=int, default=48)
    ap.add_argument("--reviews", default=A.DEFAULT_REVIEWS)
    ap.add_argument("--styles", default=A.DEFAULT_STYLES)
    ap.add_argument("--desktop-copy", action="store_true",
                    help="同时把结果同步到桌面静态副本（双击 file:// 用）")
    a = ap.parse_args()

    data = enrich(A.build(a.reviews, a.styles), a.styles)
    st = data["stats"]
    th = data["thumbs"]
    html = render_html(data, a.max_thumb)
    open(a.out, "w", encoding="utf-8").write(html)

    if a.desktop_copy:
        dst = os.path.join(os.path.expanduser("~"), "Desktop", "风格图鉴.html")
        if os.path.abspath(dst) != os.path.abspath(a.out):
            try:
                shutil.copyfile(a.out, dst)
                print(f"     [桌面副本] 已同步 → {dst}")
            except Exception as e:
                print(f"     [桌面副本] 同步失败: {e}")

    print(f"\n[ok] {a.out}  ({os.path.getsize(a.out):,} bytes)")
    print(f"     图片 {len(data['images'])} 张 · 版 {data.get('build_id','—')} "
          f"· 标签维度 {len(data['facets'])} 个 · 每页 {a.max_thumb} 张")
    print(f"     词条 {st['distinct_styles']} · 风格库 {st['distinct_libs']} · "
          f"主题 {st['distinct_themes']} · 技法 {st['distinct_techs']}")
    if th["ok"]:
        n, have = len(data["images"]), th["have"]
        cv = th["covers"]
        print(f"     缩略图 {have}/{n} 张可用"
              + (f"，缺 {n - have} 张（那些会退回原图）" if n != have else ""))
        print("     封面 大文件夹 %d / 文件夹 %d / 日期 %d / 批次 %d"
              % (len(cv.get("bigfolder") or {}), len(cv.get("folder") or {}),
                 len(cv.get("day") or {}), len(cv.get("run") or {})))
    else:
        print("     [warn] 没有可用缩略图，网格将直接引原图"
              "（慢，且大图可能显示不出来）—— 先跑 build_thumbs.py")


if __name__ == "__main__":
    main()
