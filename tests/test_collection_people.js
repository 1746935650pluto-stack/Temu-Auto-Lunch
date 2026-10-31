const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function setup() {
  const element = () => ({children: [], value: '', textContent: '', classList: {add() {}, remove() {}}, focus() {},
    append(...nodes) {this.children.push(...nodes);}, replaceChildren() {this.children = [];},
    setAttribute(key, value) {this[key] = value;}, addEventListener(key, callback) {this[key] = callback;}});
  const nodes = new Map();
  const $ = selector => {if (!nodes.has(selector)) nodes.set(selector, element()); return nodes.get(selector);};
  const state = {mode: 'live', page: 1, products: [], poolSelected: new Map(), people: new Map(), collectorId: '',
    collectorAccountId: 1, searchText: '', productRequest: 0, personnelRequest: 0, pageSummaryRequest: 0,
    aliasRevision: 0, aliasSaving: false, aliasDialog: null};
  const timers = [];
  const ctx = vm.createContext({$, URLSearchParams, liveState: state, activeMiaoshouCollectionAccountId: 1,
    document: {createElement: element}, window: {clearTimeout() {}, setTimeout(fn) {timers.push(fn); return timers.length;}},
    setLiveError: (node, message) => {node.replaceChildren(); node.textContent = message;}, selectLiveProduct() {}, showToast() {}});
  vm.runInContext(source.slice(source.indexOf('function liveCollectorId('), source.indexOf('async function loadLiveRecords(')), ctx);
  const loadPageSummary = ctx.loadLivePageSummary;
  ctx.loadLivePageSummary = () => {};
  return {ctx, state, $, timers, loadPageSummary};
}
const row = (id, person) => ({collectBoxDetailId: id, subAppAccountId: person, title: `item-${id}`});
const result = (rows, people = []) => ({accountId: 1, data: {detailList: rows}, people, pageSize: 50});

test('page size defaults to 50, restores a scalar preference and tolerates blocked storage', () => {
  const {ctx, state, $} = setup();
  assert.equal(ctx.readLivePageSize(), 50);
  ctx.window.localStorage = {getItem: () => '100'};
  assert.equal(ctx.readLivePageSize(), 100);
  ctx.window.localStorage.getItem = () => '500';
  assert.equal(ctx.readLivePageSize(), 50);
  ctx.window.localStorage.getItem = () => {throw Error('blocked');};
  assert.equal(ctx.readLivePageSize(), 50);
  state.pageSize = 100; state.products = Array.from({length: 50}, (_, i) => row(i + 1, '11'));
  ctx.renderLivePageTools();
  assert.equal($('#livePageSize').value, '100'); assert.equal($('#liveNext').disabled, true);
  state.products = Array.from({length: 100}, (_, i) => row(i + 1, '11'));
  ctx.renderLivePageTools(); assert.equal($('#liveNext').disabled, false);
});

test('changing page size retains selection and submitted filters but resets page and request totals', async () => {
  const {ctx, state, $} = setup(); ctx.loadLivePersonnel = () => {};
  state.page = 9; state.pageSize = 20; state.searchText = '帽子'; state.collectorId = '11';
  state.poolSelected.set(123, row(123, '11')); state.pageSummary = {status: 'ready', totalPages: 20};
  const saved = [], urls = [];
  ctx.window.localStorage = {setItem: (...args) => saved.push(args)};
  ctx.apiGet = async url => {urls.push(url); return {...result([row(1, '11')]), pageSize: 100,
    filter: {status: 'ready', totalItems: 101, totalPages: 2}};};
  await ctx.changeLivePageSize({target: {value: '100'}});
  const params = new URLSearchParams(urls[0].split('?')[1]);
  assert.equal(params.get('pageSize'), '100'); assert.equal(params.get('page'), '1');
  assert.equal(params.get('titleKeyword'), '帽子'); assert.equal(params.get('collectorId'), '11');
  assert.equal(state.poolSelected.size, 1); assert.equal(state.pageSummary.totalPages, 2);
  assert.equal(saved.length, 1); assert.equal(saved[0][1], '100');
  assert.equal($('#livePageSize').value, '100');
});

