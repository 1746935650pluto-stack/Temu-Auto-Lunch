(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const labels = {orders: '订单数', packages: '包裹数', quantity: '已知件数'};
  let accountId = null, visible = false, revision = 0, controller = null;
  let data = null, catalog = [], selected = '', callbacks = {}, viewMode = 'bar';
  const day = offset => new Date(Date.now() + 8 * 3600000 + offset * 86400000).toISOString().slice(0, 10);
  const number = value => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;
  function clear(message) {
    data = null; el('orderShopPlot').replaceChildren(); el('orderShopSummary').replaceChildren();
    el('orderShopStatus').textContent = message; el('orderShopHint').textContent = '';
  }
  function shortcuts() {
    const start = el('orderShopStart').value, end = el('orderShopEnd').value;
    el('orderShopToday').setAttribute('aria-pressed', String(start === day(0) && end === day(0)));
    el('orderShopWeek').setAttribute('aria-pressed', String(start === day(-6) && end === day(0)));
  }
  function chartView(mode) {
    viewMode = mode;
    [['orderShopBarView','bar'],['orderShopPieView','pie']].forEach(([id,key])=>{
      el(id).setAttribute('aria-pressed',String(mode===key));
      el(id).className=`button ${mode===key?'primary':'subtle'}`;
    });
    el('orderShopPlot').setAttribute('aria-label',mode==='pie'?'各店铺数据占比饼状图':'各店铺数据柱状图，可横向滚动');
    render();
  }
  function render() {
    if (!data) return;
    const metric = Object.hasOwn(labels, el('orderShopMetric').value) ? el('orderShopMetric').value : 'orders';
    const label = labels[metric];
    el('orderShopTitle').textContent = `${label} · ${viewMode === 'pie' ? '店铺占比' : '店铺对比'}`;
    el('orderShopCaption').textContent = `${data.accountName} · ${data.start} — ${data.end} · ${data.shops.length} 家已记录店铺`;
    el('orderShopSummary').replaceChildren(...Object.entries(labels).map(([key, text]) => {
      const item = document.createElement('span'), value = document.createElement('strong');
      item.textContent = text + ' '; value.textContent = number(data.totals[key]).toLocaleString('zh-CN'); item.append(value); return item;
    }));
    const warnings = [...data.warnings];
    if (data.totals.quantityMissing) warnings.push(`${data.totals.quantityMissing} 个包裹缺少件数`);
    el('orderShopStatus').textContent = warnings.length ? warnings.join('；') : '按下单日期统计，同一店铺的拆分包裹只计一单。';
    const shops = [...data.shops].sort((a, b) => number(b[metric]) - number(a[metric]) || a.displayName.localeCompare(b.displayName, 'zh-CN'));
    if (!shops.length) {
      el('orderShopPlot').replaceChildren();
      el('orderShopHint').textContent = '该账号暂无已保存订单，请同步订单后查看店铺数据。';
      return;
    }
    if (viewMode === 'pie') {
      const colors=new Map([...shops].sort((a,b)=>a.shopKey.localeCompare(b.shopKey)).map((shop,index)=>[shop.shopKey,window.proportionCharts.colors[index%window.proportionCharts.colors.length]]));
      const result=window.proportionCharts.render(el('orderShopPlot'),{
        label,description:`${data.accountName}各店铺${label}占比`,selected,
        entries:shops.map(shop=>({key:shop.shopKey,label:shop.displayName,value:number(shop[metric]),color:colors.get(shop.shopKey),shop,selectable:!!shop.shopKey})),
        onDescribe:text=>{el('orderShopHint').textContent=text;},
        onSelect:row=>{const shop=row.shop;if(visible&&accountId===shop.accountId&&shop.shopKey)callbacks.onSelect?.(shop);},
      });
      el('orderShopHint').textContent=result.total?'占比按当前账号和所选日期汇总 · 点击扇区或图例筛选下方订单明细。':'该日期范围内当前指标均为 0，暂无占比。';
      return;
    }
    const width = Math.max(640, el('orderShopPlot').clientWidth || 0, shops.length * 104 + 72), span = (width - 72) / shops.length;
    const maximum = Math.max(0, ...shops.map(shop => number(shop[metric])));
    const power = 10 ** Math.floor(Math.log10(Math.max(1, maximum / 4)));
    const step = Math.max(1, [1, 2, 5, 10].find(n => n * power >= maximum / 4) * power), ceiling = Math.max(4, Math.ceil(maximum / step) * step);
    const svgNode = (tag, attrs = {}, text) => {
      const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
      Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
      if (text !== undefined) node.textContent = text;
      return node;
    };
    const svg = svgNode('svg', {width, height: 270, viewBox: `0 0 ${width} 270`, role: 'group', 'aria-label': `${data.accountName}各店铺${label}`});
    for (let value = 0; value <= ceiling; value += step) {
      const y = 218 - value / ceiling * 174;
      svg.append(svgNode('line', {x1: 52, x2: width - 20, y1: y, y2: y, stroke: '#edf1f6', 'stroke-dasharray': value ? '3 4' : 'none'}),
        svgNode('text', {x: 42, y: y + 4, 'text-anchor': 'end', fill: '#94a3b8', 'font-size': 11}, value.toLocaleString()));
    }
    shops.forEach((shop, index) => {
      const value = number(shop[metric]), height = value / ceiling * 174, barWidth = Math.min(46, span * .45);
      const x = 52 + span * (index + .5), caption = `${shop.displayName} · ${label} ${value.toLocaleString()}`;
      const group = svgNode('g', {tabindex: 0, role: shop.shopKey ? 'button' : 'img', 'aria-label': caption, 'data-shop-key': shop.shopKey});
      const fill = !selected || selected === shop.shopKey ? '#3b82f6' : '#b7d1fb';
      group.append(svgNode('title', {}, `${caption}${shop.shopId ? ` · 店铺编号 ${shop.shopId}` : ''}`),
        svgNode('rect', {x: x - barWidth / 2, y: 218 - height, width: barWidth, height, rx: 4, fill}),
        svgNode('text', {x, y: 209 - height, 'text-anchor': 'middle', fill: '#475569', 'font-size': 12}, value));
      const limit = Math.max(6, Math.min(14, Math.floor(span / 11))), name = shop.displayName.length > limit ? shop.displayName.slice(0, limit - 1) + '…' : shop.displayName;
      group.append(svgNode('text', {x, y: 244, 'text-anchor': 'middle', fill: '#64748b', 'font-size': 11}, name));
      const describe = () => { el('orderShopHint').textContent = caption; };
      group.addEventListener('mouseenter', describe); group.addEventListener('focus', describe);
      const choose = () => { if (visible && accountId === shop.accountId && shop.shopKey) callbacks.onSelect?.(shop); };
      if (shop.shopKey) {
        group.style.cursor = 'pointer'; group.addEventListener('click', choose);
        group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose(); } });
      }
      svg.append(group);
    });
    el('orderShopPlot').replaceChildren(svg);
    el('orderShopHint').textContent = maximum ? '点击柱体筛选下方订单明细；悬停或聚焦可查看数值。' : '该日期范围内，各已记录店铺的当前指标均为 0。';
  }
  async function load() {
    if (!visible || !accountId) return;
    const request = ++revision, requestedAccount = accountId;
    controller?.abort(); controller = new AbortController(); shortcuts(); clear('正在读取当前账号的店铺数据…');
    const query = new URLSearchParams({accountId: String(accountId), start: el('orderShopStart').value, end: el('orderShopEnd').value});
    try {
      const response = await fetch(`/api/statistics/shops?${query}`, {signal: controller.signal});
      const result = await response.json().catch(() => ({}));
      if (request !== revision || accountId !== requestedAccount || !visible) return;
      if (!response.ok) throw new Error(result.error || '店铺统计读取失败');
      if (result.data?.accountId !== requestedAccount || !Array.isArray(result.data.shops) ||
          result.data.shops.some(shop => shop.accountId !== requestedAccount || typeof shop.shopKey !== 'string' || typeof shop.displayName !== 'string')) throw new Error('店铺统计账号不匹配，请刷新后重试');
      data = result.data; catalog = data.shops; render(); callbacks.onLoaded?.(requestedAccount);
    } catch (error) {
      if (request === revision && visible && error.name !== 'AbortError') clear(`店铺统计读取失败：${error.message}`);
    }
  }
  function open(id, name = '') {
    if (accountId !== Number(id)) { catalog = []; selected = ''; }
    accountId = Number(id); visible = true;
    el('orderShopCaption').textContent = `${name || `账号 ${accountId}`} · 店铺数据`;
    return load();
  }
  function close() { visible = false; revision += 1; controller?.abort(); data = null; catalog = []; }
  document.addEventListener('DOMContentLoaded', () => {
    el('orderShopStart').value = day(0); el('orderShopEnd').value = day(0); shortcuts();
    el('orderShopMetric').value = 'orders'; el('orderShopMetric').addEventListener('change', render);
    chartView('bar');
    el('orderShopBarView').addEventListener('click',()=>chartView('bar'));
    el('orderShopPieView').addEventListener('click',()=>chartView('pie'));
    el('orderShopForm').addEventListener('submit', event => { event.preventDefault(); load(); });
    ['orderShopStart', 'orderShopEnd'].forEach(id => el(id).addEventListener('change', () => { revision += 1; controller?.abort(); clear('日期已修改，请查询店铺数据。'); shortcuts(); }));
    [['orderShopToday', 0], ['orderShopWeek', -6]].forEach(([id, offset]) => el(id).addEventListener('click', () => {
      el('orderShopStart').value = day(offset); el('orderShopEnd').value = day(0); load();
    }));
  });
  window.orderShopCharts = {open, close, configure: options => { callbacks = options; },
    shopsFor: id => Number(id) === accountId ? catalog : [],
    selectionChanged: key => { selected = String(key || ''); render(); },
    ordersSynced: id => { if (visible && Number(id) === accountId) return load(); }};
})();
