const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {createDocument}=require('./dom_fixture');
const source=fs.readFileSync(require('node:path').join(__dirname,'..','shared-automation.js'),'utf8');
const code=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));

function setup({virtual=false}={}) {
  const document=createDocument(), calls=[], notices=[];
  for(const kind of ['Pool','Jobs'])for(const suffix of ['List','Pager','Count','Partition'])document.createNode(`shared${kind}${suffix}`);
  for(const id of ['sharedTransfers','taskBadge','automation'])document.createNode(id);
  document.getElementById('automation').classList.add('active');
  const state={ready:true,revisions:{},kind:'jobs',config:{mode:'live'},
    pool:{page:1,pageSize:500,selected:new Map()},jobs:{page:1,pageSize:500,group:'attention',selected:new Map()}};
  const frames=new Map();let frameId=0;
  const context=vm.createContext({state,document,Map,Set,URLSearchParams,Error,AbortController,
    $:id=>document.getElementById(id),notice:message=>notices.push(message),updateTaskControls(){},
    updateTaskSummary(){},updateShopOptions(){},autoResumeDatabaseLockedTransfers(){},
    name:id=>`Account ${id}`,shopName:(id,shop)=>`${id}/${shop}`,taskEventTime:value=>value || '',statusName:value=>value,
    api:async(route,body)=>{calls.push({route,body});return {page:1,pages:1,total:0,pageSize:500,jobs:[]};},
    params:()=>new URLSearchParams(),window:virtual?{innerHeight:1000,requestAnimationFrame:fn=>{frames.set(++frameId,fn);return frameId;},cancelAnimationFrame:id=>frames.delete(id)}:{},jobDetails(){},editJob(){},reconcile(){},discardTransfer(){},resumeLockedTransfer(){},
    deleteRecords(){},deleteButton:(label,action)=>context.button(label,action)});
  if(virtual){
    const proto=Object.getPrototypeOf(document.createElement('div'));
    proto.getBoundingClientRect=function(){return {height:this._height || (this.tagName==='ARTICLE'?220:this.children.reduce((sum,node)=>sum+node.getBoundingClientRect().height,0)),width:1200};};
    for(const kind of ['Pool','Jobs']){const list=document.getElementById(`shared${kind}List`);list.clientHeight=600;list.clientWidth=1200;}
    vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'..','virtual-list.js'),'utf8'),context);
  }
  for(const [start,end] of [
    ['  function element(', '  async function api('],
    ['  function pager(', '  async function setAutomationPause('],
    ['  function rows(', '  function autoResumeDatabaseLockedTransfers('],
    ['  async function refresh(', '  async function config('],
    ['  async function jobAction(', '  function deleteButton('],
    ['  function selectBar(', '  function mount('],
  ])vm.runInContext(code(start,end),context);
  const jobs=Array.from({length:500},(_,i)=>({type:'job',key:`1:${i+1}`,jobId:i+1,accountId:1,sourceAccountId:1,
    detailId:i+1,shopId:7,available:true,status:'failed',title:`Item ${i+1}`,message:'old',site:'US',poolId:1}));
  const flush=()=>{let count=0;while(frames.size){if(++count>30)throw Error('render loop');const ready=[...frames.values()];frames.clear();ready.forEach(fn=>fn());}};
  return {context,state,document,calls,notices,jobs,flush};
}
const response=entries=>({entries,jobs:entries.filter(row=>row.type!=='transfer'),transfers:entries.filter(row=>row.type==='transfer'),page:1,pages:2,total:600,pageSize:500});

