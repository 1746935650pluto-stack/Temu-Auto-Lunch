const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function setup() {
  const element = () => ({children: [], dataset: {}, attrs: {}, hidden: false, value: '',
    append(...nodes) { this.children.push(...nodes); }, replaceChildren() { this.children = []; },
    setAttribute(k, v) { this.attrs[k] = v; }, addEventListener(k, fn) { this[k] = fn; }});
  const nav = element(); nav.id = 'miaoshouCollectionNav';
  const all = () => { const result = []; const visit = node => { result.push(node); (node.children || []).forEach(visit); }; visit(nav); return result; };
  const fields = new Map();
  const $ = selector => {
    if (selector.includes('[data-account-id="1"]')) return all().find(node => node.dataset?.accountId === '1');
    const found = all().find(node => '#' + node.id === selector);
    if (found) return found;
    if (!fields.has(selector)) fields.set(selector, element());
    return fields.get(selector);
  };
  const pages = [];
  const accounts = [1, 2, 3, 4].map(id => ({id, name: id === 1 ? '妙手采集箱' : `妙手采集箱 ${id}`, deletable: id > 1}));
  const messages = [], requests = [];
  const context = vm.createContext({$, $$: () => [], document: {createElement: element, createTextNode: text => ({text})},
    miaoshouCollectionAccounts: accounts, activeMiaoshouCollectionAccountId: 1, miaoshouCollectionExpanded: false,
    collectionAccountMutationPending: false, collectionAccountRevision: 0, window: {confirm: () => true, prompt: () => null},
    showToast: message => messages.push(message), fetch: async (url, options) => {
      requests.push({url, ...options}); return {ok: true, json: async () => ({data: {id: 5, accounts: [...accounts, {id: 5, name: '妙手采集箱 5', deletable: true}]}})};
    },
    resetLiveCollectorAccount: () => {}, liveState: {mode: 'demo'},
    renderOrderAccounts: () => {}, activatePage: page => pages.push(page), apiGet: async () => ({accounts})});
  vm.runInContext(source.slice(source.indexOf('function accountById('), source.indexOf('function organizeLiveModules(')), context);
  context.renderMiaoshouCollectionAccounts(accounts);
  return {context, nav, $, accounts, pages, messages, requests};
}

test('sidebar numbers stay consecutive without changing account navigation IDs', () => {
  const {context, $, accounts} = setup();
  context.renderMiaoshouCollectionAccounts(accounts.filter(account => account.id !== 3));
  const buttons = $('#miaoshouCollectionChildren').children.filter(node => node.dataset.accountId);
  assert.deepEqual(buttons.map(node => node.children[0].textContent), ['1', '2', '3']);
  assert.deepEqual(buttons.map(node => node.dataset.accountId), ['1', '2', '4']);
  buttons[2].click();
  assert.equal(context.activeMiaoshouCollectionAccountId, 4);
});

test('credential setup saves the captured account and clears inputs without starting sync or publishing', async () => {
  const {context, $, accounts, requests} = setup();
  context.openMiaoshouCollectionAccount(3);
  $('#collectionAppKey').value = 'fixture-key';
  $('#collectionAppSecret').value = 'fixture-secret';
  context.fetch = async (url, options) => {
    requests.push({url, ...options});
    assert.equal($('#saveCollectionCredentials').disabled, true);
    return {ok: true, json: async () => ({data: {id: 3, configured: true,
      accounts: accounts.map(a => a.id === 3 ? {...a, configured: true, deletable: false} : a)}})};
  };
  let prevented = false;
  await context.saveCollectionCredentials({preventDefault() { prevented = true; }});
  assert.ok(prevented);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, '/api/miaoshou-accounts/3/credentials');
  assert.deepEqual(JSON.parse(requests[0].body), {appKey: 'fixture-key', appSecret: 'fixture-secret', confirmation: 'SAVE_COLLECTION_API_CREDENTIALS'});
  assert.equal($('#collectionAppKey').value, '');
  assert.equal($('#collectionAppSecret').value, '');
  assert.equal($('#collectionCredentialForm').hidden, true);
  assert.match($('#collectionCredentialStatus').textContent, /保存成功/);
  assert.match($('#collectionAccountCardDescription').textContent, /尚未验证连接/);
  assert.equal(context.collectionAccountMutationPending, false);
});

