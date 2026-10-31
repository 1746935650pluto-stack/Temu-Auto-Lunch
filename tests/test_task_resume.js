const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

test('resuming one product only locks that product; failures unlock it', async () => {
  const calls = [];
  const pending = [];
  const messages = [];
  const state = { jobs: [{jobId:'a', detailId:1}, {jobId:'b', detailId:2}], resumingJobIds:new Set() };
  const context = vm.createContext({ automationState: state, renderAutomationJobs: () => {},
    loadAutomationJobs: async () => {}, automationJobFeedback: (...args) => messages.push(args),
    fetch: (_url, options) => { calls.push(JSON.parse(options.body)); return new Promise(resolve => pending.push(resolve)); }
  });
  vm.runInContext(source.slice(source.indexOf('async function resumeAutomationJob'), source.indexOf('async function cancelPausedAutomationJob')), context);
  const a = context.resumeAutomationJob('a', '');
  await context.resumeAutomationJob('a', '');
  const b = context.resumeAutomationJob('b', '');
  assert.equal(calls.length, 2);
  assert.equal(state.resumingJobIds.size, 2);
  pending[0]({ok:true, json:async () => ({})});
  pending[1]({ok:false, json:async () => ({error:'模拟失败'})});
  await Promise.all([a,b]);
  assert.equal(state.resumingJobIds.size, 0);
  assert.ok(messages.some(([text]) => text.includes('ID 2') && text.includes('模拟失败')));
});