test('actual shared page selects and clears 500 rows locally without replacing nodes or issuing GETs',async()=>{
  const {context,state,document,calls,jobs}=setup();context.rows('jobs',response(jobs));
  const list=document.getElementById('sharedJobsList'),original=[...list.children],created=document.created;
  const bar=context.selectBar('jobs');const afterBar=document.created;
  await bar.children[0].dispatch('click');
  assert.equal(state.jobs.selected.size,500);assert.equal(calls.length,0);assert.equal(document.created,afterBar);
  assert.ok([...state.jobs.rowViews.values()].every(view=>view.checkbox.checked));
  await bar.children[1].dispatch('click');
  assert.equal(state.jobs.selected.size,0);assert.equal(calls.length,0);assert.equal(document.created,afterBar);
  assert.deepEqual(list.children,original);assert.ok(document.created>=created);
  assert.ok([...state.jobs.rowViews.values()].every(view=>!view.checkbox.checked));
});

test('background response preserves clicks, DOM focus, scroll and in-progress page input for 500 shared rows',async()=>{
  const {context,state,document,jobs}=setup();context.rows('jobs',response(jobs));
  const list=document.getElementById('sharedJobsList'),original=[...list.children],checkbox=state.jobs.rowViews.get('1:6').checkbox;
  let finish;context.api=()=>new Promise(resolve=>{finish=resolve;});
  const refreshing=context.refresh('jobs',{background:true});
  checkbox.checked=true;await checkbox.dispatch('change');checkbox.focus();list.scrollTop=1800;
  assert.equal(state.jobs.selected.size,1);
  const created=document.created;
  finish(response(jobs.map(row=>row.key==='1:6'?{...row,message:'new progress'}:{...row})));await refreshing;
  assert.equal(state.jobs.selected.size,1);assert.equal(state.jobs.selected.get('1:6').message,'new progress');
  assert.equal(checkbox.checked,true);assert.equal(document.activeElement,checkbox);assert.equal(list.scrollTop,1800);
  assert.deepEqual(list.children,original);assert.equal(document.created,created);
  const page=state.jobs.pagerView.page;page.value='12';page.focus();
  context.rows('jobs',response(jobs));assert.equal(page.value,'12');assert.equal(document.activeElement,page);
});

test('mixed entries preserve backend ordering and review transfers enter only local batch deletion',async()=>{
  const {context,state,document,jobs}=setup();
  const transfer={type:'transfer',key:'transfer:copy-a',transfer_id:'copy-a',status:'needs_review',message:'needs check',
    payload:{sourceAccountId:1,targetAccountId:2,detailId:99}};
  context.rows('jobs',response([jobs[0],transfer,jobs[1]]));
  assert.deepEqual([...state.jobs.rowViews.keys()],['1:1','transfer:copy-a','1:2']);
  assert.equal(document.getElementById('sharedJobsList').children[1],state.jobs.rowViews.get('transfer:copy-a').line);
  assert.match(state.jobs.rowViews.get('transfer:copy-a').line.textContent,/清除本地副本任务/);
  assert.ok(state.jobs.rowViews.get('transfer:copy-a').checkbox);
  const bar=context.selectBar('jobs');await bar.children[0].dispatch('click');assert.equal(state.jobs.selected.size,3);
  assert.equal(document.getElementById('sharedTransfers').children.length,0);
});

test('batch retry rejects a second click, reports progress and refreshes only after all 500 actions',async()=>{
  const {context,state,calls,notices,jobs}=setup();jobs.forEach(row=>state.jobs.selected.set(row.key,row));
  let finish;let reads=0;
  context.api=(route,body)=>{calls.push({route,body});if(calls.length===1)return new Promise(resolve=>{finish=resolve;});return Promise.resolve({});};
  context.refresh=async()=>{reads++;assert.equal(calls.length,500);};
  const batch=context.retrySelected();await context.retrySelected();assert.equal(calls.length,1);assert.equal(state.retrying,true);assert.equal(reads,0);
  finish({});await batch;
  assert.equal(calls.length,500);assert.equal(reads,1);assert.equal(state.retrying,false);assert.equal(state.jobs.selected.size,0);
  assert.ok(notices.some(text=>text.includes('500/500')));
});

