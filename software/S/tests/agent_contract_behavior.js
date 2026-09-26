// Exercise the page's public status/lease affordance through the real action handlers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');

function section(start, end) {
  const first = source.indexOf(start);
  const last = source.indexOf(end, first + start.length);
  assert.ok(first >= 0 && last > first, `missing page action section: ${start}`);
  return source.slice(first, last);
}

const elements = {
  agentActionStatus: {dataset: {}, textContent: ''},
  effective: {className: '', innerHTML: ''},
  drawer: {hidden: true},
  seedMode: {value: 'random'},
  seedValue: {value: ''},
};
const body = {dataset: {}, classList: {toggle() {}}};
const requests = [];
const notices = [];
let preflightReady = true;
let takeoverReady = true;
const context = vm.createContext({
  $: id => elements[id],
  document: {body},
  clientId: 'agent-contract-test',
  leaseState: {held: false, mine: false},
  renderLeaseBanner() {},
  setBusy(button, busy) {button.disabled = busy;},
  notify(message) {notices.push(message);},
  batchConfig: () => ({workflow: 'fixture.json'}),
  syncBundle: async () => {},
  renderEffective() {},
  renderRail() {},
  renderWorkflowFacts() {},
  goStep() {},
  poll() {},
  openDrawer: async () => {},
  staleNotice: () => '',
  esc: value => String(value),
  lastSeedValue: () => 0,
  lastInspect: {},
  comfyConnected: true,
  bundle: {items: [{title: '测试任务'}]},
  api: async path => {
    requests.push(path);
    if (path === '/api/preflight') {
      if (preflightReady === null) throw new Error('网络连接中断');
      if (!preflightReady) {
        const error = new Error('工作流不兼容');
        error.payload = {preflight_errors: [{title: '工作流不兼容'}]};
        throw error;
      }
      return {report: {ready: true}};
    }
    if (path === '/api/lease/claim') {
      if (!takeoverReady) throw new Error('租约服务不可用');
      return {lease: {held: true, mine: true}};
    }
    if (path === '/api/start') return {ok: true};
    throw new Error(`unexpected request: ${path}`);
  },
});
vm.runInContext([
  section('function publishAgentStatus(', 'function setBusy('),
  section('async function preflight(body=batchConfig()){', '/* ---- 导入即预检'),
  section('function applyLease(lease){', 'function renderLeaseBanner(){'),
  section('async function takeOverLease(btn){', 'async function releaseLease(){'),
  section('async function preflightAction(button){', 'async function poll('),
].join('\n'), context);

(async () => {
  const button = {disabled: false};
  context.applyLease({held: true, mine: false});
  assert.equal(body.dataset.agentLease, 'read-only');
  await context.preflightAction(button);
  assert.equal(elements.agentActionStatus.dataset.agentState, 'blocked');
  assert.equal(requests.length, 0, '只读页面不能请求预检');
  await context.startBatch(button);
  assert.equal(elements.agentActionStatus.dataset.agentAction, 'start-batch');
  assert.equal(elements.agentActionStatus.dataset.agentState, 'blocked');
  assert.equal(requests.length, 0, '只读页面不能提交批次');

  context.applyLease({held: true, mine: true});
  assert.equal(body.dataset.agentLease, 'mine');
  await context.preflightAction(button);
  assert.equal(elements.agentActionStatus.dataset.agentAction, 'preflight');
  assert.equal(elements.agentActionStatus.dataset.agentState, 'succeeded');
  assert.equal(requests.join(','), '/api/preflight');
  await context.startBatch(button);
  assert.equal(elements.agentActionStatus.dataset.agentState, 'succeeded');
  assert.equal(requests.join(','), '/api/preflight,/api/preflight,/api/start', '人工与 Agent 共用预检后启动路径');

  preflightReady = false;
  await assert.rejects(context.preflightAction(button), /工作流不兼容/);
  assert.equal(elements.agentActionStatus.dataset.agentState, 'blocked');
  await context.startBatch(button);
  assert.equal(elements.agentActionStatus.dataset.agentState, 'blocked');
  assert.equal(requests.filter(path => path === '/api/start').length, 1, '预检失败不得再次启动');
  preflightReady = null;
  await assert.rejects(context.preflightAction(button), /网络连接中断/);
  assert.equal(elements.agentActionStatus.dataset.agentState, 'failed', '非预检阻断应保留故障状态');
  context.applyLease({held: true, mine: false});
  const takeover = context.takeOverLease(button);
  assert.equal(elements.agentActionStatus.dataset.agentState, 'running');
  await takeover;
  assert.equal(elements.agentActionStatus.dataset.agentState, 'succeeded');
  assert.equal(body.dataset.agentLease, 'mine');
  takeoverReady = false;
  await context.takeOverLease(button);
  assert.equal(elements.agentActionStatus.dataset.agentState, 'failed');
  assert.ok(notices.length > 0);
  context.applyLease({held: false, mine: false});
  assert.equal(body.dataset.agentLease, 'free');
  console.log('agent DOM action status: ok');
})().catch(error => {console.error(error); process.exitCode = 1;});
