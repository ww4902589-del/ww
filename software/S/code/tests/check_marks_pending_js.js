/* 从 build_atlas 渲染出的真页面里抠出真函数来跑，验证「收藏夹记录 vs 能显示的图」
   这一对数字的口径。为什么不用读代码的方式检查：这里的 bug 正是「两个数字来自
   不同来源」—— 夹标签读 s.paths.size（记录条数），网格读 D.images 过滤后的结果。
   只读代码看不出它们会分叉，必须把函数抓出来喂一组「记录里有、索引里没有」的
   数据真跑一遍。

   依赖：Node（managed）。
   用法：node tests/check_marks_pending_js.js [页面路径]
        页面路径默认 run/_atlas_page.html（由 run/_render_page_for_js.py 生成）。
*/
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const HTML_PATH = process.argv[2] || path.join(__dirname, '..', 'run', '_atlas_page.html');
if (!fs.existsSync(HTML_PATH)) {
  console.error('找不到页面：' + HTML_PATH + '\n先跑：python run/_render_page_for_js.py');
  process.exit(2);
}
const html = fs.readFileSync(HTML_PATH, 'utf8');
const m = html.match(/<script>([\s\S]*)<\/script>/);
if (!m) throw new Error('未找到 <script> 块');
const JS = m[1];

/* 按「函数名 + 括号配平」抠出单个函数源码 */
function grab(name) {
  const re = new RegExp('function\\s+' + name + '\\s*\\(', 'g');
  const mm = re.exec(JS);
  if (!mm) throw new Error('找不到函数 ' + name);
  let i = JS.indexOf('(', mm.index);
  let depth = 0;
  for (; i < JS.length; i++) {
    if (JS[i] === '(') depth++;
    else if (JS[i] === ')') { depth--; if (depth === 0) break; }
  }
  const j = JS.indexOf('{', i);
  depth = 0;
  for (let k = j; k < JS.length; k++) {
    if (JS[k] === '{') depth++;
    else if (JS[k] === '}') { depth--; if (depth === 0) return JS.slice(mm.index, k + 1); }
  }
  throw new Error('函数 ' + name + ' 括号不配平');
}

/* 抠出 `let _IX=...;` 这种单行声明 */
function grabDecl(prefix) {
  const line = JS.split('\n').find(l => l.trim().startsWith(prefix));
  if (!line) throw new Error('找不到声明：' + prefix);
  return line.trim();
}

const NEED = ['uidPath', 'ix', 'findImage', 'resolveSet', 'setImages', 'setPaths',
              'canonUid', 'canonSets',
              'baseName', 'dirName', 'shortDir'];
const src = [grabDecl('let _IX'), 'const gnorm=p=>String(p||"").replace(/\\\\/g,"/").toLowerCase();']
  .concat(NEED.map(grab)).join('\n');

/* ---------- 一组能逼出「记录里有、索引里没有」的数据 ----------
   两个夹：
     默认 —— 2 张真在索引里 + 1 条指向已搬走的旧路径 + 1 条指向索引里不存在的路径
     1    —— 1 张真在索引里 + 1 条与「默认」重复的旧路径（同一张图两处登记）
   旧版的行为：setImages() 只回 2 张与 1 张，而标签上的数字是 4 与 2。 */
const D = {
  build_id: 'js-test',
  images: [
    { uid: 'C:/img/live1.png#b1#1', path: 'C:/img/live1.png', name: 'live1.png' },
    { uid: 'C:/img/live2.png#b1#2', path: 'C:/img/live2.png', name: 'live2.png' },
    { uid: 'C:/img/other.png#b2#7', path: 'C:/img/other.png', name: 'other.png' },
    // 同名不同目录 —— 「待定位」候选就靠这个找回来
    { uid: 'C:/moved/live1.png#b1#1', path: 'C:/moved/live1.png', name: 'live1.png' },
  ],
};
const SETS = [
  { id: 's1', name: '默认', paths: new Set([
    'C:/img/live1.png#b1#1',
    'C:/img/live2.png#b1#2',
    'C:/gone/vanished.png#b9#9',          // 文件已不在磁盘上
    'C:/img/other.png#b2#7',              // 在磁盘上但不在索引里（同一回事）
  ]), note: {}, color: 0 },
  { id: 's2', name: '1', paths: new Set([
    'C:/moved/live1.png#b1#1',
    'C:/gone/vanished.png#b9#9',          // 与「默认」重复的旧路径
  ]), note: {}, color: 1 },
];
const exists = p => !String(p).includes('/gone/');
const fsStub = { existsSync: exists };

const sandbox = { D, SETS, console, _IX: null, require: () => fsStub };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);

const {
  resolveSet, setImages, findImage, baseName, uidPath,
} = sandbox;

/* ---------- 断言 ---------- */
let pass = 0, fail = 0;
function eq(what, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log((ok ? 'PASS ' : 'FAIL ') + what +
    (ok ? '' : '\n     got  ' + JSON.stringify(got) + '\n     want ' + JSON.stringify(want)));
  ok ? pass++ : fail++;
}
function ok_(what, cond, detail) {
  console.log((cond ? 'PASS ' : 'FAIL ') + what + (cond ? '' : '  :: ' + JSON.stringify(detail)));
  cond ? pass++ : fail++;
}

const r1 = resolveSet(SETS[0]);
eq('默认夹：能显示的图（按索引匹配 + 去重）', r1.live.map(i => i.name), ['live1.png', 'live2.png', 'other.png']);
eq('默认夹：待定位的记录原样留着，不丢', r1.dead,
  ['C:/gone/vanished.png#b9#9']);
