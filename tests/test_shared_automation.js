const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '..', 'shared-automation.js'), 'utf8');
function functions(start, end) { return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start))); }

test('a new backend epoch restores polling and rejects delayed old snapshots',()=>{
  const state={minimumActivityGeneration:8,activityEpoch:'old',jobs:{}};
  const ctx=vm.createContext({state,Set,$:()=>null,renderQueueActivity(){},renderTodayStoreStats(){},updateTaskControls(){}});
  vm.runInContext(functions('  function consumeActivity(', '  async function loadQueueActivity('),ctx);
  const snapshot=(epoch,generation)=>({activeTransfers:0,inFlightTransfers:0,paused:false,_snapshot:{epoch,generation}});
  assert.equal(ctx.consumeActivity(snapshot('new',0)),true);
  assert.equal(state.minimumActivityGeneration,0);
  assert.equal(ctx.consumeActivity(snapshot('old',9)),false);
  assert.equal(ctx.consumeActivity(snapshot('new',1)),true);
});

test('a status timeout retains the last complete counters and its timestamp', () => {
  const node={dataset:{},textContent:''};
  const activity={copyQueued:2,copyRunning:1,editQueued:3,editRunning:4,pendingPublish:215,
    publishing:1,running:6,_snapshot:{generatedAt:'2026-10-28T12:00:00Z',status:'ready'}};
  const state={queueActivity:activity,jobs:{counts:{publish:215}}};
  const ctx=vm.createContext({state,$:id=>id==='sharedQueueActivity'?node:null,taskEventTime:value=>value});
  vm.runInContext(functions('  function renderQueueActivity(', '  function renderTodayStoreStats('),ctx);
  ctx.renderQueueActivity(null,'读取超时');
  assert.equal(node.dataset.state,'stale');
  assert.match(node.textContent,/待发布 215 条/);
  assert.match(node.textContent,/2026-10-28T12:00:00Z/);
  assert.match(node.textContent,/上次成功快照/);
  assert.equal(activity._snapshot.status,'ready');
  state.queueActivity=null;
  ctx.renderQueueActivity(null,'读取超时');
  assert.equal(node.dataset.state,'loading');
  assert.match(node.textContent,/自动重试/);
});

test('failed initialization resets its promise and succeeds on the next poll', async () => {
  const state={ready:false}, nodes={}, timers=[];
  let attempt=0;
  const ctx=vm.createContext({state,AbortController,Error,
    window:{setTimeout(fn){timers.push(fn);return timers.length;},clearTimeout(){}},
    fetch:async()=>({ok:++attempt>1,json:async()=>({sharedAutomation:true,mode:'demo'})}),
    api:async()=>({accounts:[]}),$:id=>nodes[id]||={},accountOptions(){}});
  vm.runInContext(functions('  async function initialize()', "  document.addEventListener('DOMContentLoaded'"),ctx);
  await assert.rejects(ctx.initialize());
  assert.equal(state.ready,false);assert.equal(state.initializing,null);
  await ctx.initialize();
  assert.equal(state.ready,true);assert.equal(attempt,2);
});

test('slow shop configuration does not block local jobs and activity reads', async () => {
  let release;
  const configuration=new Promise(resolve=>{release=resolve;}), seen=[];
  const ctx=vm.createContext({state:{config:{targets:[]}},initialize:async()=>{},
    refresh:async kind=>seen.push(kind),loadQueueActivity:async()=>seen.push('activity'),
    config:()=>configuration,notice(){},Promise});
  vm.runInContext(functions('  async function open(', '  async function preview'),ctx);
  const opened=ctx.open('tasks');
  await new Promise(setImmediate);
  assert.deepEqual(seen,['jobs','activity']);
  release();await opened;
});

test('partition filtering is explicit and never silently uses an old backend', () => {
  const nodes={sharedPoolPartition:{value:'2'}};
  const ctx=vm.createContext({state:{pool:{page:1}},URLSearchParams,$:id=>nodes[id]});
  vm.runInContext(functions('  function params','  async function refresh'),ctx);
  assert.equal(ctx.params('pool').get('poolId'),'2');
  assert.match(source,/capabilities\.poolFiltering === true && capabilities\.poolIds\?\.includes\(partition\)/);
  assert.match(source,/partition <= data\.poolCount/);
  assert.match(source,/池 3（新增）/);
  assert.match(source,/sharedPoolActivity/);
});

test('task and pool page sizes offer 50/100/200/500, reset page and selection', async () => {
  const nodes = {}, state={jobs:{page:3,pageSize:50,selected:new Map([['old',{}]])},pool:{page:2,pageSize:50,selected:new Map([['old',{}]])}}, refreshes=[];
  const element = (tag,text='') => ({tag,text,children:[],value:'',events:{},attrs:{},
    append(...children){this.children.push(...children);},replaceChildren(){this.children=[];},
    setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,fn){this.events[k]=fn;}});
  nodes.sharedJobsPager=element('div');nodes.sharedPoolPager=element('div');
  const ctx=vm.createContext({state,element,$:id=>nodes[id],URLSearchParams,
    input:(_,value)=>Object.assign(element('input'),{value}),
    select:(id,values)=>{const n=element('select');n.values=values;nodes[id]=n;return n;},
    button:(text,action)=>Object.assign(element('button',text),{action}),
    updateCount(){},refresh:async kind=>refreshes.push(kind)});
  vm.runInContext(functions('  function pager','  function selection'),ctx);
  vm.runInContext(functions('  function params','  async function refresh'),ctx);
  ctx.pager('jobs',{page:3,pages:9,total:421,pageSize:50});
  const size=nodes.sharedJobsPageSize;
  assert.deepEqual(Array.from(size.values,entry=>entry[0]),['50','100','200','500']);
  size.value='500';await size.events.change();
  assert.equal(state.jobs.page,1);assert.equal(state.jobs.pageSize,500);assert.equal(state.jobs.selected.size,0);
  assert.deepEqual(refreshes,['jobs']);assert.equal(ctx.params('jobs').get('pageSize'),'500');
  assert.equal(ctx.params('pool').get('pageSize'),'50');
  ctx.pager('jobs',{page:1,pages:1,total:421,pageSize:500});
  assert.equal(nodes.sharedJobsPageSize.value,'500');
  ctx.pager('pool',{page:2,pages:2,total:60,pageSize:50});
  const poolSize=nodes.sharedPoolPageSize;
  assert.deepEqual(Array.from(poolSize.values,entry=>entry[0]),['50','100','200','500']);
  poolSize.value='200';await poolSize.events.change();
  assert.equal(state.pool.page,1);assert.equal(state.pool.pageSize,200);assert.equal(state.pool.selected.size,0);
  assert.deepEqual(refreshes,['jobs','pool']);assert.equal(ctx.params('pool').get('pageSize'),'200');
});

