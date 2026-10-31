const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function setup(fetch = async () => ({}), confirm = () => true) {
  const element = () => ({children: [], events: {}, classList: {toggle() {}},
    append(...children) { this.children.push(...children); }, replaceChildren() { this.children = []; },
    setAttribute(name, value) { this[name] = value; }, addEventListener(name, fn) { this.events[name] = fn; }});
  const nodes = new Map();
  const $ = id => { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); };
  const state = {jobs: [{jobId:'a', detailId:1, status:'ready_publish'}, {jobId:'b', detailId:2, status:'ready_publish'},
    {jobId:'c', detailId:3, status:'running'}], selectedPublishJobIds:new Set(), removingPublishJobs:false};
  const messages = [];
  const ctx = vm.createContext({automationState:state, document:{createElement:element}, $, fetch,
    window:{confirm}, automationShopName:()=>'店铺', loadAutomationJobs:async()=>{},
    automationJobFeedback:(...args)=>messages.push(args)});
  vm.runInContext(source.slice(source.indexOf('function allReadyPublishJobs'), source.indexOf('function renderPublishProgress')), ctx);
  vm.runInContext(source.slice(source.indexOf('function renderPublishProgress'), source.indexOf('function renderAutomationJobs')), ctx);
  return {ctx, state, $, messages};
}

test('queue selection excludes active jobs and stale IDs; checkbox is last action', () => {
  const {ctx,state,$} = setup();
  state.selectedPublishJobIds.add('c');
  ctx.selectAllPublishQueue();
  assert.deepEqual([...state.selectedPublishJobIds], ['a','b']);
  const rows = $('#publishQueueList').children;
  assert.equal(rows.length, 2);
  assert.equal(rows[0].children.at(-1).children.at(-1).type, 'checkbox');
  state.jobs[0].status = 'running';
  ctx.renderPublishQueue();
  assert.deepEqual([...state.selectedPublishJobIds], ['b']);
  ctx.selectAllPublishQueue();
  assert.equal(state.selectedPublishJobIds.size, 0);
});

test('single and bulk removal require confirmation, debounce, preserve failed selection', async () => {
  let resolveRequest;
  const calls=[];
  const {ctx,state,$,messages} = setup((url, options) => {
    calls.push({url,...options});
    return new Promise(resolve=>{ resolveRequest=resolve; });
  });
  state.selectedPublishJobIds.add('a');
  const pending=ctx.removePublishQueueJobs(['a','a','c']);
  await ctx.removePublishQueueJobs(['b']);
  assert.equal(calls.length, 1);
  assert.equal($('#publishReadyAutomationJobs').disabled, true);
  assert.equal(calls[0].method, 'DELETE');
  assert.deepEqual(JSON.parse(calls[0].body), {jobIds:['a'], confirmation:'REMOVE_PUBLISH_QUEUE_ITEMS'});
  resolveRequest({ok:false, json:async()=>({error:'已经开始发布'})});
  await pending;
  assert.equal(state.removingPublishJobs, false);
  assert.equal(state.selectedPublishJobIds.has('a'), true);
  assert.ok(messages.at(-1)[0].includes('已经开始发布'));
  const next=ctx.removePublishQueueJobs(['a','b']);
  resolveRequest({ok:true, json:async()=>({data:{jobIds:['a','b'],removed:2}})});
  await next;
  assert.equal(state.selectedPublishJobIds.size, 0);
  assert.ok(messages.at(-1)[0].includes('保留'));
  let canceledCalls=0;
  const canceled=setup(()=>{canceledCalls++;}, ()=>false);
  await canceled.ctx.removePublishQueueJobs(['a']);
  assert.equal(canceledCalls, 0);
});

test('removed queue records remain classified as successfully saved', () => {
  const {ctx} = setup();
  vm.runInContext(source.slice(source.indexOf('function automationJobSaved'), source.indexOf('function visibleAutomationJobs')), ctx);
  assert.equal(ctx.automationJobSaved({status:'canceled', step:'publish', events:[
    {step:'save',status:'done'}, {step:'publish',status:'canceled'}]}), true);
});

test('publish queue renders the title returned by the task record, including repaired historical AI titles', () => {
  const {ctx,state,$} = setup();
  state.jobs[0].title = 'AI 修改后的中文标题';
  state.jobs[0].thumbnail = 'https://example.com/product.jpg';
  ctx.renderPublishQueue();
  const title = $('#publishQueueList').children[0].children[1].children[0];
  assert.equal(title.textContent, 'AI 修改后的中文标题');
  assert.equal($('#publishQueueList').children[0].children[0].children[0].alt, 'AI 修改后的中文标题的商品图片');
});

test('multi-store selection filters rows and removal; exhausted selections stay scoped', () => {
  const {ctx,state,$} = setup();
  state.jobs = [1,2,3].map((id) => ({jobId:String(id),shopId:id,status:'ready_publish'}));
  state.publishQueueShopIds = ['1','3'];
  assert.deepEqual(Array.from(ctx.visibleReadyPublishJobs(), job=>job.shopId), [1,3]);
  ctx.selectAllPublishQueue();
  assert.deepEqual([...state.selectedPublishJobIds], ['1','3']);
  const select = $('#publishQueueShop');
  Object.defineProperty(select, 'options', {get:()=>select.children});
  ctx.appendOption = (target,value,text) => target.append({value,text});
  state.jobs = [state.jobs[1]];
  ctx.renderPublishQueueShopFilter();
  assert.deepEqual(select.options.filter(o=>o.selected).map(o=>o.value), ['1','3']);
  assert.equal(ctx.visibleReadyPublishJobs().length, 0);
  assert.match(select.options.find(o=>o.value==='1').text, /0/);
});

test('all and selected shops are mutually exclusive, clearing does not select all', () => {
  const {ctx,state} = setup();
  ctx.saveAutomationPreferences = () => {};
  const change = values => ctx.changePublishQueueShops({selectedOptions:values.map(value=>({value}))});
  change(['','1','2']);
  assert.deepEqual(Array.from(ctx.selectedPublishShopIds()), ['1','2']);
  change(['','1','2']);
  assert.equal(ctx.selectedPublishShopIds(), null);
  change([]);
  assert.deepEqual(Array.from(ctx.selectedPublishShopIds()), []);
  assert.equal(ctx.visibleReadyPublishJobs().length, 0);
  assert.equal(state.selectedPublishJobIds.size, 0);
});


test('one-second publish default replaces legacy ten seconds and preserves explicit choices',()=>{
  const start=source.indexOf('const rememberedAutomationPreferences = automationPreferences();');
  const end=source.indexOf("if (/^\\d+$/.test(String(rememberedAutomationPreferences.publishCount",start);
  assert.ok(start>=0&&end>start);
  for(const [preferences,expected] of [[{},'1'],[{intervalSeconds:'10'},'1'],
    [{intervalSeconds:'7'},'7'],[{intervalSeconds:'10',publishIntervalDefaultVersion:2},'10']]) {
    const input={value:'1'};
    const context=vm.createContext({automationPreferences:()=>preferences,$:()=>input});
    vm.runInContext(source.slice(start,end),context);
    assert.equal(input.value,expected);
  }
});