test('rapid size switches discard stale products and stale summaries', async () => {
  const {ctx, state, $, loadPageSummary} = setup(); ctx.loadLivePersonnel = () => {};
  const pending = []; ctx.apiGet = url => new Promise(resolve => pending.push({url, resolve}));
  state.pageSize = 20;
  const oldProducts = ctx.loadLiveProducts();
  const oldSummary = loadPageSummary();
  const newProducts = ctx.changeLivePageSize({target: {value: '100'}});
  pending[2].resolve({...result([row(100, '11')]), pageSize: 100}); await newProducts;
  pending[0].resolve({...result([row(20, '11')]), pageSize: 20}); await oldProducts;
  pending[1].resolve({status: 'ready', totalItems: 123, totalPages: 7, pageSize: 20}); await oldSummary;
  assert.equal(state.products[0].collectBoxDetailId, 100); assert.equal(state.pageSummary, null);
  assert.match(pending[1].url, /pageSize=20/);
  const summary = loadPageSummary();
  assert.match(pending[3].url, /pageSize=100/);
  pending[3].resolve({status: 'ready', totalItems: 123, totalPages: 2, pageSize: 100}); await summary;
  assert.match($('#livePageLabel').textContent, /1 \/ 2 页/);
});

test('invalid size and transfer-in-progress cannot change pagination or submit writes', async () => {
  const {ctx, state} = setup(); state.pageSize = 50; state.page = 3;
  ctx.apiGet = () => {throw Error('must not request');};
  for (const value of ['0', '500', 'no']) {
    const target = {value}; await ctx.changeLivePageSize({target}); assert.equal(target.value, '50');
  }
  state.poolTransferring = true;
  const target = {value: '100'}; await ctx.changeLivePageSize({target});
  assert.equal(target.value, '50'); assert.equal(state.page, 3); assert.equal(state.pageSize, 50);
});

test('mismatched size response is rejected instead of mixing page layouts', async () => {
  const {ctx, state, $} = setup(); state.pageSize = 100;
  ctx.apiGet = async () => ({...result([row(1, '11')]), pageSize: 20});
  await ctx.loadLiveProducts();
  assert.equal(state.products.length, 0); assert.match($('#liveProducts').textContent, /分页数量不一致/);
});

test('busy legacy backend keeps 20-row semantics until safe restart without overwriting preference', async () => {
  const {ctx, state, $} = setup(); ctx.loadLivePersonnel = () => {};
  state.pageSize = 100;
  ctx.window.localStorage = {setItem() {throw Error('must retain the preferred size');}};
  ctx.apiGet = async () => ({accountId: 1, data: {detailList: Array.from({length: 20}, (_, i) => row(i + 1, '11'))}});
  await ctx.loadLiveProducts();
  assert.equal(state.pageSize, 20); assert.equal(state.products.length, 20);
  assert.equal($('#livePageSize').disabled, true); assert.equal($('#livePageSize').value, '20');
  assert.match($('#liveCacheStatus').textContent, /安全重启/);
  assert.equal($('#liveNext').disabled, false);
  const target = {value: '100'};
  await ctx.changeLivePageSize({target}); assert.equal(target.value, '20');
});

test('optional shop failure does not erase loaded product rows', async () => {
  const {ctx, $} = setup();
  const notices = [];
  ctx.showToast = message => notices.push(message);
  ctx.appendOption = () => {};
  $('#liveSite').options = [];
  ctx.loadLiveProducts = async () => { $('#liveProducts').textContent = 'cached product'; };
  ctx.apiGet = async url => {
    if (url.startsWith('/api/shops')) throw Error('shop unavailable');
    return {data: {publishSiteOptions: []}};
  };
  vm.runInContext(source.slice(source.indexOf('async function loadLiveCatalog('), source.indexOf('async function syncCollectionInformation(')), ctx);
  const errors = await ctx.loadLiveCatalog();
  assert.equal($('#liveProducts').textContent, 'cached product');
  assert.match(notices[0], /shop unavailable/);
  assert.equal(errors[0], 'shop unavailable');
});

test('background page check preserves cached rows while loading and after failure', async () => {
  const {ctx, state, $} = setup();
  ctx.loadLivePersonnel = () => {};
  ctx.apiGet = async () => ({...result([row(1, '11')]), cache: {cached: true, refreshing: true}});
  await ctx.loadLiveProducts();
  const previousRows = $('#liveProducts').children;
  let resolve;
  ctx.apiGet = () => new Promise(done => {resolve = done;});
  const pending = ctx.loadLiveProducts(true);
  assert.equal($('#liveProducts').children, previousRows);
  assert.equal(state.products[0].collectBoxDetailId, 1);
  assert.equal(state.productsLoading, false);
  resolve({...result([row(2, '11')]), cache: {cached: true, refreshing: false, added: 1, updated: 0}});
  await pending;
  assert.equal(state.products[0].collectBoxDetailId, 2);
  ctx.apiGet = async () => {throw Error('offline');};
  await ctx.loadLiveProducts(true);
  assert.equal(state.products[0].collectBoxDetailId, 2);
  assert.match($('#liveCacheStatus').textContent, /已保留本机缓存/);
});