test('task center renders realtime local queue cards and shows submitted times', () => {
  assert.match(source, /队列状态：\$\{mode\} · 运行中 \$\{running\} 条 · 编辑排队 \$\{editQueued\} 条 · 编辑中 \$\{editRunning\} 条/);
  assert.match(source, /副本排队 \$\{copyQueued\} 条 · 副本中 \$\{copyRunning\} 条 · 待发布 \$\{pendingPublish\} 条 · 发布中 \$\{publishing\} 条/);
  assert.match(source, /activity\.syncing && '采集\/订单同步中'/);
  assert.match(source, /state\.jobs\?\.counts\?\.publish/);
  assert.match(source, /实时状态 · 北京时间 \$\{refreshedAt\}/);
  assert.match(source, /shared-pool-card/);
  assert.match(source, /今日店铺处理与发布（北京时间/);
  assert.match(source, /renderTodayStoreStats\(activity\.todayStoreStats\)/);
  assert.match(source, /\? 4000 : 10000/);
  assert.match(source, /visibilitychange/);
  assert.match(source, /提交队列：\$\{taskEventTime\(row\.queuedAt \|\| row\.createdAt\)\}/);
  assert.match(source, /提交队列：\$\{taskEventTime\(row\.queuedAt \|\| row\.updated\)\}/);
  assert.match(source, /采集时间：妙手未返回 · 本机入池：\$\{taskEventTime\(row\.createdAt\)\}（北京时间）/);
  assert.match(source, /采集时间：\$\{row\.collectedAt\}（妙手返回）/);
});

test('running publication interval can be adjusted without cancelling the current submission', () => {
  assert.match(source, /api\('publish-interval',\{intervalSeconds:interval,confirmation:'UPDATE_SHARED_PUBLISH_INTERVAL'\}\)/);
  assert.match(source, /当前正在提交的商品不会中断；从下一件开始按新间隔执行/);
  assert.match(source, /state\.queueActivity\?\.publishing/);
});

test('publish preview is single-flight and recovers from a closed or timed-out dialog', () => {
  assert.match(source, /publishPreviewLoading: null/);
  assert.match(source, /正在生成发布预览，请等待当前请求完成或关闭确认窗口/);
  assert.match(source, /abortController\.abort\(\)/);
  assert.match(source, /window\.setTimeout\(\(\)=>\{dialog\.previewTimedOut=true;abortController\?\.abort\(\);\},30000\)/);
  assert.match(source, /生成发布预览超时，未提交任何发布任务；请稍后重试/);
});

test('repeated bulk selection replaces the prior selection without a 50-item cap', () => {
  assert.match(source, /state\[kind\]\.selected\.clear\(\);/);
  assert.match(source, /选择本页全部可操作记录（替换当前）/);
  assert.ok(!source.includes('selected.size >= 50'));
});

test('pool confirmation runs through save without a second one-click transfer button', () => {
  assert.ok(source.includes('确认分配并提交待发布队列'));
  assert.ok(!source.includes('一键处理并加入待发布队列'));
  assert.ok(source.includes("api('run', {previewId:plan.previewId, confirmation:'SAVE_SHARED_AUTOMATION_BATCH'})"));
});

test('task center provides a persisted pause and resume control for safe backend restarts', () => {
  assert.ok(source.includes("api('pause', {paused, confirmation:'SET_SHARED_AUTOMATION_PAUSE'})"));
  assert.ok(source.includes("pause.id='sharedAutomationPause'"));
  assert.ok(source.includes("state.config?.paused ? '恢复自动处理' : '暂停自动处理'"));
  assert.match(source, /已暂停领取新任务；进行中的请求会继续完成。请以实时队列状态确认是否已结束，再重启后台/);
  assert.match(source, /已恢复自动处理，排队任务会继续编辑保存/);
});

test('rapid page-size switches and auto refresh retain the latest size and reject legacy pagination', async () => {
  const pending=[], shown=[], notices=[];
  const state={ready:true,revisions:{},kind:'jobs',jobs:{page:1,pageSize:100,selected:new Map()}};
  const badge={classList:{toggle(){}}};
  const ctx=vm.createContext({state,URLSearchParams,$:id=>id==='taskBadge'?badge:null,
    api:url=>new Promise(resolve=>pending.push({url,resolve})),rows:(kind,data)=>shown.push(data),
    updateTaskSummary(){},updateShopOptions(){},notice:message=>notices.push(message)});
  vm.runInContext(functions('  function params','  async function config'),ctx);
  const old=ctx.refresh('jobs');state.jobs.pageSize=500;const latest=ctx.refresh('jobs');
  assert.match(pending[0].url,/pageSize=100/);assert.match(pending[1].url,/pageSize=500/);
  pending[1].resolve({page:1,pageSize:500,total:700});await latest;
  pending[0].resolve({page:2,pageSize:100,total:700});await old;
  assert.equal(shown.length,1);assert.equal(state.jobs.page,1);assert.equal(state.jobs.pageSize,500);
  const refresh=ctx.refresh('jobs');assert.match(pending[2].url,/pageSize=500/);
  pending[2].resolve({page:1,total:700});await refresh;
  assert.equal(shown.length,1);assert.match(notices.at(-1),/未显示旧分页数据/);
});