test('failed credentials save clears secrets and preserves account state', async () => {
  const {context, $} = setup();
  context.openMiaoshouCollectionAccount(3);
  $('#collectionAppKey').value = 'fixture-key';
  $('#collectionAppSecret').value = 'fixture-secret';
  context.fetch = async () => ({ok: false, json: async () => ({error: '本机配置保存失败'})});
  await context.saveCollectionCredentials();
  assert.match($('#collectionCredentialStatus').textContent, /保存失败/);
  assert.equal($('#collectionAppSecret').value, '');
  assert.notEqual(context.accountById(3).configured, true);
  assert.equal($('#saveCollectionCredentials').disabled, false);
});

test('credential submit validates blanks, prevents duplicates and ignores stale account reads', async () => {
  const {context, $, accounts, requests} = setup();
  context.openMiaoshouCollectionAccount(3);
  await context.saveCollectionCredentials();
  assert.equal(requests.length, 0);
  assert.match($('#collectionCredentialStatus').textContent, /请同时填写/);
  let finishSave, finishRead;
  context.apiGet = () => new Promise(resolve => { finishRead = resolve; });
  const oldRead = context.loadMiaoshouCollectionAccounts();
  context.fetch = (url, options) => { requests.push({url, ...options}); return new Promise(resolve => { finishSave = resolve; }); };
  $('#collectionAppKey').value = 'fixture-key';
  $('#collectionAppSecret').value = 'fixture-secret';
  const saving = context.saveCollectionCredentials();
  await context.saveCollectionCredentials();
  assert.equal(requests.length, 1);
  finishSave({ok: true, json: async () => ({data: {id: 3, configured: true,
    accounts: accounts.map(a => a.id === 3 ? {...a, configured: true, deletable: false} : a)}})});
  await saving;
  finishRead({accounts});
  await oldRead;
  assert.equal(context.accountById(3).configured, true);
});

