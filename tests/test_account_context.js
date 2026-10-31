const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'account-context.js'), 'utf8');

function setup(search = '?collectionAccount=2') {
  const requests = [], navigations = [], alerts = [];
  const window = {location: {search, href: 'http://127.0.0.1:8787/' + search, origin: 'http://127.0.0.1:8787',
    assign: url => navigations.push(url)}, alert: message => alerts.push(message), confirm: () => true,
    fetch: async (url, options) => {requests.push({url, options}); return {ok: true};}};
  const document = {querySelector: () => null};
  vm.runInNewContext(source, {window, document, URL, URLSearchParams, Headers});
  return {window, document, requests, navigations, alerts};
}

test('every collection API uses fixed account, including edits, AI and tasks', async () => {
  const {window, requests} = setup();
  for (const url of ['/api/health', '/api/shops', '/api/item', '/api/categories', '/api/automation/pool', '/api/automation/jobs', '/api/collection-sync', '/api/collection-sync/settings'])
    await window.fetch(url);
  await window.fetch('/api/item/save', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
  assert.ok(requests.every(request => request.options.headers.get('X-Miaoshou-Account-Id') === '2'));
  assert.equal(requests.at(-1).options.headers.get('Content-Type'), 'application/json');
});

test('order and registry contexts remain explicit; no account header sent externally', async () => {
  const {window, requests} = setup();
  for (const url of ['/api/orders?accountId=1', '/api/orders/sync', '/api/miaoshou-accounts', '/api/miaoshou-accounts/manage', '/api/settings/deepseek', '/api/automation/shared/pool', '/api/automation/shared/run', 'https://example.com/api/shops'])
    await window.fetch(url);
  assert.ok(requests.every(request => !request.options.headers.has('X-Miaoshou-Account-Id')));
});

test('shared target editor navigation carries the explicit job and drops old navigation state', () => {
  const {window, navigations} = setup('?collectionAccount=2&sharedEditJob=old');
  window.collectionAccountContext.switchTo(1, {sharedEditJob:'new-target-job'});
  assert.equal(navigations[0], 'http://127.0.0.1:8787/?collectionAccount=1&sharedEditJob=new-target-job');
});

test('global performance reads never inherit the selected collection header', async () => {
  for (const selected of [1,2,7]) {
    const {window,requests}=setup('?collectionAccount='+selected);
    await window.fetch('/api/performance');
    assert.equal(requests[0].options.headers.has('X-Miaoshou-Account-Id'),false);
    await window.fetch('/api/performance?accountId=4');
    assert.equal(requests[1].options.headers.has('X-Miaoshou-Account-Id'),false);
    // Explicit diagnostic scoping still remains available to its caller.
    await window.fetch('/api/performance',{headers:{'X-Miaoshou-Account-Id':'4'}});
    assert.equal(requests[2].options.headers.get('X-Miaoshou-Account-Id'),'4');
  }
});

test('switch reloads document rather than reusing old selections or async state', () => {
  const {window, navigations} = setup();
  window.collectionAccountContext.switchTo(3);
  assert.equal(navigations[0], 'http://127.0.0.1:8787/?collectionAccount=3');
  assert.equal(window.collectionAccountContext.id, 2);
});

test('unsaved editor can cancel switching', () => {
  const {window, document, navigations} = setup();
  document.querySelector = () => ({classList: {contains: () => false}});
  window.confirm = () => false;
  window.collectionAccountContext.switchTo(1);
  assert.equal(navigations.length, 0);
});

test('invalid account URL never selects an arbitrary account', () => {
  for (const search of ['', '?collectionAccount=0', '?collectionAccount=99', '?collectionAccount=NaN'])
    assert.equal(setup(search).window.collectionAccountContext.id, 1);
});