test('allocation confirmation and totals precede all details without submitting automatically', async () => {
  const node = (tag = 'div', text = '', className = '') => ({tag, text, className, children: [],
    append(...items) {this.children.push(...items);}, prepend(...items) {this.children.unshift(...items);}});
  const state = {config: {mode: 'live'}, pool: {selected: new Map([['2:1', {sourceAccountId: 2, detailId: 1}]])}};
  const calls = [];
  const plan = {previewId: 'preview-token', copyCount: 1, taskCount: 1, skipped: [],
    transfers: Array.from({length: 50}, (_, i) => ({sourceAccountId: 2, detailId: i + 1, title: '商品',
      shopIds: [7], targetAccountId: 1, needsCopy: true}))};
  const ctx = vm.createContext({state, element: node, $: () => ({value: '1'}), chosenTargets: () => [],
    name: id => `采集箱${id}`, shopName: (account, shop) => `${account}/${shop}`,
    button: (text, action) => ({text, action}),
    modal: () => {const d = {content: node(), message: node(), overlay: {remove() {}}}; state.modal = d; return d;},
    api: async (route, body) => {calls.push({route, body}); return route === 'preview' ? plan : {batchId: 'batch'};},
    updateCount() {}, notice() {}, refresh: async kind => {assert.equal(kind,'pool');},
    document: {querySelector: () => {throw Error('Must stay in automation pool');}}});
  vm.runInContext(functions('  async function preview()', '  async function bindPeople()'), ctx);
  await ctx.preview();
  const dialog = state.modal, actions = dialog.content.children[0], submit = actions.children[0];
  assert.equal(actions.className, 'shared-preview-actions');
  assert.equal(submit.text, '确认分配并提交待发布队列');
  assert.equal(actions.children[1], dialog.message); assert.match(dialog.message.textContent, /1 个副本、1 个保存任务/);
  assert.equal(calls.length, 1); assert.equal(submit.disabled, false);
  await submit.action();
  assert.equal(calls[1].route, 'run'); assert.equal(calls[1].body.previewId, 'preview-token');
  assert.equal(calls[1].body.confirmation, 'SAVE_SHARED_AUTOMATION_BATCH');
  assert.ok(!calls.some(call => call.route.includes('publish')));
  state.config.mode = 'demo'; await ctx.preview();
  assert.equal(state.modal.content.children[0].children[0].disabled, true);
});

test('copy attribute default notes are shown as text in job details', () => {
  assert.ok(source.includes('跨账号创建前属性处理记录'));
  assert.ok(source.includes("job.preparationNotes.forEach(note=>notes.append(element('li',note)))"));
});

test('copy repair offers explicit retry for title validation but not unknown network outcomes', async () => {
  const state={}, calls=[];
  const ctx=vm.createContext({state,name:id=>`Account ${id}`,input:()=>({value:''}),
    element:(_,text)=>({text}),field:(_,node)=>node,button:(text,action)=>({text,action}),
    modal:()=>{const d={content:{children:[],append(...nodes){this.children.push(...nodes);}},overlay:{remove(){}}};state.modal=d;return d;},
    api:async(route,body)=>{calls.push({route,body});},refresh:async()=>{}});
  vm.runInContext(functions('  async function reconcile(row)', '  function publishConfirmation'),ctx);
  let retry;
  for(const message of ['产品属性【包含的组件】必填','英文标题不能超过250个字符','siteCollectItemInfo.sizeCharts[0].sizeItems[0].groupIdAndValueMap 必填']) {
    await ctx.reconcile({transfer_id:'copy',message,payload:{sourceAccountId:2,targetAccountId:1,detailId:123}});
    retry=state.modal.content.children.find(n=>/重试创建/.test(n.text));
    assert.ok(retry);assert.equal(calls.length,0);
  }
  await retry.action();
  assert.equal(calls[0].body.confirmation,'RECONCILE_SHARED_COPY');
  await ctx.reconcile({message:'Unable to reach MiaoShou API',payload:{}});
  assert.ok(!state.modal.content.children.some(n=>n.text==='同步源属性并重试创建'));
});

