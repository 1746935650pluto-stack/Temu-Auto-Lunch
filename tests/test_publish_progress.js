const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function setup() {
  const nodes = new Map();
  const $ = id => {
    if (!nodes.has(id)) nodes.set(id, {value:3, classList:{contains:()=>false}, setAttribute(name, value) {this[name]=value;}});
    return nodes.get(id);
  };
  const state = {jobs:[], jobsLoaded:true, jobsLoading:false, publishProgress:null, publishStarting:false};
  const delays = [];
  const messages = [];
  const ctx = vm.createContext({automationState:state, liveState:{multiShopPublishing:true}, $, window:{clearTimeout(){}, setTimeout(fn, delay){delays.push(delay);}},
    automationJobFeedback:(...args)=>messages.push(args), automationShopName:(id)=>`店铺 ${id}`,
    renderAutomationJobs(){}, renderAutomationPool(){}, loadAutomationJobShopNames(){}});
  vm.runInContext(source.slice(source.indexOf('function selectedPublishShopIds'), source.indexOf('function changePublishQueueShops')), ctx);
  vm.runInContext(source.slice(source.indexOf('function renderPublishProgress'), source.indexOf('function selectAllPublishQueue')), ctx);
  vm.runInContext(source.slice(source.indexOf('async function publishReadyAutomationJobs'), source.indexOf('async function addSelectedToAutomationPool')), ctx);
  return {ctx,state,$,delays,messages};
}
const progress = (extra={}) => ({batchId:'batch', status:'running',total:38, handled:12,
  submitted:10, needsReview:1, removed:1, remaining:26, currentDetailId:3426480265, intervalSeconds:3, ...extra});

test('progress displays separate outcomes, current ID and prevents duplicate release', () => {
  const {ctx,state,$} = setup();
  ctx.renderPublishProgress();
  assert.equal($('#publishProgress').hidden, true);
  state.publishProgress = progress();
  ctx.renderPublishProgress();
  assert.equal($('#publishProgress').hidden, false);
  assert.equal($('#publishProgressBar').max, 38);
  assert.equal($('#publishProgressBar').value, 12);
  assert.equal($('#publishProgressCount').textContent, '处理进度 12/38（31%）');
  assert.ok($('#publishProgressSummary').textContent.includes('已提交 10 · 失败/需核查 1 · 已移出 1 · 剩余 26'));
  assert.ok($('#publishProgressCurrent').textContent.includes('3426480265'));
  assert.equal($('#publishReadyAutomationJobs').disabled, true);
  assert.equal($('#publishQueuePublishInterval').disabled, true);
  state.publishProgress = progress({currentDetailId:null});
  ctx.renderPublishProgress();
  assert.ok($('#publishProgressCurrent').textContent.includes('间隔 3 秒'));
});

test('completed and interrupted batches never claim final listing success', () => {
  const {ctx,state,$} = setup();
  state.publishProgress = progress({status:'completed',handled:38,submitted:36,remaining:0,currentDetailId:null});
  ctx.renderPublishProgress();
  assert.ok($('#publishProgressTitle').textContent.includes('需核查'));
  assert.ok($('#publishProgressCurrent').textContent.includes('最终上架结果请到发布记录核对'));
  assert.equal($('#publishReadyAutomationJobs').disabled, false);
  state.publishProgress = progress({status:'interrupted',currentDetailId:null});
  ctx.renderPublishProgress();
  assert.equal($('#publishProgressTitle').textContent, '发布已中断');
  assert.ok($('#publishProgressCurrent').textContent.includes('不会自动发布'));
});

