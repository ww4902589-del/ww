const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '..', 'src', 'app.js'), 'utf8');
const functions = source.slice(
  source.indexOf('function updateReviewJumpStatus('),
  source.indexOf('async function reviewConfirm(')
);
const keyboard = source.slice(
  source.indexOf("document.addEventListener('keydown',e=>{"),
  source.indexOf('/* ---- 阶段 2', source.indexOf("document.addEventListener('keydown',e=>{"))
);
assert.ok(functions.startsWith('function updateReviewJumpStatus('));
assert.ok(keyboard.startsWith("document.addEventListener('keydown',e=>{"));

// The evaluated region calls helpers that live elsewhere in app.js, and one of them
// (the seed read-back helper) arrived with a later feature branch. Slice the real
// definition in rather than stubbing it, so this test keeps exercising app.js itself;
// if the helper is renamed or removed the slice comes back empty and the assert fires.
const seedHelper = source.slice(
  source.indexOf('function seedKeyOf('),
  source.indexOf('\nfunction ', source.indexOf('function seedKeyOf(') + 1)
);
assert.ok(seedHelper.startsWith('function seedKeyOf('), 'renderReview 依赖的种子键函数必须仍能从 app.js 切出');
// Guard the end bound too: renaming the *next* declaration (say to `const x=` or
// `async function`) would otherwise let the slice swallow it silently, because the
// start assertion would still pass.
assert.ok(seedHelper.trimEnd().endsWith('}'), '种子键切片必须在一个完整函数处结束');
assert.ok(!seedHelper.slice(0, -2).includes('\nfunction '), '种子键切片不得把后面的函数一起切进来');

const calls = [];
let keyHandler;
const cards = new Map([2, 7, 13].map(index => [index, {
  id: `card-${index}`,
  classList: {toggle: (name, enabled) => calls.push(['class', index, name, enabled])},
  scrollIntoView: options => calls.push(['scroll', index, options.block]),
  focus: options => calls.push(['focus', index, options.preventScroll]),
}]));
const elements = {
  reviewJumpIndex: {value: '', focus: () => calls.push(['input-focus']), select: () => calls.push(['select'])},
  reviewJumpStatus: {textContent: ''},
  drawer: {hidden: true},
  ...Object.fromEntries([...cards].map(([index, card]) => [`card-${index}`, card])),
};
const context = vm.createContext({
  reviewState: {results: [
    {index: 2, copied_to: 'a.png'},
    {index: 7, copied_to: ''},
    {index: 13, copied_to: 'c.png'},
  ]},
  activeIndex: -1,
  currentStep: 4,
  $: id => elements[id],
  document: {
    getElementById: id => elements[id],
    querySelectorAll: selector => selector === '.review-card' ? [...cards.values()] : [],
    querySelector: () => null,
    addEventListener: (name, handler) => {if (name === 'keydown') keyHandler = handler;},
  },
  reviewConfirm: (index, status) => calls.push(['confirm', index, status]),
  reviewReject: index => calls.push(['reject', index]),
  openViewer: index => calls.push(['viewer', index]),
  closeDrawer: () => {elements.drawer.hidden = true; calls.push(['close-drawer']);},
});
vm.runInContext(seedHelper + functions + keyboard, context);
const readActive = () => vm.runInContext('activeIndex', context);
const key = (name, options = {}) => {
  let prevented = false;
  keyHandler({
    key: name, defaultPrevented: false, ctrlKey: false, altKey: false,
    metaKey: false, shiftKey: false,
    target: {closest: () => null},
    preventDefault: () => {prevented = true;},
    ...options,
  });
  return prevented;
};

assert.equal(context.jumpToReview(), false);
assert.match(elements.reviewJumpStatus.textContent, /正整数/);
elements.reviewJumpIndex.value = '3';
assert.equal(context.jumpToReview(), false);
assert.equal(readActive(), -1);
assert.match(elements.reviewJumpStatus.textContent, /没有第 3 张/);
elements.reviewJumpIndex.value = '7';
assert.equal(context.jumpToReview(), true);
assert.equal(readActive(), 7);
assert.ok(calls.some(row => row[0] === 'scroll' && row[1] === 7 && row[2] === 'center'));
assert.ok(calls.some(row => row[0] === 'focus' && row[1] === 7 && row[2] === true));
assert.match(elements.reviewJumpStatus.textContent, /当前第 7 张/);