test('all review transfers offer local cleanup regardless of error text', () => {
  const render = functions('  function createRowView(', '  function autoResumeDatabaseLockedTransfers');
  assert.match(render, /if \(row.status === 'needs_review'\)/);
  assert.match(render, /清除本地副本任务/);
  // Error-specific recovery controls may vary, but cleanup must remain
  // available for every review record in the shared status branch.
  assert.match(render, /actions\.append\(deleteButton\('清除本地副本任务'/);
});

test('transfer cleanup requires confirmation, prevents double submit and keeps failed dialog', async () => {
  const state = {}, calls = [], pending = [];
  const ctx = vm.createContext({state,name:id=>`Account ${id}`,
    element:(_,text)=>({text}), button:(text,action)=>({text,action}),
    modal:()=>{const dialog={content:{children:[],append(...items){this.children.push(...items);}},
      overlay:{remove(){dialog.removed=true;}}};state.modal=dialog;return dialog;},
    api:(path,body)=>{calls.push({path,body});return new Promise((resolve,reject)=>pending.push({resolve,reject}));},
    refresh:async()=>{},notice:()=>{}});
  vm.runInContext(functions('  function discardTransfer', '  function statusName'),ctx);
  ctx.discardTransfer({transfer_id:'review',payload:{sourceAccountId:2,targetAccountId:1,detailId:123}});
  assert.equal(calls.length,0);
  const dialog=state.modal, confirm=dialog.content.children.find(n=>n.action).action;
  const failed=confirm();await confirm();assert.equal(calls.length,1);
  pending[0].reject(Error('network'));await assert.rejects(failed,/network/);
  assert.equal(state.modal,dialog);assert.ok(!dialog.removed);
  const retry=confirm();
  assert.equal(calls[1].path,'discard-transfer');
  assert.equal(calls[1].body.transferId,'review');
  assert.equal(calls[1].body.confirmation,'DISCARD_SHARED_TRANSFER');
  pending[1].resolve({discarded:true});await retry;
  assert.equal(state.modal,null);assert.equal(dialog.removed,true);
  await confirm();assert.equal(calls.length,2);
});

test('mixed target shops retain account identity even with identical shop IDs', () => {
  const ctx = vm.createContext({});
  vm.runInContext(functions('  function chosenTargets', '  function modal'), ctx);
  const result = ctx.chosenTargets({selectedOptions:[{value:'1:7'},{value:'2:7'},{value:'2:8'}]});
  assert.deepEqual(JSON.parse(JSON.stringify(result)), [
    {targetAccountId:1,shopId:7},{targetAccountId:2,shopId:7},{targetAccountId:2,shopId:8}]);
});

test('pool person filter retains source account and target shop filter qualifies target account', () => {
  const nodes = {sharedPoolSource:{value:'2'},sharedPoolPerson:{value:'11'},
    sharedJobsShop:{value:'2:7'},sharedJobsAccount:{value:'1'},sharedJobsPerson:{value:'1:11'}};
  const ctx = vm.createContext({URLSearchParams,state:{pool:{page:3},jobs:{page:4}},$:id=>nodes[id]});
  vm.runInContext(functions('  function params', '  async function refresh'),ctx);
  assert.equal(ctx.params('pool').get('sourceAccountId'),'2');
  assert.equal(ctx.params('pool').get('personId'),'11');
  assert.equal(ctx.params('jobs').get('accountId'),'2');
  assert.equal(ctx.params('jobs').get('shopId'),'7');
  assert.equal(ctx.params('jobs').get('personKey'),'1:11');
  assert.equal(ctx.params('jobs').has('personId'),false);
});

test('late list responses cannot overwrite a newer filter or page', async () => {
  const pending = [], shown = [];
  const state = {ready:true,revisions:{},pool:{page:1},kind:'jobs'};
  const ctx = vm.createContext({state,$:()=>({classList:{contains:()=>true}}),params:()=>'',
    api:()=>new Promise(resolve=>pending.push(resolve)),rows:(_,data)=>shown.push(data),notice:()=>{}});
  vm.runInContext(functions('  async function refresh', '  async function config'),ctx);
  const old = ctx.refresh('pool'), latest = ctx.refresh('pool');
  pending[1]({page:2,items:[{sourceAccountId:2}]});await latest;
  pending[0]({page:1,items:[{sourceAccountId:1}]});await old;
  assert.equal(shown.length,1);assert.equal(state.pool.page,2);
  assert.equal(shown[0].items[0].sourceAccountId,2);
});

test('new UI uses explicit preview and publish confirmations without a 50-item selection cap', () => {
  assert.ok(source.includes('SAVE_SHARED_AUTOMATION_BATCH'));
  assert.ok(source.includes('PUBLISH_SHARED_READY_JOBS'));
  assert.ok(!source.includes('每批最多选择 50 件'));
  assert.ok(source.includes('state.config.mode !== \'live\''));
  assert.ok(!source.includes('.innerHTML'));
  assert.ok(!source.includes('localStorage.setItem'));
});

test('sidebar entry is named automation pool without changing the route', () => {
  const html = fs.readFileSync(require('node:path').join(__dirname, '..', 'index.html'), 'utf8');
  assert.match(html, /data-page="automation"><span>⚡<\/span>自动化池<\/button>/);
});

function deletionHarness() {
  const entries = [1,2].map(accountId=>({key:`${accountId}:${'a'.repeat(32)}`,accountId,jobId:'a'.repeat(32),status:'needs_input'}));
  const state = {jobs:{selected:new Map(entries.map(r=>[r.key,r]))},revisions:{jobs:2},modal:null};
  const calls = [], messages = [], pending = [];
  function node(text) { return {text,children:[],classList:{add(){}},append(...children){this.children.push(...children);}}; }
  const ctx = vm.createContext({state,name:id=>`Account ${id}`,element:(_,text)=>node(text),
    button:(text,action)=>({...node(text),action}),
    modal:()=>{const dialog={content:node(''),message:{},overlay:{remove(){dialog.removed=true;}}};state.modal=dialog;return dialog;},
    api:(route,body)=>{calls.push({route,body});return new Promise((resolve,reject)=>pending.push({resolve,reject}));},
    notice:message=>messages.push(message),refresh:async()=>{},updateCount:()=>{}});
  vm.runInContext(functions('  function canDeleteJob', '  async function jobDetails'),ctx);
  const confirm=()=>state.modal.content.children.find(n=>n.id==='sharedConfirmDelete').action();
  return {ctx,state,calls,messages,pending,entries,confirm};
}

test('batch deletion sends qualified IDs and retains only failed selections', async () => {
  const {ctx,state,calls,pending,entries,messages,confirm} = deletionHarness();
  await ctx.deleteRecords();assert.equal(calls.length,0);
  const done = confirm();
  assert.equal(calls.length,1);assert.equal(calls[0].route,'delete-records');
  assert.equal(calls[0].body.confirmation,'DELETE_SHARED_JOB_RECORDS');
  assert.deepEqual(Array.from(calls[0].body.jobs,r=>r.accountId),[1,2]);
  pending[0].resolve({deleted:[entries[1]],errors:[{...entries[0],message:'running'}]});await done;
  assert.deepEqual([...state.jobs.selected.keys()],[entries[0].key]);
  assert.equal(state.deleting,false);assert.equal(state.revisions.jobs,3);
  assert.ok(messages[0].includes('已删除 1 条'));assert.ok(messages[0].includes('1 条未删除'));
});

test('canceling deletion sends nothing and transport failure preserves selection', async () => {
  const {ctx,state,calls,pending,confirm} = deletionHarness();
  await ctx.deleteRecords();assert.equal(calls.length,0);
  state.modal.content.children.find(n=>n.text==='取消，保留记录').action();
  assert.equal(state.modal,null);assert.equal(state.jobs.selected.size,2);assert.equal(calls.length,0);
  await ctx.deleteRecords();const failed=confirm();
  pending[0].reject(Error('network unavailable'));await assert.rejects(failed,/network unavailable/);
  assert.equal(state.jobs.selected.size,2);assert.equal(state.deleting,false);
});

test('duplicate deletion clicks cannot send a second request; only explicit statuses allowed', async () => {
  const {ctx,state,calls,pending,entries,confirm} = deletionHarness();
  await ctx.deleteRecords();const first=confirm();await confirm();await ctx.deleteRecords();assert.equal(calls.length,1);
  pending[0].resolve({deleted:entries,errors:[]});await first;
  assert.equal(state.jobs.selected.size,0);
  for(const status of ['needs_input','queued','done','failed','needs_review','interrupted','canceled'])
    assert.equal(ctx.canDeleteJob({status}),true);
  for(const status of ['running','ready_publish','unknown'])assert.equal(ctx.canDeleteJob({status}),false);
});

test('stale confirmation dialog cannot issue deletion after cancellation', async () => {
  const {ctx,state,calls,entries}=deletionHarness();
  await ctx.deleteRecords();const old=state.modal;
  old.content.children.find(n=>n.text==='取消，保留记录').action();
  await ctx.performDeleteRecords(entries,old);assert.equal(calls.length,0);
  assert.equal(state.jobs.selected.size,2);
});

test('task toolbar shows publication only in publish group and gates actions by selection', () => {
  const nodes = Object.fromEntries(['sharedJobActions','sharedPublishSelected','sharedRetrySelected','sharedDeleteSelected','sharedPublishSettings','sharedSelectionHint','sharedPublishCountField','sharedPublishCountPreview','sharedPublishCount'].map(id=>[id,{}]));
  nodes.sharedPublishCount.value='209';
  const state = {config:{mode:'live'},jobs:{group:'attention',selected:new Map()}};
  const canDeleteJob=r=>!['running','ready_publish'].includes(r.status);
  const ctx = vm.createContext({state,$:id=>nodes[id],canDeleteJob,canDeleteRecord:r=>r.type === 'transfer'
    ? ['needs_review','discarded'].includes(r.status) : canDeleteJob(r)});
  vm.runInContext(functions('  function updateTaskControls','  function updateTaskSummary'),ctx);
  ctx.updateTaskControls();assert.equal(nodes.sharedPublishSelected.hidden,true);assert.equal(nodes.sharedRetrySelected.disabled,true);
  state.jobs.selected.set('1:a',{status:'needs_input',available:true});ctx.updateTaskControls();
  assert.equal(nodes.sharedRetrySelected.disabled,true);assert.equal(nodes.sharedDeleteSelected.disabled,false);
  state.jobs.selected.set('1:a',{status:'failed',available:true});ctx.updateTaskControls();assert.equal(nodes.sharedRetrySelected.disabled,false);
  state.jobs.group='publish';state.jobs.selected.set('1:a',{status:'ready_publish',available:true});ctx.updateTaskControls();
  assert.equal(nodes.sharedPublishSelected.hidden,false);assert.equal(nodes.sharedPublishSelected.disabled,false);
  assert.equal(nodes.sharedRetrySelected.hidden,true);assert.equal(nodes.sharedDeleteSelected.hidden,true);
  state.config.mode='demo';ctx.updateTaskControls();assert.equal(nodes.sharedPublishSelected.disabled,true);
  assert.equal(nodes.sharedPublishCountPreview.disabled,false);assert.equal(nodes.sharedPublishCountField.hidden,false);
  nodes.sharedPublishCount.value='1.5';ctx.updateTaskControls();assert.equal(nodes.sharedPublishCountPreview.disabled,true);
  assert.ok(nodes.sharedSelectionHint.textContent.includes('只读'));
  state.jobs.group='processing';state.jobs.selected.set('1:a',{status:'running',available:true});ctx.updateTaskControls();
  assert.equal(nodes.sharedPublishSelected.hidden,true);assert.equal(nodes.sharedDeleteSelected.disabled,true);
  assert.equal(nodes.sharedPublishCountField.hidden,true);assert.equal(nodes.sharedPublishCountPreview.hidden,true);
});

test('group filters are explicit and status names distinguish saved, submitted, and removed', () => {
  const ctx = vm.createContext({URLSearchParams,state:{jobs:{page:2,group:'attention'}},$:()=>null});
  vm.runInContext(functions('  function statusName','  async function refresh'),ctx);
  assert.equal(ctx.params('jobs').get('taskGroup'),'attention');
  assert.equal(ctx.statusName('needs_input'),'待你处理');
  assert.equal(ctx.statusName('done'),'已提交发布');
  assert.equal(ctx.statusName('canceled','publish'),'已移出发布队列');
  assert.equal(ctx.statusName('running','publish'),'正在发布');
});

function shopCountsHarness() {
  const shop = {value:'',options:[{value:'',textContent:'全部店铺'}],replaceChildren(){this.options=[];},append(item){this.options.push(item);}};
  const hint = {};
  const state = {ready:true,revisions:{},kind:'queue',jobs:{page:3,group:'publish',selected:new Map([['selected',{}]])},
    config:{accounts:[{id:1,name:'一号'},{id:2,name:'二号'}],targets:[
      {accountId:1,shops:[{shopId:7,shopNick:'同名店'},{shopId:8,shopNick:'空店'}]},
      {accountId:2,shops:[{shopId:7,shopNick:'同名店'}]}]}};
  const ctx = vm.createContext({state,$:id=>id==='sharedJobsShop'?shop:id==='sharedShopPendingHint'?hint:{classList:{toggle(){}}},
    option:(node,value,textContent)=>node.append({value,textContent})});
  vm.runInContext(functions('  function name','  function accountOptions'),ctx);
  return {ctx,state,shop,hint};
}

test('shop counts qualify identical shop IDs, preserve selection and order, and add unnamed shops',()=>{
  const {ctx,state,shop,hint}=shopCountsHarness();
  ctx.updateShopOptions([{accountId:1,shopId:7,count:61},{accountId:2,shopId:7,count:2},{accountId:2,shopId:99,count:4}]);
  assert.deepEqual(shop.options.map(o=>o.value),['','1:7','1:8','2:7','2:99']);
  assert.match(shop.options[1].textContent,/一号.*同名店.*待发布 61$/);
  assert.match(shop.options[2].textContent,/待发布 0$/);
  assert.match(shop.options[3].textContent,/二号.*待发布 2$/);
  assert.match(shop.options[4].textContent,/店铺 99.*待发布 4$/);
  shop.value='2:7';const existing=shop.options[3];
  ctx.updateShopOptions([{accountId:1,shopId:7,count:60},{accountId:2,shopId:7,count:1},{accountId:2,shopId:99,count:4}]);
  assert.equal(shop.value,'2:7');assert.equal(shop.options[3],existing);
  assert.equal(state.jobs.page,3);assert.equal(state.jobs.selected.size,1);
  state.jobs.group='attention';ctx.updateShopOptions();
  assert.equal(hint.hidden,true);assert.ok(shop.options.every(o=>!o.textContent.includes('待发布')));
  state.jobs.group='publish';ctx.updateShopOptions();assert.equal(hint.hidden,false);
});

test('unknown counts are not zero, failed refresh retains previous totals, config refresh keeps current shop',()=>{
  const {ctx,state,shop,hint}=shopCountsHarness();
  ctx.updateShopOptions();assert.match(shop.options[1].textContent,/待发布 —$/);
  ctx.updateShopOptions([{accountId:1,shopId:7,count:12}]);
  ctx.updateShopOptions([{accountId:1,shopId:7,count:'invalid'}]);
  assert.match(shop.options[1].textContent,/待发布 12$/);assert.match(hint.textContent,/刷新失败/);
  shop.value='1:7';state.config.targets=[];ctx.updateShopOptions();
  assert.equal(shop.value,'1:7');assert.match(shop.options[1].textContent,/店铺 7.*待发布 12$/);
  ctx.updateShopOptions([]);assert.equal(shop.value,'1:7');assert.match(shop.options[1].textContent,/待发布 0$/);
  assert.equal(state.jobs.shopCountsStale,false);
});

test('late and failed job responses do not overwrite current shop totals or selection',async()=>{
  const {ctx,state,shop,hint}=shopCountsHarness(), pending=[];
  ctx.api=()=>new Promise((resolve,reject)=>pending.push({resolve,reject}));
  ctx.params=()=>'';ctx.rows=()=>{};ctx.notice=()=>{};
  ctx.updateTaskSummary=data=>ctx.updateShopOptions(data.shopPendingCounts);
  vm.runInContext(functions('  async function refresh','  async function config'),ctx);
  const old=ctx.refresh('jobs'),latest=ctx.refresh('jobs');
  pending[1].resolve({page:3,totalAll:4,shopPendingCounts:[{accountId:1,shopId:7,count:4}]});await latest;
  pending[0].resolve({page:1,totalAll:99,shopPendingCounts:[{accountId:1,shopId:7,count:99}]});await old;
  assert.match(shop.options[1].textContent,/待发布 4$/);assert.equal(state.jobs.page,3);
  const failed=ctx.refresh('jobs');pending[2].reject(Error('offline'));await failed;
  assert.match(shop.options[1].textContent,/待发布 4$/);assert.match(hint.textContent,/刷新失败/);
  assert.equal(state.jobs.selected.size,1);
});

test('pool pager shares selection tools above the list, while task pagination remains below',()=>{
  assert.ok(source.includes("poolTools.append(selectBar('pool'),poolPager)"));
  assert.ok(source.includes('pool.append(poolTools,poolList)'));
  assert.ok(source.includes("['sharedJobsList','shared-list'],['sharedJobsPager','shared-pager']"));
  assert.equal((source.match(/poolPager.id='sharedPoolPager'/g)||[]).length,1);
});

test('task account and shop stay visible; search and status collapse without clearing filters',async()=>{
  const nodes = new Map(), state={revisions:{},jobs:{group:'publish',page:3,selected:new Map([['x',{}]])}};
  const refreshed=[], timers=new Map();let timerId=0,now=0;
  const window={setTimeout(fn,delay){timers.set(++timerId,{fn,at:now+delay});return timerId;},clearTimeout(id){timers.delete(id);}};
  const advance=async milliseconds=>{
    now+=milliseconds;
    for(const [id,timer] of [...timers])if(timer.at<=now){timers.delete(id);await timer.fn();}
  };
  function element(tag,text='',cls='') {
    const node={tag,textContent:text,children:[],value:'',events:{},attrs:{},classList:{add(){}},
      append(...children){this.children.push(...children);children.forEach(child=>child.parent=this);},
      setAttribute(key,value){this.attrs[key]=value;},addEventListener(event,handler){this.events[event]=handler;},
      querySelectorAll(tag){return this.children.flatMap(child=>[...(child.tag===tag?[child]:[]),...child.querySelectorAll(tag)]);}};
    Object.defineProperty(node,'id',{set(value){this.key=value;nodes.set(value,this);},get(){return this.key;}});
    return node;
  }
  const ctx=vm.createContext({state,window,$:id=>nodes.get(id),element,
    input:id=>{const node=element('input');node.id=id;return node;},
    select:id=>{const node=element('select');node.id=id;return node;},
    field:(text,child)=>{const node=element('label',text);node.append(child);return node;},
    button:(text,action)=>Object.assign(element('button',text),{action}),
    taskGroups:{publish:{statuses:['ready_publish']}},statusName:s=>s,
    updateCount:()=>{},updatePersonOptions:()=>{},refresh:async kind=>refreshed.push(kind)});
  vm.runInContext(functions('  function updateMoreFilters','  function selectBar'),ctx);
  const bar=ctx.toolbar('jobs'),extra=nodes.get('sharedExtraFilters'),more=nodes.get('sharedMoreFilters');
  for(const key of ['Account','Shop','Person'])assert.equal(nodes.get('sharedJobs'+key).parent.parent,bar);
  for(const key of ['Source','Keyword','Status'])assert.equal(nodes.get('sharedJobs'+key).parent.parent,extra);
  assert.equal(extra.hidden,true);more.action();assert.equal(extra.hidden,false);
  const previousRead=new AbortController();state.jobs.controller=previousRead;
  const keyword=nodes.get('sharedJobsKeyword');keyword.value='手套';keyword.events.input({isComposing:false});
  assert.equal(previousRead.signal.aborted,true);assert.equal(state.revisions.jobs,1);
  assert.equal(state.jobs.selectionBlocked,true);assert.equal(timers.size,1);
  assert.equal(more.textContent,'更多筛选 (1)');
  more.action();assert.equal(extra.hidden,true);assert.equal(keyword.value,'手套');
  assert.equal(state.jobs.page,3);assert.equal(state.jobs.selected.size,1);assert.equal(refreshed.length,0);
  nodes.get('sharedJobsShop').value='2:7';nodes.get('sharedJobsShop').events.change();
  assert.equal(nodes.get('sharedJobsAccount').value,'2');assert.equal(more.textContent,'更多筛选 (1)');
  assert.equal(timers.size,1);assert.equal(state.revisions.jobs,2);
  assert.equal(state.jobs.page,3);assert.equal(state.jobs.selected.size,1);
  await advance(249);assert.equal(refreshed.length,0);
  await advance(1);assert.equal(timers.size,0);
  assert.equal(state.jobs.page,1);assert.equal(state.jobs.selected.size,0);assert.deepEqual(refreshed,['jobs']);
  keyword.value='';nodes.get('sharedJobsStatus').value='ready_publish';ctx.updateMoreFilters();
  assert.equal(more.textContent,'更多筛选 (1)');
  nodes.get('sharedJobsStatus').value='';ctx.updateMoreFilters();assert.equal(more.textContent,'更多筛选');
  const person=nodes.get('sharedJobsPerson');person.value='2:11';state.jobs.page=4;state.jobs.selected.set('old',{});
  person.events.change();assert.equal(state.jobs.page,4);assert.equal(state.jobs.selected.size,1);assert.equal(refreshed.length,1);
  await advance(250);assert.equal(state.jobs.page,1);assert.equal(state.jobs.selected.size,0);assert.equal(person.value,'2:11');
  assert.deepEqual(refreshed,['jobs','jobs']);
  const account=nodes.get('sharedJobsAccount');account.value='1';account.events.change();assert.equal(person.value,'2:11');
  assert.equal(nodes.get('sharedJobsShop').value,'');
  const source=nodes.get('sharedJobsSource');source.value='1';source.events.change();assert.equal(person.value,'');
  assert.equal(timers.size,1);assert.equal(refreshed.length,2);
  await advance(250);assert.deepEqual(refreshed,['jobs','jobs','jobs']);
});

function publishQuantityHarness(mode='live') {
  const state={config:{mode},jobs:{selected:new Map([['unrelated',{accountId:9}]])}};
  const nodes={sharedPublishCount:{value:'209'},sharedPublishInterval:{value:'2'}};
  const calls=[],pending=[];
  function element(tag,text='') {return {tag,textContent:text,children:[],events:{},
    addEventListener(event,handler){this.events[event]=handler;},replaceChildren(){this.children=[];},
    append(...children){this.children.push(...children);}};}
  const ctx=vm.createContext({state,$:id=>nodes[id],element,
    input:(_,value,type)=>({...element('input'),value,type}),field:(text,child)=>{const node=element('label',text);node.append(child);return node;},
    button:(text,action)=>({...element('button',text),action}),
    modal:()=>{const dialog={content:element('div'),message:{},overlay:{remove(){dialog.removed=true;}}};state.modal=dialog;return dialog;},
    params:()=>new URLSearchParams({page:'3',taskGroup:'publish',accountId:'2',shopId:'7',keyword:'glove',personKey:'1:11'}),
    api:(path,body)=>{calls.push({path,body});return new Promise((resolve,reject)=>pending.push({resolve,reject}));},
    name:id=>`Account ${id}`,shopName:(account,shop)=>`Account ${account}/Shop ${shop}`,personLabel:key=>`人员 ${key}`,
    notice:()=>{},updateTaskControls:()=>{},refresh:async()=>{}});
  vm.runInContext(functions('  function publishConfirmation','  function updateMoreFilters'),ctx);
  const plan={previewId:'frozen',requestedCount:209,total:60,count:60,intervalSeconds:2,shops:[{accountId:2,shopId:7,count:60}],
    sample:[{title:'Glove',sourceDetailId:123,sourceAccountId:1,accountId:2,shopId:7}]};
  return {ctx,state,nodes,calls,pending,plan};
}

test('quantity preview ignores page and selection, uses frozen token, and prevents double release',async()=>{
  const {ctx,state,calls,pending,plan}=publishQuantityHarness();
  const preview=ctx.publishByCount();assert.equal(calls.length,1);
  assert.equal(calls[0].path,'publish-preview');assert.equal(calls[0].body.publishCount,209);
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0].body.filters)),{accountId:'2',shopId:'7',keyword:'glove',personKey:'1:11'});
  pending[0].resolve(plan);await preview;
  const confirm=state.modal.content.children.find(n=>n.textContent==='确认发布 60 件');
  assert.ok(state.modal.content.children.some(n=>n.textContent==='所属人员：人员 1:11'));
  assert.ok(confirm);assert.equal(calls.length,1);
  const release=confirm.action();await confirm.action();assert.equal(calls.length,2);
  assert.deepEqual(JSON.parse(JSON.stringify(calls[1].body)),{previewId:'frozen',confirmation:'PUBLISH_SHARED_READY_JOBS'});
  pending[1].resolve({submitted:60});await release;
  assert.equal(state.modal,null);assert.equal(state.publishing,false);assert.equal(state.jobs.selected.size,0);
});