test('same-name people have distinct ID options and safe text labels; absent ID is explicit', () => {
  const {ctx, state, $} = setup();
  state.products = [row(1, '11'), row(2, '22'), row(3, '')];
  ctx.mergeLivePeople([{id: '11', name: '同名'}, {id: '22', name: '同名'}]);
  ctx.renderLiveProductRows();
  assert.deepEqual($('#liveCollectorFilter').children.map(option => option.value), ['', '11', '22']);
  const owners = $('#liveProducts').children.map(node => node.children[1].children[1].children[2].textContent);
  assert.match(owners[0], /同名.*11/);
  assert.match(owners[1], /同名.*22/);
  assert.match(owners[2], /未返回所属人员/);
  state.people.set('11', {name: '<img onerror=alert(1)>'});
  ctx.renderLiveProductRows();
  assert.match($('#liveProducts').children[0].children[1].children[1].children[2].textContent, /<img/);
  assert.equal(ctx.liveCollectorId('9007199254740993'), '9007199254740993');
  assert.equal(ctx.liveCollectorId(9007199254740993), '');
  assert.equal(ctx.liveCollectorId(true), '');
});

test('quick page and search requests discard old responses and old errors', async () => {
  const {ctx, state, $} = setup();
  ctx.loadLivePersonnel = () => {};
  const pending = [];
  ctx.apiGet = url => new Promise((resolve, reject) => pending.push({url, resolve, reject}));
  const first = ctx.loadLiveProducts();
  state.page = 2;
  const second = ctx.loadLiveProducts();
  pending[1].resolve(result([row(2, '22')])); await second;
  pending[0].resolve(result([row(1, '11')])); await first;
  assert.equal(state.products[0].collectBoxDetailId, 2);
  const third = ctx.loadLiveProducts();
  $('#liveKeyword').value = '123'; state.page = 1;
  const fourth = ctx.submitLiveSearch();
  pending[3].resolve(result([row(123, '11')])); await fourth;
  pending[2].reject(Error('stale error')); await third;
  assert.equal(state.products[0].collectBoxDetailId, 123);
  assert.match(pending[3].url, /collectBoxDetailId=123/);
  assert.doesNotMatch($('#liveProducts').textContent, /stale error/);
});

test('switching collection accounts ignores late products and personnel', async () => {
  const {ctx, state, $} = setup();
  const pending = [];
  ctx.apiGet = url => new Promise(resolve => pending.push({url, resolve}));
  const products = ctx.loadLiveProducts();
  const people = ctx.loadLivePersonnel();
  ctx.activeMiaoshouCollectionAccountId = 2;
  ctx.resetLiveCollectorAccount(2);
  pending[0].resolve(result([row(1, '11')]));
  pending[1].resolve({accountId: 1, people: [{id: '11', name: 'wrong-account'}]});
  await Promise.all([products, people]);
  assert.equal(state.products.length, 0);
  assert.equal(state.people.size, 0);
  assert.equal($('#liveProducts').children.length, 0);
  assert.equal($('#liveCollectorFilter').children.length, 1);
  assert.equal(state.collectorId, '');
});

test('global filter waits for complete results then uses filtered pagination and search', async () => {
  const {ctx, state, $, timers} = setup();
  state.collectorId = '11'; $('#liveKeyword').value = '123';
  ctx.loadLivePersonnel = () => {};
  const urls = [];
  const responses = [
    {...result([]), filter: {status: 'loading', scanned: 500}},
    {...result([row(123, '11')]), filter: {status: 'ready', totalItems: 1, totalPages: 1}},
  ];
  ctx.apiGet = async url => {urls.push(url); return responses.shift();};
  await ctx.submitLiveSearch();
  assert.equal(state.productsLoading, true);
  assert.match($('#liveProducts').children[0].textContent, /已检查 500/);
  assert.equal($('#livePoolSelectAll').disabled, true);
  timers[0]();
  assert.match($('#liveProducts').children[0].textContent, /已检查 500/);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(state.productsLoading, false);
  assert.equal(state.products[0].collectBoxDetailId, 123);
  assert.equal(state.pageSummary.totalItems, 1);
  assert.equal($('#liveNext').disabled, true);
  assert.match(urls[1], /collectorId=11/);
  assert.match(urls[1], /collectBoxDetailId=123/);
});

