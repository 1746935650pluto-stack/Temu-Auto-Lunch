const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'..','shared-automation.js'),'utf8');
const code=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));

function setup() {
  const taskGroups=Object.fromEntries(['processing','attention','publish','history'].map(key=>[key,{label:key,hint:key}]));
  const nodes={}, pending=[], notices=[];
  for(const key of Object.keys(taskGroups)) {
    const value={textContent:'—'};
    nodes['sharedTab'+key]={value,querySelector:()=>value,setAttribute(){}};
  }
  for(const id of ['sharedTaskLoadState','sharedPersonHint','sharedJobsView','sharedJobsHint','sharedJobsTotal','taskBadge'])
    nodes[id]={textContent:'',hidden:false,classList:{toggle(){}}};
  const state={ready:true,kind:'jobs',revisions:{},capabilities:{taskSummary:true},
    jobs:{page:1,pageSize:50,group:'attention',selected:new Map([['selected',{key:'selected'}]])}};
  let query='page=1&pageSize=50&taskGroup=attention';
  const ctx=vm.createContext({state,taskGroups,Map,Set,URLSearchParams,Error,AbortController,
    $:id=>nodes[id],params:()=>new URLSearchParams(query),window:{setTimeout:()=>1,clearTimeout(){}},
    api:route=>new Promise((resolve,reject)=>pending.push({route,resolve,reject})),
    updatePersonOptions(){},updateShopOptions(){},updateTaskControls(){},renderQueueActivity(){},
    rows(){},notice:text=>notices.push(text)});
  vm.runInContext(code('  function updateTaskCounts(', '  function renderQueueActivity('),ctx);
  vm.runInContext(code('  async function refresh(', '  async function config('),ctx);
  const data=(n=7)=>({page:1,pageSize:50,total:n,totalAll:n,entries:[],personOptions:[],
    counts:{processing:0,attention:n,publish:3,history:2},transferCounts:{processing:0,attention:1,publish:0,history:0}});
  return {ctx,state,nodes,pending,notices,data,setQuery:value=>{query=value;}};
}

test('local counters appear while task detail request is still pending',async()=>{
  const {ctx,state,nodes,pending,data}=setup();
  const list=ctx.refresh('jobs'),summary=ctx.loadTaskSummary();
  assert.equal(pending[0].route.startsWith('jobs?'),true);assert.equal(pending[1].route,'summary');
  pending[1].resolve(data());await summary;
  assert.equal(nodes.sharedTabattention.value.textContent,8);
  assert.equal(state.jobs.loading,true);
  pending[0].resolve(data());await list;
  assert.equal(nodes.sharedTaskLoadState.hidden,true);
});

test('late summary cannot overwrite a newer list or a newly selected filter',async()=>{
  const {ctx,nodes,pending,data,setQuery}=setup();
  const old=ctx.loadTaskSummary();ctx.updateTaskSummary(data(20));
  pending[0].resolve(data(1));await old;assert.equal(nodes.sharedTabattention.value.textContent,21);
  const stale=ctx.loadTaskSummary();setQuery('accountId=2');pending[1].resolve(data(3));await stale;
  assert.equal(nodes.sharedTabattention.value.textContent,21);
  await ctx.loadTaskSummary();assert.equal(pending.length,2);
});

test('card rendering failures retain counters and selection and recover on the next refresh',async()=>{
  const {ctx,state,nodes,pending,data}=setup();
  ctx.rows=()=>{throw Error('card error');};
  const failed=ctx.refresh('jobs');pending[0].resolve(data());await failed;
  assert.equal(nodes.sharedTabattention.value.textContent,8);
  assert.match(nodes.sharedTaskLoadState.textContent,/读取失败.*10 秒后自动重试/);
  assert.equal(state.jobs.selected.size,1);assert.equal(state.jobs.loading,false);
  ctx.rows=()=>{};const retry=ctx.refresh('jobs',{background:true});pending[1].resolve(data(9));await retry;
  assert.equal(nodes.sharedTabattention.value.textContent,10);
  assert.equal(state.jobs.loadError,false);assert.equal(nodes.sharedTaskLoadState.hidden,true);
});

test('failed counter read preserves the last successful counts',async()=>{
  const {ctx,nodes,pending,data}=setup();ctx.updateTaskSummary(data());
  const summary=ctx.loadTaskSummary();pending[0].reject(Error('offline'));await summary;
  assert.equal(nodes.sharedTabattention.value.textContent,8);
});

test('optional person names cannot block local cards or clear the selected filter',async()=>{
  const {ctx,state,nodes,pending,data}=setup();let rendered=0;
  ctx.updatePersonOptions=()=>{throw Error('invalid owner labels');};ctx.rows=()=>rendered++;
  const refresh=ctx.refresh('jobs');pending[0].resolve(data());await refresh;
  assert.equal(rendered,1);assert.equal(nodes.sharedTabattention.value.textContent,8);
  assert.equal(state.jobs.selected.size,1);assert.equal(state.jobs.loadError,false);
  assert.match(nodes.sharedPersonHint.textContent,/保留当前筛选/);
});
