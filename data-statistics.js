(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const metrics = ['collected', 'tasks', 'saved', 'submitted', 'editFailed', 'publishFailed', 'needsInput', 'orders', 'packages', 'quantity'];
  const headings = ['日期', '妙手账号', '采集入池', '新建任务', '保存成功', '发布已提交', '编辑异常', '发布异常', '待选择', '订单数', '包裹数', '已知件数'];
  let revision = 0, current = null, requestController = null;
  function beijingDate(offset = 0) {
    return new Date(Date.now() + 8 * 3600000 + offset * 86400000).toISOString().slice(0, 10);
  }
  function invalidate() {
    revision += 1;
    requestController?.abort();
    current = null;
    el('statisticsExport').disabled = true;
    el('statisticsRows').replaceChildren();
    el('statisticsCards').replaceChildren();
    el('statisticsStatus').textContent = '筛选已修改，请查询统计。';
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
    const cards = [['采集入池', data.totals.collected], ['保存成功', data.totals.saved],
      ['发布已提交', data.totals.submitted], ['编辑／发布异常', data.totals.editFailed + data.totals.publishFailed],
      ['订单数', data.totals.orders]];
    el('statisticsCards').replaceChildren(...cards.map(([label, value]) => {
      const card = document.createElement('div'), title = document.createElement('span'), number = document.createElement('strong');
      title.textContent = label; number.textContent = String(value); card.append(title, number); return card;
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
    el('statisticsStatus').textContent = `${data.start} 至 ${data.end} · ${data.rows.length} 条每日账号记录${sync}。${[...data.warnings, ...missing].join('；')}`;
    el('statisticsExport').disabled = false;
  }
  async function open() {
    const request = ++revision;
    requestController?.abort(); requestController = new AbortController();
    current = null; el('statisticsExport').disabled = true;
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
    const active = page === 'dataStatistics' || page === 'orders';
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
    el('statisticsStart').value = beijingDate(-6); el('statisticsEnd').value = beijingDate();
    el('statisticsFilters').addEventListener('submit', event => { event.preventDefault(); open(); });
    ['statisticsStart', 'statisticsEnd', 'statisticsAccount'].forEach(id => el(id).addEventListener('change', invalidate));
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
