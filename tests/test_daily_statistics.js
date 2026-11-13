const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createDocument} = require('./dom_fixture');
const source = fs.readFileSync(path.join(__dirname, '..', 'data-statistics.js'), 'utf8');
function setup(clock = null, schedule = () => {}) {
  const document = createDocument(), events = {}, requests = [], opened = [];
  for (const id of ['statisticsAutoSyncStatus','statisticsSyncHistory','statisticsSyncOrders','statisticsSyncStatus','statisticsAccount','statisticsStart','statisticsEnd','statisticsExport','statisticsRows','statisticsCards','statisticsStatus','statisticsTabs','statisticsDailyTab','statisticsOrdersTab','statisticsFilters','statisticsToday','statisticsWeek','statisticsMetric','statisticsTable','statisticsChartPanel','statisticsChartView','statisticsPieView','statisticsTableView','statisticsChartTitle','statisticsChartLegend','statisticsChartPlot','statisticsChartHint','statisticsChartCaption']) document.createNode(id);
  document.body = document.createElement('body');
  document.createNode('collectionContext').className = 'collection-context-bar';
  document.createElementNS = (ns, tag) => document.createElement(tag);
  document.addEventListener = (name, callback) => events[name] = callback;
  const ClockDate = clock ? class extends Date { static now() { return clock.now; } } : Date;
  const intervals = new Map(); let intervalId = 0;
  const context = {clearTimeout: () => {}, setInterval: fn => { intervals.set(++intervalId, fn); return intervalId; }, clearInterval: id => intervals.delete(id), intervals, document, window: {}, Date: ClockDate, URLSearchParams, AbortController, Blob, URL, setTimeout: schedule,
    openOrderAccount: id => opened.push(id), activeOrderAccountId: 2, activatePage: id => opened.push(id),
    fetch: (url, options) => new Promise(resolve => requests.push({url, options, resolve}))};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'..','proportion-chart.js'),'utf8'),context);
  vm.runInNewContext(source, context); events.DOMContentLoaded();
  return {...context, requests, opened, el: id => document.getElementById(id)};
}
const result = name => ({start:'2026-11-09',end:'2026-11-09',accounts:[{id:1,name}],rows:[{
  date:'2026-11-09',accountId:1,accountName:name,collected:3,tasks:2,saved:1,submitted:1,editFailed:0,publishFailed:0,needsInput:0,orders:4,packages:5,quantity:6,amounts:{USD:'12.30'},amountMissing:2}],
  totals:{collected:3,saved:1,submitted:1,editFailed:0,publishFailed:0,orders:4,amounts:{USD:'12.30'},amountMissing:2},warnings:[]});

test('initial navigation requests only today in Beijing even on a different UTC day', async () => {
  const s = setup({now: Date.parse('2026-11-12T16:15:00Z')});
  assert.equal(s.el('statisticsStart').value, '2026-11-13');
  assert.equal(s.el('statisticsEnd').value, '2026-11-13');
  assert.equal(s.el('statisticsToday').attrs['aria-pressed'], 'true');
  assert.equal(s.el('statisticsWeek').attrs['aria-pressed'], 'false');
  s.window.dataStatistics.setPage('dataStatistics');
  const pending = s.window.dataStatistics.open();
  const params = new URL(s.requests[0].url, 'http://localhost').searchParams;
  assert.equal(params.get('start'), '2026-11-13');
  assert.equal(params.get('end'), '2026-11-13');
  s.requests[0].resolve({ok:true,json:async()=>({data:result('today')})}); await pending;
});