ok_('默认夹：可显示 + 待定位 == 记录条数（两个数字必须能对上）',
  r1.live.length + r1.dead.length === SETS[0].paths.size,
  { live: r1.live.length, dead: r1.dead.length, records: SETS[0].paths.size });

const r2 = resolveSet(SETS[1]);
eq('夹「1」：同一条旧记录在两处登记，两边都留着', r2.dead, ['C:/gone/vanished.png#b9#9']);
ok_('夹「1」：可显示 1 张、待定位 1 条 —— 旧版这里是「1 张 / 数字写 2」', r2.live.length === 1 && r2.dead.length === 1,
  { live: r2.live.length, dead: r2.dead.length });

/* 同一张图被 uid 一条 + 路径一条登记时要去重（否则夹里的数字比真实张数多） */
const dupSet = { id: 's3', name: 'dup', paths: new Set([
  'C:/img/live1.png#b1#1', 'C:/img/live1.png']), note: {} };
const r3 = resolveSet(dupSet);
ok_('uid 与路径双写同一张图，只算一张', r3.live.length === 1 && r3.live[0].uid === 'C:/img/live1.png#b1#1',
  r3.live.map(i => i.uid));

/* 路径兜底：uid 漂移（批次/序号变了）时仍应命中同一张图 */
const drifted = { id: 's4', name: 'drift', paths: new Set(['C:/img/live1.png#OLD#999']), note: {} };
const r4 = resolveSet(drifted);
ok_('uid 漂移时按路径兜底命中', r4.live.length === 1 && r4.live[0].path === 'C:/img/live1.png', r4);

/* setImages 仍是「只取能显示的」，老调用方不受影响 */
eq('setImages() 仍返回可显示的那部分', setImages(SETS[0]).length, 3);

/* ---------- uid 规范化：掐掉「旧标签页把陈旧 uid 并回来」这一类回潮 ----------
   真数据上发生过：迁移把 30 条死引用 + 2 条 uid 漂移修好之后，一个升级前
   打开的旧标签页做了一次「本地 ∪ 服务端」的并集写回，那 32 条原样回来了
   （46/36 → 63/51）。规范化之后同一个文件在两边算出同一个 uid，并集变幂等。 */
const { canonUid, canonSets } = sandbox;

eq('规范 uid：漂移的批次/序号换成索引里的真 uid',
  canonUid('C:/img/live1.png#OLD#999'), 'C:/img/live1.png#b1#1');
eq('规范 uid：文件确实不在索引里 → 原样保留（那是「待定位」，不能抹掉）',
  canonUid('C:/gone/vanished.png#b9#9'), 'C:/gone/vanished.png#b9#9');

/* 旧 uid + 新 uid 指的是同一个文件 → 塌缩成一条 */
const stale = [{ id: 's1', name: '默认', paths: new Set([
  'C:/img/live1.png#b1#1', 'C:/img/live1.png#OLD#999',
  'C:/img/live2.png#b1#2']), note: {}, color: 0 }];
const fixed = canonSets(stale);
eq('规范化把「同一文件的两个 uid」并成一条', [...fixed[0].paths],
  ['C:/img/live1.png#b1#1', 'C:/img/live2.png#b1#2']);
ok_('规范化不改动真丢失的记录（守恒）',
  [...fixed[0].paths].length + 0 === 2 &&
  canonSets([{ id: 'x', name: 'x', paths: new Set(['C:/gone/vanished.png#b9#9']), note: {} }])[0]
    .paths.has('C:/gone/vanished.png#b9#9'), null);

/* 幂等 —— 已经规整过就**返回同一个数组引用**，否则每轮 applyRemoteData
   都会判成「变了」而白白写一次盘（页面上是每次刷新都 POST 一次 /api/marks）。 */
const clean = [{ id: 's1', name: '默认', paths: new Set([
  'C:/img/live1.png#b1#1', 'C:/img/live2.png#b1#2']), note: {}, color: 0 }];
ok_('已经规整过的集合原样返回（同一个引用，不触发多余写回）',
  canonSets(clean) === clean, null);

/* 旧标签页回潮的完整场景：服务端那份（规范）∪ 本地那份（陈旧）→ 规范化后
   必须收敛回服务端那份，而不是两边叠加。 */
const serverSide = [{ id: 's1', name: '默认', paths: new Set([
  'C:/img/live1.png#b1#1', 'C:/img/live2.png#b1#2', 'C:/img/other.png#b2#7']), note: {}, color: 0 }];
const unioned = [{ id: 's1', name: '默认', paths: new Set([
  'C:/img/live1.png#b1#1', 'C:/img/live2.png#b1#2', 'C:/img/other.png#b2#7',
  'C:/img/live1.png#OLD#999', 'C:/img/live2.png#OLD#999']), note: {}, color: 0 }];
eq('旧标签页并回来的陈旧 uid 会被收敛掉（回潮不再叠加）',
  [...canonSets(unioned)[0].paths], [...serverSide[0].paths]);

/* 索引缓存：D.images 换了就重建 */
const before = sandbox.ix().n;
D.images = D.images.slice(0, 2);
ok_('索引反查表会跟着数据重建', sandbox.ix().n === 2 && before === 4, { before, after: sandbox.ix().n });

/* 待定位那一行要用到的路径工具 */
eq('baseName 取文件名', baseName('C:/a/b/图.png'), '图.png');
eq('uidPath 剥掉 #批次#序号', uidPath('C:/a/b/图.png#x#1'), 'C:/a/b/图.png');

console.log('\n合计 ' + (pass + fail) + ' 项：通过 ' + pass + '，失败 ' + fail);
process.exit(fail ? 1 : 0);