test('failed retries retain only failed selection and account-qualified references',async()=>{
  const {context,state,jobs}=setup();const failed={...jobs[1],key:'2:2',accountId:2};
  state.jobs.selected.set(jobs[0].key,jobs[0]);state.jobs.selected.set(failed.key,failed);
  const seen=[];context.api=async(route,body)=>{seen.push(body);if(body.accountId===2)throw Error('busy');};context.refresh=async()=>{};
  await context.retrySelected();assert.deepEqual([...state.jobs.selected.keys()],['2:2']);
  assert.deepEqual(seen.map(body=>[body.accountId,body.jobId]),[[1,1],[2,2]]);
});

test('classified safe batch excludes uncertain records, blocks repeat clicks and refreshes once',async()=>{
  const {context,state,calls,notices,jobs}=setup();context.window.confirm=()=>true;
  const transfer={type:'transfer',key:'transfer:copy-a',transfer_id:'copy-a',safeBatchEligible:true};
  state.jobs.rows=[{...jobs[0],safeBatchEligible:true},transfer,{...jobs[1],safeBatchEligible:false}];
  let finish,reads=0;
  const accepted={counts:{processed:1,failed:0,skipped:0},failed:[]};
  context.api=(route,body)=>{calls.push({route,body});return calls.length===1 ? new Promise(resolve=>{finish=resolve;}) : Promise.resolve(accepted);};
  context.refresh=async()=>{reads++;};
  const pending=context.handleKnownFailures();await context.handleKnownFailures();await context.retrySelected();
  assert.equal(calls.length,1);assert.equal(reads,0);assert.equal(state.handlingFailures,true);
  finish(accepted);await pending;
  assert.equal(calls.length,2);assert.equal(reads,1);assert.equal(state.handlingFailures,false);
  assert.equal(calls[0].route,'failure-batch');assert.equal(calls[0].body.entries[0].jobId,1);
  assert.equal(calls[1].body.entries[0].transferId,'copy-a');assert.match(notices.at(-1),/已接受 2 条/);
});

test('classified batch preserves failure and skip counts and cancel makes no write',async()=>{
  const {context,state,calls,notices,jobs}=setup();state.jobs.rows=jobs.slice(0,3).map(row=>({...row,safeBatchEligible:true}));
  context.window.confirm=()=>false;await context.handleKnownFailures();assert.equal(calls.length,0);
  context.window.confirm=()=>true;let requests=0;context.refresh=async()=>{};
  context.api=async()=>{requests++;if(requests===1)throw Error('offline');return {counts:{processed:0,failed:requests===2?1:0,skipped:requests===3?1:0},failed:[]};};
  await context.handleKnownFailures();assert.match(notices.at(-1),/失败 2 条，跳过 1 条/);assert.equal(state.handlingFailures,false);
});

test('capabilities accept supported older pool counts and reject unsupported partitions without dropping list',async()=>{
  const {context,state,document,jobs,notices}=setup();context.rows('jobs',response(jobs.slice(0,1)));
  const list=document.getElementById('sharedJobsList'),row=list.children[0];
  document.getElementById('sharedJobsPartition').value='2';
  context.api=async()=>({...response(jobs.slice(0,1)),poolCount:2});await context.refresh('jobs');assert.equal(notices.length,0);
  state.capabilities={poolFiltering:true,poolIds:[1,2]};document.getElementById('sharedJobsPartition').value='3';
  await context.refresh('jobs');assert.match(notices.at(-1),/暂不支持/);assert.equal(list.children[0],row);
});