test('week shortcut stays usable and reentering after midnight resets to the new day', async () => {
  const clock = {now: Date.parse('2026-11-12T16:15:00Z')}, s = setup(clock);
  s.window.dataStatistics.setPage('dataStatistics');
  await s.el('statisticsWeek').dispatch('click');
  let params = new URL(s.requests[0].url, 'http://localhost').searchParams;
  assert.equal(params.get('start'), '2026-11-07');
  assert.equal(params.get('end'), '2026-11-13');
  assert.equal(s.el('statisticsWeek').attrs['aria-pressed'], 'true');
  s.window.dataStatistics.setPage('settings');
  clock.now = Date.parse('2026-11-13T16:01:00Z');
  s.window.dataStatistics.setPage('dataStatistics');
  const pending = s.window.dataStatistics.open();
  params = new URL(s.requests[1].url, 'http://localhost').searchParams;
  assert.equal(params.get('start'), '2026-11-14');
  assert.equal(params.get('end'), '2026-11-14');
  assert.equal(s.requests[0].options.signal.aborted, true);
  assert.equal(s.el('statisticsToday').attrs['aria-pressed'], 'true');
  s.requests[1].resolve({ok:true,json:async()=>({data:result('new day')})}); await pending;
  s.requests[0].resolve({ok:true,json:async()=>({data:result('old week')})});
  await new Promise(resolve => setImmediate(resolve));
  assert.match(s.el('statisticsRows').textContent, /new day/);
  assert.doesNotMatch(s.el('statisticsRows').textContent, /old week/);
});
test('late responses cannot overwrite newer account filters', async () => {
  const s = setup(); const first = s.window.dataStatistics.open(); const second = s.window.dataStatistics.open();
  s.requests[1].resolve({ok:true,json:async()=>({data:result('new')})}); await second;
  s.requests[0].resolve({ok:true,json:async()=>({data:result('old')})}); await first;
  assert.match(s.el('statisticsRows').textContent,/new/); assert.doesNotMatch(s.el('statisticsRows').textContent,/old/);
});
test('render escapes account names and opens matching order account', async () => {
  const s = setup(); const pending = s.window.dataStatistics.open();
  s.requests[0].resolve({ok:true,json:async()=>({data:result('<script>account</script>')})}); await pending;
  assert.equal(s.el('statisticsRows').children[0].children[1].textContent,'<script>account</script>');
  assert.equal(s.el('statisticsRows').children[0].children[1].children.length,0);
  await s.el('statisticsRows').children[0].children[9].children[0].dispatch('click');
  assert.deepEqual(s.opened,[1]); assert.equal(s.el('statisticsExport').disabled,false);
  assert.equal(s.el('statisticsRows').children[0].children.length,12);
  assert.equal(s.el('statisticsCards').children.length,5);
  assert.doesNotMatch(s.el('statisticsCards').textContent+s.el('statisticsStatus').textContent,/金额|币种|USD/);
});
test('date edits clear old data and disable stale exports without fetching', async () => {
  const s = setup(); const pending = s.window.dataStatistics.open();
  s.requests[0].resolve({ok:true,json:async()=>({data:result('one')})}); await pending;
  await s.el('statisticsStart').dispatch('change');
  assert.equal(s.el('statisticsRows').children.length,0);assert.equal(s.el('statisticsExport').disabled,true);
  assert.equal(s.requests.length,1);
});
test('integrated tabs retain order account and hide outside statistics', async () => {
  const s=setup();s.window.dataStatistics.setPage('orders');
  assert.equal(s.el('collectionContext').hidden,true);
  assert.equal(s.document.body.classList.contains('statistics-workspace-active'),true);
  assert.equal(s.el('statisticsTabs').hidden,false);assert.equal(s.el('statisticsOrdersTab').attrs['aria-selected'],'true');
  await s.el('statisticsOrdersTab').dispatch('click');assert.deepEqual(s.opened,[2]);
  s.window.dataStatistics.setPage('dataStatistics');assert.equal(s.el('collectionContext').hidden,true);
  s.window.dataStatistics.setPage('settings');assert.equal(s.el('statisticsTabs').hidden,true);assert.equal(s.el('collectionContext').hidden,false);assert.equal(s.document.body.classList.contains('statistics-workspace-active'),false);
});
test('request failures show errors and keep export disabled', async () => {
  const s=setup();const pending=s.window.dataStatistics.open();
  s.requests[0].resolve({ok:false,json:async()=>({error:'invalid range'})});await pending;
  assert.match(s.el('statisticsStatus').textContent,/invalid range/);assert.equal(s.el('statisticsExport').disabled,true);
});

