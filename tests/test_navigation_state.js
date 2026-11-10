const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'..','navigation-state.js'),'utf8');
function setup(url='http://127.0.0.1:8787/',loading=false){
  const events={},documentEvents={},stack=[{url,state:null}],visited=[];let index=0,active={page:'workbench',orderAccountId:1};
  const window={location:{href:url},addEventListener:(name,fn)=>events[name]=fn};
  window.history={get state(){return stack[index].state;},replaceState(state,title,next){stack[index]={url:next,state};window.location.href=next;},
    pushState(state,title,next){stack.splice(index+1);stack.push({url:next,state});index++;window.location.href=next;},
    back(){if(index){index--;window.location.href=stack[index].url;events.popstate?.({state:stack[index].state});}}};
  const document={readyState:loading?'loading':'complete',addEventListener:(name,fn)=>documentEvents[name]=fn};
  vm.runInNewContext(source,{window,document,URL});const nav=window.workbenchNavigation;
  nav.configure({navigate(route){active={...route};visited.push({...route});nav.record(route.page,route.orderAccountId);},current:()=>active});
  return{window,nav,stack,visited,dom:()=>documentEvents.DOMContentLoaded(),go(page,id=active.orderAccountId){active={page,orderAccountId:id};nav.record(page,id);},get active(){return active;}};
}
test('refresh restores module and order account after DOM and account initialization',()=>{
  const s=setup('http://127.0.0.1:8787/?page=orders&orderAccount=4&collectionAccount=2',true);
  s.nav.record('workbench',1);s.nav.ready();assert.equal(s.visited.length,0);s.dom();
  assert.equal(s.active.page,'orders');assert.equal(s.active.orderAccountId,4);assert.equal(s.stack.length,1);
  assert.equal(new URL(s.window.location.href).searchParams.get('collectionAccount'),'2');
  const reload=setup(s.window.location.href);reload.nav.ready();assert.equal(reload.active.page,'orders');assert.equal(reload.active.orderAccountId,4);
});
test('browser back returns to preceding modules and account selections without new entries',()=>{
  const s=setup();s.nav.ready();s.go('dataStatistics');s.go('orders',2);s.go('orders',4);s.go('tasks');
  assert.equal(s.stack.length,5);s.window.history.back();assert.equal(s.active.page,'orders');assert.equal(s.active.orderAccountId,4);
  s.window.history.back();assert.equal(s.active.orderAccountId,2);s.window.history.back();assert.equal(s.active.page,'dataStatistics');
  assert.equal(s.stack.length,5);
});
test('repeated page refreshes do not create duplicate back entries',()=>{
  const s=setup();s.nav.ready();s.go('tasks');s.go('tasks');s.go('tasks');assert.equal(s.stack.length,2);
  s.window.history.back();assert.equal(s.active.page,'workbench');
});
test('user navigation while account metadata loads takes priority over the initial route',()=>{
  const s=setup('http://127.0.0.1:8787/?page=orders&orderAccount=2',true);s.go('dataStatistics');s.nav.ready();s.dom();
  assert.equal(s.active.page,'dataStatistics');
});
test('unknown routes and out-of-range order accounts cannot select arbitrary destinations',()=>{
  const s=setup('http://127.0.0.1:8787/?page=https://example.test&orderAccount=99');s.nav.ready();assert.equal(s.active.page,'workbench');
  const orders=setup('http://127.0.0.1:8787/?page=orders&orderAccount=99');orders.nav.ready();assert.equal(orders.active.orderAccountId,1);
});
test('leaving the editor removes obsolete task-editor parameters and navigation order matches request',()=>{
  const s=setup('http://127.0.0.1:8787/?sharedEditJob=old');s.nav.ready();s.go('orders',4);
  assert.equal(new URL(s.window.location.href).searchParams.has('sharedEditJob'),false);
  const html=fs.readFileSync(path.join(__dirname,'..','index.html'),'utf8');
  assert.ok(html.indexOf('data-page="tasks"')<html.indexOf('data-page="dataStatistics"'));
  assert.ok(html.indexOf('data-page="dataStatistics"')<html.indexOf('data-page="publishRecords"'));
  assert.match(html,/<span>⌘<\/span>采集箱<\/button>/);
  assert.ok(html.indexOf('id="miaoshouCollectionChildren"')>html.indexOf('<main class="main-content">'));
  assert.ok(html.includes('id="collectionAccountSwitchToggle"'));
});

test('desktop tabs receive only module and account route state and can switch within the document',()=>{
  const s=setup('http://127.0.0.1:8787/?collectionAccount=2&sharedEditJob=private-job');
  const messages=[];s.window.temuDesktop={navigationChanged:value=>messages.push(value)};s.nav.ready();
  s.go('orders',4);
  assert.equal(messages.at(-1).collectionAccountId,2);assert.equal(messages.at(-1).orderAccountId,4);
  assert.deepEqual(Object.keys(messages.at(-1)).sort(),['collectionAccountId','orderAccountId','page','title']);
  assert.equal(s.nav.open({page:'tasks',collectionAccountId:2}),true);assert.equal(s.active.page,'tasks');
  assert.equal(s.nav.open({page:'https://example.com',collectionAccountId:2}),false);
});
test('desktop tabs use the guarded account switch when their collection context differs',()=>{
  const s=setup();s.nav.ready();const requests=[];
  s.window.collectionAccountContext={switchTo:(id,value)=>{requests.push({id,...value});return false;}};
  assert.equal(s.nav.open({page:'orders',collectionAccountId:2,orderAccountId:4}),false);
  assert.equal(s.active.page,'workbench');assert.equal(requests[0].id,2);assert.equal(requests[0].orderAccountId,4);
  assert.equal(s.nav.open({page:'orders',collectionAccountId:99}),false);assert.equal(requests.length,1);
});
