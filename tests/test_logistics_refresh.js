const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '..', 'shared-automation.js'), 'utf8');
function setup(api) {
  const state = {config:{mode:'live'}}, actions=[];
  const element = (tag,text='') => ({tag,textContent:text,children:[],value:'',events:{},
    append(...items){this.children.push(...items);},replaceChildren(){this.children=[];this.value='';},
    addEventListener(name,fn){this.events[name]=fn;}});
  const dialog = {content:element('div')};state.modal=dialog;
  const ctx=vm.createContext({state,api,element,Intl,Date,
    select:()=>element('select'),option:(s,value,text)=>s.append({value,text}),
    button:(text,action)=>Object.assign(element('button',text),{action}),
    jobAction:async(...args)=>actions.push(args)});
  vm.runInContext(source.slice(source.indexOf('  function taskEventTime('),source.indexOf('  async function jobDetails(')),ctx);
  return {ctx,state,dialog,actions,row:{accountId:2,jobId:'abc',available:true},message:element('p'),
    job:{step:'freight',choices:{freight:{options:[{id:'old',name:'旧模板'}]}}}};
}
const renamed = {step:'freight',message:'已同步',choices:{freight:{
  options:[{id:'1',name:'普通'},{id:'2',name:'通用'}],refreshedAt:'2026-10-20T10:00:00+00:00'}}};

test('opening paused logistics automatically syncs target account, selects the unique default and shows Beijing time', async()=>{
  const calls=[],s=setup(async(...args)=>{calls.push(args);return renamed;});
  await s.ctx.jobLogistics(s.dialog,s.row,s.job,s.message);
  const [select,refresh,status,resume]=s.dialog.content.children;
  assert.equal(calls[0][0],'action');assert.equal(calls[0][1].accountId,2);
  assert.equal(calls[0][1].action,'refresh-logistics');
  assert.equal(select.value,'2');assert.equal(resume.disabled,false);
  assert.match(status.textContent,/北京时间 2026-10-20 18:00:00/);
  assert.equal(s.actions.length,0);
  await resume.action();assert.equal(s.actions[0][2].selectedId,'2');
  await refresh.action();assert.equal(calls.length,2);
});

test('auto-queued store tasks report their queue count and no longer offer a second resume', async()=>{
  const queued={...renamed,status:'queued',message:'已重新加入编辑队列',autoQueuedJobs:6};
  const s=setup(async()=>queued);
  await s.ctx.jobLogistics(s.dialog,s.row,s.job,s.message);
  const [select,refresh,status,resume]=s.dialog.content.children;
  assert.match(status.textContent,/6 条同店铺任务重新加入编辑队列/);
  assert.equal(resume.disabled,true);assert.equal(refresh.disabled,true);assert.equal(select.disabled,false);
});

test('failed refresh leaves old names visible but cannot resume until a successful retry', async()=>{
  let fail=true;const s=setup(async()=>{if(fail)throw Error('网络失败');return renamed;});
  await s.ctx.jobLogistics(s.dialog,s.row,s.job,s.message);
  const [select,refresh,status,resume]=s.dialog.content.children;
  assert.match(status.textContent,/同步失败/);assert.equal(resume.disabled,true);
  select.value='old';select.events.change();await resume.action();assert.equal(s.actions.length,0);
  fail=false;await refresh.action();assert.equal(select.value,'2');assert.equal(resume.disabled,false);
});

test('an empty refreshed list cannot submit an obsolete template', async()=>{
  const s=setup(async()=>({...renamed,choices:{freight:{options:[],refreshedAt:'2026-10-20T10:00:00Z'}}}));
  await s.ctx.jobLogistics(s.dialog,s.row,s.job,s.message);
  const [select,,,resume]=s.dialog.content.children;
  assert.equal(select.disabled,true);assert.equal(resume.disabled,true);
  await resume.action();assert.equal(s.actions.length,0);
});
