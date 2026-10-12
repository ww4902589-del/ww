// 验证：图库筛选后，点图 → 下一张 应走筛选结果（hit），不是全量
const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const src = scripts[scripts.length - 1];

function mkEl() {
  const el = {
    id: '', textContent: '', value: '', src: '', className: '',
    style: new Proxy({}, { get: (t, k) => (k in t ? t[k] : (t[k] = () => {})), set: () => true }),
    dataset: new Proxy({}, { get: () => undefined, set: () => true }),
    classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, contains(c) { return this._s.has(c); }, toggle(c, f) { f ? this._s.add(c) : this._s.delete(c); } },
    appendChild() {}, removeChild() {}, remove() {}, querySelector() { return mkEl(); }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { left: 0, top: 0, right: 0, bottom: 0 }; },
    setAttribute() {}, getAttribute() { return ''; }, addEventListener() {}, closest() { return mkEl(); },
    getElementsByTagName() { return []; }, insertBefore() {}, contains() { return false; }, focus() {}, click() {},
  };
  return el;
}
const cache = {};
const document = {
  getElementById(id) { return cache[id] || (cache[id] = mkEl()); },
  querySelector(s) { return cache[s] || (cache[s] = mkEl()); },
  querySelectorAll() { return []; }, createElement() { return mkEl(); }, createTextNode() { return mkEl(); },
  addEventListener() {}, body: mkEl(), documentElement: mkEl(), head: mkEl(),
};
const ctx = vm.createContext({
  document, window: { addEventListener() {}, _shown: null },
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  Chart: function () { return { destroy() {} }; }, fetch: () => Promise.reject(new Error('x')),
  setInterval: () => 0, setTimeout: () => 0, clearTimeout: () => {}, clearInterval: () => {},
  console, JSON, Math, Object, Array, Set, Map, Date, RegExp, Error,
  parseFloat, parseInt, String, Number, isNaN, isFinite, Boolean,
  encodeURIComponent, decodeURIComponent, Promise, Symbol,
  location: { protocol: 'file:', reload() {} }, localStorage: { getItem: () => null, setItem() {} },
});
const EXPOSE = `;globalThis.__x={get hit(){return hit},get vi(){return vi},get PAGE(){return PAGE},
  get sel(){return sel},setSel(k,v){sel[k]=new Set([v]);},get cur(){return cur()},
  get D(){return D},
  apply,openV,step,renderGrid,get PER(){return PER}};`;
new vm.Script(src + EXPOSE, { filename: 'a.js' }).runInContext(ctx);
const X = ctx.__x;

// 先取一个确定非空、且元素数 < 全量的筛选维度值
const allImgs = X.D.images;
console.log('全量:', allImgs.length);
const folderCount = {};
allImgs.forEach(im => { folderCount[im.folder] = (folderCount[im.folder] || 0) + 1; });
const pick = Object.entries(folderCount).filter(([k, v]) => v > 1 && v < 50).sort((a, b) => a[1] - b[1])[0];
console.log('筛选 folder =', pick[0], '共', pick[1], '张');

X.setSel('folder', pick[0]);
X.apply();
console.log('hit 长度 =', X.hit.length, '（期望', pick[1], '）');

X.openV(0);   // 点第 1 张
console.log('打开后 cur 长度 =', X.cur.length, '目标 =', X.cur[X.vi].path.split('/').pop());
X.step(1);
const after = X.cur[X.vi];
console.log('下一张 →', after.path.split('/').pop(), 'folder =', after.folder);

const ok = X.hit.length === pick[1] && X.cur.length === pick[1] && after.folder === pick[0];
console.log(ok ? '✅ PASS：筛选后翻页锁在 hit 内' : '❌ FAIL');
process.exit(ok ? 0 : 1);