test('chart sorts dates and accounts and compares values on one zero-based scale', async () => {
  const s=setup(), data=result('NL'), base=data.rows[0];
  data.rows=[{...base,date:'2026-11-10',accountId:2,accountName:'Bao',orders:15},
    {...base,date:'2026-11-09',orders:206}, {...base,date:'2026-11-10',orders:103},
    {...base,date:'2026-11-09',accountId:2,accountName:'Bao',orders:32}];
  const pending=s.window.dataStatistics.open();s.requests[0].resolve({ok:true,json:async()=>({data})});await pending;
  const bars=s.el('statisticsChartPlot').children[0].children.filter(node=>node.attrs['data-chart-account']);
  assert.deepEqual(bars.map(node=>[node.attrs['data-chart-date'],node.attrs['data-chart-account']]),
    [['2026-11-09','1'],['2026-11-09','2'],['2026-11-10','1'],['2026-11-10','2']]);
  assert.equal(Number(bars[0].children[1].attrs.height),Number(bars[2].children[1].attrs.height)*2);
  assert.equal(bars[0].children[1].attrs.fill,bars[2].children[1].attrs.fill);
  assert.match(s.el('statisticsChartLegend').textContent,/NLBao/);
  await bars[1].dispatch('focus');assert.match(s.el('statisticsChartHint').textContent,/2026-11-09.*Bao.*32/);
  await bars[1].dispatch('keydown',{key:'Enter'});assert.deepEqual(s.opened,[2]);
});

test('metric and table switches reuse loaded data without another request', async () => {
  const s=setup(), pending=s.window.dataStatistics.open();
  s.requests[0].resolve({ok:true,json:async()=>({data:result('one')})});await pending;
  assert.equal(s.el('statisticsTable').hidden,true);
  s.el('statisticsMetric').value='collected';await s.el('statisticsMetric').dispatch('change');
  assert.match(s.el('statisticsChartTitle').textContent,/采集入池/);
  const bar=s.el('statisticsChartPlot').children[0].children.find(node=>node.attrs['data-chart-account']);
  assert.match(bar.attrs['aria-label'],/采集入池 3/);
  await bar.dispatch('click');assert.equal(s.opened.length,0);
  await s.el('statisticsTableView').dispatch('click');
  assert.equal(s.el('statisticsTable').hidden,false);assert.equal(s.el('statisticsChartPanel').hidden,true);
  await s.el('statisticsChartView').dispatch('click');assert.equal(s.el('statisticsChartPanel').hidden,false);
  assert.equal(s.requests.length,1);
});

test('zero values use finite geometry and editing dates clears the chart', async () => {
  const s=setup(),data=result('<img account>');data.rows[0].orders=0;
  const pending=s.window.dataStatistics.open();s.requests[0].resolve({ok:true,json:async()=>({data})});await pending;
  const svg=s.el('statisticsChartPlot').children[0];
  assert.doesNotMatch(JSON.stringify(svg.children.map(node=>node.attrs)),/NaN|Infinity/);
  const group=svg.children.find(node=>node.attrs['data-chart-account']);
  assert.equal(group.children[1].attrs.height,'0');assert.match(s.el('statisticsChartHint').textContent,/均为 0/);
  assert.equal(s.el('statisticsChartLegend').children[0].children[1].textContent,'<img account>');
  await s.el('statisticsStart').dispatch('change');assert.equal(s.el('statisticsChartPlot').children.length,0);
});

function accountResult(id,name,orders) {
  const data=result(name);
  data.accounts=[{id:1,name:'NL'},{id:2,name:'Bao'},{id:4,name:'LULU'}];
  data.rows[0].accountId=id;data.rows[0].orders=orders;data.totals.orders=orders;
  return data;
}

test('account pie aggregates the selected date range per account and opens exact accounts without fetching',async()=>{
  const s=setup(),data=result('Alpha'),base=data.rows[0];data.start='2026-11-08';data.end='2026-11-09';
  data.accounts=[{id:1,name:'Alpha'},{id:2,name:'Beta'},{id:3,name:'Zero'}];
  data.rows=[{...base,date:'2026-11-08',orders:6},{...base,orders:2},
    {...base,accountId:2,accountName:'Beta',orders:2},{...base,accountId:3,accountName:'Zero',orders:0}];data.totals.orders=10;
  const pending=s.window.dataStatistics.open();s.requests[0].resolve({ok:true,json:async()=>({data})});await pending;
  await s.el('statisticsPieView').dispatch('click');assert.match(s.el('statisticsChartTitle').textContent,/账号占比/);
  const rows=s.el('statisticsChartPlot').querySelectorAll('button');assert.equal(rows.length,3);
  assert.match(rows[0].attrs['aria-label'],/Alpha.*8.*80.00%/);assert.match(rows[1].attrs['aria-label'],/Beta.*2.*20.00%/);assert.match(rows[2].attrs['aria-label'],/0.00%/);
  await rows[1].dispatch('click');assert.deepEqual(s.opened,[2]);assert.equal(s.requests.length,1);
});