test('missing names still show IDs and failed name requests keep filtering available', async () => {
  const {ctx, state, $} = setup();
  state.products = [row(1, '11')]; ctx.mergeLivePeople([]); ctx.renderLiveProductRows();
  ctx.apiGet = async () => {throw Error('no permission');};
  await ctx.loadLivePersonnel();
  assert.match($('#liveProducts').children[0].children[1].children[1].children[2].textContent, /未匹配人员.*11/);
  assert.equal($('#liveCollectorFilter').children[1].value, '11');
  assert.match($('#liveCollectorStatus').textContent, /仍可按账号 ID 筛选/);
});

test('mismatched account and mismatched filter results fail closed', async () => {
  const {ctx, state, $} = setup();
  ctx.loadLivePersonnel = () => {};
  ctx.apiGet = async () => ({...result([row(1, '11')]), accountId: 2});
  await ctx.loadLiveProducts();
  assert.equal(state.products.length, 0);
  state.collectorId = '11';
  ctx.apiGet = async () => ({...result([row(2, '22')]), filter: {status: 'ready', totalItems: 1, totalPages: 1}});
  await ctx.loadLiveProducts();
  assert.equal(state.products.length, 0);
  assert.match($('#liveProducts').textContent, /筛选结果不一致/);
});

test('name search spans pages and combines with personnel while keeping the submitted keyword', async () => {
  const {ctx, state, $} = setup(); ctx.loadLivePersonnel = () => {};
  state.collectorId = '11'; state.page = 5; $('#liveKeyword').value = '  手套 Purple  ';
  const urls = [];
  ctx.apiGet = async url => {
    urls.push(url);
    return {...result([{...row(1, '11'), title: '冬季手套 Purple'}]),
      filter: {status: 'ready', totalItems: 25, totalPages: 2}};
  };
  await ctx.submitLiveSearch();
  let params = new URLSearchParams(urls[0].split('?')[1]);
  assert.equal(params.get('titleKeyword'), '手套 Purple'); assert.equal(params.has('collectBoxDetailId'), false);
  assert.equal(params.get('collectorId'), '11'); assert.equal(params.get('page'), '1');
  assert.equal(state.pageSummary.totalItems, 25); assert.equal($('#liveNext').disabled, false);
  $('#liveKeyword').value = '未提交的新关键词'; state.page = 2;
  await ctx.loadLiveProducts();
  params = new URLSearchParams(urls[1].split('?')[1]);
  assert.equal(params.get('titleKeyword'), '手套 Purple'); assert.equal(params.get('page'), '2');
  assert.equal($('#liveNext').disabled, true);
});

test('name-only search uses full filter pagination; clearing restores listing and absent results are explicit', async () => {
  const {ctx, state, $} = setup(); ctx.loadLivePersonnel = () => {};
  const urls = [];
  ctx.apiGet = async url => { urls.push(url); return {...result([]), filter: {status: 'ready', totalItems: 0, totalPages: 0}}; };
  $('#liveKeyword').value = '帽子'; await ctx.submitLiveSearch();
  assert.equal(state.pageSummary.totalItems, 0);
  assert.match($('#liveProducts').children[0].textContent, /没有符合/);
  ctx.apiGet = async url => {urls.push(url); return result([row(1, '11')]);};
  $('#liveKeyword').value = '   '; await ctx.submitLiveSearch();
  assert.equal(state.searchText, ''); assert.equal(state.pageSummary, null);
  assert.equal(new URLSearchParams(urls[1].split('?')[1]).has('titleKeyword'), false);
  assert.equal(state.products.length, 1);
});

