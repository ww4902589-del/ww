/* 抽取 compare_live.html 里的真实函数源码来跑，验证「按批次自动分夹」。
   为什么不用读代码的方式检查：函数里的分支（uid 缺失、跨批次、老数据兼容）
   光看是看不出问题的，必须真执行一遍。

   依赖：Node（managed）。
   用法：node tests/check_batch_sets_js.js
*/
const fs = require('fs');
const path = require('path');

const HTML = path.join(__dirname, '..', 'tools', 'compare_live.html');
const html = fs.readFileSync(HTML, 'utf8');

/* ---------- 从 html 里抠出要用的源码 ---------- */
function scriptBody() {
  const m = html.match(/<script>([\s\S]*?)<\/script>/);
  if (!m) throw new Error('未找到 <script> 块');
  return m[1];
}
const JS = scriptBody();

/* 按「函数名 + 括号配平」抠出单个函数源码 */
function grab(name) {
  const re = new RegExp('function\\s+' + name + '\\s*\\(', 'g');
  const m = re.exec(JS);
  if (!m) throw new Error('找不到函数 ' + name);
  let i = JS.indexOf('(', m.index);
  let depth = 0, start = i;
  for (; i < JS.length; i++) {
    if (JS[i] === '(') depth++;
    else if (JS[i] === ')') { depth--; if (depth === 0) break; }
  }
  let j = JS.indexOf('{', i);
  depth = 0;
  for (let k = j; k < JS.length; k++) {
    if (JS[k] === '{') depth++;
    else if (JS[k] === '}') { depth--; if (depth === 0) return JS.slice(m.index, k + 1); }
  }
  throw new Error('函数 ' + name + ' 括号不配平');
}
/* 抠出含某名字的单行 const 声明（SET_DEL/SET_KEEP 是同行声明） */
function grabConst(name) {
  const lines = JS.split('\n');
  const out = [];
  for (const ln of lines) {
    if (!/^\s*const\s/.test(ln)) continue;
    // 只收「声明了 name 这个名字」的行（避免把引用行也收进来）
    if (new RegExp('(^|[,\\s{])' + name + '\\s*[:=]').test(ln)) out.push(ln.trim());
  }
  if (!out.length) throw new Error('找不到 const ' + name);
  return out.join('\n');
}

/* ---------- 组装一个可执行的作用域 ---------- */
const need = ['batchOf', 'verdictSet', 'verdictSetIds', 'isVerdictSet', 'setColor',
              'inheritSetId', 'targetSet',
              'verdictOf', 'uidPath', 'pathKey', 'setsOf'];
const src = [
  grabConst('SET_DEL'),
  grabConst('VERDICT_COLOR'),
  grabConst('VERDICT_SLOT'),
  grabConst('MCOLORS'),
  'let ITEMS=[]; let MARK_SETS=[],MK_UID={},MK_PATH={};',
  ...need.map(grab),
  'return {batchOf,verdictSet,verdictSetIds,isVerdictSet,setColor,inheritSetId,targetSet,verdictOf,setsOf,setItems:(x)=>{ITEMS=x},setMarks:(x)=>{MARK_SETS=x;mkIndex&&mkIndex()}};'].join('\n');

/* mkIndex 也要（setsOf 依赖 MK_UID/MK_PATH） */
const src2 = src.replace(
  'return {batchOf',
  grab('mkIndex') + '\nreturn {batchOf'
);
const api = new Function(src2)();

/* ---------- 断言框架 ---------- */
let pass = 0, fail = 0;
const failures = [];
function ok(cond, label) {
  if (cond) { pass++; console.log('  OK   ' + label); }
  else { fail++; failures.push(label); console.log(' FAIL  ' + label); }
}
function eq(a, b, label) {
  const same = JSON.stringify(a) === JSON.stringify(b);
  if (same) { pass++; console.log('  OK   ' + label + '  = ' + JSON.stringify(a)); }
  else { fail++; failures.push(label); console.log(' FAIL  ' + label + '  期望 ' + JSON.stringify(b) + ' 实际 ' + JSON.stringify(a)); }
}