assert.equal(key('j'), true);
assert.equal(readActive(), 13);
assert.equal(key('k'), true);
assert.equal(readActive(), 7);
assert.equal(key('g'), true);
assert.ok(calls.some(row => row[0] === 'input-focus'));
assert.equal(key('y'), false); // No image for #7; review actions stay guarded.
assert.ok(!calls.some(row => row[0] === 'confirm'));
assert.equal(key('j'), true);
assert.equal(key('y'), true);
assert.ok(calls.some(row => row[0] === 'confirm' && row[1] === 13));

const count = calls.length;
context.currentStep = 3;
assert.equal(key('j'), false);
context.currentStep = 4;
assert.equal(key('j', {target: {closest: () => ({tagName: 'INPUT'})}}), false);
assert.equal(key('j', {ctrlKey: true}), false);
context.document.querySelector = () => ({open: true});
assert.equal(key('j'), false);
assert.equal(calls.length, count);

elements.drawer.hidden = false;
assert.equal(key('Escape', {target: {closest: selector => selector === 'button,a,summary' ? {} : null}}), false);
assert.equal(elements.drawer.hidden, true);
assert.ok(calls.some(row => row[0] === 'close-drawer'));

// A status/SSE snapshot redraws every card. The focused card must regain focus.
const renderSource = source.slice(
  source.indexOf('function renderReview(v){'),
  source.indexOf('function setReviewDensity(', source.indexOf('function renderReview(v){'))
);
const oldCard = {id: 'card-7', classList: {contains: name => name === 'review-card'}};
const newCard = {focus: options => calls.push(['restored-focus', options.preventScroll])};
const redrawDocument = {activeElement: oldCard};
const redrawElements = {
  abortBanner: {hidden: true, textContent: ''},
  reviewSummary: {textContent: ''},
  confirmAllButton: {textContent: ''},
  reviewJumpStatus: {textContent: ''},
  'card-7': newCard,
};
Object.defineProperty(redrawElements, 'reviewBoard', {
  value: {set innerHTML(_html) {redrawDocument.activeElement = null;}},
});
const redrawContext = vm.createContext({
  reviewState: {results: [], review: {}}, activeIndex: 7, currentStep: 4,
  dupGroups: new Set(), document: redrawDocument,
  $: id => redrawElements[id],
  cardHtml: row => `<article id="card-${row.index}"></article>`,
  syncReviewPicks: () => {}, updateReviewJumpStatus: () => {},
});
// renderReview reads the seed key through a helper defined elsewhere in app.js, so the
// slice alone is not self-contained. Evaluate the real definition alongside it.
vm.runInContext(seedHelper + renderSource, redrawContext);
redrawContext.renderReview({results: [{index: 7, copied_to: 'image.png'}], review: {by_status: {}}});
assert.ok(calls.some(row => row[0] === 'restored-focus' && row[1] === true));
const restoredCount = calls.filter(row => row[0] === 'restored-focus').length;
redrawDocument.activeElement = oldCard;
redrawContext.currentStep = 3;
redrawContext.renderReview({results: [{index: 7, copied_to: 'image.png'}], review: {by_status: {}}});
assert.equal(calls.filter(row => row[0] === 'restored-focus').length, restoredCount);
redrawDocument.activeElement = oldCard;
redrawContext.currentStep = 4;
delete redrawElements['card-7'];
redrawContext.renderReview({results: [{index: 9, copied_to: 'other.png'}], review: {by_status: {}}});
assert.equal(calls.filter(row => row[0] === 'restored-focus').length, restoredCount);
assert.equal(vm.runInContext('activeIndex', redrawContext), -1);

console.log('review navigation behavior passed');