test('pie metrics and summary cards stay linked while switching accounts keeps pie view',async()=>{
  const s=setup(),pending=s.window.dataStatistics.open(),data=accountResult(1,'NL',103);
  data.rows[0].editFailed=2;data.rows[0].publishFailed=3;data.totals.editFailed=2;data.totals.publishFailed=3;
  s.requests[0].resolve({ok:true,json:async()=>({data})});await pending;await s.el('statisticsPieView').dispatch('click');
  await s.el('statisticsCards').children[4].dispatch('click');assert.equal(s.el('statisticsPieView').attrs['aria-pressed'],'true');
  assert.match(s.el('statisticsChartPlot').textContent,/100.00%/);assert.match(s.el('statisticsChartPlot').querySelectorAll('div').map(n=>n.attrs['aria-label']||'').join(' '),/异常 5/);
  s.el('statisticsAccount').value='2';const next=s.el('statisticsAccount').dispatch('change');assert.equal(s.el('statisticsChartPlot').children.length,0);
  s.requests[1].resolve({ok:true,json:async()=>({data:accountResult(2,'Bao',15)})});await next;
  assert.equal(s.el('statisticsPieView').attrs['aria-pressed'],'true');assert.doesNotMatch(s.el('statisticsChartPlot').textContent,/NL/);assert.equal(s.requests.length,2);
});

test('all-zero account pie remains explicit and table/bar toggles retain existing behavior',async()=>{
  const s=setup(),data=result('Zero');data.rows[0].orders=0;const pending=s.window.dataStatistics.open();s.requests[0].resolve({ok:true,json:async()=>({data})});await pending;
  await s.el('statisticsPieView').dispatch('click');assert.match(s.el('statisticsChartPlot').textContent,/暂无占比/);assert.equal(s.el('statisticsChartPlot').querySelectorAll('g').length,0);
  await s.el('statisticsTableView').dispatch('click');assert.equal(s.el('statisticsMetric').disabled,true);
  await s.el('statisticsChartView').dispatch('click');assert.equal(s.el('statisticsMetric').disabled,false);assert.equal(s.el('statisticsChartPlot').children[0].tagName,'SVG');
});

test('changing account automatically refreshes cards, chart, rows and export using current dates', async()=>{
  const s=setup();s.el('statisticsStart').value='2026-11-04';s.el('statisticsEnd').value='2026-11-10';
  const initial=s.window.dataStatistics.open();s.requests[0].resolve({ok:true,json:async()=>({data:accountResult(1,'NL',103)})});await initial;
  s.el('statisticsAccount').value='2';const pending=s.el('statisticsAccount').dispatch('change');
  assert.equal(s.requests.length,2);
  const query=new URL(s.requests[1].url,'http://localhost').searchParams;
  assert.equal(query.get('accountId'),'2');assert.equal(query.get('start'),'2026-11-04');assert.equal(query.get('end'),'2026-11-10');
  assert.equal(s.el('statisticsCards').children.length,0);assert.equal(s.el('statisticsRows').children.length,0);
  assert.equal(s.el('statisticsChartPlot').children.length,0);assert.equal(s.el('statisticsExport').disabled,true);
  assert.match(s.el('statisticsStatus').textContent,/正在读取/);
  s.requests[1].resolve({ok:true,json:async()=>({data:accountResult(2,'Bao',15)})});await pending;
  assert.equal(s.el('statisticsAccount').value,'2');assert.equal(s.el('statisticsExport').disabled,false);
  assert.match(s.el('statisticsRows').textContent,/Bao/);assert.doesNotMatch(s.el('statisticsRows').textContent,/NL/);
  assert.equal(s.el('statisticsCards').children[0].children[1].textContent,'15');
  const bar=s.el('statisticsChartPlot').children[0].children.find(node=>node.attrs['data-chart-account']);
  assert.equal(bar.attrs['data-chart-account'],'2');assert.match(bar.attrs['aria-label'],/Bao.*15/);
});