test('windowed 500-item pages mount bounded rows and keep full-page selection through the last item',async()=>{
  for(const kind of ['pool','jobs']){
    const {context,state,document,calls,jobs,flush}=setup({virtual:true});
    context.rows(kind,{...response(jobs),items:jobs});flush();
    const list=document.getElementById(kind==='pool'?'sharedPoolList':'sharedJobsList');
    assert.ok(state[kind].rowViews.size<30);assert.ok(list.children.length<32);
    const bar=context.selectBar(kind);await bar.children[0].dispatch('click');assert.equal(state[kind].selected.size,500);
    list.scrollTop=state[kind].virtualList.offsets[499];await list.dispatch('scroll');flush();
    assert.ok(state[kind].rowViews.size<30);assert.ok(state[kind].rowViews.has('1:500'));
    const checkbox=state[kind].rowViews.get('1:500').checkbox;assert.equal(checkbox.checked,true);
    checkbox.checked=false;await checkbox.dispatch('change');assert.equal(state[kind].selected.size,499);
    await bar.children[1].dispatch('click');assert.equal(state[kind].selected.size,0);
    assert.equal(calls.length,0);
  }
});

test('windowed rows retain a pending button across detaching and reattaching without double submission',async()=>{
  const {context,state,document,jobs,flush}=setup({virtual:true});context.rows('jobs',response(jobs));flush();
  let finish,requests=0;context.jobDetails=()=>{requests++;return new Promise(resolve=>{finish=resolve;});};
  const list=document.getElementById('sharedJobsList'),view=state.jobs.rowViews.get('1:1');
  const button=view.line.querySelectorAll('button')[0];const pending=button.dispatch('click');assert.equal(button.disabled,true);
  list.scrollTop=90000;await list.dispatch('scroll');flush();assert.equal(list.contains(button),false);
  finish();await pending;assert.equal(button.disabled,false);
  list.scrollTop=0;await list.dispatch('scroll');flush();
  assert.equal(state.jobs.rowViews.get('1:1'),view);assert.equal(requests,1);assert.equal(button.disabled,false);
});

test('windowed background updates preserve focus and scroll anchor while refreshing offscreen selection',async()=>{
  const {context,state,document,jobs,flush}=setup({virtual:true});context.rows('jobs',response(jobs));flush();
  const list=document.getElementById('sharedJobsList');list.scrollTop=6000;await list.dispatch('scroll');flush();
  const virtual=state.jobs.virtualList,anchor=virtual.anchor();
  const row=state.jobs.rowViews.get(anchor.key),checkbox=row.checkbox;checkbox.checked=true;await checkbox.dispatch('change');checkbox.focus();
  state.jobs.selected.set('1:500',jobs[499]);
  const updated=jobs.map(item=>({...item,message:'updated'}));
  const topView=[...state.jobs.rowViews.values()][0];topView.line._height=800;
  context.rows('jobs',response(updated));flush();
  assert.equal(document.activeElement,checkbox);assert.equal(checkbox.checked,true);
  assert.equal(virtual.anchor().key,anchor.key);assert.equal(virtual.anchor().delta,anchor.delta);
  assert.equal(state.jobs.selected.get('1:500').message,'updated');
  list.scrollTop=100000;await list.dispatch('scroll');flush();
  assert.ok(list.contains(checkbox));assert.ok(state.jobs.rowViews.size<35);
  const focusedIndex=virtual.index.get(anchor.key);assert.ok(state.jobs.rowViews.has(jobs[focusedIndex+1].key));
});

test('virtual mixed rows preserve order, page resets, focus removal and empty list handling',async()=>{
  const {context,state,document,jobs,flush}=setup({virtual:true});
  const transfer={type:'transfer',key:'transfer:mixed',status:'needs_review',payload:{sourceAccountId:1,targetAccountId:2,detailId:99}};
  context.rows('jobs',response([jobs[0],transfer,...jobs.slice(1)]));flush();
  assert.deepEqual([...state.jobs.rowViews.keys()].slice(0,3),['1:1','transfer:mixed','1:2']);
  const list=document.getElementById('sharedJobsList');state.jobs.rowViews.get('1:1').checkbox.focus();
  context.rows('jobs',response(jobs.slice(1)));flush();assert.equal(document.activeElement,list);
  list.scrollTop=10000;await list.dispatch('scroll');flush();
  context.rows('jobs',response(jobs.slice(0,50)),{reset:true});flush();
  assert.equal(list.scrollTop,0);assert.equal(state.jobs.rowViews.size,50);assert.equal(list.children.length,50);
  context.rows('jobs',response([]));flush();assert.equal(state.jobs.rowViews.size,0);assert.match(list.textContent,/暂无/);
});

