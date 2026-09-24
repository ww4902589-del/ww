const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'src', 'app.js'), 'utf8');
const start = source.indexOf('const STYLE_STOP_WORDS=');
const end = source.indexOf('function addLora(){', start);
assert.ok(start > 0 && end > start);
const behavior = source.slice(start, end);

const anchor = {
  library: 'anime_动漫_styles', name: 'Watercolor Anime', display_name: '水彩动漫',
  prompt: 'soft watercolor anime shading',
};
const partner = {
  library: 'painting_绘画_styles', name: 'Soft Watercolor', display_name: '柔和水彩画',
  prompt: 'soft watercolor illustration',
};
const photo = {
  library: 'photo_摄影_styles', name: 'Watercolor Photo', display_name: '水彩摄影',
  prompt: 'soft watercolor photograph',
};
const blank = {
  library: 'painting_绘画_styles', name: 'Blank Watercolor', display_name: '空模板水彩',
  prompt: '',
};
const rows = [anchor, partner, photo, blank];
const ui = {
  styleRecommendations: {innerHTML: ''},
  styleRecommendationStatus: {textContent: ''},
};
const selectedStyles = [];
const selectedLoras = [{name: 'preserve.safetensors', strength: 0.7}];
const lorasBefore = JSON.stringify(selectedLoras);
let renderCount = 0;
let chosen = anchor;
const context = vm.createContext({
  inventory: {styles: rows, loras: selectedLoras},
  selectedStyles, selectedLoras,
  $: id => ui[id],
  styleSource: () => chosen,
  renderStyleBasket: () => {renderCount++; context.clearStyleRecommendations();},
  esc: value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c])),
});
vm.runInContext(behavior, context);

assert.ok(context.styleTerms('柔和水彩画').includes('水彩'));
const ranked = context.recommendStylePartners(rows, anchor, []);
assert.equal(ranked.length, 1);
assert.equal(ranked[0].row.name, partner.name);
assert.ok(ranked[0].shared.length > 0);
assert.equal(context.recommendStylePartners(rows, anchor, [partner]).length, 0);
assert.equal(context.recommendStylePartners(rows, {...anchor, prompt: ''}, []).length, 0);

context.showStyleRecommendations();
assert.match(ui.styleRecommendationStatus.textContent, /1 个本地风格搭配/);
assert.match(ui.styleRecommendations.innerHTML, /柔和水彩画/);
assert.doesNotMatch(ui.styleRecommendations.innerHTML, /safetensors/);
context.applyStyleRecommendation(0);
assert.deepEqual(selectedStyles.map(x => x.name), [anchor.name, partner.name]);
assert.equal(JSON.stringify(selectedLoras), lorasBefore);
assert.equal(renderCount, 1);
assert.equal(ui.styleRecommendations.innerHTML, '');

selectedStyles.splice(0, selectedStyles.length, anchor);
context.showStyleRecommendations();
assert.match(ui.styleRecommendations.innerHTML, /加入组合/);
rows.splice(rows.indexOf(partner), 1);
context.applyStyleRecommendation(0);
assert.deepEqual(selectedStyles.map(x => x.name), [anchor.name]);
assert.match(ui.styleRecommendationStatus.textContent, /已变化/);
assert.equal(JSON.stringify(selectedLoras), lorasBefore);

rows.push(partner);
selectedStyles.splice(0, selectedStyles.length);
chosen = anchor;
context.showStyleRecommendations();
chosen = photo;
context.applyStyleRecommendation(0);
assert.equal(selectedStyles.length, 0);
assert.match(ui.styleRecommendationStatus.textContent, /已变化/);

selectedStyles.push(anchor, {...anchor, name: 'second'}, {...anchor, name: 'third'}, {...anchor, name: 'fourth'});
context.showStyleRecommendations();
assert.match(ui.styleRecommendationStatus.textContent, /达到 4 个/);
assert.equal(ui.styleRecommendations.innerHTML, '');
assert.equal(JSON.stringify(selectedLoras), lorasBefore);
assert.doesNotMatch(behavior, /\bapi\s*\(/);

console.log('local style recommendation behavior passed');