test('read-only quantity preview cannot release, and dismissed late responses cannot open confirmation',async()=>{
  const {ctx,state,calls,pending,plan}=publishQuantityHarness('demo');
  const preview=ctx.publishByCount();pending[0].resolve(plan);await preview;
  const confirm=state.modal.content.children.find(n=>n.textContent==='确认发布 60 件');
  assert.equal(confirm.disabled,true);await assert.rejects(confirm.action(),/只读/);assert.equal(calls.length,1);
  const late=ctx.publishByCount();const abandoned=state.modal;state.modal=null;
  pending[1].resolve(plan);await late;
  assert.equal(abandoned.content.children.length,0);assert.equal(state.modal,null);
});

test('invalid quantity or interval sends no request; transport error preserves selection and releases UI lock',async()=>{
  const {ctx,state,nodes,calls,pending,plan}=publishQuantityHarness();
  for(const value of ['','0','1.5','NaN']) {
    nodes.sharedPublishCount.value=value;await assert.rejects(ctx.publishByCount(),/数量/);
  }
  nodes.sharedPublishCount.value='50';nodes.sharedPublishInterval.value='0';
  await assert.rejects(ctx.publishByCount(),/间隔/);assert.equal(calls.length,0);
  nodes.sharedPublishInterval.value='2';const preview=ctx.publishByCount();pending[0].resolve(plan);await preview;
  const dialog=state.modal,confirm=dialog.content.children.find(n=>n.textContent==='确认发布 60 件');
  const release=confirm.action();pending[1].reject(Error('offline'));await assert.rejects(release,/offline/);
  assert.equal(state.publishing,false);assert.equal(state.jobs.selected.size,1);assert.equal(state.modal,dialog);
  state.modal=null;await confirm.action();assert.equal(calls.length,2);
});