test('new reads cancel the prior request and late responses cannot replace current results',async()=>{
  const {context,state,jobs}=setup();const pending=[];
  context.api=(route,body,options)=>new Promise(resolve=>pending.push({signal:options.signal,resolve}));
  const first=context.refresh('jobs'),second=context.refresh('jobs');assert.equal(pending[0].signal.aborted,true);
  pending[1].resolve(response([jobs[1]]));await second;
  pending[0].resolve(response([jobs[0]]));await first;
  assert.deepEqual([...state.jobs.rowViews.keys()],['1:2']);
});

test('search and filter changes coalesce and Chinese composition never submits unfinished input',async()=>{
  const {context,document}=setup();const timers=new Map();let next=0,reads=0;
  context.window.setTimeout=fn=>{timers.set(++next,fn);return next;};context.window.clearTimeout=id=>timers.delete(id);
  context.refresh=async()=>{reads++;};context.updateMoreFilters=()=>{};
  vm.runInContext(code('  function toolbar(', '  function selectBar('),context);
  context.toolbar('pool');
  const input=document.getElementById('sharedPoolKeyword');
  for(const value of ['冬','冬季','冬季商品']){input.value=value;await input.dispatch('input');}
  assert.equal(reads,0);assert.equal(timers.size,1);
  await input.dispatch('compositionstart');assert.equal(timers.size,0);
  await input.dispatch('input',{isComposing:true});await input.dispatch('keydown',{key:'Enter',isComposing:true});assert.equal(reads,0);
  await input.dispatch('compositionend');assert.equal(timers.size,1);
  const flush=async()=>{const ready=[...timers.values()];timers.clear();for(const fn of ready)await fn();};
  await flush();assert.equal(reads,1);
  const person=document.getElementById('sharedPoolPerson');person.value='1';await person.dispatch('change');person.value='2';await person.dispatch('change');
  assert.equal(timers.size,1);await flush();assert.equal(reads,2);
  input.value='另一个';await input.dispatch('input');await input.dispatch('keydown',{key:'Enter'});
  assert.equal(timers.size,0);assert.equal(reads,3);
});


test('successful queued recovery closes stale detail and reports where progress appears',async()=>{
  const {context,state,jobs,notices}=setup();let removed=0,refreshes=0;
  state.modal={overlay:{remove(){removed++;}}};state.jobs.selected.set(jobs[0].key,jobs[0]);
  context.api=async()=>({accepted:true,status:'queued'});context.refresh=async()=>{refreshes++;};
  await context.jobAction(jobs[0],'resume');
  assert.equal(removed,1);assert.equal(state.modal,null);assert.equal(refreshes,1);
  assert.equal(state.jobs.selected.size,0);assert.match(notices.at(-1),/已重新加入编辑队列/);
  assert.match(notices.at(-1),/处理中/);
});

test('failed recovery keeps detail and selection available for correction',async()=>{
  const {context,state,jobs}=setup();let removed=0;
  state.modal={overlay:{remove(){removed++;}}};state.jobs.selected.set(jobs[0].key,jobs[0]);
  context.api=async()=>{throw Error('账号不可用');};context.refresh=async()=>{throw Error('unexpected refresh');};
  await assert.rejects(context.jobAction(jobs[0],'resume'),/账号不可用/);
  assert.equal(removed,0);assert.ok(state.modal);assert.equal(state.jobs.selected.size,1);
});