console.log('=== A. batchOf：从 uid 取批次 ===');
eq(api.batchOf({uid: 'C:/x/a.png#926266f5#36'}), '926266f5', '常规 uid');
eq(api.batchOf({uid: 'C:/x/a.png#_UNNAMED#None'}), 'x', '_UNNAMED/None 归到 x');
eq(api.batchOf({uid: ''}), 'x', '空 uid 归到 x');
eq(api.batchOf({}), 'x', '无 uid 字段归到 x');
eq(api.batchOf({uid: 'C:/x/a.png'}), 'x', '不带后缀的裸路径归到 x');
eq(api.batchOf({uid: 'C:/x/a#b/c.png#5f7c3c25#7'}), '5f7c3c25', '路径段含 # 也能正确取批次');

console.log('\n=== B. verdictSet：派生夹的 id 与名字 ===');
eq(api.verdictSet('del', '926266f5'), {id: 'verdict-reject@926266f5', name: '淘汰·926266f5'}, '淘汰夹');
eq(api.verdictSet('keep', '926266f5'), {id: 'verdict-keep@926266f5', name: '保留·926266f5'}, '保留夹');
eq(api.verdictSet('del', null).id, 'verdict-reject@x', '批次缺失时用 x');

console.log('\n=== C. isVerdictSet：新旧 id 都要认 ===');
ok(api.isVerdictSet('verdict-reject', 'del'), '老的无后缀 id 认得出（兼容旧数据）');
ok(api.isVerdictSet('verdict-reject@926266f5', 'del'), '新的派生 id 认得出');
ok(!api.isVerdictSet('verdict-reject@926266f5', 'keep'), '淘汰夹不会被当成保留夹');
ok(!api.isVerdictSet('默认', 'del'), '普通夹不会被当成判定夹');
ok(!api.isVerdictSet('verdict-rejector', 'del'), '前缀相似但分隔符不对，不误认');

console.log('\n=== D. verdictOf：不同批次的图各自认自己的判定（核心需求） ===');
const items = [
  {uid: 'C:/D/926266f5/001.png#926266f5#1', path: 'C:/D/926266f5/001.png'},
  {uid: 'C:/D/926266f5/002.png#926266f5#2', path: 'C:/D/926266f5/002.png'},
  {uid: 'C:/D/1c0c149f/001.png#1c0c149f#1', path: 'C:/D/1c0c149f/001.png'},
];
const sets = [
  {id: 'verdict-reject@926266f5', name: '淘汰·926266f5', paths: [items[0].uid], note: {}, color: 0},
  {id: 'verdict-keep@926266f5', name: '保留·926266f5', paths: [items[1].uid], note: {}, color: 2},
  {id: 'verdict-keep@1c0c149f', name: '保留·1c0c149f', paths: [items[2].uid], note: {}, color: 2},
];
api.setItems(items);
api.setMarks(sets);
eq(api.verdictOf(items[0]), 'del', '批次A 第1张 = 淘汰');
eq(api.verdictOf(items[1]), 'keep', '批次A 第2张 = 保留');
eq(api.verdictOf(items[2]), 'keep', '批次B 第1张 = 保留（不串味）');

console.log('\n=== E. 隔离性：同一批次内互不影响，跨批次互不影响 ===');
// 批次A 判了 2 张，批次B 只有 1 张；检查计数
const nDel = items.filter(it => api.verdictOf(it) === 'del').length;
const nKeep = items.filter(it => api.verdictOf(it) === 'keep').length;
eq([nDel, nKeep], [1, 2], '淘汰 1 / 保留 2');
// 把批次B 那张从保留夹拿掉，批次A 的两张必须不动
sets[2].paths = [];
api.setMarks(sets);
eq(api.verdictOf(items[2]), '', '批次B 取消后为空');
eq(api.verdictOf(items[1]), 'keep', '批次A 的保留仍在（没被误伤）');

