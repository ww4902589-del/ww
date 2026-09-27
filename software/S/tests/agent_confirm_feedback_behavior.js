// A declared Agent action must expose the same lease and confirmation outcome as a human click.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');

function section(start, end) {
  const first = source.indexOf(start);
  const last = source.indexOf(end, first + start.length);
  assert.ok(first >= 0 && last > first, `missing action section: ${start}`);
  return source.slice(first, last);
}

const status = {dataset: {}, textContent: ''};
const body = {dataset: {}, classList: {toggle() {}}};
const requests = [];
const reviews = [];
let consent = false;
let rejectRequest = false;
let conflictRequest = false;
let promptCount = 0;
const context = vm.createContext({
  $: id => id === 'agentActionStatus' ? status : null,
  document: {body},
  leaseState: {held: false, mine: false},
  reviewState: {results: []},
  renderLeaseBanner() {},
  confirm: () => {promptCount++; return consent;},
  setBusy(button, busy) {button.disabled = busy;},
  renderReview(review) {reviews.push(review);},
  notify() {},
  api: async (path, options) => {
    requests.push({path, body: JSON.parse(options.body)});
    if (conflictRequest) {
      const error = new Error('另一个页面正在编辑');
      error.payload = {lease: {held: true, mine: false}};
      throw error;
    }
    if (rejectRequest) throw new Error('网络暂不可用');
    return {review: {accepted: true}};
  },
});
vm.runInContext([
  section('function publishAgentStatus(', 'function isPreflightBlocked('),
  section('function applyLease(lease){', 'function renderLeaseBanner(){'),
  section('async function confirmAllPending(btn){', 'async function reloadSchema(btn){'),
].join('\n'), context);

(async () => {
  const button = {disabled: false};
  context.reviewState.results = Array.from({length: 6}, (_, index) => ({
    index: index + 1, copied_to: 'output.png', review_status: '待确认',
  }));
  context.applyLease({held: true, mine: false});
  await context.confirmAllPending(button);
  assert.equal(status.dataset.agentState, 'blocked');
  assert.equal(requests.length, 0);
  assert.equal(promptCount, 0, '只读页不应弹出批量确认框');

  context.applyLease({held: true, mine: true});
  context.reviewState.results = [];
  await context.confirmAllPending(button);
  assert.equal(status.dataset.agentState, 'blocked');
  assert.equal(requests.length, 0);

  context.reviewState.results = Array.from({length: 6}, (_, index) => ({
    index: index + 1, copied_to: 'output.png', review_status: '待确认',
  }));
  await context.confirmAllPending(button);
  assert.equal(status.dataset.agentState, 'blocked', '用户取消须可被 Agent 区分');
  assert.equal(requests.length, 0);

  consent = true;
  const accepted = context.confirmAllPending(button);
  assert.equal(status.dataset.agentState, 'running');
  await accepted;
  assert.equal(status.dataset.agentAction, 'confirm-all');
  assert.equal(status.dataset.agentState, 'succeeded');
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, '/api/review/confirm');
  assert.equal(requests[0].body.status, '已通过');
  assert.equal(requests[0].body.indexes.length, 6);
  assert.equal(reviews.length, 1);

  rejectRequest = true;
  await context.confirmAllPending(button);
  assert.equal(status.dataset.agentState, 'failed');
  assert.equal(reviews.length, 1, '失败请求不得刷新为通过');
  rejectRequest = false;
  conflictRequest = true;
  await context.confirmAllPending(button);
  assert.equal(status.dataset.agentState, 'blocked', '租约冲突应明确阻断');
  assert.equal(body.dataset.agentLease, 'read-only');
  assert.equal(reviews.length, 1, '租约冲突不得刷新为通过');
  console.log('agent confirm feedback: ok');
})().catch(error => {console.error(error); process.exitCode = 1;});
