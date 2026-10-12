// 从 compare_live.html 里抽出真实的判定相关函数，在 Node 里跑一遍，
// 确认前端算出的状态与后端口径一致。不是重写一份逻辑来对拍 ——
// 是把 html 里的源码抠出来执行，改了源码这条就会跟着变。
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'tools', 'compare_live.html'), 'utf8');

// 抠出需要的函数体（按函数名精确取，避免整段 script 里有 DOM 依赖）
function grab(name) {
  const re = new RegExp('function\\s+' + name + '\\s*\\([^)]*\\)\\s*\\{', 'm');
  const m = re.exec(html);
  if (!m) throw new Error('找不到函数 ' + name);
  let i = m.index + m[0].length, depth = 1;
  while (i < html.length && depth > 0) {
    const c = html[i];
    if (c === '{') depth++;
    else if (c === '}') depth--;
    else if (c === '/' && html[i + 1] === '/') { while (i < html.length && html[i] !== '\n') i++; }
    i++;
  }
  return html.slice(m.index, i);
}

function grabConst(name) {
  // 有些常量是同一行多声明（const A='x', B='y';），所以不能只匹配 `const NAME=`
  const lines = html.split(/\r?\n/);
  const hit = lines.find(l =>
    new RegExp('^\\s*const\\s+[^;]*\\b' + name + '\\s*=').test(l));
  if (!hit) throw new Error('找不到常量 ' + name);
  return hit.trim();
}

// 按「整行声明」收集，同一行只收一次（SET_DEL/SET_KEEP 是同一行）
const constLines = [];
function grabConst(name) {
  const lines = html.split(/\r?\n/);
  const hit = lines.find(l =>
    new RegExp('^\\s*const\\s+[^;]*\\b' + name + '\\s*=').test(l));
  if (!hit) throw new Error('找不到常量 ' + name);
  const t = hit.trim();
  if (constLines.indexOf(t) === -1) constLines.push(t);
  return '';   // 由 constLines 统一拼接
}

const src = [
  grabConst('MCOLORS'), grabConst('SET_DEL'), grabConst('SET_KEEP'),
  grabConst('VERDICT_COLOR'), grabConst('VERDICT_SLOT'),
  constLines.join('\n'),
  'let MARK_SETS=[],MK_UID={},MK_PATH={};',
  'let ITEMS=[],VIEW=[],VI=0;',
  grab('uidPath'), grab('pathKey'), grab('verdictSetIds'), grab('isVerdictSet'), grab('setColor'),
  grab('mkIndex'), grab('setsOf'), grab('isMarked'),
  grab('verdictOf'), grab('findSet'),
].join('\n');

const mod = { exports: {} };
new Function('module', 'exports', src + `
  module.exports={MCOLORS,SET_DEL,SET_KEEP,VERDICT_COLOR,VERDICT_SLOT,uidPath,pathKey,setColor,
    mkIndex,setsOf,isMarked,verdictOf,findSet,
    getMarks(){return MARK_SETS;},
    setMarks(m){MARK_SETS=m;mkIndex();},
    setItems(i){ITEMS=i;}};
`)(mod, mod.exports);

const F = mod.exports;
let ok = true;
const check = (label, cond, detail) => {
  console.log((cond ? '  PASS  ' : '  FAIL  ') + label + (detail !== undefined ? '  | ' + detail : ''));
  if (!cond) ok = false;
};

console.log('\n[1] 常量与图鉴色号对齐');
const atlas = fs.readFileSync(path.join(__dirname, '..', 'tools', 'build_atlas.py'), 'utf8');
const pal = /const COLORS=\[([^\]]+)\]/.exec(atlas)[1];
check('MCOLORS 与图鉴 COLORS 同一套',
  pal.replace(/[\s']/g, '') === F.MCOLORS.join(',').replace(/[\s']/g, ''),
  F.MCOLORS.join(','));
