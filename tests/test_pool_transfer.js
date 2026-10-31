const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const start = source.indexOf('async function addPoolSelectionToAutomationPool()');
const end = source.indexOf('async function removeAutomationPoolItem(', start);
const response = (status, body) => ({ status, ok: status >= 200 && status < 300, json: async () => body });

async function transfer(responses, ids = [123, 124, 125]) {
  const selected = new Map(ids.map(id => [id, { collectBoxDetailId: id, editModel: 'site' }]));
  const feedback = [];
  const requests = [];
  const button = {};
  let refreshes = 0;
  const context = vm.createContext({
    liveState: { poolSelected: selected },
    $: selector => selector === '#liveShop' ? { value: '' } : button,
    poolTransferStatus: (message, tone) => feedback.push({ message, tone }),
    showToast: () => {},
    loadAutomationPool: async () => { refreshes++; },
    loadLiveProducts: async () => { refreshes++; },
    updateLivePoolSelectionControls: () => { button.disabled = false; },
    fetch: async (url, options) => {
      requests.push({ url, body: JSON.parse(options.body) });
      const result = responses.shift();
      if (result instanceof Error) throw result;
      assert.ok(result, 'unexpected request');
      return result;
    },
  });
  vm.runInContext(source.slice(start, end), context);
  await vm.runInContext('addPoolSelectionToAutomationPool()', context);
  return { selected: [...selected.keys()], feedback: feedback.at(-1), requests, button, refreshes };
}

test('partial success shows failed ID and reason, preserving only failed selections', async () => {
  const result = await transfer([response(200, { data: {
    items: [{ detailId: 123 }, { detailId: 125 }],
    failures: [{ detailId: 124, error: 'numberValue: 期望 int, 实际 double(85.5)' }],
  } })]);
  assert.deepEqual(result.selected, [124]);
  assert.match(result.feedback.message, /已加入 2 件，跳过重复 0 件，失败 1 件/);
  assert.match(result.feedback.message, /采集箱 ID 124：numberValue/);
  assert.match(result.feedback.message, /85\.5/);
  assert.equal(result.refreshes, 2);
  assert.equal(result.button.textContent, '加入自动化池');
});

test('all failed IDs remain selected, and all successes clear selections', async () => {
  const failed = await transfer([response(200, { data: {
    items: [], failures: [123, 124, 125].map(detailId => ({ detailId, error: '读取失败' })),
  } })]);
  assert.deepEqual(failed.selected, [123, 124, 125]);
  assert.match(failed.feedback.message, /已加入 0 件，跳过重复 0 件，失败 3 件/);
  for (const id of failed.selected) assert.ok(failed.feedback.message.includes(`采集箱 ID ${id}`));
  const success = await transfer([response(200, { data: {
    items: [123, 124, 125].map(detailId => ({ detailId })), failures: [],
  } })]);
  assert.deepEqual(success.selected, []);
  assert.equal(success.feedback.tone, 'success');
});

test('more than five hundred selected products are submitted as consecutive safe-size batches', async () => {
  const ids = Array.from({length: 501}, (_, index) => index + 1);
  const result = await transfer([
    response(200, {data: {items: ids.slice(0, 500).map(detailId => ({detailId})), failures: []}}),
    response(200, {data: {items: [{detailId: 501}], failures: []}}),
  ], ids);
  assert.equal(result.requests.length, 2);
  assert.equal(result.requests[0].body.items.length, 500);
  assert.equal(result.requests[1].body.items.length, 1);
  assert.deepEqual(result.selected, []);
  assert.match(result.feedback.message, /已加入 501 件，跳过重复 0 件，失败 0 件/);
});

test('legacy single-item fallback continues after HTTP or network failures', async () => {
  for (const error of [response(502, { error: '属性错误' }), new Error('网络错误')]) {
    const result = await transfer([response(404, {}), response(200, {}), error, response(200, {})]);
    assert.deepEqual(result.selected, [124]);
    assert.equal(result.requests.length, 4);
    assert.equal(result.requests[3].body.detailId, 125);
    assert.match(result.feedback.message, /采集箱 ID 124/);
    assert.match(result.feedback.message, /已加入 2 件，跳过重复 0 件，失败 1 件/);
  }
});

test('batch import blocks repeat clicks until reads and refreshes finish', async () => {
  const selected = new Map([[123, {collectBoxDetailId:123, editModel:'site'}]]);
  const state = {poolSelected:selected, poolTransferring:false};
  const button = {};
  let calls = 0, finishRequest, finishPoolRefresh, finishLiveRefresh, refreshes = 0;
  const context = vm.createContext({
    liveState:state, $:selector=>selector==='#liveShop' ? {value:''} : button,
    poolTransferStatus(){}, showToast(){}, updateLivePoolSelectionControls(){},
    fetch:()=>{calls++;return new Promise(resolve=>{finishRequest=resolve;});},
    loadAutomationPool:()=>{refreshes++;return new Promise(resolve=>{finishPoolRefresh=resolve;});},
    loadLiveProducts:()=>{refreshes++;return new Promise(resolve=>{finishLiveRefresh=resolve;});},
  });
  vm.runInContext(source.slice(start, end), context);
  const pending = context.addPoolSelectionToAutomationPool();
  await context.addPoolSelectionToAutomationPool();
  assert.equal(calls, 1);
  assert.equal(state.poolTransferring, true);
  finishRequest(response(200, {data:{items:[{detailId:123}],failures:[]}}));
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(refreshes, 2, 'both independent refreshes start concurrently');
  assert.equal(state.poolTransferring, true);
  finishPoolRefresh(); finishLiveRefresh();
  await pending;
  assert.equal(state.poolTransferring, false);
  assert.equal(button.textContent, '加入自动化池');
});
