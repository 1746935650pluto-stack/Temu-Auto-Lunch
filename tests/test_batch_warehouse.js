const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const job = (id, options, extra = {}) => ({
  jobId: String(id), detailId: Number(id), status: 'needs_input', step: 'warehouse',
  choices: { warehouse: { options } }, ...extra,
});

function setup(jobs, fetch = async () => ({ ok: true, json: async () => ({ data: {} }) })) {
  const state = {
    jobs, selectedWarehouseJobIds: new Set(), resumingJobIds: new Set(),
    batchWarehouseUpdating: false, batchWarehouseId: '',
  };
  const messages = [];
  const ctx = vm.createContext({
    automationState: state, fetch, window: { confirm: () => true },
    $: () => ({ value: 'warehouse-a' }), renderAutomationJobs: () => {},
    loadAutomationJobs: async () => {}, automationJobFeedback: (...args) => messages.push(args),
  });
  vm.runInContext(source.slice(source.indexOf('function batchWarehouseCandidates'), source.indexOf('function updateAutomationJobBatchControls')), ctx);
  vm.runInContext(source.slice(source.indexOf('async function batchUpdateWarehouses'), source.indexOf('async function restoreDeletedAutomationJob')), ctx);
  return { ctx, state, messages };
}

test('batch warehouse selection only offers warehouses shared by every selected paused job', () => {
  const { ctx, state } = setup([
    job(1, [{ id: 'warehouse-a', name: '美国仓' }, { id: 'warehouse-b', name: '备用仓' }]),
    job(2, [{ id: 'warehouse-a', name: '美国仓' }]),
    job(3, [{ id: 'warehouse-b', name: '备用仓' }], { status: 'failed' }),
  ]);
  state.selectedWarehouseJobIds = new Set(['1', '2', '3']);
  assert.deepEqual(Array.from(ctx.batchWarehouseCandidates(), entry => entry.jobId), ['1', '2']);
  assert.deepEqual(Array.from(ctx.sharedBatchWarehouseOptions(), entry => entry.id), ['warehouse-a']);
});

test('batch warehouse update resumes selected jobs without publishing and keeps failed selections', async () => {
  const calls = [];
  const { ctx, state, messages } = setup([
    job(1, [{ id: 'warehouse-a', name: '美国仓' }]),
    job(2, [{ id: 'warehouse-a', name: '美国仓' }]),
  ], async (url, options) => {
    const body = JSON.parse(options.body);
    calls.push({ url, body });
    if (body.jobId === '2') return { ok: false, json: async () => ({ error: '仓库已失效' }) };
    return { ok: true, json: async () => ({ data: { status: 'running' } }) };
  });
  state.selectedWarehouseJobIds = new Set(['1', '2']);
  await ctx.batchUpdateWarehouses();
  assert.equal(calls.length, 2);
  assert.ok(calls.every((call) => call.url === '/api/automation/resume'));
  assert.ok(calls.every((call) => call.body.selectedId === 'warehouse-a'));
  assert.ok(calls.every((call) => !call.url.includes('publish')));
  assert.deepEqual(Array.from(state.selectedWarehouseJobIds), ['2']);
  assert.equal(state.batchWarehouseUpdating, false);
  assert.match(messages.at(-1)[0], /1\/2/);
});