test('asset and template sidebar entries are removed without deleting compatible sections',()=>{
  const html=fs.readFileSync(require('node:path').join(__dirname,'..','index.html'),'utf8');
  assert.ok(!html.includes('data-page="products"'));assert.ok(!html.includes('data-page="templates"'));
  assert.ok(html.includes('id="products"'));assert.ok(html.includes('id="templates"'));
});

test('multi-shop dialog submits an independently limited quantity for every selected shop',async()=>{
  const {ctx,state,nodes,calls,pending,plan}=publishQuantityHarness('demo');
  state.config.accounts=[{id:1,configured:true},{id:2,configured:true},{id:3,configured:true,blocked:true}];
  state.config.targets=[{accountId:1,shops:[{shopId:7}]},{accountId:2,shops:[{shopId:7}]},{accountId:3,shops:[{shopId:7}]}];
  state.jobs.shopPendingCounts=new Map([['1:7',3],['2:7',4],['3:7',9],['2:99',2]]);
  nodes.sharedJobsShop={value:'1:7'};
  const choices=ctx.publishShopChoices();assert.equal(choices.length,4);
  assert.equal(choices.find(r=>r.key==='3:7').available,false);
  ctx.publishMultipleShops();const dialog=state.modal;
  const buttons=dialog.content.children.flatMap(n=>[n,...n.children]).filter(n=>n.tag==='button');
  const preview=buttons.find(n=>n.textContent==='预览所选店铺发布');assert.equal(preview.disabled,false);
  buttons.find(n=>n.textContent==='清空店铺选择').action();assert.equal(preview.disabled,true);
  await assert.rejects(preview.action(),/选择/);assert.equal(calls.length,0);
  buttons.find(n=>n.textContent==='选择有待发布的店铺').action();assert.equal(preview.disabled,false);
  const walk=node=>[node,...node.children.flatMap(walk)];
  const quantities=walk(dialog.content).filter(n=>n.type==='number'&&!n.max);
  quantities[0].value='2';quantities[0].events.input();
  quantities[1].value='3';quantities[1].events.input();
  quantities[3].value='1';quantities[3].events.input();
  const request=preview.action();assert.equal(calls[0].path,'publish-preview');
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0].body.filters)),{keyword:'glove',personKey:'1:11'});
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0].body.shopCounts)),[
    {accountId:1,shopId:7,publishCount:2},{accountId:2,shopId:7,publishCount:3},{accountId:2,shopId:99,publishCount:1}]);
  assert.equal(calls[0].body.publishCount,6);
  pending[0].resolve(plan);await request;
  assert.equal(dialog.content.children.find(n=>n.textContent==='确认发布 60 件').disabled,true);
  assert.equal(calls.length,1);
});

