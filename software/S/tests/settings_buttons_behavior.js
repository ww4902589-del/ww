// Behaviour check for the page's real settings functions, with a small DOM stub.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(process.argv[2], 'utf8');
function section(start, end) {
  const a = source.indexOf(start);
  const b = source.indexOf(end, a + start.length);
  assert(a >= 0 && b > a, `missing function boundary: ${start}`);
  return source.slice(a, b);
}

const snippet = [
  section('function renderPresetOptions(){', 'function renderImagePresetOptions(){'),
  section('async function deletePreset(button){', 'async function restoreDefaultPresets(btn){'),
  section('async function clearSavedParams(btn){', 'function togglePrompt('),
].join('\n');

const elements = Object.fromEntries([
  'presetPicker', 'assignPreset', 'presetName', 'updatePresetButton',
  'reloadPresetButton', 'deletePresetButton', 'workflow',
].map(id => [id, { value: '', disabled: false, innerHTML: '' }]));
elements.presetPicker.value = 'saved';
elements.assignPreset.value = 'saved';
elements.workflow.value = 'flow.json';
const notices = [];
let requests = [];
const context = {
  inventory: { style_lora_presets: [{ id: 'saved', name: 'Saved' }] },
  activePresetId: 'saved', bundle: null, workbench: { workflow_defaults: { steps: 20 } },
  $: id => elements[id], esc: x => String(x),
  presets: () => context.inventory.style_lora_presets,
  presetById: id => context.inventory.style_lora_presets.find(x => x.id === id),
  opts() {}, showPresetName() {}, renderBundle() {}, renderConfigFeedback() {},
  renderWorkbench() { context.rendered = true; },
  setBusy(button, busy) { button.disabled = busy; },
  notify(message, type) { notices.push({ message, type }); },
  confirm() { return true; },
  async api(path) {
    requests.push(path);
    if (path === '/api/delete-style-lora-preset') return { presets: [] };
    if (path === '/api/params/clear') return { params: { workflow_defaults: {} } };
    throw new Error('unexpected request');
  },
};
vm.createContext(context);
vm.runInContext(snippet, context);

(async () => {
  await context.deletePreset(elements.deletePresetButton);
  assert.equal(requests.length, 1);
  assert.equal(elements.presetPicker.value, '');
  for (const id of ['updatePresetButton', 'reloadPresetButton', 'deletePresetButton']) {
    assert.equal(elements[id].disabled, true, `${id} must be disabled without a selected preset`);
  }
  assert(notices.some(x => x.type === 'success' && x.message.includes('已删除预设')));

  await context.clearSavedParams({ disabled: false });
  assert.equal(context.workbench.workflow_defaults.steps, undefined);
  assert.equal(context.rendered, true);
  assert(requests.includes('/api/params/clear'));

  context.api = async () => { throw new Error('write failed'); };
  context.workbench = { workflow_defaults: { steps: 30 } };
  context.rendered = false;
  const before = notices.length;
  await context.clearSavedParams({ disabled: false });
  assert.equal(context.workbench.workflow_defaults.steps, 30);
  assert.equal(context.rendered, false);
  assert.deepEqual(notices.slice(before).map(x => x.type), ['error']);
})().catch(error => { console.error(error); process.exitCode = 1; });