test('rapid automatic account changes abort earlier reads and ignore late responses', async()=>{
  const s=setup();s.el('statisticsAccount').value='2';const earlier=s.el('statisticsAccount').dispatch('change');
  s.el('statisticsAccount').value='4';const latest=s.el('statisticsAccount').dispatch('change');
  assert.equal(s.requests.length,2);assert.equal(s.requests[0].options.signal.aborted,true);
  assert.equal(s.requests[1].options.signal.aborted,false);
  s.requests[1].resolve({ok:true,json:async()=>({data:accountResult(4,'LULU',10)})});await latest;
  s.requests[0].resolve({ok:true,json:async()=>({data:accountResult(2,'Bao',15)})});await earlier;
  assert.equal(s.el('statisticsAccount').value,'4');assert.match(s.el('statisticsRows').textContent,/LULU/);
  assert.doesNotMatch(s.el('statisticsRows').textContent,/Bao/);assert.match(s.el('statisticsChartLegend').textContent,/LULU/);
  assert.equal(s.el('statisticsCards').children[0].children[1].textContent,'10');
});

test('automatic account refresh preserves table view and reports failures without old data', async()=>{
  const s=setup();const initial=s.window.dataStatistics.open();
  s.requests[0].resolve({ok:true,json:async()=>({data:accountResult(1,'NL',103)})});await initial;
  await s.el('statisticsTableView').dispatch('click');
  s.el('statisticsAccount').value='2';const pending=s.el('statisticsAccount').dispatch('change');
  s.requests[1].resolve({ok:true,json:async()=>({data:accountResult(2,'Bao',15)})});await pending;
  assert.equal(s.el('statisticsTable').hidden,false);assert.equal(s.el('statisticsChartPanel').hidden,true);
  assert.match(s.el('statisticsRows').textContent,/Bao/);
  s.el('statisticsAccount').value='4';const failure=s.el('statisticsAccount').dispatch('change');
  s.requests[2].resolve({ok:false,json:async()=>({error:'temporary failure'})});await failure;
  assert.match(s.el('statisticsStatus').textContent,/temporary failure/);assert.equal(s.el('statisticsExport').disabled,true);
  assert.equal(s.el('statisticsRows').children.length,0);assert.equal(s.el('statisticsCards').children.length,0);
});

test('summary cards switch chart metrics and dropdown without fetching again', async()=>{
  const s=setup(),data=result('NL');Object.assign(data.rows[0],{saved:5,submitted:7,editFailed:2,publishFailed:4});
  Object.assign(data.totals,{saved:5,submitted:7,editFailed:2,publishFailed:4});
  const pending=s.window.dataStatistics.open();s.requests[0].resolve({ok:true,json:async()=>({data})});await pending;
  const expected=[['orders',4],['collected',3],['saved',5],['submitted',7],['failed',6]];
  for(let index=0;index<expected.length;index++){
    const card=s.el('statisticsCards').children[index];assert.equal(card.tagName,'BUTTON');
    await card.dispatch('click');assert.equal(s.el('statisticsMetric').value,expected[index][0]);
    const bar=s.el('statisticsChartPlot').children[0].children.find(node=>node.attrs['data-chart-account']);
    assert.ok(bar.attrs['aria-label'].endsWith(' '+expected[index][1]));
    assert.equal(card.attrs['aria-pressed'],'true');
    assert.equal(s.el('statisticsCards').children.filter(node=>node.attrs['aria-pressed']==='true').length,1);
  }
  assert.match(s.el('statisticsChartTitle').textContent,/编辑／发布异常/);assert.equal(s.requests.length,1);
  s.el('statisticsMetric').value='collected';await s.el('statisticsMetric').dispatch('change');
  assert.equal(s.el('statisticsCards').children[1].attrs['aria-pressed'],'true');
  assert.equal(s.el('statisticsCards').children[4].attrs['aria-pressed'],'false');
});

test('clicking a summary card opens its chart from table view and retains selection after account refresh', async()=>{
  const s=setup(),pending=s.window.dataStatistics.open();
  s.requests[0].resolve({ok:true,json:async()=>({data:accountResult(1,'NL',103)})});await pending;
  await s.el('statisticsTableView').dispatch('click');await s.el('statisticsCards').children[1].dispatch('click');
  assert.equal(s.el('statisticsTable').hidden,true);assert.equal(s.el('statisticsChartPanel').hidden,false);
  assert.equal(s.el('statisticsMetric').disabled,false);assert.equal(s.el('statisticsMetric').value,'collected');
  s.el('statisticsAccount').value='2';const refresh=s.el('statisticsAccount').dispatch('change');
  s.requests[1].resolve({ok:true,json:async()=>({data:accountResult(2,'Bao',15)})});await refresh;
  assert.equal(s.el('statisticsMetric').value,'collected');
  assert.equal(s.el('statisticsCards').children[1].attrs['aria-pressed'],'true');
  assert.equal(s.el('statisticsCards').children[0].attrs['aria-pressed'],'false');assert.equal(s.requests.length,2);
});

