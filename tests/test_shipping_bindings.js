const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const {createDocument}=require('./dom_fixture');
const source=fs.readFileSync(require('node:path').join(__dirname,'..','shipping-bindings.js'),'utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function setup(){
  const document=createDocument(), requests=[], window={};
  for(const id of ['shippingBindingForm','shippingBindingStatus','saveShippingBinding','resetShippingBinding','refreshShippingBinding']) document.createNode(id);
  for(const id of ['shippingAccount','shippingShop','shippingSite','shippingFreight','shippingWarehouse'])document.createNode(id,'select');
  vm.runInNewContext(source,{document,window,URLSearchParams,AbortController,Error,fetch:(url,init)=>new Promise((resolve,reject)=>requests.push({url,init,resolve,reject}))});
  return {document,window,requests,node:id=>document.getElementById(id),reply(i,data,ok=true){requests[i].resolve({ok,json:async()=>ok?{data}:{error:'fixture failure'}});}};
}
const accounts={accounts:[{id:1,name:'A',configured:true},{id:2,name:'B',configured:true}]};
const account=id=>({...accounts,accountId:id,scope:'scope-'+id,shops:[{id:'7',name:'Shop'}],sites:[{id:'US',name:'US'}]});
const selected=id=>({...account(id),shopId:7,site:'US',freight:[{id:'f',name:'Freight'}],warehouses:[{id:'w',name:'Warehouse'}],binding:{freightId:'',warehouseId:'',revision:0}});
async function ready(){const s=setup();s.window.shippingBindings.open();s.reply(0,accounts);await settle();s.node('shippingAccount').value='2';const a=s.node('shippingAccount').dispatch('change');s.reply(1,account(2));await a;s.node('shippingShop').value='7';const b=s.node('shippingShop').dispatch('change');s.reply(2,selected(2));await b;return s;}
test('changing accounts clears old choices and ignores a late response',async()=>{
  const s=await ready();s.node('shippingAccount').value='1';const a=s.node('shippingAccount').dispatch('change');
  assert.equal(s.node('saveShippingBinding').disabled,true);assert.equal(s.node('shippingFreight').value,'');
  s.node('shippingAccount').value='2';const b=s.node('shippingAccount').dispatch('change');
  assert.equal(s.requests[3].init.signal.aborted,true);s.reply(4,account(2));await b;s.reply(3,account(1));await a;
  assert.equal(s.node('shippingAccount').value,'2');assert.equal(s.node('saveShippingBinding').disabled,true);
});
test('save carries exact target and revision, is single-flight and requires rereading after failure',async()=>{
  const s=await ready();s.node('shippingFreight').value='f';s.node('shippingWarehouse').value='w';
  await s.node('shippingFreight').dispatch('change');assert.equal(s.node('saveShippingBinding').disabled,false);
  await s.node('shippingBindingForm').dispatch('submit');await s.node('shippingBindingForm').dispatch('submit');
  assert.equal(s.requests.length,4);const body=JSON.parse(s.requests[3].init.body);
  assert.deepEqual(body,{accountId:2,shopId:7,site:'US',scope:'scope-2',revision:0,freightId:'f',warehouseId:'w',confirmation:'SAVE_SHIPPING_BINDING'});
  s.reply(3,{},false);await settle();assert.equal(s.node('saveShippingBinding').disabled,true);assert.match(s.node('shippingBindingStatus').textContent,/重新读取/);
});
test('successful save confirms binding and reset uses its returned revision',async()=>{
  const s=await ready();s.node('shippingFreight').value='f';s.node('shippingWarehouse').value='w';await s.node('shippingBindingForm').dispatch('submit');
  s.reply(3,{...selected(2),binding:{freightId:'f',warehouseId:'w',revision:1}});await settle();assert.equal(s.node('resetShippingBinding').disabled,false);
  const pending=s.node('resetShippingBinding').dispatch('click');const body=JSON.parse(s.requests[4].init.body);assert.equal(body.revision,1);assert.equal(body.confirmation,'RESET_SHIPPING_BINDING');
  s.reply(4,{...selected(2),binding:{freightId:'',warehouseId:'',revision:2}});await pending;assert.equal(s.node('resetShippingBinding').disabled,true);
});
test('leaving settings cancels reads and late replies cannot restore an old selection',async()=>{
  const s=setup();s.window.shippingBindings.open();s.window.shippingBindings.close();s.reply(0,accounts);await settle();
  assert.equal(s.requests[0].init.signal.aborted,true);assert.equal(s.node('saveShippingBinding').disabled,true);assert.equal(s.node('shippingAccount').options.length,0);
});
test('clearing the selected account clears dependent choices without an invalid request',async()=>{
  const s=await ready(), before=s.requests.length;s.node('shippingAccount').value='';await s.node('shippingAccount').dispatch('change');
  assert.equal(s.requests.length,before);assert.equal(s.node('saveShippingBinding').disabled,true);assert.match(s.node('shippingBindingStatus').textContent,/请选择要绑定的账号/);
});