function personOptionsHarness() {
  const nodes={}, state={ready:true,revisions:{},kind:'jobs',jobs:{page:2,group:'publish',selected:new Map([['kept',{}]])}};
  function element(tag,textContent='') {
    const node={tag,textContent,value:'',dataset:{},children:[],classList:{toggle(){}},
      append(...items){this.children.push(...items);},replaceChildren(){this.children=[];}};
    Object.defineProperty(node,'options',{get(){return this.children.flatMap(n=>n.tag==='option'?[n]:n.options);}});
    return node;
  }
  nodes.sharedJobsPerson=element('select');nodes.sharedJobsSource={value:''};nodes.sharedPersonHint={};nodes.taskBadge=element('b');
  const ctx=vm.createContext({state,$:id=>nodes[id],element,name:id=>`采集箱 ${id}`,
    option:(node,value,text)=>node.append(Object.assign(element('option',text),{value})),
    updateShopOptions(){},updateTaskSummary(){},params:()=>'',notice(){}});
  vm.runInContext(functions('  function personLabel','  function accountOptions'),ctx);
  return {ctx,state,nodes};
}

test('person options group source accounts, preserve aliases and selection including unknown',()=>{
  const {ctx,state,nodes}=personOptionsHarness(), dropdown=nodes.sharedJobsPerson;
  ctx.updatePersonOptions();assert.equal(dropdown.disabled,true);
  const options=[{sourceAccountId:1,personId:'11',personKey:'1:11',personName:'备注'},
    {sourceAccountId:2,personId:'11',personKey:'2:11',personName:'备注'}];
  ctx.updatePersonOptions(options);assert.equal(dropdown.disabled,false);
  assert.deepEqual(dropdown.children.filter(n=>n.tag==='optgroup').map(n=>n.label),['采集箱 1','采集箱 2']);
  assert.deepEqual(dropdown.options.map(n=>n.value),['','unknown','1:11','2:11']);
  dropdown.value='2:11';const original=dropdown.children;ctx.updatePersonOptions(options);
  assert.equal(dropdown.children,original);assert.equal(dropdown.value,'2:11');
  ctx.updatePersonOptions([]);assert.equal(dropdown.value,'2:11');assert.ok(dropdown.options.some(n=>n.value==='2:11'));
  assert.throws(()=>ctx.updatePersonOptions([{...options[0],personKey:'2:11'}]),/读取失败/);
  assert.equal(dropdown.value,'2:11');
  dropdown.value='unknown';ctx.updatePersonOptions(options);assert.equal(dropdown.value,'unknown');
  nodes.sharedJobsSource.value='1';ctx.updatePersonOptions();assert.ok(!dropdown.options.some(n=>n.value==='2:11'));
  assert.equal(state.jobs.page,2);assert.equal(state.jobs.selected.size,1);
});

