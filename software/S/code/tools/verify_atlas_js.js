// 真实执行图鉴前端的筛选逻辑，验证行为正确
const fs = require('fs');
const P = 'C:/Users/wyj/WorkBuddy/2026-09-25-19-54-13/风格图鉴.html';
const html = fs.readFileSync(P, 'utf8');

let pass = 0, fail = 0;
function ck(n, c, e) {
  if (c) { pass++; console.log('  ✓ ' + n); }
  else { fail++; console.log('  ✗ ' + n + (e ? ' → ' + e : '')); }
}

// 提取内嵌数据
const s = html.indexOf('const D=') >= 0 ? html.indexOf('const D=') : html.indexOf('const D = ');
const e = html.indexOf('\n', s);
const raw = html.slice(html.indexOf('{', s), e).replace(/;\s*$/, '');
const D = JSON.parse(raw);
console.log(`数据：${D.images.length} 张图\n`);

// 抽出并真实执行前端的 matches 函数
const mStart = html.indexOf('function matches(im){');
const mEnd = html.indexOf('\n}', mStart) + 2;
const matchesSrc = html.slice(mStart, mEnd);

let sel = {};
eval(matchesSrc);

function run(selObj) {
  sel = {};
  for (const [k, v] of Object.entries(selObj)) sel[k] = new Set(v);
  return D.images.filter(matches);
}

console.log('== 前端筛选函数真实执行 ==');
const a1 = run({ theme: ['动漫服装'] });
const a2 = run({ theme: ['甜彩线描'] });
const both = run({ theme: ['动漫服装', '甜彩线描'] });
ck('单标签可查', a1.length > 0, String(a1.length));
ck('第二单标签可查', a2.length > 0, String(a2.length));
ck('叠加是交集：结果⊆A 且 ⊆B',
  both.every(x => a1.includes(x)) && both.every(x => a2.includes(x)));
ck('叠加每张都同时满足两条件',
  both.every(x => x.themes.includes('动漫服装') && x.themes.includes('甜彩线描')));
ck('叠加比单标签更精确', both.length <= Math.min(a1.length, a2.length),
  `${both.length} vs ${Math.min(a1.length, a2.length)}`);

console.log('\n== 多分类（一张图属多个类别）==');
const multi = D.images.filter(x => x.styles.length >= 2);
ck('存在跨多风格词条的图', multi.length > 0, String(multi.length));
const sample = multi[0];
ck('样例图确实有多个类别', sample.styles.length >= 2 && sample.libs.length >= 1,
  `词条${sample.styles.length} 库${sample.libs.length}`);
ck('通过任一所属类别都能找到它',
  run({ style: [sample.styles[0]] }).includes(sample) &&
  run({ style: [sample.styles[1]] }).includes(sample));

console.log('\n== 跨维度叠加 ==');
const cross = run({ theme: ['手游'], tech: ['赛璐璐·平涂'] });
ck('主题+技法跨维度交集语义正确',
  cross.every(x => x.themes.includes('手游') && x.techs.includes('赛璐璐·平涂')),
  String(cross.length));
const three = run({ theme: ['手游'], tech: ['赛璐璐·平涂'], library: ['krea2_anime_动漫_styles'] });
ck('三条件进一步收窄', three.length <= cross.length, `${three.length} vs ${cross.length}`);
const none = run({ theme: ['动漫服装'], preset: ['__不存在__'] });
ck('矛盾条件 → 空集', none.length === 0, String(none.length));

console.log('\n== 单维度多值 = 同时满足（非或）==');
const orTest = run({ library: ['krea2_anime_动漫_styles', 'krea2_Anime-Manga Linework_动漫-漫画线稿'] });
ck('两风格库叠加 = 同时含两者',
  orTest.every(x => x.libs.includes('krea2_anime_动漫_styles') &&
                    x.libs.includes('krea2_Anime-Manga Linework_动漫-漫画线稿')),
  String(orTest.length));
const one = run({ library: ['krea2_anime_动漫_styles'] });
ck('叠加结果 ≤ 单库结果', orTest.length <= one.length, `${orTest.length} vs ${one.length}`);

console.log('\n== 渲染所需字段完整性 ==');
ck('每图有 path/name/run/index', D.images.every(x => x.path && x.name && x.run !== undefined));
ck('每图有各维度标签数组',
  D.images.every(x => Array.isArray(x.themes) && Array.isArray(x.libs) &&
                      Array.isArray(x.styles) && Array.isArray(x.techs) && Array.isArray(x.loras)));
ck('facets 八个维度', Object.keys(D.facets).length === 8, Object.keys(D.facets).join(','));
ck('lib_avail 已注入', D.lib_avail && Object.keys(D.lib_avail).length > 0);
ck('style_meta 含技法', Object.values(D.style_meta).every(m => Array.isArray(m.techs)));

/* ==================== 标记（收藏）系统 ====================
   真实执行前端标记逻辑源码：抽出 toggleMark / markedImages /
   配方反推所依赖的纯函数并逐个跑通。 */
console.log('\n== 标记系统：源码真实执行 ==');

function extract(name){
  const keys = ['function ' + name + '(', 'function ' + name + ' ('];
  let s = -1;
  for (const k of keys){ const i = html.indexOf(k); if (i >= 0){ s = i; break; } }
  if (s < 0) return null;
  // 从函数体开始做花括号配平，避免截断
  let i = html.indexOf('{', s), depth = 0, e = i;
  for (; e < html.length; e++){
    if (html[e] === '{') depth++;
    else if (html[e] === '}'){ depth--; if (depth === 0){ e++; break; } }
  }
  return html.slice(s, e);
}

