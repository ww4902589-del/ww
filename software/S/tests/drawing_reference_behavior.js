// Exercise the drawing controls through their DOM events and the existing import route.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..', 'src');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const source = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const encoderStart = source.indexOf('async function fileBase64(');
const encoderEnd = source.indexOf("$('imageFiles')", encoderStart);
const start = source.indexOf('/* ---- 绘画参考画布');
const end = source.indexOf('/* ----', start + 5);
assert.ok(encoderStart >= 0 && encoderEnd > encoderStart && start >= 0 && end > start, '绘画参考应复用图片编码且有独立事件区段');
for (const id of ['drawingCanvas', 'drawingImport', 'drawingStatus', 'drawingTool']) {
  assert.match(html, new RegExp(`id="${id}"`), `${id} 应出现在页面上`);
}

const listeners = new Map();
const calls = [];
const drawCalls = [];
const elements = {};
let canvasInk = false;
function element(id, value = '') {
  return elements[id] = {
    value, disabled: false, textContent: '', files: [],
    addEventListener(name, fn) { listeners.set(`${id}:${name}`, fn); },
  };
}
const canvas = element('drawingCanvas');
canvas.width = 640;
canvas.height = 400;
canvas.getBoundingClientRect = () => ({left: 0, top: 0, width: 640, height: 400});
canvas.setPointerCapture = () => {};
canvas.releasePointerCapture = () => {};
canvas.toBlob = (callback, format) => {
  assert.equal(format, 'image/png', '画布应以 PNG 导出');
  callback({arrayBuffer: async () => Uint8Array.from([137, 80, 78, 71]).buffer});
};
canvas.getContext = () => ({
  fillStyle: '', strokeStyle: '', lineWidth: 0, lineCap: '', lineJoin: '',
  fillRect: () => {canvasInk = false;}, beginPath: () => {}, moveTo: () => {}, lineTo: () => {},
  stroke() {canvasInk = this.strokeStyle !== '#ffffff';},
  fill() {canvasInk = this.fillStyle !== '#ffffff';},
  arc: (...args) => drawCalls.push(['dot', ...args]), rect: (...args) => drawCalls.push(['rect', ...args]),
  ellipse: (...args) => drawCalls.push(['ellipse', ...args]),
  getImageData: () => ({data: Uint8ClampedArray.from([canvasInk ? 0 : 255, 255, 255, 255])}),
  putImageData: image => {canvasInk = image.data[0] !== 255;},
  drawImage: (...args) => {canvasInk = true; drawCalls.push(['background', ...args.slice(1)]);},
});
element('drawingTool', 'brush');
element('drawingColor', '#222222');
element('drawingSize', '5');
element('drawingStatus');
element('drawingImport');
element('drawingClear');
element('drawingUndo');
element('drawingRedo');
element('drawingExport');
element('drawingBackground');
element('imageMode', 'ai_enhance');
element('mappingButton');
element('bundleInfo');

const context = vm.createContext({
  $: id => elements[id],
  api: async (route, options) => {
    calls.push([route, JSON.parse(options.body)]);
    return {bundle: {items: [{title: '绘画参考', metadata: {source_image: 'imports/a/drawing.png'}}]}};
  },
  notify: message => calls.push(['notify', message]),
  setBusy: (button, busy) => { button.disabled = busy; },
  renderBundle: () => calls.push(['render']),
  precheckAfterImport: () => calls.push(['precheck']),
  selectedPromptIndexes: new Set(),
  importMapping: null,
  bundle: null,
  btoa: bytes => Buffer.from(bytes, 'binary').toString('base64'),
  Uint8Array, Uint8ClampedArray,
  Image: class {
    width = 320;
    height = 200;
    set src(_value) { Promise.resolve().then(() => this.onload()); }
  },
  URL: {createObjectURL: () => 'blob:fake', revokeObjectURL: () => {}},
  setTimeout: callback => callback(),
  document: {createElement: () => ({click: () => calls.push(['download'])})},
});
vm.runInContext(source.slice(encoderStart, encoderEnd), context);
vm.runInContext(source.slice(start, end), context);
async function fire(id, event, detail = {}) {
  const handler = listeners.get(`${id}:${event}`);
  assert.ok(handler, `${id}:${event} 应可操作`);
  await handler({pointerId: 1, clientX: 10, clientY: 10, preventDefault() {}, ...detail});
}