test('long ranges keep one bar per visible account and date in a scrollable chart', async () => {
  const s=setup(),data=result('one'),base=data.rows[0];data.rows=[];
  for(let day=1;day<=30;day++) for(let account=1;account<=7;account++) data.rows.push({...base,date:`2026-10-${String(day).padStart(2,'0')}`,accountId:account,accountName:`account-${account}`,orders:day});
  const pending=s.window.dataStatistics.open();s.requests[0].resolve({ok:true,json:async()=>({data})});await pending;
  const svg=s.el('statisticsChartPlot').children[0];
  assert.equal(svg.children.filter(node=>node.attrs['data-chart-account']).length,210);
  assert.ok(Number(svg.attrs.width)>760);assert.equal(s.el('statisticsChartLegend').children.length,7);
});

test('single-day comparisons spread account bars across the plot and label each account', async () => {
  const s=setup(),data=result('NL'),base=data.rows[0];
  s.el('statisticsChartPlot').clientWidth=1000;
  data.rows=Array.from({length:7},(_,index)=>({...base,accountId:index+1,accountName:`account-${index+1}`,orders:10*(index+1)}));
  const pending=s.window.dataStatistics.open();s.requests[0].resolve({ok:true,json:async()=>({data})});await pending;
  const svg=s.el('statisticsChartPlot').children[0],bars=svg.children.filter(node=>node.attrs['data-chart-account']);
  const first=Number(bars[0].children[1].attrs.x),last=Number(bars[6].children[1].attrs.x);
  assert.ok(last-first>Number(svg.attrs.width)*.65);
  for(let index=1;index<=7;index++) assert.ok(svg.children.some(node=>node.textContent===`account-${index}`));
  assert.match(s.el('statisticsChartTitle').textContent,/订单数对比/);
  assert.equal(s.el('statisticsCards').children[0].children[0].textContent,'订单数');
});

const tick = () => new Promise(resolve => setImmediate(resolve));
const reply = (s, index, data, ok = true) => s.requests[index].resolve({ok, json: async () => ok ? {data} : {error:data}});

async function startSync(s, accounts, button='statisticsSyncOrders') {
  const offset=s.requests.length, pending=s.el(button).dispatch('click');
  reply(s,offset,{accounts});await tick();
  reply(s,offset+1,{supportsModes:true,entries:[]});await tick();
  return {pending,offset};
}
function success(s,index,extra={}) {
  const body=JSON.parse(s.requests[index].options.body);
  reply(s,index,{...body,pages:1,received:100,imported:100,...extra});
}

test('quick sync has no cache delay and duplicate quick/history clicks are blocked', async () => {
  const timers=[],s=setup(null,(fn,ms)=>timers.push({fn,ms}));
  s.window.dataStatistics.setPage('dataStatistics');s.el('statisticsAccount').value='2';
  const {pending}=await startSync(s,[{id:1,configured:true},{id:2,name:'Bao',configured:true}]);
  assert.equal(s.requests.length,3);
  assert.equal(JSON.parse(s.requests[2].options.body).mode,'recent');
  assert.equal(JSON.parse(s.requests[2].options.body).accountId,2);
  await s.el('statisticsSyncOrders').dispatch('click');await s.el('statisticsSyncHistory').dispatch('click');
  assert.equal(s.requests.length,3);assert.equal(s.el('statisticsSyncHistory').disabled,true);
  success(s,2);await tick();
  assert.equal(s.requests.length,4);assert.equal(timers.length,0);
  assert.match(s.el('statisticsSyncStatus').textContent,/已完成 1\/1.*成功 1/);
  assert.match(s.el('statisticsSyncStatus').textContent,/已读取 1 页 \/ 100 条/);
  reply(s,3,result('fresh'));await pending;
  assert.match(s.el('statisticsRows').textContent,/fresh/);
  assert.equal(s.el('statisticsSyncOrders').disabled,false);assert.equal(s.el('statisticsSyncHistory').disabled,false);
  assert.equal(s.intervals.size,1);
});