test('person responses cannot race back to old filters, failed and legacy reads never broaden them',async()=>{
  const {ctx,state,nodes}=personOptionsHarness(), pending=[], shown=[];
  const options=[{sourceAccountId:1,personId:'11',personKey:'1:11',personName:'甲'},
    {sourceAccountId:2,personId:'22',personKey:'2:22',personName:'乙'}];
  ctx.updatePersonOptions(options);nodes.sharedJobsPerson.value='1:11';
  ctx.api=()=>new Promise((resolve,reject)=>pending.push({resolve,reject}));ctx.rows=(_,data)=>shown.push(data);
  vm.runInContext(functions('  async function refresh','  async function config'),ctx);
  const older=ctx.refresh('jobs');nodes.sharedJobsPerson.value='2:22';const latest=ctx.refresh('jobs');
  pending[1].resolve({page:2,total:1,personOptions:options});await latest;
  pending[0].resolve({page:1,total:10,personOptions:[options[0]]});await older;
  assert.equal(shown.length,1);assert.equal(nodes.sharedJobsPerson.value,'2:22');assert.equal(state.jobs.page,2);
  const failed=ctx.refresh('jobs');pending[2].reject(Error('offline'));await failed;
  const legacy=ctx.refresh('jobs');pending[3].resolve({page:1,total:100});await legacy;
  assert.equal(shown.length,1);assert.equal(nodes.sharedJobsPerson.value,'2:22');assert.equal(state.jobs.selected.size,1);
});

test('task group switch retains personnel filter and clears old page selections',async()=>{
  const {ctx,state,nodes}=personOptionsHarness();nodes.sharedJobsPerson.value='2:11';
  nodes.sharedJobsStatus={replaceChildren(){}};ctx.option=()=>{};
  ctx.taskGroups={history:{statuses:['done','canceled']}};ctx.statusName=s=>s;
  ctx.updateMoreFilters=()=>{};ctx.updateCount=()=>{};ctx.refresh=async()=>{};
  vm.runInContext(functions('  async function switchTaskGroup','  function rows'),ctx);
  await ctx.switchTaskGroup('history');assert.equal(nodes.sharedJobsPerson.value,'2:11');
  assert.equal(state.jobs.page,1);assert.equal(state.jobs.selected.size,0);
});

test('manual queue reload keeps a separate explicit confirmation path',()=>{
  assert.ok(source.includes("api('reload-queue', {confirmation:'RELOAD_SHARED_QUEUE'})"));
  assert.ok(source.includes("button('一键重新加载排队任务',reloadQueuedWork)"));
  assert.ok(source.includes("result.localAccounts.length"));
});