console.log('\n=== F. 保留优先 + 旧数据兼容 ===');
const it0 = items[0];
const both = [
  {id: 'verdict-reject@926266f5', name: '淘汰·926266f5', paths: [it0.uid], note: {}, color: 0},
  {id: 'verdict-keep@926266f5', name: '保留·926266f5', paths: [it0.uid], note: {}, color: 2},
];
api.setMarks(both);
eq(api.verdictOf(it0), 'keep', '同时在两夹时取「保留」优先');
api.setMarks([{id: 'verdict-reject', name: '淘汰', paths: [it0.uid], note: {}, color: 0}]);
eq(api.verdictOf(it0), 'del', '老的固定 id 夹（升级前标的）仍能读出淘汰');

console.log('\n=== G. 同路径跨批次不误判（uid 优先） ===');
const sameName = [
  {uid: 'C:/D/926266f5/x.png#926266f5#1', path: 'C:/D/926266f5/x.png'},
  {uid: 'C:/D/1c0c149f/x.png#1c0c149f#1', path: 'C:/D/1c0c149f/x.png'},
];
api.setItems(sameName);
api.setMarks([{id: 'verdict-keep@926266f5', name: '保留·926266f5',
  paths: [sameName[0].uid], note: {}, color: 2}]);
eq(api.verdictOf(sameName[0]), 'keep', '批次A 的 x.png = 保留');
eq(api.verdictOf(sameName[1]), '', '批次B 的同名 x.png 未被误标');

console.log('\n=== H. 老夹继承：同一批的图不能分裂成两个夹（真实数据场景） ===');
/* 用户实测：他在 V2.32 标的 15 张「保留」全在无后缀 verdict-keep 里，
   且全部来自批次 926266f5。升级后如果再新建 verdict-keep@926266f5，
   同一批图就分成两个夹 —— 在用户看来等于「我之前标的没了」。 */
const legacyPath = 'C:/Users/wyj/Desktop/D/T/926266f5/002.png#926266f5#2';
api.setMarks([{id: 'verdict-keep', name: '保留', paths: [legacyPath],
  note: {}, color: 2}]);
eq(api.inheritSetId('keep', '926266f5'), 'verdict-keep',
   '同批次有老夹 → 继续写老夹（不分裂）');
eq(api.inheritSetId('keep', '1c0c149f'), 'verdict-keep@1c0c149f',
   '别的批次 → 新建自己的派生夹（照常分批次）');
eq(api.inheritSetId('del', '926266f5'), 'verdict-reject@926266f5',
   '老夹里没有淘汰 → 淘汰照常新建派生夹');
eq(api.inheritSetId('keep', 'x'), 'verdict-keep@x', '占位批次不继承');

console.log('\n=== I. targetSet：继承时的夹名沿用老名 ===');
const t1 = api.targetSet('keep', {uid: legacyPath});
eq(t1, {id: 'verdict-keep', name: '保留'}, '继承老夹时名字仍是「保留」');
const t2 = api.targetSet('keep', {uid: 'C:/D/1c0c149f/a.png#1c0c149f#1'});
eq(t2, {id: 'verdict-keep@1c0c149f', name: '保留·1c0c149f'}, '新批次用带批次的名字');

console.log('\n=== J. 老夹里没有同批次图时不继承（避免抢别批次的夹） ===');
api.setMarks([{id: 'verdict-keep', name: '保留',
  paths: ['C:/D/aaaa1111/a.png#aaaa1111#1'], note: {}, color: 2}]);
eq(api.inheritSetId('keep', '926266f5'), 'verdict-keep@926266f5',
   '老夹里只有别的批次 → 不继承，新建自己的');
eq(api.inheritSetId('keep', 'aaaa1111'), 'verdict-keep',
   '与老夹同批次 → 继承');

console.log('\n=== 结果 ===');
if (fail === 0) {
  console.log('PASS  ' + pass + ' 项全部通过');
  process.exit(0);
} else {
  console.log('FAIL  ' + fail + ' 项失败 / ' + pass + ' 项通过');
  failures.forEach(f => console.log('   - ' + f));
  process.exit(3);
}