check('淘汰色 = #d0402f', F.VERDICT_COLOR[F.SET_DEL] === '#d0402f', F.VERDICT_COLOR[F.SET_DEL]);
check('保留色 = #2f8b57', F.VERDICT_COLOR[F.SET_KEEP] === '#2f8b57', F.VERDICT_COLOR[F.SET_KEEP]);
// 色号要能对齐后端传入的 color 值（MCOLORS.indexOf）
// 写回后端的 color 必须是 MCOLORS 的合法下标。
// 这里踩过：用 MCOLORS.indexOf(自定义色) 反查得到 -1，-1 是合法 int，
// 被原样存进 _marks.json —— 前端取模保护掩盖了它，图鉴页读时才是 undefined。
for (const id of [F.SET_DEL, F.SET_KEEP]) {
  const slot = F.VERDICT_SLOT[id];
  check(id + ' 的 color 下标合法（>=0 且在表内）',
    Number.isInteger(slot) && slot >= 0 && slot < F.MCOLORS.length,
    'slot=' + slot);
}
check('淘汰/保留 的下标互不相同', F.VERDICT_SLOT[F.SET_DEL] !== F.VERDICT_SLOT[F.SET_KEEP],
  F.VERDICT_SLOT[F.SET_DEL] + ' vs ' + F.VERDICT_SLOT[F.SET_KEEP]);
check('显示色仍是用户要的红', F.VERDICT_COLOR[F.SET_DEL] === '#d0402f');
check('显示色仍是用户要的绿', F.VERDICT_COLOR[F.SET_KEEP] === '#2f8b57');

console.log('\n[2] 判定归属：从标记夹反查这张图是淘汰还是保留');
const P = 'C:/shots/';
const U = n => P + 'shot' + n + '.png#runX#' + n;
F.setMarks([
  { id: 'sDef', name: '默认', paths: [U(1), U(2)], color: 0 },
  { id: F.SET_DEL, name: '淘汰', paths: [U(3)], color: 7 },
  { id: F.SET_KEEP, name: '保留', paths: [U(4)], color: 5 },
]);
const it = n => ({ uid: U(n), path: P + 'shot' + n + '.png' });
check('1 号：既不是淘汰也不是保留（只在默认夹）', F.verdictOf(it(1)) === '', F.verdictOf(it(1)));
check('3 号：淘汰', F.verdictOf(it(3)) === 'del', F.verdictOf(it(3)));
check('4 号：保留', F.verdictOf(it(4)) === 'keep', F.verdictOf(it(4)));
check('默认夹里的图也算「已标记」', F.isMarked(it(1)) === true);
check('没人标过的图：未标记且未判定',
  F.isMarked(it(9)) === false && F.verdictOf(it(9)) === '',
  'marked=' + F.isMarked(it(9)) + ' verdict=' + F.verdictOf(it(9)));

console.log('\n[3] 一张图同时挂两个夹时，保留优先（绿盖红）');
F.setMarks([
  { id: F.SET_DEL, name: '淘汰', paths: [U(5)], color: 7 },
  { id: F.SET_KEEP, name: '保留', paths: [U(5)], color: 5 },
]);
check('同挂两夹 → 报保留', F.verdictOf(it(5)) === 'keep', F.verdictOf(it(5)));
check('两个夹都能被枚举出来（标签要显示全）', F.setsOf(it(5)).length === 2,
  F.setsOf(it(5)).map(x => x.s.name).join('/'));

console.log('\n[4] uid 漂移兜底：换了批次/序号后缀，仍靠 path 认出来');
F.setMarks([{ id: F.SET_DEL, name: '淘汰', paths: [U(6)], color: 7 }]);
check('同 path 不同 uid 后缀 → 仍判为淘汰',
  F.verdictOf({ uid: P + 'shot6.png#otherRun#999', path: P + 'shot6.png' }) === 'del');
check('反斜杠路径也能认（Windows 写法）',
  F.verdictOf({ uid: P + 'shot6.png#otherRun#999',
    path: 'C:\\shots\\shot6.png' }) === 'del');
check('uidPath 剥掉最后两段',
  F.uidPath('C:/a/b.png#run1#7') === 'C:/a/b.png', F.uidPath('C:/a/b.png#run1#7'));

console.log('\n[5] 色号回退不炸（夹没 color 时按序号取色）');
F.setMarks([{ id: 'noColor', name: '新夹', paths: [] }]);
const c = F.setColor(F.getMarks()[0], 0);
check('无 color 字段也能取到合法色值', /^#[0-9a-f]{6}$/i.test(c), c);

console.log('\n=== 结果:', ok ? '全部通过' : '有失败', '===');
process.exit(ok ? 0 : 1);