test('different accounts start together, partial failure continues and each success refreshes', async () => {
  const s=setup();s.window.dataStatistics.setPage('dataStatistics');
  const {pending}=await startSync(s,[{id:1,name:'NL',configured:true},{id:2,name:'Bao',configured:true},
    {id:4,name:'LULU',configured:true},{id:5,configured:false},{id:6,configured:true,blocked:true}]);
  assert.deepEqual(s.requests.slice(2).map(r=>JSON.parse(r.options.body).accountId),[1,2,4]);
  reply(s,3,'该账号的订单正在同步，请稍候',false);await tick();
  success(s,4,{message:'仅同步最近100条'});await tick();
  assert.equal(s.requests[5].url.startsWith('/api/statistics/daily?'),true);
  reply(s,5,result('first'));await tick();
  assert.match(s.el('statisticsRows').textContent,/first/);
  assert.match(s.el('statisticsSyncStatus').textContent,/已完成 2\/3/);
  success(s,2);await tick();reply(s,6,result('last'));await pending;
  assert.match(s.el('statisticsSyncStatus').textContent,/成功 2，失败 1，跳过 2/);
  assert.match(s.el('statisticsSyncStatus').textContent,/Bao：失败/);
  assert.match(s.el('statisticsSyncStatus').textContent,/LULU：完成.*仅同步最近100条/);
});

test('history uses its separate mode and progress rejects stale attempt data', async () => {
  const s=setup();
  const {pending}=await startSync(s,[{id:1,configured:true}], 'statisticsSyncHistory');
  const body=JSON.parse(s.requests[2].options.body);assert.equal(body.mode,'history');
  const poll=[...s.intervals.values()][0]();await tick();
  reply(s,3,{entries:[{accountId:1,requestId:body.requestId,pages:4,received:400,status:'running'},
    {accountId:1,requestId:'stale',pages:99,received:9900,status:'running'}]});await poll;
  assert.match(s.el('statisticsSyncStatus').textContent,/4 页 \/ 400 条/);
  const late=[...s.intervals.values()][0]();await tick();
  success(s,2,{pages:5,received:450,imported:450,message:'历史补齐'});await pending;
  const summary=s.el('statisticsSyncStatus').textContent;
  reply(s,4,{entries:[{accountId:1,requestId:body.requestId,pages:4,received:400}]});await late;
  assert.equal(s.el('statisticsSyncStatus').textContent,summary);
  assert.match(summary,/5 页 \/ 450 条/);assert.equal(s.intervals.size,0);
});

test('changing filters during sync preserves targets and discards older report responses', async () => {
  const s=setup();s.window.dataStatistics.setPage('dataStatistics');s.el('statisticsAccount').value='1';
  const {pending}=await startSync(s,[{id:1,configured:true},{id:2,configured:true}]);
  s.el('statisticsAccount').value='2';s.el('statisticsStart').value='2026-11-01';
  const change=s.el('statisticsAccount').dispatch('change');
  success(s,2);await tick();
  assert.equal(s.requests.filter(r=>r.url==='/api/orders/sync').length,1);
  const query=new URL(s.requests[4].url,'http://localhost').searchParams;
  assert.equal(query.get('accountId'),'2');assert.equal(query.get('start'),'2026-11-01');
  assert.equal(s.requests[3].options.signal.aborted,true);
  reply(s,4,accountResult(2,'fresh',5));await pending;
  reply(s,3,accountResult(2,'stale',1));await change;
  assert.match(s.el('statisticsRows').textContent,/fresh/);assert.doesNotMatch(s.el('statisticsRows').textContent,/stale/);
});

test('unsupported backend never silently scans history', async () => {
  const s=setup(),pending=s.el('statisticsSyncOrders').dispatch('click');
  reply(s,0,{accounts:[{id:1,configured:true}]});await tick();
  reply(s,1,'not found',false);await pending;
  assert.equal(s.requests.length,2);assert.match(s.el('statisticsSyncStatus').textContent,/后台尚未支持/);
  assert.equal(s.el('statisticsSyncOrders').disabled,false);
});

test('unconfigured accounts and mismatched replies cannot report success', async () => {
  const s=setup();let pending=s.el('statisticsSyncOrders').dispatch('click');
  reply(s,0,{accounts:[{id:1,configured:false}]});await pending;
  assert.match(s.el('statisticsSyncStatus').textContent,/没有已配置/);
  ({pending}=await startSync(s,[{id:1,configured:true}]));
  success(s,3,{accountId:2});await pending;
  assert.match(s.el('statisticsSyncStatus').textContent,/成功 0，失败 1/);
  assert.match(s.el('statisticsSyncStatus').textContent,/不匹配/);
});

