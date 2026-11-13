const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {createDocument} = require('./dom_fixture');
const source = fs.readFileSync(path.join(__dirname, '..', 'shared-automation.js'), 'utf8');

function setup() {
  const document = createDocument();
  const state = {config:{targets:[
    {accountId:1,name:'A',shops:Array.from({length:20},(_,i)=>({shopId:i+1,shopNick:`Shop ${i+1}`}))},
    {accountId:2,name:'B',shops:[{shopId:1,shopNick:'Second'}, {shopId:30,shopNick:'Other'}]},
  ]}};
  const context = vm.createContext({document,state});
  const block = (start,end) => source.slice(source.indexOf(start),source.indexOf(end));
  vm.runInContext(block('  function element(', '  function button('),context);
  vm.runInContext(block('  function input(', '  function field('),context);
  vm.runInContext(block('  function targetOptions(', '  function modal('),context);
  const select = document.createNode('sharedTargets','select'); select.multiple=true;
  const view = context.targetPicker(select); context.targetOptions(select); select.picker.refresh();
  return {context,document,state,select,view,tabs:view.children[2],search:view.children[3].children[0],
          clear:view.children[3].children[1],grid:view.children[4],footer:view.children[5]};
}

test('switches accounts and pages without losing selected account-qualified shops', async()=>{
  const s=setup(); assert.equal(s.grid.children.length,16);
  s.grid.children[0].children[0].checked=true; await s.grid.children[0].children[0].dispatch('change');
  assert.equal(s.document.activeElement,s.grid.children[0].children[0]);
  await s.tabs.children[1].dispatch('click'); assert.equal(s.grid.children.length,2);
  assert.equal(s.document.activeElement,s.tabs.children[1]);
  s.grid.children[0].children[0].checked=true; await s.grid.children[0].children[0].dispatch('change');
  assert.deepEqual(JSON.parse(JSON.stringify(s.context.chosenTargets(s.select))),[
    {targetAccountId:1,shopId:1},{targetAccountId:2,shopId:1}]);
  assert.match(s.view.children[1].textContent,/已选 2 家/);
  await s.tabs.children[0].dispatch('click'); await s.footer.children[1].children[1].dispatch('click');
  assert.equal(s.grid.children.length,4);assert.match(s.footer.textContent,/第 2 \/ 2 页/);
  s.grid.children[0].children[0].checked=true;await s.grid.children[0].children[0].dispatch('change');
  assert.equal(s.select.selectedOptions.length,3);
  s.search.value='Shop 1';await s.search.dispatch('input');
  assert.equal(s.grid.children.length,11);assert.match(s.footer.textContent,/第 1 \/ 1 页/);
  s.context.targetOptions(s.select,[...s.select.selectedOptions].map(o=>o.value));s.select.picker.refresh();
  assert.equal(s.select.selectedOptions.length,3);assert.equal(s.search.value,'Shop 1');
  await s.clear.dispatch('click');assert.equal(s.select.selectedOptions.length,0);
});

test('personnel mode disables choices without changing saved selection',async()=>{
  const s=setup();s.grid.children[0].children[0].checked=true;await s.grid.children[0].children[0].dispatch('change');
  s.select.disabled=true;s.select.picker.refresh();
  assert.equal(s.tabs.children[0].disabled,true);assert.equal(s.search.disabled,true);
  assert.equal(s.grid.children[0].children[0].disabled,true);assert.equal(s.select.selectedOptions.length,1);
  s.select.disabled=false;s.select.picker.refresh();assert.equal(s.grid.children[0].children[0].checked,true);
});
