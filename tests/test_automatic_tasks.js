const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'automatic-tasks.js'), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));
const response = (data, ok = true) => ({ok, json: async () => ok ? {data} : {error: 'fixture failure'}});
const options = () => ({mode: 'live', accounts: [1, 2].map(id => ({id, name: 'Box ' + id, configured: true})),
  targets: [1, 2].map(accountId => ({accountId, name: 'Box ' + accountId,
    shops: [{shopId: 7, shopNick: '<img onerror=x>'}], sites: [{key: 'US', value: '美国'}]})),
  people: [1, 2].map(accountId => ({key: `${accountId}:11`, accountId, personId: '11', name: 'Same name'}))});
function setup() {
  const elements = {}, requests = [], timers = [];
  function element(id) {
    return elements[id] ||= {value: '', disabled: false, checked: false, hidden: false, textContent: '',
      children: [], listeners: {}, classList: {contains: () => true}, reportValidity: () => true,
      get options() { return this.children.flatMap(c => c.tag === 'optgroup' ? c.children : [c]); },
      addEventListener(event, fn) { this.listeners[event] = fn; },
      append(child) { this.children.push(child); }, replaceChildren() { this.children = []; }};
  }
  const defaults = {autoTaskMode: 'now', autoTaskPriceMode: 'floor', autoTaskCount: '100', autoTaskPrice: '160',
    autoTaskMultiplier: '1.5', autoTaskPublishInterval: '10', autoTaskEvery: '60'};
  for (const [id, value] of Object.entries(defaults)) element(id).value = value;
  let number = 0;
  const document = {getElementById: element, createElement: tag => ({...element('new' + ++number), tag})};
  const fetch = (url, init) => new Promise((resolve, reject) => requests.push({url, init, resolve, reject}));
  const window = {};
  vm.runInNewContext(source, {document, window, fetch, Date, Intl, Error, JSON, Map, Number,
    crypto: {randomUUID: () => String(++number).padStart(32, '0')}, activatePage: () => {},
    clearTimeout: () => {}, setTimeout: (fn, delay) => { timers.push({fn, delay}); return timers.length; }});
  return {element, requests, timers, window};
}
async function opened() {
  const s = setup(); s.window.automaticTasks.open();
  s.requests[0].resolve(response(options())); s.requests[1].resolve(response({tasks: []})); await settle();
  s.element('autoTaskShop').value = '2:7'; s.element('autoTaskShop').listeners.change();
  s.element('autoTaskSite').value = 'US';
  return s;
}
const submit = s => s.element('autoTaskForm').listeners.submit({preventDefault() {}});

test('site selector gives a clear local prompt until a target shop is selected', async () => {
  const s = setup(); s.window.automaticTasks.open();
  s.requests[0].resolve(response(options())); s.requests[1].resolve(response({tasks: []})); await settle();
  assert.equal(s.element('autoTaskSite').value, '');
  assert.equal(s.element('autoTaskSite').disabled, true);
  assert.equal(s.element('autoTaskSite').options[0].textContent, '请先选择发布店铺');
  s.element('autoTaskShop').value = '2:7'; s.element('autoTaskShop').listeners.change();
  assert.equal(s.element('autoTaskSite').value, 'US');
  assert.equal(s.element('autoTaskSite').disabled, false);
});

test('explicit confirmation is required and only one shop with chosen count is submitted', async () => {
  const s = await opened(); await submit(s); assert.equal(s.requests.length, 2);
  s.element('autoTaskConfirm').checked = true;
  s.element('autoTaskSource').value = '1'; s.element('autoTaskPerson').value = '1:11';
  s.element('autoTaskCount').value = '23';
  const pending = submit(s), body = JSON.parse(s.requests[2].init.body);
  assert.equal(body.count, 23); assert.equal(body.targetAccountId, 2); assert.equal(body.shopId, 7);
  assert.equal(body.sourceAccountId, 1); assert.equal(body.personKey, '1:11');
  assert.equal(body.confirmation, 'AUTOMATIC_EDIT_AND_PUBLISH'); assert.equal(body.targets, undefined);
  await submit(s); assert.equal(s.requests.length, 3);
  s.requests[2].resolve(response({})); await settle();
  s.requests[3].resolve(response({tasks: []})); await pending;
  assert.equal(s.element('autoTaskConfirm').checked, false);
});