test('rapid name changes and switching accounts discard old results and query', async () => {
  const {ctx, state, $} = setup(); ctx.loadLivePersonnel = () => {};
  const pending = []; ctx.apiGet = () => new Promise(resolve => pending.push(resolve));
  $('#liveKeyword').value = '手套'; const old = ctx.submitLiveSearch();
  $('#liveKeyword').value = '帽子'; const recent = ctx.submitLiveSearch();
  const response = title => ({...result([{...row(1, '11'), title}]), filter: {status: 'ready', totalItems: 1, totalPages: 1}});
  pending[1](response('帽子')); await recent; pending[0](response('手套')); await old;
  assert.equal(state.products[0].title, '帽子');
  const last = ctx.loadLiveProducts();
  ctx.activeMiaoshouCollectionAccountId = 2; ctx.resetLiveCollectorAccount(2);
  pending[2](response('帽子')); await last;
  assert.equal(state.products.length, 0); assert.equal(state.searchText, ''); assert.equal($('#liveKeyword').value, '');
});

test('name search rejects legacy unfiltered responses and reports scan failures', async () => {
  const {ctx, state, $} = setup(); ctx.loadLivePersonnel = () => {};
  $('#liveKeyword').value = '手套'; ctx.apiGet = async () => result([row(1, '11')]);
  await ctx.submitLiveSearch();
  assert.equal(state.products.length, 0); assert.match($('#liveProducts').textContent, /完整采集箱筛选结果/);
  ctx.apiGet = async () => ({...result([]), filter: {status: 'failed', message: '读取失败，请刷新人员'}});
  await ctx.submitLiveSearch(); assert.match($('#liveProducts').textContent, /读取失败/);
  $('#liveKeyword').value = 'x'.repeat(201); ctx.apiGet = async () => {throw Error('must not query');};
  await ctx.submitLiveSearch(); assert.equal(state.searchText, '手套');
});

function mockAliasSave(ctx, people, calls = []) {
  ctx.fetch = async (url, options) => {
    calls.push({url, body: JSON.parse(options.body)});
    return {ok: true, json: async () => ({data: {accountId: 1, people}})};
  };
}

test('batch alias save updates rows and options without changing filters, page or selections', async () => {
  const {ctx, state, $} = setup();
  state.products = [row(1, '11'), row(2, '22')]; state.page = 3; state.collectorId = '11';
  state.poolSelected.set(1, state.products[0]);
  ctx.mergeLivePeople([{id: '11', name: '妙手姓名', account: 'login'}, {id: '22'}]);
  ctx.openPersonnelAliases();
  assert.equal($('#personnelAliasRows').children[0].children[1].textContent, '妙手姓名（login）');
  state.aliasDialog.rows[0].input.value = '  小王  ';
  state.aliasDialog.rows[1].input.value = '小王';
  const calls = [];
  mockAliasSave(ctx, [{id: '11', name: '妙手姓名', account: 'login', alias: '小王'}, {id: '22', alias: '小王'}], calls);
  await ctx.savePersonnelAliases();
  assert.deepEqual(calls, [{url: '/api/collection-personnel/aliases', body: {accountId: 1,
    aliases: [{id: '11', alias: '小王'}, {id: '22', alias: '小王'}]}}]);
  assert.equal(state.aliasDialog, null); assert.equal(state.aliasSaving, false);
  assert.equal(state.page, 3); assert.equal(state.collectorId, '11'); assert.equal(state.poolSelected.size, 1);
  const owners = $('#liveProducts').children.map(node => node.children[1].children[1].children[2].textContent);
  assert.match(owners[0], /小王.*11/); assert.match(owners[1], /小王.*22/);
  assert.equal($('#liveCollectorFilter').value, '11');
});

test('clearing alias restores automatic name or unmatched; unchanged rows are not sent', async () => {
  const {ctx, state} = setup();
  ctx.mergeLivePeople([{id: '11', name: '自动名', alias: '备注'}, {id: '22', alias: '备注'}]);
  ctx.openPersonnelAliases(); state.aliasDialog.rows[0].input.value = '';
  const calls = [];
  mockAliasSave(ctx, [{id: '11', name: '自动名', alias: ''}, {id: '22', alias: '备注'}], calls);
  await ctx.savePersonnelAliases();
  assert.equal(calls[0].body.aliases.length, 1); assert.equal(calls[0].body.aliases[0].alias, '');
  assert.match(ctx.liveCollectorLabel('11'), /自动名/);
  ctx.mergeLivePeople([{id: '22', alias: ''}]); assert.match(ctx.liveCollectorLabel('22'), /未匹配人员/);
  ctx.openPersonnelAliases(); await ctx.savePersonnelAliases(); assert.equal(calls.length, 1);
});