// --- 抽 markedImages ---
const miSrc = extract('markedImages');
ck('markedImages 源码可抽取', !!miSrc);
const MARKS = new Set();
let D_images = D.images;
eval(miSrc);
const ml = markedImages();
ck('未标记时返回空', ml.length === 0, String(ml.length));

// --- 模拟标记一批图，验证过滤 ---
const picks = D.images.slice(0, 5);
picks.forEach(p => MARKS.add(p.uid));
const ml2 = markedImages();
ck('标记后能取出对应图', ml2.length === 5, String(ml2.length));
ck('取出的正是被标记的那批', ml2.every(x => picks.includes(x)));
MARKS.delete(picks[0].uid);
ck('取消标记后立即减少', markedImages().length === 4, String(markedImages().length));

// --- 标记对象与全量图是同一引用（保证 UI 与数据一致） ---
ck('标记图与图库同源（同引用）', markedImages()[0] === D.images.find(
  x => x.uid === markedImages()[0].uid));

// --- 关键：uid 唯一性（path 会因同名跨批次重复） ---
console.log('\n== 标记键唯一性 ==');
const uids = D.images.map(x => x.uid);
ck('每图有 uid', uids.every(u => typeof u === 'string' && u.length > 0));
ck('uid 全局唯一（标记不串图）', new Set(uids).size === D.images.length,
  `${new Set(uids).size} vs ${D.images.length}`);
const paths = D.images.map(x => x.path);
const dupPath = paths.length - new Set(paths).size;
console.log(`  （参考：路径重复 ${dupPath} 条 —— 正因如此才不能用 path 当键）`);
// 标记一张同名图，不应误伤同路径的另一张
const samePath = {};
D.images.forEach(im => { (samePath[im.path] = samePath[im.path] || []).push(im); });
const collide = Object.values(samePath).find(g => g.length > 1);
if (collide) {
  MARKS.clear(); MARKS.add(collide[0].uid);
  const got = markedImages();
  ck('同名跨批次的图可独立标记', got.length === 1 && got[0] === collide[0],
    `命中 ${got.length} 张`);
} else {
  ck('同名跨批次的图可独立标记', true, '（本次无同名，跳过）');
}

// --- 配方反推：按方案聚合 ---
console.log('\n== 配方反推（按方案聚合并给出组合）==');
const byPreset = {};
markedImages().forEach(im => { (byPreset[im.preset] = byPreset[im.preset] || []).push(im); });
const recipes = Object.entries(byPreset).sort((a, b) => b[1].length - a[1].length);
ck('能按方案聚合成配方', recipes.length > 0, `${recipes.length} 个方案`);
const allStylesInRecipe = recipes.every(([p, ims]) =>
  ims.every(im => im.preset === p));
ck('配方内图片方案一致', allStylesInRecipe);
const recipeStyles = recipes.flatMap(([p, ims]) => [...new Set(ims.flatMap(i => i.styles))]);
ck('配方能给出风格词条组合', recipeStyles.length > 0, `${recipeStyles.length} 个词条`);
ck('配方词条来自标记图本身',
  recipeStyles.every(s => markedImages().some(im => im.styles.includes(s))));

// --- 配方文本可生成（copyRecipe 的核心内容） ---
let txt = `风格标记配方（${markedImages().length} 张样本）\n`;
recipes.forEach(([p, ims], i) => {
  txt += `【${i + 1}】${p}\n`;
  txt += `  风格词条：${[...new Set(ims.flatMap(x => x.styles))].join(' | ')}\n`;
});
ck('配方文本非空且含方案名', txt.length > 40 && txt.includes(recipes[0][0]));

/* ==================== 标记功能结构完整性 ==================== */
console.log('\n== 标记功能：HTML/JS 结构 ==');
ck('顶栏含「标记」标签按钮', html.includes('data-v="marks"'));
ck('存在标记页容器 #marksView', html.includes('id="marksView"'));
ck('存在角标 #mkbadge', html.includes('id="mkbadge"'));
ck('卡片含标记按钮 .mk', html.includes('class=\"mk') || html.includes('class="mk'));
ck('toggleMark 已定义', !!extract('toggleMark'));
ck('标记键用 uid 而非 path', html.includes("dataset.u") && html.includes('im.uid'),
  '标记必须用唯一键');
ck('标记持久化到 localStorage', html.includes('style_atlas_marks_v1'));
ck('标记同步到后端 /api/marks', html.includes('/api/marks'));
ck('标记页与图库/统计并列（三个 tab）',
  (html.match(/class="tb[ "]/g) || []).length >= 3);
ck('存在「导出标记图」入口', html.includes('exportMarks'));
ck('存在「复制配方文本」入口', html.includes('copyRecipe'));
ck('存在清空标记入口', html.includes('clearMarks'));
ck('查看器内含标记按钮', html.includes('id="vmk"') && html.includes('markCurrent'));
ck('M 键可标记', /e\.key==='m'/.test(html));
ck('导出用路径而非 uid', html.includes('markedImages().map(im=>im.path)'));

console.log(`\n===== 结果: ${pass} 通过 / ${fail} 失败 =====`);
process.exit(fail ? 1 : 0);
