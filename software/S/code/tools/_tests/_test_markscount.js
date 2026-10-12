// 验证页头「标记 N 张」芯片：去重计数是否正确
const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const src = scripts[scripts.length - 1];

function mkEl() {
  const el = {
    id: '', textContent: '', innerHTML: '', value: '', src: '', className: '',
    _children: [],
    style: new Proxy({}, { get: (t, k) => (k in t ? t[k] : (t[k] = () => {})), set: () => true }),
    dataset: new Proxy({}, { get: () => undefined, set: () => true }),
    classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, contains(c) { return this._s.has(c); }, toggle(c, f) { f ? this._s.add(c) : this._s.delete(c); } },
    appendChild(c) { if (c && c.id) this._children.push(c); }, removeChild() {}, remove() {},
    querySelector() { return mkEl(); }, querySelectorAll() { return []; },
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
  querySelectorAll() { return []; }, createElement() { return mkEl(); }, createTextNode(t) { return { text: t }; },
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
const EXPOSE = `;globalThis.__x={get SETS(){return SETS},setSets(a){SETS=a;},
  updateStampMarks,setImages,addToSet,newSet,get D(){return D},applyData,get stampEl(){return document.getElementById('stamp')}};`;
new vm.Script(src + EXPOSE, { filename: 'a.js' }).runInContext(ctx);
const X = ctx.__x;

// 构造：3 个夹，共登记 5 条，但只有 3 张不同的图（2 条重复）
const imgs = X.D.images;
const mk = (id, name, paths) => ({ id, name, paths: new Set(paths), note: {}, color: 0 });
X.setSets([
  mk('s1', 'A', [imgs[0].uid, imgs[1].uid]),
  mk('s2', 'B', [imgs[1].uid]),              // 与 A 重复 imgs[1]
  mk('s3', 'C', [imgs[2].uid, imgs[2].uid]),  // 同夹内重复
]);
const rawTotal = 2 + 1 + 2;
X.updateStampMarks();
const chip = cache['stampMarks'];
console.log('原始登记条数:', rawTotal, ' 期望去重后:', 3);
console.log('芯片文本:', chip ? chip.textContent : '(无)');
const ok = chip && chip.textContent.includes('标记 3 张');
console.log(ok ? '✅ PASS：去重计数正确' : '❌ FAIL：计数不对');
process.exit(ok ? 0 : 1);
