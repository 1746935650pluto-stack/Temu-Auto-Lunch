const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function setup(concurrency, priceMode='floor', priceMultiplier='1.5') {
  const fields = {
    '#automationClaimShop':{value:'123',selectedOptions:[{value:'123',textContent:'店铺'}, {value:'456',textContent:'店铺二'}]},
    '#automationPublishSite':{value:'US'}, '#automationSkuPrice':{value:'160.56'},
    '#automationPriceMode':{value:priceMode}, '#automationPriceMultiplier':{value:priceMultiplier},
    '#automationEditConcurrency':{value:String(concurrency)},
  };
  const requests=[], messages=[];
  const state = {submitting:false, selected:new Set(Array.from({length:50},(_,i)=>i+1)),jobs:[]};
  const ctx = vm.createContext({automationState:state,liveState:{mode:'live'},AUTOMATION_POOL_PAGE_SIZE:50,
    $:id=>fields[id],updateAutomationControls(){},automationClaimFeedback:(message)=>messages.push(message),
    renderAutomationJobs(){},loadAutomationJobs:async()=>{},
    fetch:async(url, options)=>{requests.push({url,body:JSON.parse(options.body)});
      return {ok:true,status:202,json:async()=>({data:{jobs:[],editConcurrency:concurrency}})};}});
  vm.runInContext(source.slice(source.indexOf('function selectedAutomationShopIds'), source.indexOf('function automationItemClaimStatus')),ctx);
  vm.runInContext(source.slice(source.indexOf('async function claimAutomationSelection'), source.indexOf('function appendOption')),ctx);
  return {ctx,requests,messages};
}

test('200 editing workers can be submitted while retaining 50-item pages and manual publishing',async()=>{
  const {ctx,requests}=setup(200);
  await ctx.claimAutomationSelection();
  assert.equal(requests.length,1);
  assert.equal(requests[0].url,'/api/automation/batch-run');
  assert.equal(requests[0].body.editConcurrency,200);
  assert.equal(requests[0].body.priceMode,'floor');
  assert.equal(requests[0].body.detailIds.length,50);
  assert.deepEqual(requests[0].body.shopIds,[123,456]);
  assert.equal(requests[0].body.confirmation,'AUTOMATE_AND_SAVE_BATCH');
  const html=fs.readFileSync(path.join(__dirname,'..','index.html'),'utf8');
  const input=html.match(/<input[^>]+id="automationEditConcurrency"[^>]*>/)[0];
  assert.ok(!input.includes('max='));
  assert.ok(input.includes('min="1"'));
});

test('source price multiplier is submitted with the batch request',async()=>{
  const {ctx,requests}=setup(8,'source_multiplier','1.5');
  await ctx.claimAutomationSelection();
  assert.equal(requests.length,1);
  assert.equal(requests[0].body.priceMode,'source_multiplier');
  assert.equal(requests[0].body.priceMultiplier,'1.5');
});

test('invalid concurrency is rejected before any task request',async()=>{
  for(const value of [0,-1,2.5,Infinity,Number.MAX_SAFE_INTEGER+1]) {
    const {ctx,requests,messages}=setup(value);
    await ctx.claimAutomationSelection();
    assert.equal(requests.length,0);
    assert.ok(messages[0].includes('大于 0 的整数'));
  }
});
