const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function setup(jobs, fetch) {
  const state = {jobs, resumingJobIds:new Set(), retryingJobIds:new Set(), batchRetrying:false};
  const messages = [];
  const ctx = vm.createContext({automationState:state, fetch, setAutomationSaveFilter:()=>{},
    renderAutomationJobs:()=>{}, loadAutomationJobs:async()=>{}, automationJobFeedback:(...args)=>messages.push(args)});
  vm.runInContext(source.slice(source.indexOf('function automationJobSaved'), source.indexOf('function visibleAutomationJobs')), ctx);
  vm.runInContext(source.slice(source.indexOf('function batchRetryCandidates'), source.indexOf('function updateAutomationJobBatchControls')), ctx);
  vm.runInContext(source.slice(source.indexOf('async function retryAutomationJobsBatch'), source.indexOf('async function publishReadyAutomationJobs')), ctx);
  return {ctx, state, messages};
}
const job = (id, status, extra={}) => ({jobId:String(id), detailId:id, shopId:10, status, ...extra});

test('batch retries only unsaved eligible records and deduplicates each product/shop', () => {
  const jobs = [job(1,'needs_input'),job(2,'failed'),job(3,'needs_review'),job(4,'interrupted'),
    job(5,'running'),job(6,'queued'),job(7,'ready_publish'),job(8,'done'),job(9,'canceled'),
    job(10,'needs_input',{step:'warehouse', choices:{warehouse:{options:[{id:'x'}]}}}),
    job(11,'needs_review',{step:'publish', events:[{step:'save',status:'done'}]}),
    job(12,'failed',{detailId:2, updatedAt:'2026-10-05'}),job(13,'failed',{detailId:5})];
  const {ctx} = setup(jobs, ()=>{});
  const ids = Array.from(ctx.batchRetryCandidates(), j=>j.jobId);
  assert.deepEqual(ids.sort(), ['1','12','3','4']);
});

test('mixed routes run independently, one failure does not abort the rest, no publish calls', async () => {
  const calls=[];
  const {ctx,state,messages} = setup([job(1,'needs_input'),job(2,'failed'),job(3,'interrupted')], async (url, options) => {
    const body=JSON.parse(options.body); calls.push({url,body});
    if(body.jobId==='2') throw new Error('网络异常');
    return {ok:true,json:async()=>({data:{status:'running'}})};
  });
  const running = ctx.retryAutomationJobsBatch();
  await ctx.retryAutomationJobsBatch();
  await running;
  assert.equal(calls.length,3);
  assert.equal(calls.find(c=>c.body.jobId==='1').url,'/api/automation/resume');
  assert.equal(calls.find(c=>c.body.jobId==='3').body.confirmation,'RETRY_AUTOMATION_SAVE');
  assert.ok(calls.every(c=>!c.url.includes('publish')));
  assert.equal(state.batchRetrying,false);
  assert.equal(state.retryingJobIds.size+state.resumingJobIds.size,0);
  assert.ok(messages.at(-1)[0].includes('2/3'));
  assert.ok(messages.at(-1)[0].includes('ID 2：网络异常'));
});

test('submission queue continues past four items and never exceeds four in-flight requests', async () => {
  const pending=[]; let active=0, maximum=0, total=0;
  const {ctx}=setup(Array.from({length:9},(_,i)=>job(i,'failed')), () => {
    active++; total++; maximum=Math.max(maximum,active);
    return new Promise(resolve=>pending.push(()=>{active--;resolve({ok:true,json:async()=>({data:{status:'running'}})});}));
  });
  const run=ctx.retryAutomationJobsBatch();
  while(total<9 || active) {
    while(pending.length) pending.shift()();
    await new Promise(resolve=>setImmediate(resolve));
  }
  await run;
  assert.equal(total,9); assert.equal(maximum,4);
});