test('poll restores progress with unchanged jobs and stays fast between submissions', async () => {
  const {ctx,state,$,delays} = setup();
  ctx.apiGet = async()=>({jobs:[], publishProgress:progress({currentDetailId:null})});
  await ctx.loadAutomationJobs();
  assert.equal(state.publishProgress.total, 38);
  assert.equal($('#publishProgressBar').value, 12);
  assert.equal(delays.at(-1), 1500);
  ctx.apiGet = async()=>{ throw Error('断网'); };
  await ctx.loadAutomationJobs();
  assert.equal(state.publishProgress.submitted, 10);
  assert.ok($('#publishProgressCurrent').textContent.includes('上次结果'));
  ctx.apiGet = async()=>({jobs:[], publishProgress:progress({status:'completed',handled:38,remaining:0})});
  await ctx.loadAutomationJobs();
  assert.equal($('#publishProgressBar').value, 38);
  assert.equal(state.publishProgressStale, false);
  assert.equal(delays.at(-1), 5000);
});

test('double click sends one publish request and initializes returned batch', async () => {
  const {ctx,state} = setup();
  let calls=0, resolve;
  ctx.fetch = () => { calls++; return new Promise(done=>{ resolve=done; }); };
  ctx.apiGet = async()=>({jobs:[], publishProgress:progress()});
  const pending = ctx.publishReadyAutomationJobs();
  await ctx.publishReadyAutomationJobs();
  assert.equal(calls, 1);
  resolve({ok:true,json:async()=>({data:{jobs:[],publishProgress:progress()}})});
  await pending;
  assert.equal(state.publishStarting, false);
  assert.equal(state.publishProgress.batchId, 'batch');
  await ctx.publishReadyAutomationJobs();
  assert.equal(calls, 1);
});

test('specified publish count is sent while an empty field still means the whole queue', async () => {
  const {ctx,$} = setup();
  const payloads = [];
  ctx.fetch = async (_url, options) => {
    payloads.push(JSON.parse(options.body));
    return {ok:true,json:async()=>({data:{jobs:[],publishProgress:progress()}})};
  };
  ctx.apiGet = async()=>({jobs:[], publishProgress:progress()});
  $('#publishQueuePublishCount').value = '12';
  await ctx.publishReadyAutomationJobs();
  assert.equal(payloads[0].publishCount, 12);
  ctx.automationState.publishProgress = {status:'completed'};
  $('#publishQueuePublishCount').value = '';
  await ctx.publishReadyAutomationJobs();
  assert.equal(payloads[1].publishCount, null);
});

test('selected shop is included in the publish request while all stores stays unscoped', async () => {
  const {ctx,state} = setup();
  const payloads = [];
  ctx.fetch = async (_url, options) => {
    payloads.push(JSON.parse(options.body));
    return {ok:true,json:async()=>({data:{jobs:[],publishProgress:progress()}})};
  };
  ctx.apiGet = async()=>({jobs:[], publishProgress:progress()});
  state.publishQueueShopId = '456';
  await ctx.publishReadyAutomationJobs();
  assert.equal(payloads[0].shopId, '456');
  state.publishQueueShopId = '';
  state.publishProgress = {status:'completed'};
  await ctx.publishReadyAutomationJobs();
  assert.equal(payloads[1].shopId, null);
});

test('multiple stores are sent as one scoped batch; empty selection and old backend cannot publish', async () => {
  const {ctx,state,messages} = setup();
  const payloads = [];
  ctx.fetch = async (_url, options) => {
    payloads.push(JSON.parse(options.body));
    return {ok:true,json:async()=>({data:{jobs:[],publishProgress:progress()}})};
  };
  ctx.apiGet = async()=>({jobs:[], publishProgress:progress()});
  state.publishQueueShopIds = [];
  await ctx.publishReadyAutomationJobs();
  assert.equal(payloads.length, 0);
  state.publishQueueShopIds = ['456','789'];
  ctx.liveState.multiShopPublishing = false;
  await ctx.publishReadyAutomationJobs();
  assert.equal(payloads.length, 0);
  assert.match(messages.at(-1)[0], /后台/);
  ctx.liveState.multiShopPublishing = true;
  await ctx.publishReadyAutomationJobs();
  assert.deepEqual(payloads[0].shopIds, [456,789]);
  assert.equal(payloads[0].shopId, '456'); // Safe on an older backend: never an all-shop fallback.
});