test('save failure retains draft; retry succeeds; duplicate submits are ignored', async () => {
  const {ctx, state, $} = setup();
  ctx.mergeLivePeople([{id: '11', alias: '原备注'}]); ctx.openPersonnelAliases();
  state.aliasDialog.rows[0].input.value = '新备注';
  let resolve, calls = 0;
  ctx.fetch = () => {calls++; return new Promise(done => {resolve = done;});};
  const saving = ctx.savePersonnelAliases(); await ctx.savePersonnelAliases();
  assert.equal(calls, 1); assert.equal($('#personnelAliasSave').disabled, true);
  ctx.closePersonnelAliases(); assert.notEqual(state.aliasDialog, null);
  resolve({ok: false, json: async () => ({error: '数据库忙'})}); await saving;
  assert.equal(state.people.get('11').alias, '原备注');
  assert.equal(state.aliasDialog.rows[0].input.value, '新备注');
  assert.match($('#personnelAliasStatus').textContent, /数据库忙.*保留/);
  assert.equal($('#personnelAliasSave').disabled, false);
  mockAliasSave(ctx, [{id: '11', alias: '新备注'}]); await ctx.savePersonnelAliases();
  assert.equal(state.people.get('11').alias, '新备注');
});

test('old personnel and product reads cannot overwrite a saved alias, including reads during save', async () => {
  const {ctx, state} = setup();
  ctx.mergeLivePeople([{id: '11', alias: '旧备注'}]);
  const pending = [];
  ctx.apiGet = () => new Promise(resolve => pending.push(resolve));
  const people = ctx.loadLivePersonnel();
  ctx.openPersonnelAliases(); state.aliasDialog.rows[0].input.value = '新备注';
  let saveResolve;
  ctx.fetch = () => new Promise(resolve => {saveResolve = resolve;});
  const save = ctx.savePersonnelAliases();
  const products = ctx.loadLiveProducts(); // Starts during save.
  saveResolve({ok: true, json: async () => ({data: {accountId: 1, people: [{id: '11', alias: '新备注'}]}})});
  await save;
  pending[0]({accountId: 1, people: [{id: '11', alias: '旧备注'}]}); await people;
  ctx.loadLivePersonnel = () => {};
  pending[1](result([row(1, '11')], [{id: '11', alias: '旧备注'}])); await products;
  assert.equal(state.people.get('11').alias, '新备注'); assert.equal(state.products.length, 1);
});

test('account switch discards draft and ignores a late save even after switching back', async () => {
  const {ctx, state, $} = setup();
  ctx.mergeLivePeople([{id: '11'}]); ctx.openPersonnelAliases();
  state.aliasDialog.rows[0].input.value = '旧账号备注';
  let resolve;
  ctx.fetch = () => new Promise(done => {resolve = done;});
  const saving = ctx.savePersonnelAliases();
  ctx.activeMiaoshouCollectionAccountId = 2; ctx.resetLiveCollectorAccount(2);
  assert.equal(state.aliasDialog, null); assert.equal($('#personnelAliasRows').children.length, 0);
  ctx.activeMiaoshouCollectionAccountId = 1; ctx.resetLiveCollectorAccount(1);
  ctx.mergeLivePeople([{id: '22', alias: '当前备注'}]); ctx.openPersonnelAliases();
  resolve({ok: true, json: async () => ({data: {accountId: 1, people: [{id: '11', alias: '旧账号备注'}]}})});
  await saving;
  assert.equal(state.people.has('11'), false); assert.equal(state.aliasDialog.rows[0].id, '22');
  assert.equal(state.people.get('22').alias, '当前备注');
});

test('aliases are safe text and validation, empty state and cancel do not write', async () => {
  const {ctx, state, $} = setup();
  ctx.fetch = () => {throw Error('must not write');};
  ctx.openPersonnelAliases(); assert.equal($('#personnelAliasSave').disabled, true);
  assert.match($('#personnelAliasStatus').textContent, /刷新人员/); ctx.closePersonnelAliases();
  ctx.mergeLivePeople([{id: '11', alias: '<img onerror=alert(1)>'}]); state.products = [row(1, '11')];
  ctx.renderLiveProductRows();
  assert.match($('#liveProducts').children[0].children[1].children[1].children[2].textContent, /<img onerror/);
  ctx.openPersonnelAliases(); state.aliasDialog.rows[0].input.value = 'x'.repeat(81);
  await ctx.savePersonnelAliases(); assert.match($('#personnelAliasStatus').textContent, /80/);
  ctx.closePersonnelAliases(); assert.equal(state.people.get('11').alias, '<img onerror=alert(1)>');
});
