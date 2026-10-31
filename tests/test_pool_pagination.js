const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function setup(count) {
  const element = () => ({children:[], value:'1', get selectedOptions(){return this.selections || [{value:this.value}];}, append(...nodes){this.children.push(...nodes);},
    replaceChildren(){this.children=[];}, setAttribute(){}, addEventListener(type, handler){this[type]=handler;}});
  const nodes = new Map();
  const $ = id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
  const state = {items:Array.from({length:count},(_,i)=>({detailId:i+1,title:`商品 ${i+1}`})),
    poolPage:1,selected:new Set(),jobs:[],submitting:false};
  const messages=[];
  const ctx=vm.createContext({automationState:state,liveState:{mode:'live'},$,document:{createElement:element},
    automationClaimFeedback(){},showToast:message=>messages.push(message),removeAutomationPoolItem(){}});
  vm.runInContext(source.slice(source.indexOf('const AUTOMATION_POOL_PAGE_SIZE'), source.indexOf('async function loadAutomationPool')),ctx);
  return {ctx,state,$,messages};
}

test('68 products render as 50 and 18 with page-only selection and total retained',()=>{
  const {ctx,state,$}=setup(68);
  ctx.renderAutomationPool();
  assert.equal($('#automationPoolList').children.length,50);
  assert.equal($('#automationPoolCount').textContent,'68');
  ctx.selectAutomationPoolPage(true);
  assert.equal(state.selected.size,50);
  ctx.changeAutomationPoolPage(2);
  assert.equal(state.selected.size,0);
  assert.equal($('#automationPoolList').children.length,18);
  assert.ok($('#automationPoolPageLabel').textContent.includes('第 2 / 2 页'));
  assert.equal($('#automationPoolNext').disabled,true);
  ctx.selectAutomationPoolPage(true);
  assert.deepEqual([...state.selected],Array.from({length:18},(_,i)=>i+51));
  ctx.changeAutomationPoolPage(1);
  assert.equal(state.selected.size,0);
});

test('all-selection skips locked tasks and removes invisible stale selections',()=>{
  const {ctx,state,$}=setup(68);
  state.selected=new Set(Array.from({length:68},(_,i)=>i+1));
  state.jobs=[{detailId:2,status:'running'},{detailId:3,status:'ready_publish'},{detailId:4,status:'needs_input'}];
  ctx.renderAutomationPool();
  assert.equal(state.selected.size,47);
  assert.equal(state.selected.has(2),false);
  assert.equal(state.selected.has(51),false);
  assert.equal($('#automationSelectAll').checked,true);
  ctx.selectAutomationPoolPage(false);
  assert.equal(state.selected.size,0);
  assert.equal($('#automationSelectAll').checked,false);
});

test('empty or shrinking pool clamps page and invalid jumps do not change selection',()=>{
  const {ctx,state,$,messages}=setup(68);
  ctx.changeAutomationPoolPage(2);
  ctx.selectAutomationPoolPage(true);
  for (const bad of [0,3,1.5,NaN])ctx.changeAutomationPoolPage(bad);
  assert.equal(messages.length,4);
  assert.equal(state.poolPage,2);
  assert.equal(state.selected.size,18);
  state.items=state.items.slice(0,20);
  ctx.renderAutomationPool();
  assert.equal(state.poolPage,1);
  assert.equal(state.selected.size,0);
  state.items=[];
  ctx.renderAutomationPool();
  assert.equal($('#automationPoolPageGo').disabled,true);
  assert.equal($('#automationPoolPrev').disabled,true);
  assert.equal($('#automationPoolNext').disabled,true);
  assert.equal($('#automationPoolCount').textContent,'0');
});

test('submitting blocks paging and select-all changes',()=>{
  const {ctx,state,$}=setup(68);
  state.submitting=true;
  ctx.renderAutomationPool();
  ctx.changeAutomationPoolPage(2);
  ctx.selectAutomationPoolPage(true);
  assert.equal(state.poolPage,1);
  assert.equal(state.selected.size,0);
  assert.equal($('#automationPoolNext').disabled,true);
  assert.equal($('#automationSelectAll').disabled,true);
});

test('claim filters use the selected target shop and reset page selection',()=>{
  const {ctx,state,$}=setup(3);
  $('#automationClaimShop').value='88';
  state.items[0].claims=[{shopId:88,status:'claimed'}];
  state.items[1].claims=[{shopId:99,status:'claimed'}];
  state.selected=new Set([1,2,3]);
  ctx.setAutomationPoolClaimFilter('claimed');
  assert.equal($('#automationPoolList').children.length,1);
  assert.equal(state.selected.size,0);
  assert.equal($('#automationClaimedCount').textContent,'1');
  assert.equal($('#automationClaimPendingCount').textContent,'2');
  ctx.setAutomationPoolClaimFilter('pending');
  assert.equal($('#automationPoolList').children.length,2);
});

test('multi-shop claims require all selected stores and show the total task count',()=>{
  const {ctx,state,$}=setup(3);
  $('#automationClaimShop').selections=[{value:'88'},{value:'99'}];
  state.items[0].claims=[{shopId:88,status:'claimed'}];
  state.items[1].claims=[{shopId:88,status:'claimed'},{shopId:99,status:'claimed'}];
  ctx.selectAutomationPoolPage(true);
  assert.ok($('#automationSelectedCount').textContent.includes('共 6 个任务'));
  ctx.setAutomationPoolClaimFilter('claimed');
  assert.equal($('#automationPoolList').children.length,1);
  assert.equal($('#automationClaimedCount').textContent,'1');
  assert.equal($('#automationClaimPendingCount').textContent,'2');
});

test('selecting one pool product keeps the rendered page intact',()=>{
  const {ctx,state,$}=setup(68);
  ctx.renderAutomationPool();
  const list=$('#automationPoolList');
  const renderedRows=list.children;
  const firstCheckbox=renderedRows[0].children[0];
  firstCheckbox.checked=true;
  firstCheckbox.change();
  assert.strictEqual($('#automationPoolList').children,renderedRows);
  assert.deepEqual([...state.selected],[1]);
  assert.match($('#automationSelectedCount').textContent,/已选 1 件/);
});
