const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function setup() {
  const element = () => ({children: [], value: '', classList: {add(){}, remove(){}}, focus(){},
    selectedOptions: [{value: '999'}], append(...nodes){this.children.push(...nodes);},
    replaceChildren(){this.children=[];}, setAttribute(){}, addEventListener(){}, querySelectorAll(){return [];}});
  const nodes = new Map();
  const $ = id => {if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id);};
  const state = {targetMode: 'personnel', items: [1,2,3].map(detailId => ({detailId, title: '商品'})),
    selected: new Set([1,2,3]), jobs: [], poolPage: 1, submitting: false,
    routing: {accountId: 1, scope: 'primary', scan: {status: 'ready', scanned: 3},
      owners: {'1': '11', '2': '22'}, bindings: {'11': [10,20], '22': [20]},
      people: [{id:'11',alias:'甲',name:'同名'}, {id:'22',name:'同名'}],
      shops: [{shopId:10,name:'一店'}, {shopId:20,name:'二店'}]}};
  $('#automationPublishSite').value = 'US';
  $('#automationSkuPrice').value = '160';
  $('#automationPriceMode').value = 'floor';
  $('#automationPriceMultiplier').value = '1.5';
  $('#automationEditConcurrency').value = '8';
  const requests=[], messages=[], timers=[];
  const ctx = vm.createContext({activeMiaoshouCollectionAccountId: 1, automationState: state, liveState: {mode:'live', people:[{id:'11',alias:'其他账号'}]},
    $, document:{createElement:element}, setTimeout:fn=>{timers.push(fn);return timers.length;}, clearTimeout(){},
    apiGet:async()=>state.routing, automationClaimFeedback:message=>messages.push(message),
    showToast(){}, renderAutomationJobs(){}, loadAutomationJobs:async()=>{},
    fetch:async(url, options)=>{requests.push({url,body:JSON.parse(options.body)});return {ok:true,json:async()=>({data:{
      jobs:[{jobId:'job1',detailId:1,shopId:10},{jobId:'job2',detailId:1,shopId:20},{jobId:'job3',detailId:2,shopId:20}],
      skipped:[{detailId:3,reason:'未返回所属人员'}]}})};}});
  vm.runInContext(source.slice(source.indexOf('const AUTOMATION_POOL_PAGE_SIZE'), source.indexOf('async function loadAutomationPool')),ctx);
  vm.runInContext(source.slice(source.indexOf('async function claimAutomationSelection'), source.indexOf('function appendOption')),ctx);
  return {ctx,state,$,requests,messages,timers};
}

test('personnel mode counts each product targets without manual shop fallback or cross-account labels',()=>{
  const {ctx,$}=setup();
  assert.deepEqual(Array.from(ctx.automationItemShopIds(1)),[10,20]);
  assert.deepEqual(Array.from(ctx.automationItemShopIds(2)),[20]);
  assert.deepEqual(Array.from(ctx.automationItemShopIds(3)),[]);
  assert.equal(ctx.automationPersonnelLabel('11'),'甲（账号 ID 11）');
  assert.equal(ctx.automationPersonnelLabel('22'),'同名（账号 ID 22）');
  ctx.renderAutomationPool();
  assert.match($('#automationSelectedCount').textContent,/共 3 个任务 · 将跳过 1 件/);
  assert.equal($('#automationClaimShop').disabled,true);
  assert.equal($('#automationClaimSubmit').disabled,false);
});

test('submit sends selected routing and retains skipped products, with no publication',async()=>{
  const {ctx,state,requests,messages}=setup();
  await ctx.claimAutomationSelection();
  assert.equal(requests.length,1);
  const body=requests[0].body;
  assert.equal(body.targetMode,'personnel');
  assert.equal(body.bindingScope,'primary');
  assert.deepEqual(body.expectedRouting,[{detailId:1,collectorId:'11',shopIds:[10,20]},
    {detailId:2,collectorId:'22',shopIds:[20]},{detailId:3,collectorId:'',shopIds:[]}]);
  assert.deepEqual([...state.selected],[3]);
  assert.match(messages.at(-1),/已跳过 1 件并保留勾选/);
  assert.equal(body.confirmation,'AUTOMATE_AND_SAVE_BATCH');
});

test('partial, failed or stale ownership cannot submit; unbound product cannot fall back',async()=>{
  for (const status of ['loading','failed','expired']) {
    const {ctx,state,requests,$}=setup(); state.routing.scan.status=status;
    ctx.updateAutomationControls(); assert.equal($('#automationClaimSubmit').disabled,true);
    await ctx.claimAutomationSelection(); assert.equal(requests.length,0);
  }
  const {ctx,state,requests}=setup();state.selected=new Set([3]);
  await ctx.claimAutomationSelection();assert.equal(requests.length,0);
});

test('late scan response cannot overwrite a newer binding revision',async()=>{
  const {ctx,state}=setup();
  let resolve;ctx.apiGet=()=>new Promise(done=>resolve=done);
  const task=ctx.loadAutomationPersonnel(true);
  state.routingSerial++;
  resolve({accountId:1,scope:'old',scan:{status:'ready'},owners:{1:'other'}});
  await task;
  assert.equal(state.routing.scope,'primary');
  assert.equal(state.routing.owners[1],'11');
});

test('mode selection during initial metadata request queues a full scan',async()=>{
  const {ctx,state,timers}=setup();
  let resolve;ctx.apiGet=()=>new Promise(done=>resolve=done);
  const task=ctx.loadAutomationPersonnel();
  await ctx.loadAutomationPersonnel(true);
  assert.equal(state.routingScanQueued,true);
  resolve({...state.routing,scan:{status:'unavailable'}});
  await task;
  assert.equal(state.routingScanQueued,false);
  assert.equal(timers.length,1);
});

test('failed binding save retains checked shops and creates no product tasks',async()=>{
  const {ctx,state,$}=setup();
  state.bindingOpen=true;state.bindingScope='primary';
  $('#personnelBindingPerson').value='11';
  const selected={value:'10',checked:true};
  $('#personnelBindingShops').querySelectorAll=()=>[selected];
  const calls=[];ctx.fetch=async(url)=>{calls.push(url);return {ok:false,json:async()=>({error:'临时故障'})};};
  await ctx.savePersonnelBinding();
  assert.equal(selected.checked,true);
  assert.equal(state.bindingSaving,false);
  assert.match($('#personnelBindingStatus').textContent,/当前勾选已保留/);
  assert.deepEqual(calls,['/api/automation/personnel-bindings']);
  assert.deepEqual(state.routing.bindings['11'],[10,20]);
});
