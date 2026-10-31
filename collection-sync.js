/* Full-box import runs on the backend; page filters never limit its scope. */
(() => {
  const accountId = window.collectionAccountContext.id;
  const button = document.getElementById('syncCollectionPool');
  const toggle = document.getElementById('hourlyCollectionSync');
  const status = document.getElementById('collectionPoolSyncStatus');
  const next = document.getElementById('collectionSyncNext');
  const details = document.getElementById('collectionPoolSyncFailures');
  let revision = 0, busy = false, timer, latest = null, lastFinish = null;
  const date = value => new Date(value * 1000).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false});
  async function request(path = '', body) {
    const response = await fetch('/api/collection-sync' + path, body === undefined ? {cache: 'no-store'} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) throw Error(response.status === 404 ? '后台尚未启用新版同步，请等待安全升级完成后刷新' : result.error || '同步请求失败');
    if (result.data?.accountId !== accountId) throw Error('同步账号不一致，请刷新页面');
    return result.data;
  }
  function render(data) {
    latest = data;
    toggle.checked = data.enabled; toggle.disabled = busy;
    button.disabled = busy || data.running;
    button.textContent = data.running ? '后台正在同步…' : '全箱一键同步到自动化池';
    next.textContent = data.enabled ? (data.nextRunAt <= Date.now() / 1000
      ? '已到同步时间，等待当前任务空闲' : `下次自动同步：${date(data.nextRunAt)}`) : '自动同步已关闭';
    const row = data.result;
    if (!row) {
      status.textContent = data.running ? '其他采集箱正在同步，请稍候。' : '一键读取当前采集箱全部商品并增量入池；已有商品自动跳过。';
      details.hidden = true; return;
    }
    const stages = {queued: '等待同步', orders: '正在同步订单', scanning: `正在读取全箱商品，已读取 ${row.total} 件`,
      importing: `正在入池：${row.processed}/${row.total} 件`, finished: row.status === 'completed' ? '同步完成' : '同步结束，部分内容需重试', interrupted: '同步已中断'};
    status.textContent = `${stages[row.stage] || '正在同步'} · 新增 ${row.added} 件 · 已有 ${row.skipped} 件 · 副本跳过 ${row.replicas} 件 · 失败 ${row.failed} 件`
      + (row.orders === null ? '' : ` · 订单 ${row.orders} 条`)
      + (row.finishedAt ? ` · ${date(row.finishedAt)}` : '')
      + (data.running && !['queued', 'running'].includes(row.status) ? '；其他采集箱仍在同步' : '');
    const errors = [row.error, row.orderError && `订单：${row.orderError}`,
      ...(row.failures || []).map(item => `商品 ${item.detailId}：${item.error}`)].filter(Boolean);
    if (row.failed > (row.failures || []).length) errors.push(`另有 ${row.failed - row.failures.length} 件失败，可再次点击全箱同步重试。`);
    const list = details.querySelector('ul'); list.replaceChildren();
    for (const error of errors) { const item = document.createElement('li'); item.textContent = error; list.append(item); }
    details.hidden = !errors.length;
    if (lastFinish !== null && row.finishedAt && row.finishedAt !== lastFinish) {
      window.sharedAutomation?.refresh('pool');
      if (typeof loadLiveProducts === 'function') loadLiveProducts(true);
      if (typeof loadOrders === 'function' && document.getElementById('orders')?.classList.contains('active')) loadOrders();
    }
    lastFinish = row.finishedAt || 0;
  }
  function schedule() {
    window.clearTimeout(timer);
    timer = window.setTimeout(refresh, latest?.running ? 3000 : 15000);
  }
  async function refresh() {
    if (busy) return;
    const current = ++revision;
    try { const data = await request(); if (current === revision) render(data); }
    catch (error) { if (current === revision) { status.textContent = `同步状态读取失败：${error.message}`; button.disabled = true; toggle.disabled = true; } }
    finally { if (current === revision) schedule(); }
  }
  async function mutate(path, body) {
    if (busy) return;
    busy = true; ++revision; window.clearTimeout(timer);
    button.disabled = toggle.disabled = true;
    try { const data = await request(path, body); busy = false; render(data); }
    catch (error) {
      busy = false;
      if (latest) render(latest);
      status.textContent = error.message;
    } finally { busy = false; schedule(); }
  }
  button.addEventListener('click', () => mutate('', {}));
  toggle.addEventListener('change', () => mutate('/settings', {enabled: toggle.checked}));
  refresh();
})();
