/* Automatic runs always carry an explicit source filter and one target shop. */
(() => {
  const $ = id => document.getElementById(id);
  let config = null, submitting = false, optionsReady = false, optionsRevision = 0, listRevision = 0;
  let timer, requestKey = null, requestSignature = '';
  const time = seconds => new Date(seconds * 1000).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false});
  const node = (tag, text, cls) => { const n = document.createElement(tag); n.textContent = text || ''; if (cls) n.className = cls; return n; };
  function option(parent, value, text) { const n = node('option', text); n.value = value; parent.append(n); }
  async function api(route, body) {
    const res = await fetch('/api/automation/shared/auto-' + route, body === undefined ? {cache: 'no-store'} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const result = await res.json();
    if (!res.ok) throw Error(result.error || '自动任务请求失败');
    return result.data;
  }
  function accountName(id) { return config?.accounts.find(a => a.id === Number(id))?.name || `采集箱 ${id}`; }
  function shopName(account, shop) {
    const row = config?.targets.find(a => a.accountId === account)?.shops.find(s => Number(s.shopId) === shop);
    return `${accountName(account)} / ${row?.shopNick || row?.platformShopName || shop}（${shop}）`;
  }
  function controls() {
    const mode = $('autoTaskMode').value, pricing = $('autoTaskPriceMode').value;
    $('autoTaskTimeField').hidden = mode === 'now'; $('autoTaskTime').required = mode !== 'now';
    $('autoTaskTime').disabled = mode === 'now';
    $('autoTaskEveryField').hidden = mode !== 'interval';
    $('autoTaskEvery').disabled = mode !== 'interval'; $('autoTaskEvery').required = mode === 'interval';
    $('autoTaskPriceField').hidden = pricing !== 'floor'; $('autoTaskMultiplierField').hidden = pricing !== 'source_multiplier';
    $('autoTaskPrice').disabled = pricing !== 'floor'; $('autoTaskPrice').required = pricing === 'floor';
    $('autoTaskMultiplier').disabled = pricing !== 'source_multiplier'; $('autoTaskMultiplier').required = pricing === 'source_multiplier';
    $('autoTaskSubmit').textContent = submitting ? '正在提交…' : mode === 'now' ? '立即执行：编辑并发布' : '创建定时编辑发布任务';
    $('autoTaskSubmit').disabled = submitting || !optionsReady || !$('autoTaskConfirm').checked;
  }
  function people(reset = false) {
    const select = $('autoTaskPerson'), source = $('autoTaskSource').value, previous = reset ? '' : select.value;
    select.replaceChildren(); option(select, '', '全部人员'); option(select, 'unknown', '未知归属');
    const groups = new Map();
    for (const person of config.people) {
      if (source !== '0' && String(person.accountId) !== source) continue;
      if (!groups.has(person.accountId)) {
        const group = node('optgroup'); group.label = accountName(person.accountId); groups.set(person.accountId, group); select.append(group);
      }
      option(groups.get(person.accountId), person.key, `${person.name}（人员 ID ${person.personId}）`);
    }
    if (previous && ![...select.options].some(o => o.value === previous)) throw Error('所选人员已不可用，请重新选择来源与人员');
    select.value = previous;
  }
  function sites(reset = false) {
    const select = $('autoTaskSite'), previous = reset ? '' : select.value;
    const selectedShop = $('autoTaskShop').value;
    select.replaceChildren();
    if (!selectedShop) {
      option(select, '', '请先选择发布店铺'); select.disabled = true; return;
    }
    const account = Number(selectedShop.split(':')[0]);
    const available = config.targets.find(t => t.accountId === account)?.sites || [];
    if (!available.length) {
      option(select, '', '该账号没有可用发布站点'); select.disabled = true; return;
    }
    for (const site of available) option(select, String(site.key), `${site.value || site.key}（${site.key}）`);
    if (previous && [...select.options].some(o => o.value === previous)) select.value = previous;
    else if ([...select.options].some(o => o.value === 'US')) select.value = 'US';
    else select.value = select.options[0].value;
    select.disabled = !optionsReady;
  }
  async function loadOptions() {
    const revision = ++optionsRevision; optionsReady = false; controls();
    for (const id of ['autoTaskSource', 'autoTaskPerson', 'autoTaskShop', 'autoTaskSite']) $(id).disabled = true;
    try {
      const data = await api('options'); if (revision !== optionsRevision) return;
      if (!Array.isArray(data.accounts) || !Array.isArray(data.targets) || !Array.isArray(data.people)) throw Error('配置响应不完整');
      config = data;
      const source = $('autoTaskSource'), shop = $('autoTaskShop'), oldSource = source.value, oldShop = shop.value;
      source.replaceChildren(); option(source, '0', '全部可用采集箱');
      for (const a of data.accounts.filter(a => a.configured && !a.blocked)) option(source, String(a.id), a.name);
      if (oldSource && ![...source.options].some(o => o.value === oldSource)) throw Error('来源账号已不可用，请重新打开页面选择');
      source.value = oldSource || '0';
      shop.replaceChildren(); option(shop, '', '请选择发布店铺');
      for (const group of data.targets) {
        const parent = node('optgroup'); parent.label = group.name;
        for (const s of group.shops) option(parent, `${group.accountId}:${s.shopId}`, `${s.shopNick || s.platformShopName || s.shopId}（${s.shopId}）`);
        shop.append(parent);
      }
      if (oldShop && ![...shop.options].some(o => o.value === oldShop)) throw Error('目标店铺已不可用，请重新打开页面选择');
      optionsReady = data.mode === 'live';
      shop.value = oldShop || ''; people(); sites();
      $('autoTaskMessage').textContent = !optionsReady ? '当前后台为只读模式，不能创建发布任务。' :
        data.errors?.length ? '部分账号配置读取失败：' + data.errors.map(e => `${accountName(e.accountId)}：${e.message}`).join('；') : '';
      for (const id of ['autoTaskSource', 'autoTaskPerson', 'autoTaskShop']) $(id).disabled = !optionsReady;
    } catch (error) { if (revision === optionsRevision) $('autoTaskMessage').textContent = `配置读取失败：${error.message}`; }
    finally { if (revision === optionsRevision) controls(); }
  }
  function payload() {
    const [account, shop] = $('autoTaskShop').value.split(':').map(Number), mode = $('autoTaskMode').value;
    return {sourceAccountId: Number($('autoTaskSource').value), personKey: $('autoTaskPerson').value,
      targetAccountId: account, shopId: shop, site: $('autoTaskSite').value, count: Number($('autoTaskCount').value),
      mode, startAt: mode === 'now' ? null : Date.parse($('autoTaskTime').value + ':00+08:00') / 1000,
      everyMinutes: mode === 'interval' ? Number($('autoTaskEvery').value) : 60, priceMode: $('autoTaskPriceMode').value,
      price: $('autoTaskPrice').value, priceMultiplier: $('autoTaskMultiplier').value,
      publishInterval: Number($('autoTaskPublishInterval').value), confirmation: 'AUTOMATIC_EDIT_AND_PUBLISH'};
  }
  async function submit(event) {
    event.preventDefault(); if (submitting || !optionsReady || !$('autoTaskConfirm').checked || !$('autoTaskForm').reportValidity()) return;
    const body = payload(), signature = JSON.stringify(body);
    if (!requestKey || signature !== requestSignature) { requestKey = crypto.randomUUID().replaceAll('-', ''); requestSignature = signature; }
    submitting = true; controls();
    try {
      await api('create', {...body, requestId: requestKey});
      $('autoTaskMessage').textContent = body.mode === 'now' ? '任务已提交，正在选取商品并排队编辑，保存成功后自动提交发布。' : '定时任务已创建，到点后自动选取商品并编辑发布。';
      $('autoTaskConfirm').checked = false; requestKey = null;
      await refresh();
    } catch (error) { $('autoTaskMessage').textContent = `提交未确认：${error.message}。可刷新记录核对；重试会沿用同一请求编号。`; }
    finally { submitting = false; controls(); }
  }
  function render(data) {
    const list = $('autoTaskList'); list.replaceChildren();
    $('autoTaskListMessage').textContent = data.globallyPaused ? '任务中心的自动处理已暂停，本模块也暂停启动和追加发布。' : '';
    if (!data.tasks.length) list.append(node('p', '暂无自动任务。'));
    for (const task of data.tasks) {
      const c = task.config, card = node('article', '', 'auto-task-card'), heading = node('div', '', 'auto-task-heading');
      heading.append(node('strong', `${shopName(c.targetAccountId, c.shopId)} · ${c.count} 件 / 轮`));
      const pause = node('button', task.paused ? '恢复任务' : '暂停任务', 'button subtle'); pause.type = 'button';
      pause.addEventListener('click', async () => {
        pause.disabled = true;
        try { await api('pause', {taskId: task.id, paused: !task.paused, confirmation: 'PAUSE_AUTOMATIC_TASK'}); await refresh(); }
        catch (error) { $('autoTaskListMessage').textContent = error.message; }
        finally { pause.disabled = false; }
      }); heading.append(pause); card.append(heading);
      const person = config?.people.find(p => p.key === c.personKey);
      card.append(node('p', `来源：${c.sourceAccountId ? accountName(c.sourceAccountId) : '创建时的全部可用采集箱'} · 人员：${person ? person.name + '（' + person.personId + '）' : c.personKey === 'unknown' ? '未知归属' : c.personKey || '全部人员'} · 站点：${c.site}`));
      card.append(node('p', `${task.paused ? '已暂停 · ' : ''}${c.mode === 'interval' ? `每 ${c.everyMinutes} 分钟一轮` : c.mode === 'once' ? '定时执行一次' : '立即执行一次'}${task.nextAt ? ` · 下次启动：${time(task.nextAt)}` : ''}`));
      if (task.paused) card.append(node('p', '已提交的编辑会继续到待发布；已经提交的发布请求不能撤回。', 'auto-task-note'));
      for (const run of task.runs) {
        const r = node('div', '', 'auto-task-run');
        const states = {preparing: '正在选取并提交', active: '编辑／发布中', completed: '本轮已提交发布', partial: '本轮结束，需查看结果'};
        r.append(node('strong', `${time(run.createdAt)} · ${states[run.state] || run.state}`));
        r.append(node('p', `本轮选取 ${run.planned} 件 · 已生成保存任务 ${run.saved} 条 · 处理中 ${run.pending} 条 · 等待自动发布 ${run.ready} 条 · 已提交发布 ${run.published} 条`));
        if (run.message) r.append(node('p', run.message));
        for (const issue of run.issues) r.append(node('p', issue));
        card.append(r);
      }
      list.append(card);
    }
  }
  async function refresh() {
    const revision = ++listRevision;
    try { const data = await api('tasks'); if (revision === listRevision) render(data); }
    catch (error) { if (revision === listRevision) $('autoTaskListMessage').textContent = `记录读取失败：${error.message}；保留上次显示。`; }
    finally {
      if (revision === listRevision) { clearTimeout(timer); timer = setTimeout(() => { if ($('automaticTasks').classList.contains('active')) refresh(); }, 5000); }
    }
  }
  $('autoTaskForm').addEventListener('submit', submit);
  $('autoTaskForm').addEventListener('change', event => {
    if (event.target !== $('autoTaskConfirm')) { $('autoTaskConfirm').checked = false; controls(); }
  });
  $('autoTaskSource').addEventListener('change', () => { people(true); $('autoTaskConfirm').checked = false; controls(); });
  $('autoTaskShop').addEventListener('change', () => { sites(true); $('autoTaskConfirm').checked = false; controls(); });
  for (const id of ['autoTaskMode', 'autoTaskPriceMode', 'autoTaskConfirm']) $(id).addEventListener('change', controls);
  $('autoTaskRefresh').addEventListener('click', () => { loadOptions(); refresh(); });
  $('autoTaskCenter').addEventListener('click', () => activatePage('tasks'));
  $('autoTaskTime').value = new Intl.DateTimeFormat('sv-SE', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}).format(new Date(Date.now() + 3600000)).replace(' ', 'T');
  window.automaticTasks = {open() { loadOptions(); refresh(); }};
})();
