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
  section('async function savePreset(asNew){', 'function enrichPresetLora('),
  section('async function deletePreset(button){', 'async function restoreDefaultPresets(btn){'),
  section('async function clearSavedParams(btn){', 'function togglePrompt('),
  section('function addLora(){', 'async function rememberLora(index){'),
  section('async function deleteLoraProfile(index,button){', 'function toggleLLM(){'),
  section('async function clearWorkbench(btn){', '/* ---- 事件流'),
].join('\n');

const elements = Object.fromEntries([
  'presetPicker', 'assignPreset', 'presetName', 'updatePresetButton',
  'reloadPresetButton', 'deletePresetButton', 'workflow', 'loraPick',
].map(id => [id, { value: '', disabled: false, innerHTML: '' }]));
elements.presetPicker.value = 'saved';
elements.assignPreset.value = 'saved';
elements.workflow.value = 'flow.json';
const notices = [];
let requests = [];
const context = {
  inventory: {
    style_lora_presets: [{ id: 'saved', name: 'Saved' }],
    loras: [{ value: 'artist.safetensors', name: 'artist.safetensors',
      trigger_words: ['old-trigger'], has_saved_profile: true }],
  },
  activePresetId: 'saved', bundle: null, workbench: { workflow_defaults: { steps: 20 } },
  selectedStyles: [], selectedLoras: [], batchParams: { steps: 42 },
  $: id => elements[id], esc: x => String(x),
  presets: () => context.inventory.style_lora_presets,
  presetById: id => context.inventory.style_lora_presets.find(x => x.id === id),
  opts() {}, showPresetName() {}, renderBundle() {}, renderConfigFeedback() {},
  renderLoras() {}, renderLoraOptions() {},
  renderWorkbench() { context.rendered = true; },
  setBusy(button, busy) { button.disabled = busy; },
  notify(message, type) { notices.push({ message, type }); },
  confirm() { return true; },
  async api(path) {
    requests.push(path);
    if (path === '/api/delete-style-lora-preset') return { presets: [] };
    if (path === '/api/save-style-lora-preset') {
      return { preset: { id: 'new', name: 'New' }, presets: [{ id: 'new', name: 'New' }] };
    }
    if (path === '/api/params/clear') return { params: { workflow_defaults: {} } };
    if (path === '/api/delete-lora-profile') return {};
    if (path === '/api/inventory') return { inventory: {
      loras: [{ value: 'artist.safetensors', name: 'artist.safetensors',
        trigger_words: [], has_saved_profile: false }],
    } };
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

  elements.presetName.value = 'New';
  context.selectedLoras = [{ name: 'artist.safetensors', strength: 1 }];
  await context.savePreset(true);
  assert.equal(elements.presetPicker.value, 'new');
  for (const id of ['updatePresetButton', 'reloadPresetButton', 'deletePresetButton']) {
    assert.equal(elements[id].disabled, false, `${id} must be enabled after saving`);
  }

  elements.loraPick.value = 'artist.safetensors';
  context.selectedLoras = [];
  context.addLora();
  assert.equal(context.selectedLoras[0].has_saved_profile, true);
  await context.deleteLoraProfile(0, { disabled: false });
  assert.equal(context.inventory.loras[0].has_saved_profile, false);
  assert.deepEqual(context.inventory.loras[0].trigger_words, []);
  assert.equal(context.selectedLoras[0].has_saved_profile, false);
  assert.deepEqual(Array.from(context.selectedLoras[0].trigger_words), ['old-trigger'],
    'the current row keeps its input after deleting the saved profile');
  context.selectedLoras = [];
  context.addLora();
  assert.deepEqual(Array.from(context.selectedLoras[0].trigger_words), [],
    're-adding uses fresh metadata instead of the deleted profile');

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

  context.loadWorkbench = async () => false;
  context.batchParams = { steps: 42 };
  await context.clearWorkbench({ disabled: false });
  assert.equal(context.batchParams.steps, 42,
    'a failed refresh must not discard the current batch values');
})().catch(error => { console.error(error); process.exitCode = 1; });