(async () => {
  await fire('drawingImport', 'click');
  assert.equal(calls.filter(row => row[0] === '/api/import-images').length, 0, '空画布不得导入');
  await fire('drawingCanvas', 'pointerdown');
  await fire('drawingCanvas', 'pointerup');
  assert.ok(drawCalls.some(row => row[0] === 'dot'), '单击画笔应留下可见圆点');
  await fire('drawingImport', 'click');
  const requests = calls.filter(row => row[0] === '/api/import-images');
  assert.equal(requests.length, 1);
  assert.equal(requests[0][1].files[0].filename, '绘画参考.png');
  assert.equal(requests[0][1].files[0].base64, 'iVBORw==');
  assert.equal(requests[0][1].mode, 'ai_enhance');
  assert.ok(calls.some(row => row[0] === 'render'));
  assert.ok(calls.some(row => row[0] === 'precheck'));
  await fire('drawingUndo', 'click');
  await fire('drawingImport', 'click');
  assert.equal(calls.filter(row => row[0] === '/api/import-images').length, 1, '撤销到空画布不得导入');
  await fire('drawingRedo', 'click');
  await fire('drawingImport', 'click');
  assert.equal(calls.filter(row => row[0] === '/api/import-images').length, 2, '重做后可以再次建立任务');
  elements.drawingTool.value = 'eraser';
  await fire('drawingCanvas', 'pointerdown');
  await fire('drawingCanvas', 'pointerup');
  await fire('drawingImport', 'click');
  assert.equal(calls.filter(row => row[0] === '/api/import-images').length, 2, '完全擦白后不得导入');
  await fire('drawingExport', 'click');
  assert.equal(calls.filter(row => row[0] === 'download').length, 0, '完全擦白后不得导出');
  await fire('drawingClear', 'click');
  await fire('drawingImport', 'click');
  assert.equal(calls.filter(row => row[0] === '/api/import-images').length, 2, '清空后不得导入');
  elements.drawingTool.value = 'rect';
  await fire('drawingCanvas', 'pointerdown');
  await fire('drawingCanvas', 'pointercancel');
  await fire('drawingCanvas', 'pointermove', {clientX: 90, clientY: 60});
  await fire('drawingCanvas', 'pointerup', {clientX: 90, clientY: 60});
  await fire('drawingImport', 'click');
  assert.equal(calls.filter(row => row[0] === '/api/import-images').length, 2, '取消的形状不得留下可导入内容');
  elements.drawingTool.value = 'eraser';
  await fire('drawingCanvas', 'pointerdown');
  await fire('drawingCanvas', 'pointerup');
  await fire('drawingImport', 'click');
  assert.equal(calls.filter(row => row[0] === '/api/import-images').length, 2, '空画布上使用橡皮不应变成有效参考');
  elements.drawingTool.value = 'rect';
  await fire('drawingCanvas', 'pointerdown');
  await fire('drawingCanvas', 'pointermove', {clientX: 90, clientY: 60});
  await fire('drawingCanvas', 'pointerup', {clientX: 90, clientY: 60});
  assert.ok(drawCalls.some(row => row[0] === 'rect' && row[1] === 10 && row[2] === 10), '矩形工具应画出从起点到终点的形状');
  elements.drawingTool.value = 'ellipse';
  await fire('drawingCanvas', 'pointerdown');
  await fire('drawingCanvas', 'pointerup', {clientX: 90, clientY: 60});
  assert.ok(drawCalls.some(row => row[0] === 'ellipse'), '椭圆工具应可使用');
  elements.drawingTool.value = 'line';
  await fire('drawingCanvas', 'pointerdown');
  await fire('drawingCanvas', 'pointerup', {clientX: 90, clientY: 60});
  assert.equal(canvas.width, 640);
  assert.equal(canvas.height, 400);
  await fire('drawingExport', 'click');
  assert.equal(calls.filter(row => row[0] === 'download').length, 1, '有内容时可以下载 PNG');
  await fire('drawingClear', 'click');
  elements.drawingBackground.files = [{name: 'scene.png', type: 'image/png', size: 128}];
  await fire('drawingBackground', 'change');
  assert.ok(drawCalls.some(row => row[0] === 'background' && row[1] === 0 && row[2] === 0 && row[3] === 640 && row[4] === 400), '同宽高比背景应完整铺入画布');
  await fire('drawingImport', 'click');
  assert.equal(calls.filter(row => row[0] === '/api/import-images').length, 3, '导入背景后可以建立任务');
  console.log('drawing reference browser import: ok');
})().catch(error => {console.error(error); process.exitCode = 1;});