test('scheduled time is explicitly Beijing time and defaults to hourly interval', async () => {
  const s = await opened(); s.element('autoTaskMode').value = 'interval'; s.element('autoTaskMode').listeners.change();
  assert.equal(s.element('autoTaskTimeField').hidden, false);
  assert.equal(s.element('autoTaskEveryField').hidden, false);
  s.element('autoTaskTime').value = '2026-10-21T18:30'; s.element('autoTaskConfirm').checked = true;
  const pending = submit(s), body = JSON.parse(s.requests[2].init.body);
  assert.equal(body.startAt, Date.parse('2026-10-21T10:30:00Z') / 1000); assert.equal(body.everyMinutes, 60);
  s.requests[2].reject(Error('offline')); await pending;
  s.element('autoTaskMode').value = 'now'; s.element('autoTaskMode').listeners.change();
  assert.equal(s.element('autoTaskTime').disabled, true); assert.equal(s.element('autoTaskEvery').disabled, true);
});

test('failed create retains idempotency key; altered configuration generates a new key', async () => {
  const s = await opened(); s.element('autoTaskConfirm').checked = true;
  let p = submit(s); const first = JSON.parse(s.requests[2].init.body).requestId;
  s.requests[2].reject(Error('timeout')); await p;
  p = submit(s); assert.equal(JSON.parse(s.requests[3].init.body).requestId, first);
  s.requests[3].reject(Error('timeout')); await p;
  s.element('autoTaskCount').value = '12'; p = submit(s);
  assert.notEqual(JSON.parse(s.requests[4].init.body).requestId, first);
  s.requests[4].reject(Error('timeout')); await p;
});

test('source changes clear incompatible person and confirmation', async () => {
  const s = await opened(); s.element('autoTaskPerson').value = '2:11'; s.element('autoTaskConfirm').checked = true;
  s.element('autoTaskSource').value = '1'; s.element('autoTaskSource').listeners.change();
  assert.equal(s.element('autoTaskPerson').value, ''); assert.equal(s.element('autoTaskConfirm').checked, false);
  assert.deepEqual(s.element('autoTaskPerson').options.map(o => o.value), ['', 'unknown', '1:11']);
  s.element('autoTaskConfirm').checked = true;
  s.element('autoTaskForm').listeners.change({target: s.element('autoTaskCount')});
  assert.equal(s.element('autoTaskConfirm').checked, false);
});

test('configuration failure blocks submit and stale response cannot restore old options', async () => {
  const s = setup(); s.window.automaticTasks.open(); s.window.automaticTasks.open();
  s.requests[2].reject(Error('offline')); await settle();
  s.requests[0].resolve(response(options())); await settle();
  assert.equal(s.element('autoTaskSubmit').disabled, true);
  assert.match(s.element('autoTaskMessage').textContent, /读取失败/);
  s.requests[1].resolve(response({tasks: []})); s.requests[3].resolve(response({tasks: []})); await settle();
});

test('history polling is read-only, retains old records on failure and renders names as text', async () => {
  const s = await opened(); s.timers.at(-1).fn();
  s.requests[2].resolve(response({tasks: [{id: 'a', config: {targetAccountId: 2, shopId: 7, count: 2,
    sourceAccountId: 1, site: 'US', mode: 'once'}, runs: []}]})); await settle();
  assert.match(s.element('autoTaskList').children[0].children[0].children[0].textContent, /<img onerror=x>/);
  s.timers.at(-1).fn(); s.requests[3].reject(Error('offline')); await settle();
  assert.equal(s.element('autoTaskList').children.length, 1);
  assert.match(s.element('autoTaskListMessage').textContent, /读取失败/);
  assert.ok(s.requests.every(r => !r.init.method));
});
