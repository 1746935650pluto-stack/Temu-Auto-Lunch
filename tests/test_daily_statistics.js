const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createDocument} = require('./dom_fixture');
const source = fs.readFileSync(path.join(__dirname, '..', 'data-statistics.js'), 'utf8');
function setup() {
  const document = createDocument(), events = {}, requests = [], opened = [];
  for (const id of ['statisticsAccount','statisticsStart','statisticsEnd','statisticsExport','statisticsRows','statisticsCards','statisticsStatus','statisticsTabs','statisticsDailyTab','statisticsOrdersTab','statisticsFilters','statisticsToday','statisticsWeek']) document.createNode(id);
  document.addEventListener = (name, callback) => events[name] = callback;
  const context = {document, window: {}, Date, URLSearchParams, AbortController, Blob, URL, setTimeout,
    openOrderAccount: id => opened.push(id), activeOrderAccountId: 2, activatePage: id => opened.push(id),
    fetch: (url, options) => new Promise(resolve => requests.push({url, options, resolve}))};
  vm.runInNewContext(source, context); events.DOMContentLoaded();
  return {...context, requests, opened, el: id => document.getElementById(id)};
}
const result = name => ({start:'2026-11-09',end:'2026-11-09',accounts:[{id:1,name}],rows:[{
  date:'2026-11-09',accountId:1,accountName:name,collected:3,tasks:2,saved:1,submitted:1,editFailed:0,publishFailed:0,needsInput:0,orders:4,packages:5,quantity:6,amounts:{USD:'12.30'}}],
  totals:{collected:3,saved:1,submitted:1,editFailed:0,publishFailed:0,orders:4,amounts:{USD:'12.30'}},warnings:[]});
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
});
test('filter changes clear old data and disable stale exports', async () => {
  const s = setup(); const pending = s.window.dataStatistics.open();
  s.requests[0].resolve({ok:true,json:async()=>({data:result('one')})}); await pending;
  await s.el('statisticsAccount').dispatch('change');
  assert.equal(s.el('statisticsRows').children.length,0);assert.equal(s.el('statisticsExport').disabled,true);
});
test('integrated tabs retain order account and hide outside statistics', async () => {
  const s=setup();s.window.dataStatistics.setPage('orders');
  assert.equal(s.el('statisticsTabs').hidden,false);assert.equal(s.el('statisticsOrdersTab').attrs['aria-selected'],'true');
  await s.el('statisticsOrdersTab').dispatch('click');assert.deepEqual(s.opened,[2]);
  s.window.dataStatistics.setPage('settings');assert.equal(s.el('statisticsTabs').hidden,true);
});
test('request failures show errors and keep export disabled', async () => {
  const s=setup();const pending=s.window.dataStatistics.open();
  s.requests[0].resolve({ok:false,json:async()=>({error:'invalid range'})});await pending;
  assert.match(s.el('statisticsStatus').textContent,/invalid range/);assert.equal(s.el('statisticsExport').disabled,true);
});
