const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const {createDocument}=require('./dom_fixture');
const source=fs.readFileSync(path.join(__dirname,'..','diagnostics-panel.js'),'utf8');
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
function fixture({selectedAccount,configured=[]}={}){
  const document=createDocument(),listeners=new Map(),timers=new Map(),requests=[];
  document.addEventListener=(key,fn)=>listeners.set(key,fn);
  document.removeEventListener=(key)=>listeners.delete(key);
  document.hidden=false;
  const parent=document.createElement('section');let active=true;
  parent.closest=()=>active ? parent : null;
  let nextTimer=0,reply=async()=>({ok:true,json:async()=>({windowSeconds:300,sampledAt:'2026-10-21T10:00:00Z',accounts:[],limits:{},global:{}})});
  const window={setTimeout:(fn,delay)=>{timers.set(++nextTimer,{fn,delay});return nextTimer;},clearTimeout:id=>timers.delete(id),confirm:()=>true};
  window.location={search:'?collectionAccount='+(selectedAccount || 1),origin:'http://127.0.0.1:8787',href:'http://127.0.0.1:8787/?collectionAccount='+(selectedAccount || 1)};
  window.fetch=async(url,options)=>{requests.push({url,options});return reply(url,options);};
  const context=vm.createContext({document,window,AbortController,Date,Error,URL,URLSearchParams,Headers,fetch:(...args)=>window.fetch(...args)});
  if(selectedAccount)vm.runInContext(fs.readFileSync(path.join(__dirname,'..','account-context.js'),'utf8'),context);
  vm.runInContext(source,context);
  const mounted=window.WorkbenchDiagnostics.mount(parent,id=>'账号 '+id,()=>configured);
  const buttons=mounted.panel.querySelectorAll('button'),select=mounted.panel.querySelectorAll('select')[0];
  return {document,parent,mounted,buttons,select,timers,requests,listeners,window,setActive:value=>{active=value;},setReply:value=>{reply=value;}};
}
test('performance polling is on demand, resumes after tab switch and disposes listeners',async()=>{
  const f=fixture();assert.equal(f.requests.length,0);
  f.mounted.panel.open=true;await f.mounted.panel.dispatch('toggle');await settle();assert.equal(f.requests.length,1);
  assert.match(f.parent.textContent,/SQLite 门锁等待/);
  const tick=()=>{const [id,timer]=[...f.timers.entries()].find(([,timer])=>timer.delay===5000);f.timers.delete(id);timer.fn();};
  f.setActive(false);tick();await settle();assert.equal(f.requests.length,1);
  f.setActive(true);tick();await settle();assert.equal(f.requests.length,2);
  f.mounted.dispose();assert.equal(f.timers.size,0);assert.equal(f.listeners.size,0);
});
test('archive requires preview and explicit confirmation, submits token once and reports partial errors',async()=>{
  const f=fixture(),preview=f.buttons[1],apply=f.buttons[2];
  assert.equal(f.select.value,'7');assert.equal(apply.disabled,true);assert.equal(f.select.options.some(o=>o.value==='30'),true);
  f.setReply(async()=>({ok:true,json:async()=>({data:{events:17,jobs:2,token:'approved'}})}));
  await preview.dispatch('click');assert.equal(apply.disabled,false);assert.match(f.parent.textContent,/17 条成功日志/);
  f.window.confirm=()=>false;await apply.dispatch('click');assert.equal(f.requests.length,1);
  let finish;f.window.confirm=()=>true;f.setReply(()=>new Promise(resolve=>{finish=resolve;}));
  const pending=apply.dispatch('click');await apply.dispatch('click');assert.equal(f.requests.length,2);
  const body=JSON.parse(f.requests[1].options.body);assert.equal(body.previewToken,'approved');assert.equal(body.retentionDays,7);
  finish({ok:false,json:async()=>({error:'范围已变',data:{deletedEvents:5}})});await pending;
  assert.equal(apply.disabled,true);assert.match(f.parent.textContent,/已移出热库 5 条/);
});
test('changing retention invalidates archive approval and metric null latency is not zero',async()=>{
  const f=fixture();f.setReply(async url=>({ok:true,json:async()=>url.includes('/api/performance')?
    {windowSeconds:300,sampledAt:'2026-10-21T10:00:00Z',accounts:[{accountId:2,active:0,limit:2,requestCount:0,latencyP95Ms:null,rateLimitedCount:0,rateLimitedRecentCount:0,timeoutCount:0,sqliteWaitCount:0,sqliteWaitMs:0}]}:
    {data:{events:12,jobs:1,token:'old'}}}));
  f.mounted.panel.open=true;await f.mounted.panel.dispatch('toggle');await settle();assert.match(f.parent.textContent,/暂无样本/);
  assert.match(f.parent.textContent,/限流标志（近 1 分钟）/);assert.match(f.parent.textContent,/正常/);
  await f.buttons[1].dispatch('click');assert.equal(f.buttons[2].disabled,false);
  f.select.value='180';await f.select.dispatch('change');assert.equal(f.buttons[2].disabled,true);
  const before=f.requests.length;await f.buttons[2].dispatch('click');assert.equal(f.requests.length,before);
});
test('performance rate flag only marks actual rate limits, not timeouts',async()=>{
  const f=fixture();f.setReply(async()=>({ok:true,json:async()=>({windowSeconds:300,sampledAt:'2026-10-21T10:00:00Z',accounts:[
    {accountId:2,active:0,limit:2,requestCount:3,latencyP95Ms:50,rateLimitedCount:0,rateLimitedRecentCount:0,timeoutCount:2,sqliteWaitCount:0,sqliteWaitMs:0},
    {accountId:3,active:0,limit:2,requestCount:4,latencyP95Ms:60,rateLimitedCount:2,rateLimitedRecentCount:2,timeoutCount:0,sqliteWaitCount:0,sqliteWaitMs:0}
  ],limits:{},global:{}})}));
  f.mounted.panel.open=true;await f.mounted.panel.dispatch('toggle');await settle();
  assert.match(f.parent.textContent,/正常/);assert.match(f.parent.textContent,/已限流（2 次）/);
});
test('configured accounts remain visible when a metrics response is incomplete',async()=>{
  const f=fixture();f.setReply(async()=>({ok:true,json:async()=>({windowSeconds:300,sampledAt:'2026-10-21T10:00:00Z',accounts:[
    {accountId:1,active:1,limit:6,requestCount:4,latencyP95Ms:80,rateLimitedCount:0,rateLimitedRecentCount:0,timeoutCount:0,sqliteWaitCount:0,sqliteWaitMs:0}
  ],limits:{},global:{}})}));
  f.mounted.dispose();
  const mounted=f.window.WorkbenchDiagnostics.mount(f.parent,id=>'账号 '+id,()=>[{id:1,name:'NL'},{id:2,name:'Bao'},{id:6,name:'TTT'}]);
  mounted.panel.open=true;await mounted.panel.dispatch('toggle');await settle();
  assert.match(f.parent.textContent,/账号 1/);assert.match(f.parent.textContent,/账号 2/);assert.match(f.parent.textContent,/账号 6/);
  assert.match(f.parent.textContent,/未返回/);assert.match(f.parent.textContent,/未获取/);
  for (const row of mounted.panel.querySelectorAll('tbody')[0].children.slice(1)) {
    assert.equal(row.children[1].textContent,'未返回');
    assert.equal(row.children[3].textContent,'—');
    assert.equal(row.children[8].textContent,'未获取');
  }
  mounted.dispose();
});

