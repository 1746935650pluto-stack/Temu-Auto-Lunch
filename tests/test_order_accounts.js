const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

test('orders folder is immediately after collections and before automation navigation',()=>{
  const html = fs.readFileSync(path.join(__dirname,'..','index.html'),'utf8');
  assert.ok(html.indexOf('id="miaoshouCollectionNav"') < html.indexOf('id="orderAccountsNav"'));
  assert.ok(html.indexOf('id="orderAccountsNav"') < html.indexOf('data-page="collectionAutomation"'));
  assert.match(html, /id="orderAccountsChildren"[^>]*hidden/);
});

test('orders retain the shared page width and enlarged record layout',()=>{
  const css=fs.readFileSync(path.join(__dirname,'..','styles.css'),'utf8');
  assert.match(css, /\.page \{[^}]*max-width: 1440px;/);
  assert.match(css, /\.publish-records-page \{ max-width: 1320px; \}/);
  assert.doesNotMatch(css, /#orders \{/);
  assert.match(css, /\.order-record-body \{[^}]*grid-template-columns: minmax\(0, 1fr\) 240px;/);
  assert.match(css, /\.order-product \{[^}]*grid-template-columns: 76px minmax\(0, 1fr\);/);
  assert.match(css, /\.order-thumb \{[^}]*width: 76px; height: 76px;/);
  assert.match(css, /\.order-product-title \{[^}]*-webkit-line-clamp: 2;[^}]*font-size: 15px;/);
  for (const selector of ['.order-record-identity','.order-number strong','.order-item-detail']) {
    const rule=css.slice(css.indexOf(`${selector} {`)).split('}')[0];
    assert.match(rule,/font-size: 13px;/);
  }
  for (const selector of ['.order-record-meta','.order-status']) {
    const rule=css.slice(css.indexOf(`${selector} {`)).split('}')[0];
    assert.match(rule,/font-size: 12px;/);
  }
});

