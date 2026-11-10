const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createDocument}=require('./dom_fixture');
const source=fs.readFileSync(path.join(__dirname,'..','order-shop-chart.js'),'utf8');
function setup(){
  const document=createDocument(),events={},requests=[],selected=[],loaded=[];
  for(const id of ['orderShopPlot','orderShopSummary','orderShopStatus','orderShopHint','orderShopStart','orderShopEnd','orderShopToday','orderShopWeek','orderShopTitle','orderShopCaption','orderShopMetric','orderShopForm','orderShopBarView','orderShopPieView'])document.createNode(id);
  document.createElementNS=(ns,tag)=>document.createElement(tag);document.addEventListener=(name,fn)=>events[name]=fn;
  const context={document,window:{},URLSearchParams,AbortController,Date,fetch:(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}))};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'..','proportion-chart.js'),'utf8'),context);
  vm.runInNewContext(source,context);events.DOMContentLoaded();
  context.window.orderShopCharts.configure({onSelect:shop=>selected.push(shop),onLoaded:id=>loaded.push(id)});
  return {...context,requests,selected,loaded,chart:context.window.orderShopCharts,el:id=>document.getElementById(id)};
}
const report=(accountId,name)=>({accountId,accountName:`account-${accountId}`,start:'2026-11-10',end:'2026-11-10',warnings:[],
  shops:[{accountId,shopId:'1',shopKey:'1',shopName:name,displayName:name,orders:3,packages:4,quantity:2,quantityMissing:0},
    {accountId,shopId:'2',shopKey:'2',shopName:'Beta',displayName:'Beta',orders:1,packages:1,quantity:9,quantityMissing:0}],
  totals:{orders:4,packages:5,quantity:11,quantityMissing:0}});
function respond(request,data){request.resolve({ok:true,json:async()=>({data})});}

test('shop pie displays current account proportions and legend/keyboard keeps exact store identity',async()=>{
  const s=setup(),pending=s.chart.open(5);respond(s.requests[0],report(5,'Alpha'));await pending;
  await s.el('orderShopPieView').dispatch('click');const rows=s.el('orderShopPlot').querySelectorAll('button');
  assert.match(rows[0].attrs['aria-label'],/Alpha.*75.00%/);assert.match(rows[1].attrs['aria-label'],/Beta.*25.00%/);
  await rows[1].dispatch('click');await s.el('orderShopPlot').querySelectorAll('g')[0].dispatch('keydown',{key:'Enter'});
  assert.equal(s.selected[0].accountId,5);assert.equal(s.selected[0].shopKey,'2');assert.equal(s.selected[1].shopKey,'1');
  s.chart.selectionChanged('2');assert.equal(s.requests.length,1);assert.equal(s.el('orderShopPieView').attrs['aria-pressed'],'true');
});

test('changing shop pie metric reuses data and late previous accounts cannot replace it',async()=>{
  const s=setup(),first=s.chart.open(1);respond(s.requests[0],report(1,'Old'));await first;await s.el('orderShopPieView').dispatch('click');
  s.el('orderShopMetric').value='quantity';await s.el('orderShopMetric').dispatch('change');
  assert.match(s.el('orderShopPlot').querySelectorAll('button')[0].attrs['aria-label'],/Beta.*81.82%/);assert.equal(s.requests.length,1);
  const stale=s.chart.open(1),current=s.chart.open(2);respond(s.requests[2],report(2,'New'));await current;respond(s.requests[1],report(1,'Old'));await stale;
  assert.equal(s.el('orderShopPieView').attrs['aria-pressed'],'true');assert.match(s.el('orderShopPlot').textContent,/New/);assert.doesNotMatch(s.el('orderShopPlot').textContent,/Old/);
});

test('zero shop pie has no slices and returning to bars requires no new request',async()=>{
  const s=setup(),data=report(1,'Zero');data.shops.forEach(row=>row.orders=0);const pending=s.chart.open(1);respond(s.requests[0],data);await pending;
  await s.el('orderShopPieView').dispatch('click');assert.match(s.el('orderShopPlot').textContent,/暂无占比/);assert.equal(s.el('orderShopPlot').querySelectorAll('g').length,0);
  await s.el('orderShopBarView').dispatch('click');assert.equal(s.el('orderShopPlot').children[0].tagName,'SVG');assert.equal(s.requests.length,1);
});
test('late account results cannot replace another account chart or catalog',async()=>{
  const s=setup(),first=s.chart.open(1),second=s.chart.open(2);respond(s.requests[1],report(2,'New'));await second;
  respond(s.requests[0],report(1,'Old'));await first;
  assert.match(s.el('orderShopPlot').textContent,/New/);assert.doesNotMatch(s.el('orderShopPlot').textContent,/Old/);
  assert.equal(s.chart.shopsFor(1).length,0);assert.deepEqual(s.loaded,[2]);
});
test('metrics reuse data and store clicks and keyboard actions keep account identity',async()=>{
  const s=setup(),pending=s.chart.open(1);respond(s.requests[0],report(1,'Alpha'));await pending;
  s.el('orderShopMetric').value='quantity';await s.el('orderShopMetric').dispatch('change');
  const bar=s.el('orderShopPlot').children[0].children.find(node=>node.attrs['data-shop-key']);
  assert.match(bar.attrs['aria-label'],/Beta.*已知件数 9/);await bar.dispatch('keydown',{key:'Enter'});
  assert.equal(s.selected[0].accountId,1);assert.equal(s.selected[0].shopKey,'2');assert.equal(s.requests.length,1);
  s.chart.selectionChanged('2');assert.equal(s.requests.length,1);
});
test('sync refreshes only the displayed account and date edits invalidate pending results',async()=>{
  const s=setup(),pending=s.chart.open(1);respond(s.requests[0],report(1,'Alpha'));await pending;
  s.chart.ordersSynced(2);assert.equal(s.requests.length,1);
  const refresh=s.chart.ordersSynced(1);assert.equal(s.requests.length,2);
  await s.el('orderShopStart').dispatch('change');respond(s.requests[1],report(1,'stale'));await refresh;
  assert.equal(s.el('orderShopPlot').children.length,0);assert.match(s.el('orderShopStatus').textContent,/日期已修改/);
});
test('leaving page cancels reads and mismatched account payloads fail closed',async()=>{
  const s=setup(),pending=s.chart.open(1);s.chart.close();respond(s.requests[0],report(1,'hidden'));await pending;
  assert.equal(s.el('orderShopPlot').children.length,0);
  const next=s.chart.open(2);respond(s.requests[1],report(1,'wrong'));await next;
  assert.match(s.el('orderShopStatus').textContent,/账号不匹配/);assert.equal(s.chart.shopsFor(2).length,0);
});
test('zero stores and unsafe-looking names remain readable text without fabricated bars',async()=>{
  const s=setup(),pending=s.chart.open(1),data=report(1,'<script>shop</script>');data.shops[0].orders=0;
  respond(s.requests[0],data);await pending;
  const bar=s.el('orderShopPlot').children[0].children.find(node=>node.attrs['data-shop-key']==='1');
  assert.equal(bar.children[0].children.length,0);assert.match(bar.children[0].textContent,/<script>shop<\/script>/);
  const next=s.chart.open(1);respond(s.requests[1],{...data,shops:[]});await next;
  assert.equal(s.el('orderShopPlot').children.length,0);assert.match(s.el('orderShopHint').textContent,/暂无已保存订单/);
});
