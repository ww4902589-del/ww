const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');
function section(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a + start.length);
  assert(a >= 0 && b > a);
  return source.slice(a, b);
}
const elements = new Proxy({}, {get(target, id) {
  return target[id] ||= {value: 'old-style', checked: false};
}});
const context = {
  selectedStyles: [{name: 'old-style'}], selectedLoras: [{name: 'old-lora'}], activePresetId: 'old',
  $: id => elements[id], currentImagePreset: () => null,
  batchOverrides: {}, renderStyleBasket() {}, renderLoras() {}, renderConfigFeedback() {}, notify() {},
};
vm.createContext(context);
vm.runInContext(section('function usePlainCombination(){', 'function renderStylePreview(){') +
  section('function batchConfig(){', 'function useDefaultOutput(){'), context);
context.usePlainCombination();
// Even if the selector is repopulated later, plain mode must not submit it.
elements.styleName.value = 'silently-restored-style';
const config = context.batchConfig();
assert.equal(config.style_application, 'none');
assert.equal(config.style_name, '');
assert.equal(config.styles.length, 0);
assert.equal(config.loras.length, 0);
assert.equal(context.activePresetId, '');
console.log('plain combination page behavior: OK');
