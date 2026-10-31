const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'collection-sync.js'), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));
const snapshot = extra => ({accountId: 2, enabled: true, running: false,
  nextRunAt: 9999999999, result: null, ...extra});
const row = extra => ({stage: 'importing', status: 'running', total: 300, processed: 20,
  added: 10, skipped: 10, replicas: 0, failed: 0, failures: [], orders: null, finishedAt: null, ...extra});
const response = (data, status = 200) => ({ok: status < 400, status, json: async () => ({data})});
function setup() {
  const elements = {}, requests = [], timers = [], refreshes = [];
  const element = id => elements[id] ||= {disabled: true, textContent: '', hidden: false,
    children: [], listeners: {}, classList: {contains: () => false},
    addEventListener(event, fn) { this.listeners[event] = fn; },
    replaceChildren() { this.children = []; }, append(child) { this.children.push(child); },
    querySelector: () => element('errors')};
  const window = {collectionAccountContext: {id: 2},
    clearTimeout: () => {}, setTimeout: (fn, delay) => {timers.push({fn, delay}); return timers.length;},
    sharedAutomation: {refresh: scope => refreshes.push(scope)}};
  const document = {getElementById: element, createElement: () => ({})};
  const fetch = (url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject}));
  vm.runInNewContext(source, {window, document, fetch, Date, Error, JSON});
  return {elements, requests, timers, refreshes, element};
}

test('whole-box button sends no pagination, selected products or person filters', async () => {
  const s = setup(); s.requests[0].resolve(response(snapshot())); await settle();
  assert.equal(s.element('syncCollectionPool').disabled, false);
  const pending = s.element('syncCollectionPool').listeners.click();
  assert.equal(s.requests[1].url, '/api/collection-sync');
  assert.deepEqual(JSON.parse(s.requests[1].options.body), {});
  assert.equal(s.element('syncCollectionPool').disabled, true);
  s.requests[1].resolve(response(snapshot({running: true, result: row()}), 202)); await pending;
  assert.match(s.element('collectionPoolSyncStatus').textContent, /20\/300/);
  assert.equal(s.timers.at(-1).delay, 3000);
  s.timers.at(-1).fn();
  s.requests[2].resolve(response(snapshot({result: row({stage: 'finished', status: 'completed', finishedAt: 100})})));
  await settle(); assert.deepEqual(s.refreshes, ['pool']);
});

test('a stale poll cannot overwrite newer sync progress', async () => {
  const s = setup(); s.requests[0].resolve(response(snapshot())); await settle();
  s.timers.at(-1).fn(); // pending old GET
  const pending = s.element('syncCollectionPool').listeners.click();
  s.requests[2].resolve(response(snapshot({running: true, result: row()}), 202)); await pending;
  s.requests[1].resolve(response(snapshot())); await settle();
  assert.match(s.element('collectionPoolSyncStatus').textContent, /20\/300/);
  assert.equal(s.element('syncCollectionPool').disabled, true);
});

test('read failure and wrong account fail closed; errors render as text', async () => {
  const s = setup(); s.requests[0].reject(Error('network unavailable')); await settle();
  assert.equal(s.element('syncCollectionPool').disabled, true);
  assert.match(s.element('collectionPoolSyncStatus').textContent, /读取失败/);
  s.timers.at(-1).fn(); s.requests[1].resolve(response(snapshot({accountId: 1}))); await settle();
  assert.match(s.element('collectionPoolSyncStatus').textContent, /账号不一致/);
  s.timers.at(-1).fn(); s.requests[2].resolve(response(snapshot({result: row({failed: 1,
    failures: [{detailId: 88, error: '<img onerror=alert(1)>'}]})}))); await settle();
  assert.equal(s.element('errors').children[0].textContent, '商品 88：<img onerror=alert(1)>');
  assert.equal(s.element('collectionPoolSyncFailures').hidden, false);
});

test('hourly setting is explicit and failed writes restore the last known setting', async () => {
  const s = setup(); s.requests[0].resolve(response(snapshot())); await settle();
  const toggle = s.element('hourlyCollectionSync'); toggle.checked = false;
  const pending = toggle.listeners.change();
  assert.equal(s.requests[1].url, '/api/collection-sync/settings');
  assert.deepEqual(JSON.parse(s.requests[1].options.body), {enabled: false});
  s.requests[1].reject(Error('保存失败')); await pending;
  assert.equal(toggle.checked, true);
  assert.match(s.element('collectionPoolSyncStatus').textContent, /保存失败/);
});