test('selected Bao context does not hide NL requests or account seven publication metrics',async()=>{
  const f=fixture({selectedAccount:2,configured:[{id:1},{id:2},{id:7}]});
  const metric=(id,requests,publish=0)=>({accountId:id,active:0,limit:6,queued:0,requestCount:requests,
    latencyP95Ms:requests?600:null,rateLimitedCount:0,rateLimitedRecentCount:0,timeoutCount:0,sqliteWaitCount:0,sqliteWaitMs:0,
    editPerMinute:0,publishPerMinute:publish});
  f.setReply(async(_url,options)=>({ok:true,json:async()=>({windowSeconds:300,sampledAt:'2026-10-28T06:42:50Z',
    accounts:options?.headers?.get('X-Miaoshou-Account-Id')==='2' ? [metric(2,0)] : [metric(1,12),metric(2,0),metric(7,5,1)],
    global:{active:1},limits:{globalCap:24,perAccountCap:6}})}));
  f.mounted.panel.open=true;await f.mounted.panel.dispatch('toggle');await settle();
  assert.equal(f.requests[0].options.headers.has('X-Miaoshou-Account-Id'),false);
  const rows=new Map(f.mounted.panel.querySelectorAll('tbody')[0].children.map(row=>[row.children[0].textContent,row]));
  assert.equal(rows.get('账号 1').children[3].textContent,'12');
  assert.equal(rows.get('账号 7').children[7].textContent,'0.0 / 1.0');
  assert.equal(rows.get('账号 2').children[1].textContent,'0 / 6');
  assert.doesNotMatch(f.parent.textContent,/未返回/);
  f.mounted.dispose();
});


test('rate flag clears old limits while retaining five-minute counters',async()=>{
  const f=fixture();f.setReply(async()=>({ok:true,json:async()=>({windowSeconds:300,rateLimitWindowSeconds:60,
    sampledAt:'2026-10-29T09:00:00Z',accounts:[{accountId:1,active:0,limit:6,requestCount:3,
      rateLimitedCount:2,rateLimitedRecentCount:0,timeoutCount:1,sqliteWaitCount:0,sqliteWaitMs:0}],limits:{},global:{}})}));
  f.mounted.panel.open=true;await f.mounted.panel.dispatch('toggle');await settle();
  const row=f.mounted.panel.querySelectorAll('tbody')[0].children[0];
  assert.equal(row.children[5].textContent,'2 / 1');
  assert.equal(row.children[8].textContent,'正常');
  assert.match(f.parent.textContent,/近 1 分钟/);f.mounted.dispose();
});

test('old backend without one-minute count is not labeled normal',async()=>{
  const f=fixture();f.setReply(async()=>({ok:true,json:async()=>({windowSeconds:300,
    sampledAt:'2026-10-29T09:00:00Z',accounts:[{accountId:1,rateLimitedCount:0}],limits:{},global:{}})}));
  f.mounted.panel.open=true;await f.mounted.panel.dispatch('toggle');await settle();
  const row=f.mounted.panel.querySelectorAll('tbody')[0].children[0];
  assert.equal(row.children[8].textContent,'未获取');f.mounted.dispose();
});