test('account switches clear credential fields and form never uses browser storage', () => {
  const {context, $} = setup();
  context.openMiaoshouCollectionAccount(3);
  $('#collectionAppSecret').value = 'fixture-secret';
  context.openMiaoshouCollectionAccount(4);
  assert.equal($('#collectionAppSecret').value, '');
  const credentialCode = source.slice(source.indexOf('function clearCollectionCredentialInputs('), source.indexOf('async function mutateEmptyCollection('));
  assert.doesNotMatch(credentialCode, /localStorage|sessionStorage|console\./);
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  assert.match(html, /id="collectionAppSecret" type="password" autocomplete="new-password"/);
  assert.match(source, /pagehide', clearCollectionCredentialInputs/);
});

test('all accounts are children and parent only expands without switching pages', () => {
  const {nav, $, pages} = setup();
  const parent = nav.children[0], children = $('#miaoshouCollectionChildren');
  assert.equal(nav.children.length, 2);
  assert.deepEqual(children.children.filter(node => node.dataset.accountId).map(node => node.dataset.accountId), ['1', '2', '3', '4']);
  assert.equal(parent.dataset.accountId, undefined);
  assert.equal(parent.dataset.page, undefined);
  assert.equal(children.children[0].children[1].text, '妙手采集箱 1');
  assert.equal(children.hidden, true);
  assert.equal(parent.attrs['aria-expanded'], 'false');
  parent.click();
  assert.equal(children.hidden, false);
  assert.equal(parent.attrs['aria-expanded'], 'true');
  assert.equal(pages.length, 0);
  parent.click();
  assert.equal(children.hidden, true);
  children.children[0].click();
  assert.equal(pages.at(-1), 'workbench');
  assert.equal(children.hidden, false);
});

test('child navigation stays expanded and account reload preserves expansion', async () => {
  const {context, nav, $, pages} = setup();
  nav.children[0].click();
  $('#miaoshouCollectionChildren').children[2].click();
  assert.equal(context.activeMiaoshouCollectionAccountId, 3);
  assert.equal(pages.at(-1), 'collectionAccount');
  assert.equal($('#collectionAccountTitle').textContent, '妙手采集箱 3');
  assert.equal($('#miaoshouCollectionChildren').hidden, false);
  await context.loadMiaoshouCollectionAccounts();
  assert.equal($('#miaoshouCollectionChildren').hidden, false);
  nav.children[0].click();
  assert.equal(context.activeMiaoshouCollectionAccountId, 3);
  assert.equal(pages.at(-1), 'collectionAccount');
  await context.loadMiaoshouCollectionAccounts();
  assert.equal($('#miaoshouCollectionChildren').hidden, true);
});

test('API failure retains usable nested fallback navigation', async () => {
  const {context, nav, $} = setup();
  context.apiGet = async () => { throw Error('offline'); };
  await context.loadMiaoshouCollectionAccounts();
  nav.children[0].click();
  assert.equal($('#miaoshouCollectionChildren').hidden, false);
  assert.equal($('#miaoshouCollectionChildren').children.filter(node => node.dataset.accountId).length, 4);
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  assert.match(html, /id="miaoshouCollectionChildren"[^>]*hidden/);
});

test('add creates a child and guards repeated clicks until completion', async () => {
  const {context, $, requests} = setup();
  const first = context.mutateEmptyCollection();
  await context.mutateEmptyCollection();
  assert.equal(requests.length, 1);
  await first;
  assert.equal(context.activeMiaoshouCollectionAccountId, 5);
  assert.equal($('#deleteEmptyCollection').hidden, false);
  assert.equal($('#addEmptyCollection').disabled, false);
});

test('deleted slot 2 is not recreated by reload and deletion returns to account 1', async () => {
  const {context, accounts, requests} = setup();
  context.fetch = async (url, options) => {
    requests.push({url, ...options});
    return {ok: true, json: async () => ({data: {id: 2, accounts: accounts.filter(a => a.id !== 2)}})};
  };
  await context.mutateEmptyCollection(2);
  assert.equal(context.activeMiaoshouCollectionAccountId, 1);
  assert.equal(requests[0].method, 'DELETE');
  context.apiGet = async () => ({accounts: accounts.filter(a => a.id !== 2)});
  await context.loadMiaoshouCollectionAccounts();
  assert.equal(context.accountById(2), undefined);
});

test('cancel, protected accounts and server failure leave the account list intact', async () => {
  const {context, requests, messages} = setup();
  await context.mutateEmptyCollection(1);
  context.window.confirm = () => false;
  await context.mutateEmptyCollection(2);
  assert.equal(requests.length, 0);
  context.window.confirm = () => true;
  context.fetch = async () => ({ok: false, json: async () => ({error: '已配置 API'})});
  await context.mutateEmptyCollection(2);
  assert.ok(context.accountById(2));
  assert.match(messages.at(-1), /已配置 API/);
  assert.equal(context.collectionAccountMutationPending, false);
});

test('settings manager lists accounts with delete buttons except primary and shows removed history', () => {
  const {context, $, accounts} = setup();
  context.renderCollectionAccountManager({accounts, removedAccounts:[{id:5,name:'旧账号'}]});
  const rows = $('#collectionAccountManager').children;
  assert.equal(rows.length,4);
  assert.equal(rows[0].children[1].textContent,'重命名采集箱');
  assert.equal(rows[0].children[2].textContent,'默认账号 · 保留');
  assert.equal(rows[1].children[1].textContent,'重命名采集箱');
  assert.equal(rows[1].children[2].textContent,'删除采集箱');
  assert.match(rows[1].children[2].attrs['aria-label'], /妙手采集箱 2/);
  assert.equal($('#removedCollectionAccounts').hidden,false);
  assert.match($('#removedCollectionAccountList').children[0].children[0].textContent,/编号 5 不复用/);
  assert.equal($('#removedCollectionAccountList').children[0].children[1].textContent,'清空本地历史');
});

test('settings rename saves only the selected local label and refreshes navigation', async () => {
  const {context, accounts, requests, messages, $} = setup();
  context.openMiaoshouCollectionAccount(4);
  assert.equal($('#currentCollectionAccount').textContent, '当前账号：妙手采集箱 4');
  context.window.prompt = (title, current) => { assert.match(title,/重命名/); assert.equal(current,'妙手采集箱 4'); return '北美箱'; };
  context.fetch = async (url, options) => {
    requests.push({url, ...options});
    return {ok:true,json:async()=>({data:{id:4,name:'北美箱',accounts:accounts.map(a=>a.id===4?{...a,name:'北美箱'}:a)}})};
  };
  await context.renameManagedCollection(4);
  assert.equal(requests[0].url,'/api/miaoshou-accounts/4/name');
  assert.deepEqual(JSON.parse(requests[0].body),{name:'北美箱',confirmation:'RENAME_COLLECTION_ACCOUNT'});
  assert.equal(context.accountById(4).name,'北美箱');
  assert.equal($('#currentCollectionAccount').textContent, '当前账号：北美箱');
  context.openMiaoshouCollectionAccount(1);
  assert.equal($('#currentCollectionAccount').textContent, '当前账号：妙手采集箱 1');
  context.openMiaoshouCollectionAccount(4);
  assert.equal($('#currentCollectionAccount').textContent, '当前账号：北美箱');
  assert.match(messages.at(-1),/北美箱/);
  context.window.prompt = () => null;
  await context.renameManagedCollection(4);
  assert.equal(requests.length,1);
});

test('settings rename leaves account list intact on server failure', async () => {
  const {context, accounts, $, requests} = setup();
  context.window.prompt = () => '新名字';
  context.fetch = async (url, options) => {requests.push({url,...options});return {ok:false,json:async()=>({error:'名称保存失败'})};};
  await context.renameManagedCollection(2);
  assert.equal(context.accountById(2).name,accounts[1].name);
  assert.match($('#collectionAccountManagerStatus').textContent,/名称保存失败/);
  assert.equal(context.collectionAccountMutationPending,false);
});

test('settings delete confirms, targets one account, clears current context only after success', async () => {
  const {context, $, accounts, requests} = setup();
  context.openMiaoshouCollectionAccount(3);
  context.window.confirm = message => {assert.match(message,/不会删除妙手商品/);return true;};
  let finished;
  context.fetch = (url,options) => {requests.push({url,...options});return new Promise(resolve=>{finished=resolve;});};
  const removing = context.removeManagedCollection(3);
  await context.removeManagedCollection(3);
  assert.equal(requests.length,1);
  assert.equal(context.activeMiaoshouCollectionAccountId,3);
  assert.equal(requests[0].url,'/api/miaoshou-accounts/3/remove');
  assert.equal(JSON.parse(requests[0].body).confirmation,'REMOVE_COLLECTION_ACCOUNT_AND_HISTORY');
  finished({ok:true,json:async()=>({data:{id:3,accounts:accounts.filter(a=>a.id!==3),removedAccounts:[{id:3,name:'妙手采集箱 3'}]}})});
  await removing;
  assert.equal(context.activeMiaoshouCollectionAccountId,1);
  assert.equal(context.accountById(3),undefined);
  assert.match($('#collectionAccountManagerStatus').textContent,/本地订单和任务历史已清除/);
});

test('cancelled or rejected settings deletion preserves account and current page', async () => {
  const {context, $, requests} = setup();
  context.openMiaoshouCollectionAccount(3);
  context.window.confirm = ()=>false;
  await context.removeManagedCollection(3);
  await context.removeManagedCollection(1);
  assert.equal(requests.length,0);
  context.window.confirm = ()=>true;
  context.fetch = async()=>({ok:false,json:async()=>({error:'后台仍有运行任务'})});
  await context.removeManagedCollection(3);
  assert.ok(context.accountById(3));
  assert.equal(context.activeMiaoshouCollectionAccountId,3);
  assert.match($('#collectionAccountManagerStatus').textContent,/运行任务/);
  assert.equal(context.collectionAccountMutationPending,false);
});

test('stale settings response cannot resurrect an account after removal', async () => {
  const {context, accounts} = setup();
  let finishRead;
  context.apiGet = ()=>new Promise(resolve=>{finishRead=resolve;});
  const reading=context.loadCollectionAccountManager();
  context.fetch=async()=>({ok:true,json:async()=>({data:{id:3,accounts:accounts.filter(a=>a.id!==3),removedAccounts:[]}})});
  await context.removeManagedCollection(3);
  finishRead({accounts});await reading;
  assert.equal(context.accountById(3),undefined);
});

test('DeepSeek uses password input, confirms changes, clears key and sends no AI request', async () => {
  const {context, $, requests} = setup();
  context.apiGet=async()=>({configured:false,model:'fixture-model'});
  await context.loadDeepSeekSettings();
  assert.equal($('#removeDeepSeekSettings').disabled,true);
  $('#deepSeekApiKey').value='fixture-ai-secret';
  context.fetch=async(url,options)=>{requests.push({url,...options});return {ok:true,json:async()=>({data:{configured:requests.length===1,model:'fixture-model'}})};};
  await context.saveDeepSeekSettings();
  assert.equal(requests[0].url,'/api/settings/deepseek');
  assert.equal(JSON.parse(requests[0].body).apiKey,'fixture-ai-secret');
  assert.equal($('#deepSeekApiKey').value,'');
  assert.equal($('#removeDeepSeekSettings').disabled,false);
  await context.saveDeepSeekSettings(null,true);
  assert.deepEqual(JSON.parse(requests[1].body),{confirmation:'REMOVE_DEEPSEEK_API'});
  assert.equal($('#deepSeekConfigured').textContent,'未配置');
  assert.equal(requests.length,2);
  const html=fs.readFileSync(path.join(__dirname,'..','index.html'),'utf8');
  assert.match(html,/id="deepSeekApiKey" type="password" autocomplete="new-password"/);
});

test('settings refreshes MiaoShou shops and cached logistics without starting product or task work', async () => {
  const {context,$,requests,messages}=setup();
  context.fetch=async(url,options)=>{requests.push({url,...options});return {ok:true,json:async()=>({data:{
    accounts:[{accountId:4,name:'LULU',shopCount:2,syncedShopCount:2,freightTemplateCount:3,warehouseCount:2}],errors:[],warnings:[]}})};};
  await context.refreshMiaoshouShops();
  assert.equal(requests.length,1);
  assert.equal(requests[0].url,'/api/settings/miaoshou-shops');
  assert.deepEqual(JSON.parse(requests[0].body),{confirmation:'REFRESH_MIAOSHOU_SHOPS'});
  assert.match($('#miaoshouShopRefreshStatus').textContent,/LULU 2 家店铺，物流 2\/2 家/);
  assert.equal($('#refreshMiaoshouShops').disabled,false);
  assert.deepEqual(messages,['妙手店铺、仓库和运费模板已更新。']);
  const html=fs.readFileSync(path.join(__dirname,'..','index.html'),'utf8');
  assert.match(html,/id="refreshMiaoshouShops"/);
});

test('DeepSeek cancellation, blank validation and failure do not claim a changed configuration', async () => {
  const {context,$,requests}=setup();
  await context.saveDeepSeekSettings();
  assert.equal(requests.length,0);
  context.renderDeepSeekSettings({configured:true,model:'fixture-model'});
  context.window.confirm=()=>false;
  await context.saveDeepSeekSettings(null,true);
  assert.equal(requests.length,0);
  context.window.confirm=()=>true;
  $('#deepSeekApiKey').value='replacement-secret';
  context.fetch=async()=>({ok:false,json:async()=>({error:'正在执行任务'})});
  await context.saveDeepSeekSettings();
  assert.equal($('#deepSeekApiKey').value,'');
  assert.equal($('#deepSeekConfigured').textContent,'已配置');
  assert.match($('#deepSeekSettingsStatus').textContent,/正在执行任务/);
  assert.equal(context.saveDeepSeekSettings.busy,false);
});

test('DeepSeek duplicate submits and stale refresh cannot override committed state', async () => {
  const {context,$,requests}=setup();
  let finishRead,finishSave;
  context.apiGet=()=>new Promise(resolve=>{finishRead=resolve;});
  const reading=context.loadDeepSeekSettings();
  $('#deepSeekApiKey').value='fixture-secret';
  context.fetch=(url,options)=>{requests.push({url,...options});return new Promise(resolve=>{finishSave=resolve;});};
  const saving=context.saveDeepSeekSettings();
  await context.saveDeepSeekSettings();
  assert.equal(requests.length,1);
  finishSave({ok:true,json:async()=>({data:{configured:true,model:'fixture-model'}})});await saving;
  finishRead({configured:false,model:'old'});await reading;
  assert.equal($('#deepSeekConfigured').textContent,'已配置');
  assert.match($('#deepSeekModel').textContent,/fixture-model/);
});
