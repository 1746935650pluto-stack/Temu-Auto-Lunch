(() => {
  'use strict';

  const $ = (selector) => document.querySelector(selector);
  const readButtons = () => [...document.querySelectorAll('[data-zhihou-read]')];
  const previewButtons = () => [...document.querySelectorAll('[data-zhihou-preview]')];
  let requestBusy = false;
  let connectedState = false;
  let storesLoaded = false;
  let storesLoading = false;
  let pendingPreview = null;
  const orderRows = new Map();
  const selectedOrderKeys = new Set();

  const labels = {
    stores: '店铺',
    channels: '承运商与渠道',
    warehouses: '仓库集群',
    orders: '订单',
  };
  const writeLabels = { create: '创建订单', sync: '同步单号／面单', cancel: '取消订单' };

  function setStatus(selector, message, failed = false) {
    const node = $(selector);
    if (!node) return;
    node.textContent = message;
    node.classList.toggle('is-error', failed);
  }

  async function request(path, body) {
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(body),
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) { /* handled below */ }
    if (!response.ok) throw new Error(payload.error || '智猴请求失败，请稍后重试');
    return payload.data;
  }

  function setControls(connected = connectedState) {
    const busy = requestBusy || storesLoading;
    readButtons().forEach((button) => { button.disabled = !connected || busy; });
    previewButtons().forEach((button) => { button.disabled = !connected || busy; });
    const orderSearch = $('#zhihouOrderSearch');
    if (orderSearch) orderSearch.disabled = !connected || busy;
    const syncStoresAndOrders = $('#zhihouSyncStoresAndOrders');
    if (syncStoresAndOrders) syncStoresAndOrders.disabled = !connected || busy;
    const disconnect = $('#zhihouDisconnect');
    if (disconnect) disconnect.disabled = !connected || busy;
    const login = $('#zhihouLoginSubmit');
    if (login) login.disabled = busy;
    const submit = $('#zhihouWriteSubmit');
    if (submit) submit.disabled = !connected || busy || !pendingPreview;
    updateSelectionControls();
  }

  function setConnection(status) {
    const connected = Boolean(status?.connected);
    connectedState = connected;
    const pill = $('#zhihouConnectionState');
    if (pill) {
      pill.textContent = connected ? '已连接' : '未连接';
      pill.className = `status-pill ${connected ? 'success' : 'neutral'}`;
      pill.title = status?.expiresAt ? `令牌到期：${status.expiresAt}` : '';
    }
    if (!connected) {
      storesLoaded = false;
      storesLoading = false;
      populateStores([]);
      if (pendingPreview) clearWritePreview('登录已断开，原写入预览已作废；重新登录后请重新生成预览。');
    }
    setControls(connected);
    return connected;
  }

  function splitValues(value) {
    return String(value || '').split(/[\s,，]+/).map((item) => item.trim()).filter(Boolean);
  }

  function safeValue(value) {
    if (value === null || value === undefined) return '';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }

  function rowsFrom(data, preferredKeys = []) {
    if (Array.isArray(data)) return data;
    if (!data || typeof data !== 'object') return [];
    const keys = [...preferredKeys, 'list', 'records', 'rows', 'items', 'data'];
    for (const key of keys) {
      const value = data[key];
      if (Array.isArray(value)) return value;
    }
    const nested = data.data;
    if (nested && nested !== data && typeof nested === 'object') return rowsFrom(nested, preferredKeys);
    return [];
  }

  function resultTotal(data, rows) {
    const value = data?.totalCount ?? data?.total ?? data?.count ?? data?.data?.totalCount ?? data?.data?.total;
    const total = Number(value);
    return Number.isFinite(total) ? total : rows.length;
  }

  function clearWritePreview(message = '填写一种写入操作后先生成预览；尚未向智猴提交。') {
    pendingPreview = null;
    const target = $('#zhihouWritePreview');
    if (target) {
      target.replaceChildren();
      const empty = document.createElement('p');
      empty.className = 'live-empty';
      empty.textContent = message;
      target.append(empty);
    }
    setControls();
  }

  function renderWritePreview(preview) {
    pendingPreview = preview;
    const target = $('#zhihouWritePreview');
    if (!target) return;
    target.replaceChildren();
    const head = document.createElement('div');
    head.className = 'zhihou-preview-head';
    const title = document.createElement('strong');
    title.textContent = `${writeLabels[preview.action] || '写入'}预览（尚未提交）`;
    const expires = document.createElement('span');
    expires.textContent = `失效时间：${String(preview.expiresAt || '').replace('T', ' ').replace('.000Z', ' UTC')}`;
    head.append(title, expires);
    const body = document.createElement('div');
    body.className = 'zhihou-preview-body';
    const note = document.createElement('p');
    note.textContent = preview.message || '请逐项核对，确认后仅提交一次。';
    const code = document.createElement('pre');
    code.textContent = JSON.stringify(preview.payload, null, 2);
    body.append(note, code);
    target.append(head, body);
    setStatus('#zhihouWriteStatus', '预览已生成。核对字段后点击“确认并仅提交一次”，随后还会出现浏览器确认。');
    setControls();
  }

  function displayWriteResult(result) {
    const target = $('#zhihouWritePreview');
    if (!target) return;
    target.replaceChildren();
    const head = document.createElement('div');
    head.className = 'zhihou-preview-head';
    const title = document.createElement('strong');
    title.textContent = `${writeLabels[result.action] || '写入'}：智猴已返回结果`;
    head.append(title);
    const body = document.createElement('div');
    body.className = 'zhihou-preview-body';
    const note = document.createElement('p');
    note.textContent = result.message || '本次预览已消费，不能再次提交。';
    const code = document.createElement('pre');
    code.textContent = JSON.stringify(result.data, null, 2);
    body.append(note, code);
    target.append(head, body);
  }

  function firstValue(row, keys, fallback = '') {
    for (const key of keys) {
      const value = row?.[key];
      if (value !== null && value !== undefined && String(value).trim()) return safeValue(value);
    }
    return fallback;
  }

  function orderKey(row, index) {
    return firstValue(row, ['id', 'orderId', 'orderNo', 'parcelNo', 'transferOrderNo'], `row-${index}`);
  }

  function updateSelectionControls() {
    const count = selectedOrderKeys.size;
    const label = $('#zhihouSelectedCount');
    if (label) label.textContent = `已选 ${count} 条`;
    const use = $('#zhihouUseSelected');
    if (use) use.disabled = requestBusy || count !== 1;
    const clear = $('#zhihouClearSelection');
    if (clear) clear.disabled = requestBusy || !count;
  }

  function clearOrderSelection() {
    selectedOrderKeys.clear();
    document.querySelectorAll('[data-zhihou-order-check]').forEach((node) => { node.checked = false; });
    const all = $('#zhihouSelectAll');
    if (all) { all.checked = false; all.indeterminate = false; }
    updateSelectionControls();
  }

  function fillCreateFromSelectedOrder() {
    if (selectedOrderKeys.size !== 1) return;
    const row = orderRows.get([...selectedOrderKeys][0]);
    if (!row) return;
    const form = document.querySelector('[data-zhihou-write="create"]');
    if (!form) return;
    const values = {
      storeId: firstValue(row, ['storeId', 'shopId']),
      orderNo: firstValue(row, ['orderNo', 'parcelNo', 'orderNumber']),
      country: firstValue(row, ['country', 'countryName'], '美国'),
      countryCode: firstValue(row, ['countryCode'], 'US'),
      zipcode: firstValue(row, ['zipcode', 'zipCode', 'postalCode']),
      fullName: firstValue(row, ['fullName', 'receiverName', 'consigneeName']),
      province: firstValue(row, ['province', 'state']),
      provinceCode: firstValue(row, ['provinceCode', 'stateCode']),
      city: firstValue(row, ['city']),
      phone: firstValue(row, ['phone', 'receiverPhone']),
      address: firstValue(row, ['address', 'address1', 'detailAddress']),
    };
    Object.entries(values).forEach(([name, value]) => {
      const input = form.elements.namedItem(name);
      if (input && value) input.value = value;
    });
    const section = form.closest('details');
    if (section) section.open = true;
    form.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setStatus('#zhihouWriteStatus', '已带入所选订单的可识别字段。渠道、分仓和报关商品信息请核对并补齐后再生成预览。');
  }

  function displayOrders(data, rows, total) {
    const target = $('#zhihouResult');
    if (!target) return;
    orderRows.clear();
    selectedOrderKeys.clear();
    rows.slice(0, 100).forEach((row, index) => orderRows.set(orderKey(row, index), row));
    const selectionBar = $('#zhihouSelectionBar');
    if (selectionBar) selectionBar.hidden = false;
    const heading = document.createElement('div');
    heading.className = 'zhihou-result-head';
    const title = document.createElement('strong');
    title.textContent = `订单 ${Number.isFinite(total) ? total : rows.length} 条`;
    const page = document.createElement('span');
    page.textContent = data?.pageNo ? `第 ${data.pageNo} 页 · 可勾选后处理` : '可勾选后处理';
    heading.append(title, page); target.append(heading);
    if (!rows.length) {
      const empty = document.createElement('p'); empty.className = 'live-empty'; empty.textContent = '没有返回可显示的订单。请调整筛选条件或确认智猴账号权限。'; target.append(empty); updateSelectionControls(); return;
    }
    const wrap = document.createElement('div'); wrap.className = 'zhihou-order-table-wrap';
    const table = document.createElement('table'); table.className = 'zhihou-order-table';
    const header = document.createElement('thead'); const headerRow = document.createElement('tr');
    const selectAllCell = document.createElement('th');
    const selectAll = document.createElement('input'); selectAll.type = 'checkbox'; selectAll.id = 'zhihouSelectAll'; selectAll.setAttribute('aria-label', '全选本页订单');
    selectAll.addEventListener('change', () => {
      const keys = [...orderRows.keys()];
      if (selectAll.checked) keys.forEach((key) => selectedOrderKeys.add(key)); else selectedOrderKeys.clear();
      document.querySelectorAll('[data-zhihou-order-check]').forEach((node) => { node.checked = selectAll.checked; });
      updateSelectionControls();
    });
    selectAllCell.append(selectAll); headerRow.append(selectAllCell);
    ['订单号', '店铺', '目的地', '渠道／状态', '创建时间', '操作'].forEach((name) => { const cell = document.createElement('th'); cell.textContent = name; headerRow.append(cell); });
    header.append(headerRow); table.append(header);
    const body = document.createElement('tbody');
    rows.slice(0, 100).forEach((row, index) => {
      const key = orderKey(row, index); const tr = document.createElement('tr');
      const checkCell = document.createElement('td'); const check = document.createElement('input'); check.type = 'checkbox'; check.dataset.zhihouOrderCheck = key; check.setAttribute('aria-label', `选择订单 ${firstValue(row, ['orderNo', 'parcelNo'], key)}`);
      check.addEventListener('change', () => {
        if (check.checked) selectedOrderKeys.add(key); else selectedOrderKeys.delete(key);
        selectAll.checked = selectedOrderKeys.size === orderRows.size; selectAll.indeterminate = selectedOrderKeys.size > 0 && selectedOrderKeys.size < orderRows.size;
        updateSelectionControls();
      });
      checkCell.append(check); tr.append(checkCell);
      const cells = [
        firstValue(row, ['orderNo', 'parcelNo', 'orderNumber'], '—'),
        firstValue(row, ['storeName', 'shopName', 'storeId', 'shopId'], '—'),
        firstValue(row, ['countryCode', 'country', 'destinationCountry'], '—'),
        [firstValue(row, ['channelName', 'channel', 'logisticsChannel'], ''), firstValue(row, ['statusName', 'status', 'parcelStatus'], '')].filter(Boolean).join(' · ') || '—',
        firstValue(row, ['createTime', 'createdAt', 'orderCreateTime'], '—'),
      ];
      cells.forEach((value, cellIndex) => { const cell = document.createElement('td'); cell.textContent = value; if (cellIndex === 0) cell.className = 'zhihou-order-number'; tr.append(cell); });
      const actionCell = document.createElement('td'); const use = document.createElement('button'); use.type = 'button'; use.className = 'zhihou-row-action'; use.textContent = '处理';
      use.addEventListener('click', () => { selectedOrderKeys.clear(); selectedOrderKeys.add(key); document.querySelectorAll('[data-zhihou-order-check]').forEach((node) => { node.checked = node === check; }); selectAll.checked = false; selectAll.indeterminate = true; updateSelectionControls(); fillCreateFromSelectedOrder(); });
      actionCell.append(use); tr.append(actionCell); body.append(tr);
    });
    table.append(body); wrap.append(table); target.append(wrap); updateSelectionControls();
  }

  function populateStores(rows) {
    const select = $('#zhihouStoreId');
    if (!select) return;
    const current = select.value;
    select.replaceChildren();
    const all = document.createElement('option'); all.value = ''; all.textContent = '全部店铺'; select.append(all);
    rows.slice(0, 200).forEach((row) => {
      const id = firstValue(row, ['id', 'storeId', 'shopId']);
      if (!id) return;
      const option = document.createElement('option'); option.value = id; option.textContent = `${firstValue(row, ['name', 'storeName', 'shopName'], '未命名店铺')} (${id})`; select.append(option);
    });
    if ([...select.options].some((option) => option.value === current)) select.value = current;
  }

  async function ensureStores(force = false, throwOnError = false) {
    if (!connectedState || storesLoading || (!force && storesLoaded)) return [];
    storesLoading = true;
    setControls();
    setStatus('#zhihouReadStatus', '正在读取店铺…');
    try {
      const result = await request('/api/zhihou/read', { confirmation: 'READ_ZHIHOU', action: 'stores' });
      const rows = rowsFrom(result.data, ['stores', 'storeList', 'shopList']);
      populateStores(rows);
      storesLoaded = true;
      setStatus('#zhihouReadStatus', rows.length ? `已同步店铺 ${rows.length} 家。` : '已同步店铺，但智猴未返回可选店铺。请确认账号权限。');
      return rows;
    } catch (error) {
      setStatus('#zhihouReadStatus', error.message || '店铺读取失败，请点击“刷新店铺”重试。', true);
      if (/登录已失效/.test(error.message || '')) await refresh();
      if (throwOnError) throw error;
      return [];
    } finally {
      storesLoading = false;
      setControls();
    }
  }

  function orderQueryBody() {
    return {
      storeId: $('#zhihouStoreId')?.value.trim() || '',
      beginCreateTime: $('#zhihouBeginTime')?.value.trim() || '',
      endCreateTime: $('#zhihouEndTime')?.value.trim() || '',
      orderNos: splitValues($('#zhihouOrderNos')?.value),
      transferOrderNos: splitValues($('#zhihouTransferOrderNos')?.value),
      pageNo: 1,
      pageSize: Number($('#zhihouPageSize')?.value || 50),
    };
  }

  async function readOrders(body = orderQueryBody()) {
    const result = await request('/api/zhihou/read', { confirmation: 'READ_ZHIHOU', action: 'orders', ...body });
    displayRows('orders', result.data);
    const rows = rowsFrom(result.data);
    return { rows, total: resultTotal(result.data, rows) };
  }

  async function syncStoresAndOrders() {
    if (requestBusy || storesLoading || !connectedState) return;
    requestBusy = true;
    setControls(true);
    setStatus('#zhihouReadStatus', '正在同步店铺和订单…');
    try {
      const stores = await ensureStores(true, true);
      const orders = await readOrders();
      const pageSize = Number($('#zhihouPageSize')?.value || 50);
      setStatus('#zhihouReadStatus', `已同步店铺 ${stores.length} 家；已同步当前筛选的订单 ${orders.rows.length}/${orders.total} 条（第 1 页，最多 ${pageSize} 条）。`);
    } catch (error) {
      setStatus('#zhihouReadStatus', error.message || '店铺或订单同步失败', true);
    } finally {
      requestBusy = false;
      await refresh();
    }
  }

  function displayRows(action, data) {
    const target = $('#zhihouResult');
    if (!target) return;
    target.replaceChildren();
    const rawRows = rowsFrom(data, action === 'stores' ? ['stores', 'storeList', 'shopList'] : []);
    const total = resultTotal(data, rawRows);
    if (action === 'stores') populateStores(rawRows);
    if (action === 'orders') {
      displayOrders(data, rawRows, total);
      return;
    }
    orderRows.clear();
    selectedOrderKeys.clear();
    const selectionBar = $('#zhihouSelectionBar');
    if (selectionBar) selectionBar.hidden = true;
    const heading = document.createElement('div');
    heading.className = 'zhihou-result-head';
    const title = document.createElement('strong');
    title.textContent = `已读取${labels[action]} ${Number.isFinite(total) ? total : rawRows.length} 条`;
    heading.append(title);
    if (action === 'orders' && data?.pageNo) {
      const page = document.createElement('span');
      page.textContent = `第 ${data.pageNo} 页`;
      heading.append(page);
    }
    target.append(heading);
    if (!rawRows.length) {
      const empty = document.createElement('p');
      empty.className = 'live-empty';
      empty.textContent = '没有返回可显示的数据。请调整筛选条件或确认智猴账号权限。';
      target.append(empty);
      return;
    }
    const list = document.createElement('div');
    list.className = 'zhihou-result-list';
    rawRows.slice(0, 100).forEach((row, index) => {
      const card = document.createElement('article');
      card.className = 'zhihou-result-row';
      const name = document.createElement('strong');
      name.textContent = safeValue(row?.name || row?.storeName || row?.orderNo || row?.parcelNo || row?.id || `记录 ${index + 1}`);
      card.append(name);
      const fields = document.createElement('dl');
      Object.entries(row && typeof row === 'object' ? row : { value: row })
        .filter(([key]) => !/token|password|secret/i.test(key))
        .slice(0, 12)
        .forEach(([key, value]) => {
          const term = document.createElement('dt');
          term.textContent = key;
          const description = document.createElement('dd');
          description.textContent = safeValue(value).slice(0, 500);
          fields.append(term, description);
        });
      card.append(fields);
      list.append(card);
    });
    target.append(list);
  }

  async function refresh() {
    try {
      const response = await fetch('/api/zhihou/status', { credentials: 'same-origin', cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || '无法读取智猴连接状态');
      const connected = setConnection(payload.data);
      if (connected) {
        setStatus('#zhihouLoginStatus', payload.data.message || '已连接');
        void ensureStores();
      }
      return connected;
    } catch (error) {
      setConnection({ connected: false });
      setStatus('#zhihouLoginStatus', error.message || '无法读取智猴连接状态', true);
      return false;
    }
  }

  async function runRead(action, body = {}) {
    if (requestBusy) return;
    requestBusy = true;
    setControls(true);
    setStatus('#zhihouReadStatus', `正在读取${labels[action]}…`);
    try {
      if (action === 'orders') await readOrders(body);
      else {
        const result = await request('/api/zhihou/read', { confirmation: 'READ_ZHIHOU', action, ...body });
        displayRows(action, result.data);
        if (action === 'stores') storesLoaded = true;
      }
      setStatus('#zhihouReadStatus', `已读取${labels[action]}；仅展示前 100 条。`);
    } catch (error) {
      setStatus('#zhihouReadStatus', error.message || '智猴查询失败', true);
      if (/登录已失效/.test(error.message || '')) await refresh();
    } finally {
      requestBusy = false;
      await refresh();
    }
  }

  async function runWritePreview(form) {
    if (requestBusy) return;
    const action = form.dataset.zhihouWrite;
    requestBusy = true;
    setControls();
    setStatus('#zhihouWriteStatus', `正在校验${writeLabels[action]}字段并生成本机预览…`);
    try {
      const fields = Object.fromEntries(new FormData(form).entries());
      const preview = await request('/api/zhihou/write-preview', {
        confirmation: 'PREVIEW_ZHIHOU_WRITE', action, ...fields,
      });
      renderWritePreview(preview);
    } catch (error) {
      setStatus('#zhihouWriteStatus', error.message || '无法生成写入预览', true);
      if (/登录已失效/.test(error.message || '')) await refresh();
    } finally {
      requestBusy = false;
      await refresh();
    }
  }

  $('#zhihouLoginForm')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (requestBusy) return;
    requestBusy = true;
    setControls(false);
    setStatus('#zhihouLoginStatus', '正在连接智猴…');
    const password = $('#zhihouPassword');
    try {
      const status = await request('/api/zhihou/login', {
        account: $('#zhihouAccount').value,
        password: password.value,
        tokenLifetime: $('#zhihouTokenLifetime').value,
        confirmation: 'LOGIN_ZHIHOU',
      });
      password.value = '';
      setConnection(status);
      setStatus('#zhihouLoginStatus', status.message || '已连接智猴');
      setStatus('#zhihouReadStatus', '已连接，可开始读取智猴数据。');
      setStatus('#zhihouWriteStatus', '已连接。填写操作字段后先生成预览。');
      await ensureStores(true, true);
      const orders = await readOrders();
      const pageSize = Number($('#zhihouPageSize')?.value || 50);
      setStatus('#zhihouReadStatus', `已同步店铺和订单；当前显示订单 ${orders.rows.length}/${orders.total} 条（第 1 页，最多 ${pageSize} 条）。`);
    } catch (error) {
      password.value = '';
      setStatus('#zhihouLoginStatus', error.message || '智猴登录失败', true);
    } finally {
      requestBusy = false;
      await refresh();
    }
  });

  $('#zhihouDisconnect')?.addEventListener('click', async () => {
    if (requestBusy) return;
    requestBusy = true;
    setControls(true);
    try {
      const status = await request('/api/zhihou/disconnect', { confirmation: 'DISCONNECT_ZHIHOU' });
      setConnection(status);
      setStatus('#zhihouLoginStatus', '已断开；内存中的智猴令牌已清除。');
      setStatus('#zhihouReadStatus', '请先登录智猴。');
      setStatus('#zhihouWriteStatus', '请先登录智猴。');
      $('#zhihouResult')?.replaceChildren();
      clearWritePreview('已断开连接，写入预览已清除。');
    } catch (error) {
      setStatus('#zhihouLoginStatus', error.message || '断开失败', true);
    } finally {
      requestBusy = false;
      await refresh();
    }
  });

  readButtons().forEach((button) => button.addEventListener('click', () => runRead(button.dataset.zhihouRead)));
  $('#zhihouSyncStoresAndOrders')?.addEventListener('click', syncStoresAndOrders);
  $('#zhihouOrderForm')?.addEventListener('submit', (event) => {
    event.preventDefault();
    runRead('orders', orderQueryBody());
  });
  $('#zhihouUseSelected')?.addEventListener('click', fillCreateFromSelectedOrder);
  $('#zhihouClearSelection')?.addEventListener('click', clearOrderSelection);
  document.querySelectorAll('.zhihou-write-form').forEach((form) => form.addEventListener('submit', (event) => {
    event.preventDefault();
    runWritePreview(form);
  }));
  $('#zhihouWriteSubmit')?.addEventListener('click', async () => {
    if (requestBusy || !pendingPreview) return;
    const action = writeLabels[pendingPreview.action] || '本次写入';
    if (!window.confirm(`确认向智猴提交“${action}”吗？该操作仅提交一次；网络异常或结果不明时不会自动重试。`)) return;
    const previewId = pendingPreview.previewId;
    pendingPreview = null;
    requestBusy = true;
    setControls();
    setStatus('#zhihouWriteStatus', `正在向智猴提交${action}；请勿重复点击…`);
    try {
      const result = await request('/api/zhihou/write-submit', {
        confirmation: 'SUBMIT_ZHIHOU_WRITE', previewId,
      });
      displayWriteResult(result);
      setStatus('#zhihouWriteStatus', `${action}已收到智猴响应。预览已消费，不能再次提交。`);
    } catch (error) {
      setStatus('#zhihouWriteStatus', `${error.message || '写入请求未完成'}。预览已消费，不会自动重试；请查询智猴订单状态后再决定下一步。`, true);
      if (/登录已失效/.test(error.message || '')) await refresh();
    } finally {
      requestBusy = false;
      await refresh();
    }
  });

  window.zhiHouModule = { refresh };
  refresh();
})();
