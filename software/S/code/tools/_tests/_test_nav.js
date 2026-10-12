// 功能级验证：看图页「下一张」是否走筛选后的集合
// 用法: node _test_nav.js <渲染好的html>
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync(process.argv[2], 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const src = scripts[scripts.length - 1];

// ---- 万能桩元素：任何属性访问都返回可用的桩/函数 ----
function mkEl() {
  const store = {};
  const el = {
    id: '', textContent: '', value: '', src: '', className: '',
    style: new Proxy({}, { get: (t, k) => (k in t ? t[k] : (t[k] = () => {})), set: () => true }),
    dataset: new Proxy({}, { get: () => undefined, set: () => true }),
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); },
      toggle(c, f) { if (f) this._s.add(c); else this._s.delete(c); },
    },
    appendChild() {}, removeChild() {}, remove() {},
    querySelector() { return mkEl(); }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }; },
    setAttribute() {}, getAttribute() { return ''; },
    addEventListener() {}, removeEventListener() {}, closest() { return mkEl(); },
    getElementsByTagName() { return []; }, insertBefore() {}, contains() { return false; },
    focus() {}, blur() {}, click() {},
  };
  return el;
}
const cache = {};
const document = {
  getElementById(id) { return cache[id] || (cache[id] = mkEl()); },
  querySelector(sel) { return cache[sel] || (cache[sel] = mkEl()); },
  querySelectorAll() { return []; },
  createElement() { return mkEl(); },
  createTextNode() { return mkEl(); },
  addEventListener() {}, removeEventListener() {},
  body: mkEl(), documentElement: mkEl(), head: mkEl(),
};
const win = { addEventListener() {}, removeEventListener() {}, _shown: null, matchMedia: () => ({ matches: false, addEventListener() {} }) };
const ctx = vm.createContext({
  document, window: win,
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  Chart: function () { return { destroy() {} }; },
  fetch: () => Promise.reject(new Error('no net')),
  setInterval: () => 0, setTimeout: () => 0, clearTimeout: () => {}, clearInterval: () => {},
  console, JSON, Math, Object, Array, Set, Map, Date, RegExp, Error,
  parseFloat, parseInt, String, Number, isNaN, isFinite, Boolean,
  encodeURIComponent, decodeURIComponent, Promise, Symbol,
  location: { reload() {}, href: '', search: '' },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
});

const EXPOSE = `;globalThis.__x = {
  get D(){return D}, get vi(){return vi}, get PAGE(){return PAGE},
  get hit(){return hit}, get sel(){return sel},
  get _shown(){return window._shown}, get _ver(){return _ver},
  get _scopeUids(){return _scopeUids},
  setScope(uids){ _scopeUids=uids.slice(); },
  openV, openVByUid, step, sync, apply, renderGrid, cur, setImages, markedImages,
  get SETS(){return SETS}, setsOf, addToSet, removeFromSet, newSet, switchSet,
};`;

try {
  new vm.Script(src + EXPOSE, { filename: 'atlas.js' }).runInContext(ctx);
} catch (e) {
  console.log('❌ 脚本加载失败:', e.message);
  console.log(e.stack.split('\n').slice(0, 3).join('\n'));
  process.exit(2);
}
const X = ctx.__x;
console.log('✅ 脚本加载成功 · 图片总数', X.D.images.length);

const imgs = X.D.images;
const s = X.SETS[0];
if (!s) { console.log('❌ 没有标记夹'); process.exit(1); }
s.paths.clear();
[imgs[2], imgs[50], imgs[100]].forEach(im => X.addToSet(im.uid, s.id));
const inSet = X.setImages(s);
console.log('标记夹「' + s.name + '」含', inSet.length, '张:',
  inSet.map(im => im.path.split('/').pop()).join(', '));

// 模拟「进入标记页」：登记 scope（等价 renderMarks 里的 _scopeUids=list.uid）
X.setScope(inSet.map(im => im.uid));

X.openVByUid(imgs[2].uid);
const list = X.cur().map(im => im.path);
console.log('\n[打开后] cur() 长度 =', list.length, ' 目标 =', list[X.vi].split('/').pop());
const isFiltered = list.length === inSet.length;
console.log(isFiltered ? '✅ cur() 是筛选后的 ' + inSet.length + ' 张'
  : `❌ cur() 是全量 ${list.length} 张（应为 ${inSet.length}）`);

X.step(1);
const after = X.cur()[X.vi].path;
console.log('[下一张] →', after.split('/').pop(), ' 期望 →', inSet[1].path.split('/').pop());
const ok = after === inSet[1].path;
console.log(ok ? '✅ PASS：下一张 = 筛选后第 2 张' : '❌ FAIL：下一张跳出了筛选集');

// 用例 2：循环回绕 —— 在最后一张按下一张应回到第 1 张
X.step(1);           // → 第 3 张
X.step(1);           // → 回绕到第 1 张
const wrap = X.cur()[X.vi].path === inSet[0].path;
console.log(wrap ? '✅ PASS：末张再下一张回绕到首张' : '❌ FAIL：回绕不正确 → ' + X.cur()[X.vi].path.split('/').pop());

// 用例 3：未登记 scope（图库直接点图）时退回 hit
X.setScope([]);
X.apply();                                  // hit = 全量（无条件）
const hlen = X.hit.length;
X.openVByUid(imgs[7].uid);
const ok3 = X.cur().length === hlen && X.cur()[X.vi].uid === imgs[7].uid;
console.log(ok3 ? '✅ PASS：无 scope 时退回图库筛选集（' + hlen + ' 张）'
  : `❌ FAIL：无 scope 行为异常（cur=${X.cur().length}, 期望=${hlen}）`);

process.exit(ok && isFiltered && wrap && ok3 ? 0 : 1);