test('leaving statistics skips hidden refresh; reentry reads current range', async () => {
  const s=setup();s.window.dataStatistics.setPage('dataStatistics');
  const {pending}=await startSync(s,[{id:1,configured:true}]);
  s.window.dataStatistics.setPage('settings');success(s,2);await pending;
  assert.equal(s.requests.length,3);
  s.window.dataStatistics.setPage('dataStatistics');const refresh=s.window.dataStatistics.open();
  assert.equal(new URL(s.requests[3].url,'http://localhost').searchParams.get('start'),s.el('statisticsStart').value);
  reply(s,3,result('fresh'));await refresh;
});

const autoResult = (finishedAt=1000) => ({enabled:true,running:false,nextRunAt:1300,accounts:[
  {accountId:1,name:'NL',status:'completed',startedAt:finishedAt-10,finishedAt,orderPages:1,orderReceived:100,orders:100}]});

test('order module installs dynamic schedule labels after the legacy app loads', () => {
  const s=setup();
  assert.match(s.window.orderAutoSyncText({enabled:true,running:true,intervalSeconds:300}),/每 5 分钟，最近 100 条/);
  assert.match(s.window.orderAutoSyncText({enabled:true,running:false,intervalSeconds:600,nextRunAt:1792468800}),/每 10 分钟/);
  assert.match(s.window.orderAutoSyncText({enabled:false}),/未启用/);
  const html=fs.readFileSync(path.join(__dirname,'..','index.html'),'utf8');
  assert.ok(html.indexOf('./data-statistics.js') < html.indexOf('./app.js'));
});

test('automatic sync completion refreshes current statistics once without POST requests', async () => {
  const s=setup();s.window.dataStatistics.setPage('dataStatistics');
  const initial=s.window.dataStatistics.open();reply(s,0,result('initial'));await initial;
  const poll=[...s.intervals.values()][0];
  let pending=poll();assert.equal(s.requests[1].url,'/api/orders/auto-sync');
  reply(s,1,autoResult());await tick();
  reply(s,2,result('automatic'));await pending;
  assert.match(s.el('statisticsRows').textContent,/automatic/);
  assert.match(s.el('statisticsAutoSyncStatus').textContent,/每 5 分钟/);
  assert.match(s.el('statisticsAutoSyncStatus').textContent,/NL：已完成 · 1 页 \/ 100 条/);
  pending=poll();reply(s,3,autoResult());await pending;
  assert.equal(s.requests.length,4);
  pending=poll();reply(s,4,autoResult(1300));await tick();
  reply(s,5,result('next round'));await pending;
  assert.equal(s.requests.filter(r=>r.options?.method==='POST').length,0);
});

test('automatic status respects unsubmitted date edits and ignores responses after leaving', async () => {
  const s=setup();s.window.dataStatistics.setPage('dataStatistics');
  const initial=s.window.dataStatistics.open();reply(s,0,result('initial'));await initial;
  s.el('statisticsStart').value='2026-11-01';await s.el('statisticsStart').dispatch('change');
  const poll=[...s.intervals.values()][0];
  let pending=poll();reply(s,1,autoResult());await pending;
  assert.equal(s.requests.length,2);assert.match(s.el('statisticsStatus').textContent,/筛选已修改/);
  pending=poll();await tick();s.window.dataStatistics.setPage('settings');
  assert.equal(s.requests[2].options.signal.aborted,true);assert.equal(s.intervals.size,0);
  const previous=s.el('statisticsAutoSyncStatus').textContent;
  reply(s,2,autoResult(2000));await pending;
  assert.equal(s.el('statisticsAutoSyncStatus').textContent,previous);
  await poll();assert.equal(s.requests.length,3);
});

test('automatic status polling is single-flight, avoids hidden pages, and survives failure', async () => {
  const s=setup();s.window.dataStatistics.setPage('dataStatistics');
  const poll=[...s.intervals.values()][0];
  s.document.hidden=true;await poll();assert.equal(s.requests.length,0);
  s.document.hidden=false;let pending=poll();await poll();assert.equal(s.requests.length,1);
  reply(s,0,'busy',false);await pending;
  assert.match(s.el('statisticsAutoSyncStatus').textContent,/稍后自动重试/);
  pending=poll();reply(s,1,autoResult());await pending;
  assert.match(s.el('statisticsAutoSyncStatus').textContent,/已完成/);
});
