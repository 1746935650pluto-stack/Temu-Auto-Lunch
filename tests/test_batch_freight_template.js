const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const job = (id, options, extra = {}) => ({
  jobId: String(id), detailId: Number(id), status: 'needs_input', step: 'freight',
  choices: { freight: { options } }, ...extra,
});

function setup(jobs, fetch = async () => ({ ok: true, json: async () => ({ data: {} }) })) {
  const state = {
    jobs, selectedFreightJobIds: new Set(), resumingJobIds: new Set(),
    batchFreightUpdating: false, batchFreightTemplateId: '',
  };
  const messages = [];
  const ctx = vm.createContext({
    automationState: state, fetch, window: { confirm: () => true },
    $: () => ({ value: 'standard' }), renderAutomationJobs: () => {},
    loadAutomationJobs: async () => {}, automationJobFeedback: (...args) => messages.push(args),
  });
  vm.runInContext(source.slice(source.indexOf('function batchFreightCandidates'), source.indexOf('function renderBatchFreightTemplateControls')), ctx);
  vm.runInContext(source.slice(source.indexOf('async function batchUpdateFreightTemplates'), source.indexOf('async function restoreDeletedAutomationJob')), ctx);
  return { ctx, state, messages };
}

test('batch freight selection only offers templates shared by every selected paused job', () => {
  const { ctx, state } = setup([
    job(1, [{ id: 'standard', name: '通用' }, { id: 'fast', name: '快速' }]),
    job(2, [{ id: 'standard', name: '通用' }]),
    job(3, [{ id: 'fast', name: '快速' }], { status: 'failed' }),
  ]);
  state.selectedFreightJobIds = new Set(['1', '2', '3']);
  assert.deepEqual(Array.from(ctx.batchFreightCandidates(), entry => entry.jobId), ['1', '2']);
  assert.deepEqual(Array.from(ctx.sharedBatchFreightOptions(), entry => entry.id), ['standard']);
});

test('batch freight update resumes selected jobs without publishing and keeps failed selections', async () => {
  const calls = [];
  const { ctx, state, messages } = setup([
    job(1, [{ id: 'standard', name: '通用' }]),
    job(2, [{ id: 'standard', name: '通用' }]),
  ], async (url, options) => {
    const body = JSON.parse(options.body);
    calls.push({ url, body });
    if (body.jobId === '2') return { ok: false, json: async () => ({ error: '模板已失效' }) };
    return { ok: true, json: async () => ({ data: { status: 'running' } }) };
  });
  state.selectedFreightJobIds = new Set(['1', '2']);
  await ctx.batchUpdateFreightTemplates();
  assert.equal(calls.length, 2);
  assert.ok(calls.every((call) => call.url === '/api/automation/resume'));
  assert.ok(calls.every((call) => call.body.selectedId === 'standard'));
  assert.ok(calls.every((call) => !call.url.includes('publish')));
  assert.deepEqual(Array.from(state.selectedFreightJobIds), ['2']);
  assert.equal(state.batchFreightUpdating, false);
  assert.match(messages.at(-1)[0], /1\/2/);
});
