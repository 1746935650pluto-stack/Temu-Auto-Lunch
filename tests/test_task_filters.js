const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function setup() {
  const jobs = [
    { jobId: 'ready', status: 'ready_publish', step: 'publish' },
    { jobId: 'done', status: 'done', step: 'complete' },
    { jobId: 'publishError', status: 'needs_review', step: 'publish', events: [{ step: 'save', status: 'done' }] },
    { jobId: 'failed', status: 'needs_review', step: 'save', events: [{ step: 'save', status: 'done' }, { step: 'save', status: 'needs_review' }] },
    { jobId: 'running', status: 'running', step: 'preflight' },
    { jobId: 'paused', status: 'needs_input', step: 'prepare' },
    { jobId: 'freightOne', status: 'needs_input', step: 'freight', choices: { freight: { options: [{ id: '1' }] } } },
    { jobId: 'freightTwo', status: 'needs_input', step: 'freight', choices: { freight: { options: [{ id: '1' }] } } },
    { jobId: 'warehouseOne', status: 'needs_input', step: 'warehouse', choices: { warehouse: { options: [{ id: 'w1' }] } } },
    { jobId: 'warehouseTwo', status: 'needs_input', step: 'warehouse', choices: { warehouse: { options: [{ id: 'w1' }] } } },
    { jobId: 'queued', status: 'queued', step: 'queued' },
    { jobId: 'canceled', status: 'canceled', step: 'canceled' },
  ];
  const state = { jobs, selectedJobIds: new Set(), selectedFreightJobIds: new Set(), selectedWarehouseJobIds: new Set(), resumingJobIds: new Set() };
  const context = vm.createContext({ automationState: state, renderAutomationJobs: () => {} });
  vm.runInContext(source.slice(source.indexOf('const automationTerminalStatuses'), source.indexOf('function updateAutomationJobBatchControls')), context);
  vm.runInContext(source.slice(source.indexOf('function batchFreightCandidates'), source.indexOf('function updateAutomationJobBatchControls')), context);
  vm.runInContext(source.slice(source.indexOf('function toggleSelectAllAutomationJobs'), source.indexOf('function automationShopName')), context);
  return { context, state, ids: () => Array.from(vm.runInContext('visibleAutomationJobs().map(job => job.jobId)', context)) };
}

test('default unsaved filter includes running, paused and failed tasks, saved includes publication failures', () => {
  const { context, ids } = setup();
  assert.deepEqual(ids(), ['failed', 'running', 'paused', 'freightOne', 'freightTwo', 'warehouseOne', 'warehouseTwo', 'queued', 'canceled']);
  vm.runInContext("setAutomationSaveFilter('saved')", context);
  assert.deepEqual(ids(), ['ready', 'done', 'publishError']);
});

test('select all chooses paused freight-template and warehouse tasks in unsaved, and terminal records in saved', () => {
  const { context, state } = setup();
  vm.runInContext('toggleSelectAllAutomationJobs()', context);
  assert.deepEqual([...state.selectedFreightJobIds], ['freightOne', 'freightTwo']);
  assert.deepEqual([...state.selectedWarehouseJobIds], ['warehouseOne', 'warehouseTwo']);
  assert.equal(state.selectedJobIds.size, 0);
  vm.runInContext("setAutomationSaveFilter('saved')", context);
  assert.equal(state.selectedFreightJobIds.size, 0);
  assert.equal(state.selectedWarehouseJobIds.size, 0);
  assert.equal(state.selectedJobIds.size, 0);
  vm.runInContext('toggleSelectAllAutomationJobs()', context);
  assert.deepEqual([...state.selectedJobIds], ['done', 'publishError']);
  vm.runInContext('toggleSelectAllAutomationJobs()', context);
  assert.equal(state.selectedJobIds.size, 0);
});
