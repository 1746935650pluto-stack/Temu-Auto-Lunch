(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const metrics = ['collected', 'tasks', 'saved', 'submitted', 'editFailed', 'publishFailed', 'needsInput', 'orders', 'packages', 'quantity'];
  const headings = ['日期', '妙手账号', '采集入池', '新建任务', '保存成功', '发布已提交', '编辑异常', '发布异常', '待选择', '订单数', '包裹数', '已知件数'];
  let revision = 0, current = null, requestController = null, viewMode = 'bar';
  const metricLabels = {...Object.fromEntries(metrics.map((key, index) => [key, headings[index + 2]])), failed: '编辑／发布异常'};
  const colors = ['#2f6fed','#0c987f','#e69520','#8954ce','#d64c75','#1795b3','#98713e','#506ab0','#6d9c32','#bf6339', '#6c5bba','#d87926','#248963','#a94378','#377fb1','#8b8932','#aa5844','#525cc2','#227b82','#a168ba'];
  function clearChart(message = '查询统计后显示图表。') {
    el('statisticsChartPlot').replaceChildren(); el('statisticsChartLegend').replaceChildren();
    el('statisticsChartHint').textContent = message;
  }
  function chartView(mode) {
    viewMode = mode;
    const table = mode === 'table';
    el('statisticsTable').hidden = !table;
    el('statisticsChartPanel').hidden = table;
    el('statisticsMetric').disabled = table;
    el('statisticsChartLegend').hidden = mode === 'pie';
    el('statisticsChartPlot').setAttribute('aria-label', mode === 'pie' ? '各账号数据占比饼状图' : '每日柱状图，可横向滚动');
    if (!table && current) renderChart();
    if (table) el('statisticsChartTitle').textContent = '每日账号明细';
    [['statisticsChartView', mode === 'bar'], ['statisticsPieView', mode === 'pie'], ['statisticsTableView', table]].forEach(([id, selected]) => {
      el(id).className = `button ${selected ? 'primary' : 'subtle'}`;
      el(id).setAttribute('aria-pressed', String(selected));
    });
  }
  function renderChart() {
    const metric = el('statisticsMetric').value || 'orders', label = metricLabels[metric] || metricLabels.orders;
    Array.from(el('statisticsCards').children).forEach(card => {
      const selected = card.dataset.metric === metric;
      card.classList.toggle('statistics-kpi-primary', selected);
      card.setAttribute('aria-pressed', String(selected));
    });
    el('statisticsChartTitle').textContent = viewMode === 'pie' ? `${label} · 账号占比` : el('statisticsTable').hidden ? `${label}趋势` : '每日账号明细';
    if (!current?.rows.length) { if(current)el('statisticsChartCaption').textContent=`${current.start} — ${current.end} · 0 个账号`;clearChart('当前筛选没有可展示的每日数据。'); return; }
    const dates = [...new Set(current.rows.map(row => row.date))].sort();
    const accounts = [...new Map(current.rows.map(row => [row.accountId, {id: row.accountId, name: row.accountName}])).values()].sort((a, b) => a.id - b.id);
    const singleDay = dates.length === 1;
    if (el('statisticsTable').hidden && singleDay) el('statisticsChartTitle').textContent = `${label}对比`;
    el('statisticsChartCaption').textContent = `${current.start} — ${current.end} · ${accounts.length} 个账号`;
    const values = new Map(current.rows.map(row => { const value = metric === 'failed' ? Number(row.editFailed || 0) + Number(row.publishFailed || 0) : Number(row[metric]); return [`${row.date}:${row.accountId}`, Number.isFinite(value) ? Math.max(0, value) : 0]; }));
    if (viewMode === 'pie') {
      el('statisticsChartTitle').textContent = `${label} · 账号占比`;
      el('statisticsChartLegend').replaceChildren();
      const result = window.proportionCharts.render(el('statisticsChartPlot'), {
        label, description: `${current.start} 至 ${current.end}各账号${label}占比`,
        entries: accounts.map(account => ({key:String(account.id),label:account.name,
          color:colors[(account.id-1)%colors.length],value:dates.reduce((sum,date)=>sum+(values.get(`${date}:${account.id}`)||0),0)})),
        onDescribe: text => { el('statisticsChartHint').textContent = text; },
        onSelect: metric === 'orders' ? row => openOrderAccount(Number(row.key)) : undefined,
      });
      el('statisticsChartHint').textContent = result.total ? '按所选日期范围汇总各账号占比 · 悬停查看数量和百分比' + (metric === 'orders' ? ' · 点击扇区或图例查看账号订单' : '') : '当前筛选下该指标均为 0，暂无占比。';
      return;
    }
    const maximum = Math.max(0, ...values.values());
    const power = 10 ** Math.floor(Math.log10(Math.max(1, maximum / 4)));
    const step = Math.max(1, [1, 2, 5, 10].find(n => n * power >= maximum / 4) * power);
    const ceiling = Math.max(4, Math.ceil(maximum / step) * step);
    const groupWidth = Math.max(72, accounts.length * 12 + 18);
    const width = Math.max(640, el('statisticsChartPlot').clientWidth || 0, dates.length * groupWidth + 82), plotWidth = width - 82;
    const groupSpace = plotWidth / dates.length, barSpace = singleDay ? groupSpace / accounts.length : Math.min(30, (groupSpace - 14) / accounts.length);
    const color = account => colors[(account.id - 1) % colors.length];
    const svgNode = (tag, attrs = {}, text) => {
      const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
      Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
      if (text !== undefined) node.textContent = text;
      return node;
    };
    const svg = svgNode('svg', {width, height: 322, viewBox: `0 0 ${width} 322`, role: 'group', 'aria-label': `${label}，按日期与妙手账号分组`});
    svg.append(svgNode('text', {x: 58, y: 19, fill: '#94a3b8', 'font-size': 11}, '数量'));
    for (let value = 0; value <= ceiling; value += step) {
      const y = 280 - value / ceiling * 240;
      svg.append(svgNode('line', {x1: 62, x2: width - 20, y1: y, y2: y, stroke: '#edf1f6', 'stroke-dasharray': value ? '3 4' : 'none'}),
        svgNode('text', {x: 50, y: y + 4, fill: '#94a3b8', 'font-size': 11, 'text-anchor': 'end'}, value.toLocaleString()));
    }
    dates.forEach((date, dateIndex) => {
      const center = 62 + groupSpace * (dateIndex + .5), start = center - accounts.length * barSpace / 2;
      if (!singleDay) svg.append(svgNode('text', {x: center, y: 306, fill: '#64748b', 'font-size': 11, 'text-anchor': 'middle'}, date.slice(5)));
      accounts.forEach((account, accountIndex) => {
        const value = values.get(`${date}:${account.id}`) || 0, height = value / ceiling * 240;
        const barWidth = singleDay ? Math.min(46, barSpace * .48) : Math.max(4, barSpace - 4);
        const x = start + accountIndex * barSpace + (barSpace - barWidth) / 2;
        const caption = `${date} · ${account.name} · ${label} ${value.toLocaleString()}`;
        const group = svgNode('g', {tabindex: 0, role: metric === 'orders' ? 'button' : 'img', 'aria-label': caption, 'data-chart-account': account.id, 'data-chart-date': date});
        group.append(svgNode('title', {}, caption),
          svgNode('rect', {x, y: 280 - height, width: barWidth, height, rx: 4, fill: color(account)}));
        if (singleDay || barSpace >= 24) group.append(svgNode('text', {x: x + barWidth / 2, y: 271 - height, 'text-anchor': 'middle', 'font-size': singleDay ? 12 : 10, fill: '#475569'}, value));
        if (singleDay) {
          const name = account.name.length > 11 ? account.name.slice(0, 10) + '…' : account.name;
          svg.append(svgNode('text', {x: x + barWidth / 2, y: 306, 'text-anchor': 'middle', 'font-size': 11, fill: '#64748b'}, name));
        }
        const describe = () => { el('statisticsChartHint').textContent = caption; };
        group.addEventListener('mouseenter', describe); group.addEventListener('focus', describe);
        if (metric === 'orders') {
          group.style.cursor = 'pointer';
          group.addEventListener('click', () => openOrderAccount(account.id));
          group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openOrderAccount(account.id); } });
        }
        svg.append(group);
      });
    });
    el('statisticsChartPlot').replaceChildren(svg);
    el('statisticsChartLegend').replaceChildren(...accounts.map(account => {
      const item = document.createElement('span'), swatch = document.createElement('i'), name = document.createElement('span');
      swatch.style.backgroundColor = color(account); name.textContent = account.name; item.append(swatch, name); return item;
    }));
    el('statisticsChartHint').textContent = maximum ? '悬停或聚焦柱体查看数值' + (metric === 'orders' ? ' · 点击柱体查看对应账号订单' : '') : '当前筛选下该指标均为 0。';
  }
  function beijingDate(offset = 0) {
    return new Date(Date.now() + 8 * 3600000 + offset * 86400000).toISOString().slice(0, 10);
  }
  function invalidate() {
    revision += 1;
    requestController?.abort();
    current = null;
    clearChart('筛选已修改，请重新查询统计。');
    el('statisticsExport').disabled = true;
    el('statisticsRows').replaceChildren();
    el('statisticsCards').replaceChildren();
    el('statisticsStatus').textContent = '筛选已修改，请查询统计。';
    dateShortcuts();
  }
  function dateShortcuts() {
    const today = beijingDate(), start = el('statisticsStart').value, end = el('statisticsEnd').value;
    el('statisticsToday').setAttribute('aria-pressed', String(start === today && end === today));
    el('statisticsWeek').setAttribute('aria-pressed', String(start === beijingDate(-6) && end === today));
  }
  function accountsChanged(accounts) {
    const selected = el('statisticsAccount').value || 'all';
    const options = [{id: 'all', name: '全部账号'}, ...accounts];
    el('statisticsAccount').replaceChildren(...options.map(account => {
      const option = document.createElement('option');
      option.value = String(account.id); option.textContent = account.name;
      return option;
    }));
    el('statisticsAccount').value = options.some(a => String(a.id) === selected) ? selected : 'all';
  }
  function render(data) {
    current = data;
    accountsChanged(data.accounts);
    const cards = [['orders', '订单数', data.totals.orders, '按下单日期去重'], ['collected', '采集入池', data.totals.collected, '本地新增商品'],
      ['saved', '保存成功', data.totals.saved, '已保存至妙手'], ['submitted', '发布已提交', data.totals.submitted, '妙手已接受请求'],
      ['failed', '编辑／发布异常', data.totals.editFailed + data.totals.publishFailed, `编辑 ${data.totals.editFailed} · 发布 ${data.totals.publishFailed}`]];
    el('statisticsCards').replaceChildren(...cards.map(([key, label, value, note]) => {
      const card = document.createElement('button'), title = document.createElement('span'), number = document.createElement('strong'), detail = document.createElement('small');
      card.type = 'button'; card.className = 'statistics-kpi'; card.dataset.metric = key;
      card.title = `查看${label}统计图`;
      card.addEventListener('click', () => { el('statisticsMetric').value = key; chartView(viewMode === 'pie' ? 'pie' : 'bar'); });
      title.textContent = label; number.textContent = Number(value || 0).toLocaleString('zh-CN'); detail.textContent = note; card.append(title, number, detail); return card;
    }));
    el('statisticsRows').replaceChildren(...data.rows.map(row => {
      const tr = document.createElement('tr');
      const values = [row.date, row.accountName, ...metrics.map(key => row[key])];
      values.forEach((value, index) => {
        const td = document.createElement('td'); td.textContent = String(value);
        if (index === 11 && row.quantityMissing) td.textContent = `${row.quantity}（${row.quantityMissing} 包裹缺失）`;
        if (index === 9) {
          const button = document.createElement('button'); button.type = 'button'; button.className = 'statistics-order-link';
          button.textContent = String(value); button.title = `查看${row.accountName}订单明细`;
          button.addEventListener('click', () => openOrderAccount(row.accountId)); td.replaceChildren(button);
        }
        tr.append(td);
      });
      return tr;
    }));
    const missing = [];
    if (data.totals.quantityMissing) missing.push(`${data.totals.quantityMissing} 个包裹缺少件数`);
    const syncTimes = Object.values(data.lastOrderSync || {}).sort();
    const sync = syncTimes.length ? `；订单最近同步 ${new Date(syncTimes.at(-1)).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false})}` : '；订单尚无本地同步记录';
    el('statisticsStatus').textContent = `${data.rows.length} 条每日账号记录${sync}。${[...data.warnings, ...missing].join('；')}`;
    el('statisticsExport').disabled = false;
    renderChart();
  }
  async function open() {
    dateShortcuts();
    const request = ++revision;
    requestController?.abort(); requestController = new AbortController();
    current = null; el('statisticsExport').disabled = true;
    clearChart('正在读取每日账号统计…');
    el('statisticsRows').replaceChildren(); el('statisticsCards').replaceChildren();
    el('statisticsStatus').textContent = '正在读取每日账号统计…';
    const query = new URLSearchParams({start: el('statisticsStart').value, end: el('statisticsEnd').value, accountId: el('statisticsAccount').value || 'all'});
    try {
      const response = await fetch(`/api/statistics/daily?${query}`, {signal: requestController.signal});
      const result = await response.json();
      if (request !== revision) return;
      if (!response.ok || !Array.isArray(result.data?.rows)) throw new Error(result.error || '统计结果不完整');
      render(result.data);
    } catch (error) {
      if (request === revision && error.name !== 'AbortError') el('statisticsStatus').textContent = `统计读取失败：${error.message}`;
    }
  }
  function setPage(page) {
    // Navigation starts with today's Beijing date. In-page queries and account
    // changes still use the user's selected range via open().
    if (page === 'dataStatistics') {
      el('statisticsStart').value = el('statisticsEnd').value = beijingDate();
      dateShortcuts();
    }
    const active = page === 'dataStatistics' || page === 'orders';
    document.body.classList.toggle('statistics-workspace-active', active);
    const collectionContext = document.querySelector('.collection-context-bar');
    if (collectionContext) collectionContext.hidden = active;
    el('statisticsTabs').hidden = !active;
    ['statisticsDailyTab', 'statisticsOrdersTab'].forEach((id, index) => {
      const selected = index === 0 ? page === 'dataStatistics' : page === 'orders';
      el(id).setAttribute('aria-selected', String(selected));
      el(id).className = `button ${selected ? 'primary' : 'subtle'}`;
    });
  }
  function csvCell(value) {
    let text = String(value ?? '');
    if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
    return '"' + text.replace(/"/g, '""') + '"';
  }
  document.addEventListener('DOMContentLoaded', () => {
    el('statisticsMetric').value = 'orders';
    chartView('bar');
    el('statisticsMetric').addEventListener('change', renderChart);
    el('statisticsChartView').addEventListener('click', () => chartView('bar'));
    el('statisticsPieView').addEventListener('click', () => chartView('pie'));
    el('statisticsTableView').addEventListener('click', () => chartView('table'));
    el('statisticsStart').value = el('statisticsEnd').value = beijingDate();
    dateShortcuts();
    el('statisticsFilters').addEventListener('submit', event => { event.preventDefault(); open(); });
    ['statisticsStart', 'statisticsEnd'].forEach(id => el(id).addEventListener('change', invalidate));
    el('statisticsAccount').addEventListener('change', open);
    [['statisticsToday', 0], ['statisticsWeek', -6]].forEach(([id, offset]) => el(id).addEventListener('click', () => {
      el('statisticsStart').value = beijingDate(offset); el('statisticsEnd').value = beijingDate(); open();
    }));
    el('statisticsDailyTab').addEventListener('click', () => activatePage('dataStatistics'));
    el('statisticsOrdersTab').addEventListener('click', () => openOrderAccount(activeOrderAccountId));
    el('statisticsExport').addEventListener('click', () => {
      if (!current) return;
      const data = [[...headings, '缺少件数的包裹数'], ...current.rows.map(row => [row.date, row.accountName, ...metrics.map(key => row[key]), row.quantityMissing])];
      const url = URL.createObjectURL(new Blob(['\ufeff' + data.map(row => row.map(csvCell).join(',')).join('\r\n')], {type: 'text/csv;charset=utf-8'}));
      const link = document.createElement('a'); link.href = url; link.download = `妙手每日统计_${current.start}_${current.end}.csv`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  });
  window.dataStatistics = {open, setPage, accountsChanged};
})();