test('order breakpoints retain aligned metadata and larger mobile thumbnail without overflow',()=>{
  const css=fs.readFileSync(path.join(__dirname,'..','styles.css'),'utf8');
  const tablet=css.slice(css.indexOf('@media (max-width: 1050px)'),css.indexOf('@media (max-width: 620px)'));
  assert.match(tablet,/\.order-record-body \{ grid-template-columns: minmax\(0, 1fr\);/);
  assert.match(tablet,/padding: 0 0 0 90px;/);
  const mobile=css.slice(css.indexOf('@media (max-width: 620px)'),css.indexOf('.live-panel {'));
  assert.match(mobile,/grid-template-columns: 64px minmax\(0, 1fr\);/);
  assert.match(mobile,/\.order-thumb \{ width: 64px; height: 64px; \}/);
  assert.match(mobile,/\.order-product-title \{ font-size: 14px; \}/);
  assert.match(mobile,/\.order-record-meta \{ padding-left: 0;/);
});

function setup() {
  const element = () => ({children:[], dataset:{}, value:'', classList:{contains:()=>false,toggle(){}},
    append(...children){this.children.push(...children);}, replaceChildren(){this.children=[];},
    addEventListener(event,fn){this[event]=fn;}, setAttribute(name,value){this[name]=value;}});
  const nodes = new Map();
  const $ = selector => {if(!nodes.has(selector)) nodes.set(selector,element()); return nodes.get(selector);};
  const accounts = [1,2,3].map(id=>({id,name:`妙手采集箱 ${id}`}));
  const messages=[];
  const ctx = vm.createContext({$, URL, document:{createElement:element,createTextNode:text=>({text})},
    window:{setInterval:()=>1,location:{origin:'http://127.0.0.1:8787'}}, accountById:id=>accounts.find(a=>a.id===id), showToast:m=>messages.push(m)});
  vm.runInContext(source.slice(source.indexOf('function orderText'),source.indexOf('function poolTransferStatus')),ctx);
  ctx.activatePage = ()=>ctx.loadOrders();
  const setAccount = id=>vm.runInContext(`activeOrderAccountId = ${id};`,ctx);
  return {ctx,$,accounts,messages,setAccount};
}
const data = (accountId,name) => ({accountId,syncConfigured:true,summary:{count:1,lastSyncedAt:'2026-10-16T01:00:00Z'},
  orders:[{recordKey:'same',orderNo:name,shopId:accountId,shopName:`shop-${accountId}`,itemDetails:[]}]});

test('compact orders group identity, product details and logistics without dropping fields',()=>{
  const {ctx,$}=setup();
  const value=data(1,'PO-211-17118099139193601');
  Object.assign(value.orders[0],{status:'pending',imageUrl:'https://example.test/product.jpg',itemSummary:'商品完整标题',
    orderedAt:'2026-10-17 14:21:08',updatedAtRemote:'2026-10-17 14:21:26',trackingNo:'TRACK-123',
    itemDetails:[{specification:'Negro / Tamaño de etiqueta: XXL',skuId:'3451287997'}]});
  ctx.renderOrders(value);
  const html=$('#ordersList').innerHTML;
  assert.match(html,/<header class="order-record-head">[\s\S]*PO-211-17118099139193601[\s\S]*shop-1[\s\S]*order-status-pending">待处理<\/span>[\s\S]*<\/header>/);
  assert.match(html,/<div class="order-product">[\s\S]*product\.jpg[\s\S]*order-product-title[\s\S]*商品完整标题[\s\S]*Negro \/ Tamaño de etiqueta: XXL[\s\S]*3451287997/);
  assert.match(html,/<dl class="order-record-meta">[\s\S]*2026-10-17 14:21:08[\s\S]*2026-10-17 14:21:26[\s\S]*TRACK-123/);
});

test('order center shows an independent half-hour automatic synchronization schedule',()=>{
  const {ctx,$}=setup();
  ctx.renderOrders({...data(1,'one'),autoSync:{enabled:true,running:false,nextRunAt:1792468800}});
  assert.match($('#orderAutoSyncSchedule').textContent,/自动同步：每 30 分钟/);
  ctx.renderOrders({...data(1,'one'),autoSync:{enabled:true,running:true}});
  assert.match($('#orderAutoSyncSchedule').textContent,/正在同步订单/);
});

test('order layout safely escapes text and title attributes; unknown statuses stay neutral',()=>{
  const {ctx,$}=setup();
  const value=data(1,'<script>bad</script>');
  Object.assign(value.orders[0],{status:'custom" onclick="bad',shopName:'<b>shop</b>',itemSummary:'" onmouseover="bad <img>',
    imageUrl:'javascript:alert(1)',itemDetails:[{title:'<svg>',specification:'<b>red</b>',skuId:'"123'},null]});
  ctx.renderOrders(value);
  const html=$('#ordersList').innerHTML;
  assert.doesNotMatch(html,/<script>|<svg>|<img|<b>| onmouseover="| onclick="/);
  assert.match(html,/title="&quot; onmouseover=&quot;bad &lt;img&gt;"/);
  assert.match(html,/order-status-neutral/);
  assert.match(html,/&lt;b&gt;red&lt;\/b&gt;/);
  assert.match(html,/order-thumb-empty">无图/);
  assert.equal(ctx.orderImageUrl(''),'');
  assert.equal(ctx.orderImageUrl('   '),'');
  assert.equal(ctx.orderStatusTone('已发货'),'shipping');
  assert.equal(ctx.orderStatusTone('已完成'),'complete');
  assert.equal(ctx.orderStatusTone('已关闭'),'neutral');
});

test('multiple items and missing order fields retain readable fallback values',()=>{
  const {ctx,$}=setup();
  const value=data(1,'');
  value.orders[0].itemDetails=[{title:'上衣',specification:'蓝色 / L',skuId:'S1'},{title:'裤子',skuId:'S2'}];
  ctx.renderOrders(value);
  const html=$('#ordersList').innerHTML;
  assert.match(html,/订单号未返回/);
  assert.match(html,/妙手未返回商品摘要/);
  assert.match(html,/order-item-name">上衣/);
  assert.match(html,/order-item-name">裤子/);
  assert.equal((html.match(/class="order-item-detail"/g)||[]).length,2);
  assert.match(html,/<dt>运单<\/dt><dd>—<\/dd>/);
});

test('long multi-item orders preserve full title tooltip and all specs in the enlarged layout',()=>{
  const {ctx,$}=setup();
  const value=data(1,'long-title');
  const title='长商品标题以及需要完整保留的说明'.repeat(15);
  Object.assign(value.orders[0],{itemSummary:title,imageUrl:'',itemDetails:[
    {title:'商品一',specification:'超长规格'.repeat(50),skuId:'SKU-FIRST'},
    {title:'商品二',specification:'红色 / XL',skuId:'SKU-SECOND'}]});
  ctx.renderOrders(value);
  const html=$('#ordersList').innerHTML;
  assert.ok(html.includes(`title="${title}"`));
  assert.ok(html.includes('超长规格'.repeat(50)));
  assert.match(html,/SKU-FIRST[\s\S]*商品二[\s\S]*SKU-SECOND/);
  assert.match(html,/order-thumb-empty">无图/);
  assert.doesNotMatch(html,/<img/);
});

test('order redesign preserves newest-first sorting and selected shop filtering',()=>{
  const {ctx,$}=setup();
  const value=data(1,'old');
  value.orders[0].orderedAt='2026-10-16';
  value.orders.push({...value.orders[0],orderNo:'new',orderedAt:'2026-10-17'},
    {...value.orders[0],orderNo:'other-shop',shopId:2,shopName:'shop-2'});
  ctx.renderOrders(value);
  let html=$('#ordersList').innerHTML;
  assert.ok(html.indexOf('>new</strong>')<html.indexOf('>old</strong>'));
  $('#orderShopFilter').value='1';
  ctx.renderOrders(value);
  html=$('#ordersList').innerHTML;
  assert.equal($('#orderShopFilter').value,'1');
  assert.doesNotMatch(html,/other-shop/);
  assert.equal((html.match(/class="order-record-row"/g)||[]).length,2);
});

test('order children mirror collection names and display sequence, expand without navigating, and reset shop filter on switch',async()=>{
  const {ctx,$,accounts} = setup();
  accounts[0].name='NL'; accounts[1].name='Bao'; accounts[2].name='LULU';
  ctx.apiGet = async()=>data(2,'two');
  ctx.renderOrderAccounts(accounts);
  assert.deepEqual($('#orderAccountsChildren').children.map(c=>c.dataset.orderAccountId),['1','2','3']);
  assert.deepEqual($('#orderAccountsChildren').children.map(c=>c.children[0].textContent),['1','2','3']);
  assert.deepEqual($('#orderAccountsChildren').children.map(c=>c.children[1].text),['NL','Bao','LULU']);
  assert.equal($('#orderAccountsChildren').hidden,true);
  ctx.setOrderAccountsExpanded(true);
  assert.equal($('#orderAccountsToggle')['aria-expanded'],'true');
  $('#orderShopFilter').value='old-shop';
  ctx.openOrderAccount(2);
  assert.equal($('#orderShopFilter').value,'');
  assert.equal($('#orderCenterTitle').textContent,'订单中心 / Bao');
  assert.match($('#orderAccountDescription').textContent,/Bao/);
  await Promise.resolve();
  ctx.renderOrderAccounts(accounts.filter(a=>a.id!==2));
  assert.equal($('#orderAccountsChildren').children.length,2);
  assert.deepEqual($('#orderAccountsChildren').children.map(c=>c.children[0].textContent),['1','2']);
  assert.deepEqual($('#orderAccountsChildren').children.map(c=>c.children[1].text),['NL','LULU']);
});

test('out-of-order responses never render another account and old backend fails closed',async()=>{
  const {ctx,$,setAccount}=setup();
  const pending=[];
  ctx.apiGet=url=>new Promise(resolve=>pending.push({url,resolve}));
  const first=ctx.loadOrders();
  setAccount(2);
  const second=ctx.loadOrders();
  assert.match(pending[1].url,/accountId=2/);
  pending[1].resolve(data(2,'two'));
  await second;
  pending[0].resolve(data(1,'one'));
  await first;
  assert.match($('#ordersList').innerHTML,/two/);
  assert.doesNotMatch($('#ordersList').innerHTML,/one/);
  ctx.apiGet=async()=>({orders:data(1,'wrong-account').orders});
  await ctx.loadOrders();
  assert.equal($('#syncOrders').disabled,true);
  assert.doesNotMatch($('#ordersList').innerHTML,/wrong-account/);
  assert.match($('#ordersList').innerHTML,/账号订单隔离/);
});

test('sync snapshots account ID, debounces, and late completion cannot replace the current account',async()=>{
  const {ctx,$,setAccount}=setup();
  ctx.renderOrders(data(1,'one'));
  const requests=[];
  ctx.fetch=(url,options)=>new Promise(resolve=>requests.push({url,body:JSON.parse(options.body),resolve}));
  const sync=ctx.syncOrders();
  await ctx.syncOrders();
  assert.equal(requests.length,1);
  assert.equal(requests[0].body.accountId,1);
  setAccount(2);
  ctx.apiGet=async()=>data(2,'two');
  await ctx.loadOrders();
  requests[0].resolve({ok:true,json:async()=>({data:{...data(1,'updated-one'),imported:1}})});
  await sync;
  assert.match($('#ordersList').innerHTML,/two/);
  assert.doesNotMatch($('#ordersList').innerHTML,/updated-one/);
  assert.equal($('#syncOrders').disabled,false);
});
