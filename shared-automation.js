/* Shared views carry explicit source/target references; never inherit the open collection. */
(() => {
  const state = {ready: false, config: null, page: 'automation', kind: 'jobs', revisions: {}, publishPreviewLoading: null,
    pool: {page: 1, pageSize: 50, selected: new Map()}, jobs: {page: 1, pageSize: 50, group: 'attention', selected: new Map()},
    autoResumingLockedTransfers: new Set(), modal: null};
  const taskGroups = {
    processing: {label:'处理中', statuses:['queued','running'], hint:'正在排队或执行，无需重复提交。'},
    attention: {label:'需要处理', statuses:['needs_input','failed','needs_review','interrupted'], hint:'先查看原因，再补充资料或重试保存；不需要的任务可删除记录。'},
    publish: {label:'待发布', statuses:['ready_publish'], hint:'商品已保存，勾选后手动发布；不会自动上架。'},
    history: {label:'历史记录', statuses:['done','canceled'], hint:'查看已提交发布或已结束的任务；最终上架结果请到妙手发布记录核对。'}
  };
  const $ = id => document.getElementById(id);
  function element(tag, text = '', cls = '') {
    const node = document.createElement(tag); node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }
  function button(text, action, primary = false) {
    const node = element('button', text, `button ${primary ? 'primary' : 'subtle'}`); node.type = 'button';
    node.addEventListener('click', async () => {
      if (node.sharedPending) return;
      node.sharedPending = true;
      node.disabled = true;
      try { await action(); } catch (error) { notice(error.message); }
      // Windowed rows can be detached while an action finishes. The cached
      // button must be usable when that same row comes back into view.
      finally {
        node.sharedPending = false;
        node.disabled = node.sharedDisabled ? !!node.sharedDisabled() : false;
        updateTaskControls();
      }
    });
    return node;
  }
  function option(select, value, text) { const node = element('option', text); node.value = value; select.append(node); }
  function input(id, value = '', type = 'text') {
    const node = element('input'); node.id = id; node.type = type; node.value = value; return node;
  }
  function select(id, values) {
    const node = element('select'); node.id = id;
    values.forEach(([value, label]) => option(node, value, label)); return node;
  }
  function field(text, node) { const label = element('label', text); label.append(node); return label; }
  async function api(path, data, options = {}) {
    const init = data === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)};
    if (options.signal) init.signal = options.signal;
    // Bound read latency so one stalled connection cannot stop the next poll.
    // Write requests keep their existing completion/confirmation semantics.
    const controller = data === undefined && !options.signal ? new AbortController() : null;
    const timer = controller ? window.setTimeout(() => controller.abort(), options.timeoutMs || 12000) : null;
    if (controller) init.signal = controller.signal;
    try {
      const response = await fetch('/api/automation/shared/' + path, init);
      const result = await response.json();
      if (!response.ok) throw Error(result.error || '共享操作失败');
      if(result.activityEpoch && result.activityEpoch!==state.activityEpoch && !state.retiredActivityEpochs?.has(result.activityEpoch)){state.retiredActivityEpochs ||= new Set();if(state.activityEpoch)state.retiredActivityEpochs.add(state.activityEpoch);state.activityEpoch=result.activityEpoch;state.minimumActivityGeneration=0;}
      if (data !== undefined && Number.isSafeInteger(result.activityGeneration) && (!result.activityEpoch || result.activityEpoch===state.activityEpoch))
        state.minimumActivityGeneration=Math.max(state.minimumActivityGeneration || 0,result.activityGeneration);
      return result.data;
    } catch (error) {
      if (controller?.signal.aborted) throw Error('读取超时，正在等待自动重试；也可点击查询／刷新。');
      throw error;
    } finally {
      if (timer !== null) window.clearTimeout(timer);
    }
  }
  function notice(text) {
    for (const id of ['sharedPoolNotice', 'sharedJobsNotice']) if ($(id)) $(id).textContent = text;
    if (state.modal?.message) state.modal.message.textContent = text;
  }
  function name(id) {
    const account = state.config?.accounts.find(a => a.id === Number(id));
    const label = account?.name;
    return (Number(id) === 1 && label === '妙手采集箱' ? '妙手采集箱 1' : label || `采集箱 ${id}`)
      + (account?.removed ? '（已删除）' : '');
  }
  function shopName(account, shop) {
    const item = state.config?.targets.find(g => g.accountId === account)?.shops.find(s => Number(s.shopId) === shop);
    return `${name(account)} / ${item?.shopNick || item?.platformShopName || `店铺 ${shop}`}（${shop}）`;
  }
  function updateShopOptions(counts) {
    if (counts !== undefined) {
      const next = new Map();
      const valid = Array.isArray(counts) && counts.every(row => {
        if (!row || !Number.isSafeInteger(row.accountId) || row.accountId <= 0 ||
            !Number.isSafeInteger(row.shopId) || row.shopId <= 0 ||
            !Number.isSafeInteger(row.count) || row.count < 0) return false;
        const key = `${row.accountId}:${row.shopId}`;
        if (next.has(key)) return false;
        next.set(key,row.count); return true;
      });
      if (valid) {state.jobs.shopPendingCounts = next;state.jobs.shopCountsStale = false;}
      else state.jobs.shopCountsStale = true;
    }
    const shop = $('sharedJobsShop'); if (!shop) return;
    const previous = shop.value, entries = new Map(), totals = state.jobs.shopPendingCounts;
    (state.config?.targets || []).forEach(group=>group.shops.forEach(item=>{
      const account = Number(group.accountId), id = Number(item.shopId);
      entries.set(`${account}:${id}`,shopName(account,id));
    }));
    // Keep pending shops discoverable even when their remote name is unavailable.
    for (const key of totals?.keys() || []) if (!entries.has(key)) {
      const [account,id] = key.split(':').map(Number); entries.set(key,shopName(account,id));
    }
    if (previous && !entries.has(previous)) {
      const [account,id] = previous.split(':').map(Number); entries.set(previous,shopName(account,id));
    }
    const publish = state.jobs.group === 'publish';
    const options = [['','全部店铺'],...Array.from(entries,([key,label])=>[key,
      label + (publish ? ` · 待发布 ${totals ? (totals.get(key) || 0) : '—'}` : '')])];
    const current = [...shop.options];
    if (current.length === options.length && current.every((item,index)=>item.value === options[index][0])) {
      // Updating text only keeps an open native menu and its selection stable.
      current.forEach((item,index)=>{if(item.textContent !== options[index][1])item.textContent=options[index][1];});
    } else {
      shop.replaceChildren(); options.forEach(([key,label])=>option(shop,key,label)); shop.value=previous;
    }
    const hint = $('sharedShopPendingHint');
    if (hint) {
      hint.hidden = !publish;
      hint.textContent = '数量为各店铺待发布总数，不随搜索条件变化。' +
        (!totals ? '数量暂未获取，显示“—”。' : state.jobs.shopCountsStale ? '刷新失败，暂显示上次统计。' : '');
    }
  }
  function personLabel(key) {
    if (key === 'unknown') return '未知归属';
    const person = state.jobs.personOptions?.find(p=>p.personKey === key);
    if (!person) return key;
    return `${name(person.sourceAccountId)} / ${person.personName}（人员 ID ${person.personId}）`;
  }
  function updatePersonOptions(options) {
    const node = $('sharedJobsPerson'); if (!node) return;
    if (options !== undefined) {
      const keys = new Set();
      if (!Array.isArray(options) || !options.every(p=>p && Number.isSafeInteger(p.sourceAccountId) && p.sourceAccountId>0
          && typeof p.personId === 'string' && /^[1-9][0-9]{0,19}$/.test(p.personId)
          && p.personKey === `${p.sourceAccountId}:${p.personId}` && typeof p.personName === 'string'
          && !keys.has(p.personKey) && keys.add(p.personKey))) throw Error('所属人员列表读取失败，保留当前筛选');
      state.jobs.personOptions = options;
    }
    const hint = $('sharedPersonHint');
    node.disabled = !state.jobs.personOptions;
    if (hint) {hint.hidden = !node.disabled;hint.textContent = '正在加载本地人员信息；读取失败时将自动重试。';}
    if (node.disabled) return;
    const previous = node.value, source = $('sharedJobsSource')?.value;
    const entries = state.jobs.personOptions.filter(p=>!source || String(p.sourceAccountId)===source);
    // A refresh must not silently broaden the filter if the selected person no
    // longer has visible records (for example, their last queued item finished).
    if (previous && previous !== 'unknown' && !entries.some(p=>p.personKey===previous)) {
      const [account, person] = previous.split(':');
      entries.push({sourceAccountId:Number(account),personId:person,personKey:previous,personName:'未匹配人员'});
    }
    const signature = JSON.stringify(entries.map(p=>[p.personKey,p.personName,name(p.sourceAccountId)]));
    if (node.dataset.personOptions === signature) return;
    node.replaceChildren();option(node,'','全部人员');option(node,'unknown','未知归属');
    const groups = new Map();
    for (const person of entries) {
      let group = groups.get(person.sourceAccountId);
      if (!group) {group=element('optgroup');group.label=name(person.sourceAccountId);groups.set(person.sourceAccountId,group);node.append(group);}
      option(group,person.personKey,`${person.personName}（人员 ID ${person.personId}）`);
    }
    node.value=previous;node.dataset.personOptions=signature;
  }
  function accountOptions(node, all = true) {
    const previous = node.value; node.replaceChildren();
    if (all) option(node, '', '全部账号');
    (state.config?.accounts || []).forEach(a => option(node, String(a.id), `${name(a.id)}${a.removed ? '' : a.blocked ? '（凭据变化，已暂停）' : !a.configured ? '（未配置）' : ''}`));
    if ([...node.options].some(o => o.value === previous)) node.value = previous;
  }
  function targetOptions(node, chosen = []) {
    node.replaceChildren();
    (state.config?.targets || []).forEach(group => {
      const parent = element('optgroup'); parent.label = group.name;
      group.shops.forEach(shop => {
        const value = `${group.accountId}:${shop.shopId}`;
        const item = element('option', `${shop.shopNick || shop.platformShopName || shop.shopId}（${shop.shopId}）`);
        item.value = value; item.selected = chosen.includes(value); parent.append(item);
      });
      node.append(parent);
    });
  }
  function chosenTargets(node) {
    return [...node.selectedOptions].map(o => {const [targetAccountId, shopId] = o.value.split(':').map(Number); return {targetAccountId, shopId};});
  }
  function modal(title) {
    state.modal?.dismiss?.();
    const overlay = element('div', '', 'modal shared-modal'); overlay.setAttribute('role', 'dialog'); overlay.setAttribute('aria-modal', 'true');
    const card = element('div', '', 'modal-card shared-modal-card');
    const heading = element('h2', title); heading.id = 'sharedDialogTitle'; overlay.setAttribute('aria-labelledby', heading.id);
    const abortController = new AbortController();
    const value = {overlay, card, content: null, message: null, heading, abortController, closed: false};
    const dismiss = () => {value.closed = true; abortController.abort(); overlay.remove(); if (state.modal === value) state.modal = null;};
    value.dismiss = dismiss;
    const close = button('关闭', dismiss);
    close.classList.add('shared-modal-close');
    const content = element('div'), message = element('p', '', 'shared-message'); message.setAttribute('role', 'status');
    value.content = content; value.message = message;
    card.append(close, heading, content, message); overlay.append(card); document.body.append(overlay);
    state.modal = value; close.focus(); return value;
  }
  function pager(kind, response) {
    const node = $(`shared${kind === 'pool' ? 'Pool' : 'Jobs'}Pager`);
    let view = state[kind].pagerView;
    if (!view) {
      const label = element('span');
      const page = input('', '', 'number'); page.min = '1'; page.setAttribute('aria-label', '共享列表跳转页码');
      const jump = async next => {state[kind].page = Math.max(1, Math.min(view.response.pages, Number(next) || 1)); await refresh(kind);};
      const prev = button('上一页', () => jump(view.response.page - 1));
      const next = button('下一页', () => jump(view.response.page + 1));
      const size = select(kind === 'pool' ? 'sharedPoolPageSize' : 'sharedJobsPageSize',
        [50,100,200,500].map(n=>[String(n),`${n} 条 / 页`]));
      size.setAttribute('aria-label', kind === 'pool' ? '自动化池每页条数' : '任务中心每页条数');
      size.addEventListener('change', async () => {
        if (kind === 'jobs' && state.deleting) {size.value=String(state.jobs.pageSize || 50);return;}
        const value = Number(size.value);
        if (![50,100,200,500].includes(value)) return;
        state[kind].pageSize=value;state[kind].page=1;state[kind].selected.clear();updateCount(kind);
        await refresh(kind);
      });
      node.replaceChildren(); node.append(size, label, page, button('跳转', () => jump(page.value)), prev, next);
      view = state[kind].pagerView = {label, page, prev, next, size, response: null};
      prev.sharedDisabled = () => view.response.page <= 1;
      next.sharedDisabled = () => view.response.page >= view.response.pages;
    }
    view.label.textContent = `第 ${response.page} / ${response.pages} 页 · 共 ${response.total} 条`;
    if (view.response?.page !== response.page || typeof document === 'undefined' || document.activeElement !== view.page)
      view.page.value = String(response.page);
    view.page.max = String(response.pages); view.size.value = String(state[kind].pageSize || 50);
    view.response = response;
    view.prev.disabled = !!view.prev.sharedPending || view.prev.sharedDisabled();
    view.next.disabled = !!view.next.sharedPending || view.next.sharedDisabled();
  }
  function selection(kind, row) {
    const node = input('', '', 'checkbox'); node.checked = state[kind].selected.has(row.key);
    node.sharedRow = row;
    node.disabled = !canSelectRecord(kind, row);
    node.setAttribute('aria-label', `选择 ${row.sourceAccountId}:${row.detailId}${row.shopId ? ` / ${row.accountId}:${row.shopId}` : ''}`);
    node.addEventListener('change', () => {
      const current = node.sharedRow;
      if (state[kind].selectionBlocked) { node.checked = state[kind].selected.has(current.key); return; }
      if (node.checked) state[kind].selected.set(current.key, current); else state[kind].selected.delete(current.key);
      updateCount(kind);
    }); return node;
  }
  function updateCount(kind) {
    $(`shared${kind === 'pool' ? 'Pool' : 'Jobs'}Count`).textContent = `已选 ${state[kind].selected.size} 件`;
    if (kind === 'jobs') updateTaskControls();
  }
  function syncSelection(kind) {
    for (const view of state[kind].rowViews?.values() || []) if (view.checkbox)
      view.checkbox.checked = state[kind].selected.has(view.row.key);
    updateCount(kind);
  }
  async function setAutomationPause() {
    const paused = !state.config?.paused;
    const result = await api('pause', {paused, confirmation:'SET_SHARED_AUTOMATION_PAUSE'});
    state.config = {...state.config, paused:result.paused};
    if (result.paused) {
      notice('已暂停领取新任务；进行中的请求会继续完成。请以实时队列状态确认是否已结束，再重启后台。');
    } else {
      notice('已恢复自动处理，排队任务会继续编辑保存，完成后进入待发布队列。');
    }
    updateTaskControls();
    await refresh('jobs');
  }
  async function reloadQueuedWork() {
    if (!window.confirm('将重新加载仍处于“排队中”的副本创建与保存任务。不会重试需核查、失败、已发布或结果不明的记录；保存完成后仍按原规则进入待发布队列。继续吗？')) return;
    state.reloading = true; updateTaskControls();
    try {
      const result = await api('reload-queue', {confirmation:'RELOAD_SHARED_QUEUE'});
      notice(result.started
        ? `已重新加载 ${result.sharedTransfers} 条跨账号排队任务${result.localAccounts.length ? `，以及采集箱 ${result.localAccounts.join('、')} 的本地保存队列` : ''}。`
        : result.message);
      await refresh('jobs');
    } finally { state.reloading = false; updateTaskControls(); }
  }
  function updateTaskControls() {
    if (!$('sharedJobActions')) return;
    const selected = [...state.jobs.selected.values()], group = state.jobs.group;
    const publish = $('sharedPublishSelected'), retry = $('sharedRetrySelected'), remove = $('sharedDeleteSelected');
    const writable = state.config?.mode === 'live';
    const pause = $('sharedAutomationPause');
    if (pause) {
      pause.textContent = state.config?.paused ? '恢复自动处理' : '暂停自动处理';
      pause.disabled = !writable || !!state.pauseChanging;
    }
    const reload = $('sharedReloadQueued');
    if (reload) reload.disabled = !writable || !!state.reloading || !!state.config?.paused;
    $('sharedPublishSettings').hidden = group !== 'publish'; publish.hidden = group !== 'publish';
    const countField=$('sharedPublishCountField'), preview=$('sharedPublishCountPreview');
    if(countField && preview) {
      const count=Number($('sharedPublishCount').value);
      countField.hidden=group !== 'publish';preview.hidden=group !== 'publish';
      preview.textContent=Number.isInteger(count)&&count>0?`预览发布前 ${count} 件`:'预览按数量发布';
      preview.disabled=!!state.publishing || !Number.isInteger(count) || count<1;
    }
    const multi=$('sharedPublishMulti');
    if(multi){multi.hidden=group!=='publish';multi.disabled=!!state.publishing;}
    const adjust=$('sharedPublishIntervalAdjust');
    // Do not show a control that an older, still-running backend cannot
    // service.  It appears automatically after the normal backend restart.
    const dynamicPublishInterval = Number.isSafeInteger(state.queueActivity?.publishing)
      && typeof state.queueActivity?.publishWorker === 'boolean';
    if(adjust){adjust.hidden=group!=='publish'||!dynamicPublishInterval;adjust.disabled=!writable||!!state.publishing;}
    publish.textContent = selected.length ? `发布选中 ${selected.length} 件` : '发布选中商品';
    publish.disabled = !!state.deleting || !!state.publishing || !writable || !selected.length || selected.some(r=>r.status !== 'ready_publish' || !r.available);
    retry.hidden = group !== 'attention';
    retry.disabled = !!state.deleting || !!state.retrying || !!state.handlingFailures || !writable || !selected.length || selected.some(r=>!['failed','needs_review','interrupted'].includes(r.status) || !r.available);
    const known = $('sharedKnownFailures');
    if (known) {
      const count = (state.jobs.rows || []).filter(row=>row.safeBatchEligible).length;
      known.hidden = group !== 'attention';
      known.textContent = state.handlingFailures ? '正在分类处理…' : `批量处理本页可恢复问题 (${count})`;
      known.disabled = !writable || !count || !!state.handlingFailures || !!state.retrying || !!state.deleting;
    }
    remove.hidden = group === 'publish';
    remove.disabled = !!state.deleting || !!state.retrying || !!state.handlingFailures || !selected.some(canDeleteRecord);
    $('sharedSelectionHint').textContent = !writable && group === 'publish' ? '当前只读模式：可以预览数量，不能真实发布。按数量取当前筛选的前 N 件（跨页总量），不受勾选影响。' :
      group === 'attention' ? `${state.config?.paused ? '自动处理已暂停；恢复后继续排队任务。' : !writable ? '当前只读模式，不能重试保存。' : ''}待处理任务请逐条查看原因；失败／中断可批量重试。删除仅移除本地记录。` :
      group === 'processing' ? '运行中的任务不能删除；删除排队任务会同时结束排队。' :
      group === 'publish' ? '按数量取当前筛选的前 N 件（跨页总量），不受勾选影响；也可单独发布勾选商品。两种方式均须确认后发布。' : '删除记录不会删除妙手商品。';
  }
  function updateTaskCounts(data) {
    if (!Object.keys(taskGroups).every(key=>Number.isSafeInteger(data.counts?.[key]) && data.counts[key]>=0
      && Number.isSafeInteger(data.transferCounts?.[key] ?? 0) && (data.transferCounts?.[key] ?? 0)>=0))
      throw Error('本地任务统计不完整，正在等待重试。');
    state.jobs.counts = data.counts;
    state.jobs.countsRevision = (state.jobs.countsRevision || 0) + 1;
    for (const [key, group] of Object.entries(taskGroups)) {
      const tab = $('sharedTab'+key), total = (data.counts?.[key] || 0) + (data.transferCounts?.[key] || 0);
      tab.querySelector('b').textContent = total;
      tab.setAttribute('aria-pressed', String(key === state.jobs.group));
    }
  }
  async function loadTaskSummary() {
    if (!state.ready || !state.capabilities?.taskSummary || state.jobs.summaryLoading) return;
    const query = params('jobs');
    if ([...query.keys()].some(key=>!['page','pageSize','taskGroup'].includes(key))) return;
    const signature = query.toString(), revision = state.jobs.countsRevision || 0;
    state.jobs.summaryLoading = true;
    try {
      const data = await api('summary', undefined, {timeoutMs:5000});
      if (signature !== params('jobs').toString() || revision !== (state.jobs.countsRevision || 0)) return;
      updateTaskCounts(data);
    } catch (error) {
      // The list has its own recovery path; a failed counter read must not
      // erase valid counts or prevent that list request from completing.
      if (!state.jobs.counts) {
        const status=$('sharedTaskLoadState');
        if(status) status.textContent='本地统计暂未读取成功，正在重试；任务列表继续加载。';
      }
    } finally { state.jobs.summaryLoading = false; }
  }
  function updateTaskSummary(data) {
    // Counters must not depend on card rendering or optional owner metadata.
    updateTaskCounts(data);
    try { updatePersonOptions(data.personOptions); }
    catch (error) {
      const hint=$('sharedPersonHint');
      if (hint) {hint.hidden=false;hint.textContent='人员名称暂未更新，保留当前筛选；下次刷新自动重试。';}
    }
    updateShopOptions(data.shopPendingCounts);
    $('sharedJobsView').textContent = taskGroups[state.jobs.group].label;
    $('sharedJobsHint').textContent = taskGroups[state.jobs.group].hint;
    const transfers = data.transferCounts?.[state.jobs.group] || 0;
    $('sharedJobsTotal').textContent = Array.isArray(data.entries)
      ? `共 ${data.total} 条任务 · 保存任务与副本处理统一分页`
      : `共 ${data.total} 条保存任务${transfers ? ` · ${transfers} 条跨账号副本处理（最多显示 ${data.pageSize || state.jobs.pageSize || 50} 条）` : ''}`;
    // A pre-upgrade backend does not yet return per-queue totals.  Its task
    // list is already refreshed on this page, so use that local snapshot as a
    // temporary, filter-consistent fallback instead of showing zero.
    if (state.queueActivity) renderQueueActivity(state.queueActivity);
    updateTaskControls();
  }
  function renderQueueActivity(activity, error = '') {
    const node = $('sharedQueueActivity'); if (!node) return;
    if (error) {
      if (!state.queueActivity) {
        node.dataset.state = 'loading';
        node.textContent = '正在连接本地队列，稍后自动重试；商品与任务数据不受影响';
        return;
      }
      activity = {...state.queueActivity, _snapshot: {...state.queueActivity._snapshot, status:'stale'}};
    }
    // Older backends only return the two transfer fields.  Keep that response
    // readable until the local server is restarted, then show the full
    // cross-account + ordinary-edit + publication snapshot.
    const copyQueued = Number.isSafeInteger(activity.copyQueued)
      ? activity.copyQueued : Math.max(0, activity.activeTransfers - activity.inFlightTransfers);
    const copyRunning = Number.isSafeInteger(activity.copyRunning)
      ? activity.copyRunning : activity.inFlightTransfers;
    const editQueued = Number.isSafeInteger(activity.editQueued) ? activity.editQueued : 0;
    const editRunning = Number.isSafeInteger(activity.editRunning) ? activity.editRunning : 0;
    const pendingPublish = Number.isSafeInteger(activity.pendingPublish)
      ? activity.pendingPublish : Math.max(0, Number(state.jobs?.counts?.publish) || 0);
    const publishing = Number.isSafeInteger(activity.publishing) ? activity.publishing : 0;
    const waiting = copyQueued + editQueued;
    // Prefer the backend aggregate so every executor uses one consistent
    // definition.  Keep the component sum as a compatibility fallback for
    // older local servers.
    const running = Number.isSafeInteger(activity.running)
      ? Math.max(0, activity.running) : copyRunning + editRunning + publishing;
    const background = [
      activity.syncing && '采集/订单同步中',
      activity.editing && '编辑执行器运行中',
      (activity.publishWorker === true || activity.publishing === true) && '发布执行器运行中',
      activity.autoTasksBusy && '自动任务运行中',
      activity.reloading && '正在重新加载队列',
    ].filter(Boolean);
    const mode = activity.paused ? '已暂停' : running ? '正在执行' : waiting ? '等待执行' : background.length ? '后台工作中' : '空闲';
    node.dataset.state = activity.paused ? 'paused' : running || background.length ? 'running' : waiting ? 'queued' : 'idle';
    const refreshedAt = taskEventTime(activity._snapshot?.generatedAt || new Date().toISOString());
    node.textContent = `队列状态：${mode} · 运行中 ${running} 条 · 编辑排队 ${editQueued} 条 · 编辑中 ${editRunning} 条 · 副本排队 ${copyQueued} 条 · 副本中 ${copyRunning} 条 · 待发布 ${pendingPublish} 条 · 发布中 ${publishing} 条${background.length ? ` · ${background.join('、')}` : ''} · 北京时间 ${refreshedAt} · 任务活跃时约每 4 秒更新`;
    if (activity._snapshot?.status==='stale') {
      node.dataset.state='stale';node.textContent+=' · 暂显示上次成功快照，后台正在更新';
    }
    const pools = $('sharedPoolActivity');
    if (pools && Array.isArray(activity.pools)) {
      pools.replaceChildren();
      const heading = element('div', '', 'shared-pool-activity-heading');
      heading.append(element('strong', '各账号独立池'), element('span', `实时状态 · 北京时间 ${refreshedAt}`));
      pools.append(heading);
      const accounts = new Map();
      for (const row of activity.pools) {
        const key = `${row.accountId}:${row.accountName}`;
        if (!accounts.has(key)) accounts.set(key, {name: row.accountName, rows: []});
        accounts.get(key).rows.push(row);
      }
      for (const account of accounts.values()) {
        const section = element('section', '', 'shared-pool-account');
        const totals = account.rows.reduce((sum, row) => ({
          queued: sum.queued + Number(row.editQueued || 0), running: sum.running + Number(row.editRunning || 0),
          publish: sum.publish + Number(row.pendingPublish || 0), publishing: sum.publishing + Number(row.publishing || 0),
        }), {queued: 0, running: 0, publish: 0, publishing: 0});
        const header = element('header', '', 'shared-pool-account-head');
        header.append(element('strong', account.name), element('span',
          `编辑 ${totals.queued + totals.running} · 待发布 ${totals.publish}${totals.publishing ? ` · 发布中 ${totals.publishing}` : ''}`));
        const grid = element('div', '', 'shared-pool-grid');
        for (const row of account.rows.sort((left, right) => left.poolId - right.poolId)) {
          const active = Number(row.editQueued || 0) + Number(row.editRunning || 0) + Number(row.publishing || 0) > 0;
          const card = element('article', '', `shared-pool-card${active ? ' is-active' : ''}`);
          const cardHead = element('div', '', 'shared-pool-card-head');
          cardHead.append(element('strong', `池 ${row.poolId}`), element('span', active ? '处理中' : '空闲'));
          const metrics = element('div', '', 'shared-pool-metrics');
          for (const [label, value, tone] of [
            ['编辑排队', row.editQueued, 'queue'], ['编辑中', row.editRunning, 'running'],
            ['待发布', row.pendingPublish, 'publish'], ['发布中', row.publishing, 'publishing'],
          ]) {
            const metric = element('span', '', `shared-pool-metric ${tone}`);
            metric.append(element('small', label), element('b', String(value || 0)));
            metrics.append(metric);
          }
          card.append(cardHead, metrics); grid.append(card);
        }
        section.append(header, grid); pools.append(section);
      }
    }
  }
  function renderTodayStoreStats(summary) {
    const node = $('sharedTodayStoreStats'); if (!node) return;
    const stores = summary?.stores;
    if (!Array.isArray(stores) || !stores.length) {node.hidden=true;node.replaceChildren();return;}
    const valid = stores.filter(row => Number.isSafeInteger(row?.accountId) && row.accountId>0
      && Number.isSafeInteger(row?.shopId) && row.shopId>0
      && Number.isSafeInteger(row?.processed) && row.processed>=0
      && Number.isSafeInteger(row?.published) && row.published>=0
      && (row.processed || row.published));
    if (!valid.length) {node.hidden=true;node.replaceChildren();return;}
    node.hidden=false;node.replaceChildren(element('strong',`今日店铺处理与发布（北京时间 ${summary.date || '今天'}）`));
    if(summary.generatedAt)node.append(element('small',`统计时间：${taskEventTime(summary.generatedAt)}（北京时间）`));
    const list=element('div','','shared-today-store-list');
    valid.forEach(row=>list.append(element('span',`${shopName(row.accountId,row.shopId)}：处理 ${row.processed} 件 · 发布 ${row.published} 件`)));
    node.append(list);
  }
  function consumeActivity(activity) {
    const epoch=activity._snapshot?.epoch;
    state.retiredActivityEpochs ||= new Set();
    if(epoch && state.retiredActivityEpochs.has(epoch))return false;
    if(epoch && epoch!==state.activityEpoch){
      if(state.activityEpoch)state.retiredActivityEpochs.add(state.activityEpoch);
      state.activityEpoch=epoch;state.minimumActivityGeneration=0;state.activityRevision=null;state.jobs.activityDirty=true;
    }
    if(activity.loading){const node=$('sharedQueueActivity');if(node&&!state.queueActivity){node.dataset.state='loading';node.textContent='正在准备本地队列快照，数据就绪后自动更新…';}return false;}
    if(Number.isSafeInteger(activity._snapshot?.generation) && activity._snapshot.generation<(state.minimumActivityGeneration || 0))return false;
    if (!Number.isSafeInteger(activity.activeTransfers) || activity.activeTransfers < 0 ||
        !Number.isSafeInteger(activity.inFlightTransfers) || activity.inFlightTransfers < 0 ||
        typeof activity.paused !== 'boolean') throw Error('返回的数据不完整');
    if (activity.queueRevision && activity.queueRevision !== state.activityRevision) {
      state.activityRevision=activity.queueRevision;state.jobs.activityDirty=true;
    }
    state.queueActivity = activity;
    if (state.config) state.config = {...state.config, paused: activity.paused};
    renderQueueActivity(activity);renderTodayStoreStats(activity.todayStoreStats);updateTaskControls();
    if (state.jobs.loadError) void loadTaskSummary();
    return true;
  }
  async function loadQueueActivity() {
    if (!state.ready || document.hidden || !$('tasks')?.classList.contains('active')) return;
    if (window.WorkbenchLiveStatus?.isLive()) return;
    if (state.activityPending) return;
    state.activityPending = true;
    const revision = state.revisions.activity = (state.revisions.activity || 0) + 1;
    try {
      const activity = await api('activity');
      if (revision !== state.revisions.activity) return;
      consumeActivity(activity);
    } catch (error) {
      if (revision === state.revisions.activity) renderQueueActivity(null, error.message || '请求失败');
    } finally { state.activityPending=false; }
  }
  async function switchTaskGroup(group) {
    state.jobs.group = group; state.jobs.page = 1; state.jobs.selected.clear();
    state.kind = group === 'publish' ? 'queue' : 'jobs';
    const statuses = $('sharedJobsStatus'); statuses.replaceChildren(); option(statuses,'','本组全部状态');
    taskGroups[group].statuses.forEach(status=>option(statuses,status,statusName(status)));
    updateMoreFilters();
    updateCount('jobs');
    // Apply context immediately; late responses cannot restore the previous group.
    updateTaskSummary({counts:state.jobs.counts,total:0});
    await refresh('jobs');
  }
  function rows(kind, data, options = {}) {
    const list = $(`shared${kind === 'pool' ? 'Pool' : 'Jobs'}List`);
    const entries = kind === 'pool' ? data.items : (data.entries || [
      ...(data.jobs || []), ...(data.transfers || []).map(row=>({...row,type:'transfer',key:`transfer:${row.transfer_id}`}))]);
    state[kind].rows = entries;
    let unavailable = 0;
    for (const row of entries) if (state[kind].selected.has(row.key)) {
      if (!canSelectRecord(kind, row)) {
        state[kind].selected.delete(row.key); unavailable++;
      } else state[kind].selected.set(row.key,row);
    }
    if (unavailable) notice(`${unavailable} 条已选记录已不可操作，已取消其选择。`);
    if (window.SharedVirtualList && window.requestAnimationFrame) {
      if (!state[kind].virtualList) state[kind].virtualList = new window.SharedVirtualList(list, {
        createView: row => createRowView(kind, row),
        estimateHeight: row => kind === 'pool' ? 180 : row.type === 'transfer' ? 240 : 300,
        onViews: views => {state[kind].rowViews = views;}
      });
      state[kind].virtualList.setRows(entries, options);
      pager(kind, data); updateCount(kind);
      if (kind === 'jobs') {
        $('sharedTransfers').replaceChildren();
        autoResumeDatabaseLockedTransfers(data.transfers || []);
      }
      return;
    }
    const previous = state[kind].rowViews || new Map(), next = new Map(), scrollTop = list.scrollTop;
    const focused = typeof document !== 'undefined' && list.contains(document.activeElement) ? document.activeElement : null;
    if (!previous.size) list.replaceChildren();
    for (const row of entries) {
      const view = previous.get(row.key) || createRowView(kind, row);
      view.update(row); next.set(row.key, view);
    }
    for (const [key, view] of previous) if (!next.has(key)) view.line.remove();
    let position = 0;
    for (const view of next.values()) {
      if (list.children[position] !== view.line) list.insertBefore(view.line, list.children[position] || null);
      position++;
    }
    if (!entries.length) list.replaceChildren(element('p', '暂无符合条件的记录', 'live-empty'));
    state[kind].rowViews = next;
    if (focused && list.contains(focused) && document.activeElement !== focused) focused.focus({preventScroll:true});
    list.scrollTop = scrollTop;
    pager(kind, data); updateCount(kind);
    if (kind === 'jobs') {
      $('sharedTransfers').replaceChildren();
      autoResumeDatabaseLockedTransfers(data.transfers || []);
    }
  }
  function createRowView(kind, initial) {
    const transfer = initial.type === 'transfer';
    const line = element('article', '', transfer ? 'shared-transfer' : 'shared-row');
    const body = transfer ? line : element('div', '', 'shared-row-body');
    const view = {line, row:initial, checkbox:null};
    const title = element('strong'), owner = element('p'), time = element('p', '', 'shared-job-ids');
    const route = element('p'), pool = element('p'), ids = element('p', '', 'shared-job-ids');
    const message = element('p', '', 'shared-task-message'), actions = element('div', '', 'shared-actions');
    const badge = element('span'), unavailable = element('p', '账号未配置或凭据已变化；暂停写入，请核查账号', 'shared-error');
    const picture = element('img'); picture.alt = ''; picture.loading = 'lazy'; picture.decoding = 'async';
    if (transfer) {
      // A review-only transfer is safe to clear locally in a batch.  It stays
      // excluded from retry/resume actions, which have a different safety rule.
      view.checkbox = selection(kind, initial);
      line.append(view.checkbox, title, owner, time, message, actions);
    }
    else {
      view.checkbox = selection(kind, initial);
      body.append(badge, title, pool, route, ids, owner, time, message, actions, unavailable);
      line.append(view.checkbox, picture, body);
    }
    const setText = (node, value) => {if (node.textContent !== value) node.textContent = value;};
    view.update = row => {
      view.row = row;
      if (transfer) {
        view.checkbox.sharedRow = row;
        view.checkbox.checked = state[kind].selected.has(row.key);
        view.checkbox.disabled = !canSelectRecord(kind, row);
        const p = row.payload;
        setText(title, `${name(p.sourceAccountId)} / ${p.detailId} → ${name(p.targetAccountId)}`);
        setText(owner, `所属人员：${row.personId ? `${row.personName}（人员 ID ${row.personId}）` : '未知归属'}`);
        setText(time, `提交队列：${taskEventTime(row.queuedAt || row.updated)}`);
        setText(message, `${statusName(row.status)} · ${row.message || '等待创建或复用目标副本'}`);
      } else {
        view.checkbox.sharedRow = row; view.checkbox.checked = state[kind].selected.has(row.key);
        view.checkbox.disabled = !canSelectRecord(kind, row);
        const src = row.thumbnail || '';
        if (view.thumbnail !== src) {if (src) picture.src = src; else picture.removeAttribute('src');view.thumbnail=src;}
        picture.hidden = !src;
        setText(title, row.title || '未返回商品名称'); setText(pool, row.poolId ? `自动化池 ${row.poolId}` : '');pool.hidden=!row.poolId;
        unavailable.hidden = !!row.available;
        if (kind === 'pool') {
          badge.hidden=true;ids.hidden=true;actions.hidden=true;
          setText(route, `${name(row.sourceAccountId)} · 源商品 ID ${row.sourceDetailId || row.detailId}`);
          setText(owner, `所属人员：${row.personId ? `${row.personName}（账号 ID ${row.personId}）` : '未返回所属人员'}`);
          setText(time, row.collectedAt ? `采集时间：${row.collectedAt}（妙手返回）`
            : `采集时间：妙手未返回 · 本机入池：${taskEventTime(row.createdAt)}（北京时间）`);
          time.className='shared-collection-time';
          setText(message, (row.copies || []).length ? '目标副本：' + row.copies.map(c=>`${name(c.targetAccountId)} / ${c.detailId || '待核查'}`).join('；') : '');
        } else {
          line.classList.add('shared-job-row');line.dataset.status=row.status;
          badge.className='shared-status shared-status-'+row.status;setText(badge,statusName(row.status,row.step));
          route.className='shared-job-route';owner.className='shared-job-ids';
          setText(route, `${name(row.sourceAccountId)} → ${shopName(row.accountId, row.shopId)} · ${row.site}`);
          setText(ids, `源商品 ${row.sourceDetailId || row.detailId} · 目标商品 ${row.detailId}`);
          setText(owner, `所属人员：${row.personId ? `${row.personName}（人员 ID ${row.personId}）` : '未知归属'}`);
          setText(time, `提交队列：${taskEventTime(row.queuedAt || row.createdAt)}`);
          setText(message, row.message || '');
        }
        message.hidden = !message.textContent;
      }
      // Keep action nodes and keyboard focus while only progress text changes.
      const signature = [row.status,row.available,state.config?.mode,/database is (locked|busy)/i.test(row.message || '')].join(':');
      if (view.actionsSignature === signature || kind === 'pool') return;
      view.actionsSignature=signature;actions.replaceChildren();
      if (transfer) {
        if (row.status === 'needs_review') {
          actions.append(button(/database is (locked|busy)/i.test(row.message || '') ? '自动继续' : '核查副本／继续',
            () => /database is (locked|busy)/i.test(view.row.message || '') ? resumeLockedTransfer(view.row) : reconcile(view.row)));
          actions.append(deleteButton('清除本地副本任务', () => discardTransfer(view.row)));
        }
      } else {
        actions.append(button(row.status === 'needs_input' ? '查看原因／处理' : '查看详情', () => jobDetails(view.row)));
        if (!['running','queued','done','canceled'].includes(row.status)) actions.append(button('编辑商品', () => editJob(view.row)));
        if (['failed','needs_review','interrupted'].includes(row.status)) {
          const retry=button('重试保存',()=>jobAction(view.row,'retry'));retry.disabled=!row.available || state.config?.mode!=='live';actions.append(retry);
        }
        if (row.status === 'ready_publish') actions.append(button('移出发布队列',()=>jobAction(view.row,'remove-queue')));
        if (canDeleteJob(row)) actions.append(deleteButton('删除记录',()=>deleteRecords([view.row])));
      }
    };
    return view;
  }
  function autoResumeDatabaseLockedTransfers(transfers) {
    if (state.config?.mode !== 'live') return;
    const eligible = transfers.filter(row => row.status === 'needs_review'
      && /database is (locked|busy)/i.test(row.message || '')
      && !state.autoResumingLockedTransfers.has(row.transfer_id));
    if (!eligible.length) return;
    eligible.forEach(row => state.autoResumingLockedTransfers.add(row.transfer_id));
    // These errors are from the local SQLite queue before a copy reservation
    // exists. The server verifies that condition again before accepting it.
    Promise.resolve().then(async () => {
      let resumed = 0;
      for (const row of eligible) {
        try {
          await api('resume-transfer',{transferId:row.transfer_id,confirmation:'RESUME_SHARED_TO_QUEUE'});
          resumed++;
        } catch (error) {
          state.autoResumingLockedTransfers.delete(row.transfer_id);
        }
      }
      if (resumed) {
        notice(`已自动重新排队 ${resumed} 个本机数据库占用任务。`);
        await refresh('jobs');
      }
    });
  }
  function discardTransfer(row) {
    const dialog = modal('清除本地副本任务');
    dialog.content.append(element('p', `${name(row.payload.sourceAccountId)} / ${row.payload.detailId} → ${name(row.payload.targetAccountId)}`),
      element('p', '仅从任务列表移除，不删除妙手商品。后台保留副本映射和核查信息；创建结果不明的商品仍不会自动重复创建。'));
    let clearing = false;
    dialog.content.append(button('确认清除本地记录', async () => {
      if (clearing || state.modal !== dialog) return;
      clearing = true;
      try {
        await api('discard-transfer', {transferId:row.transfer_id,confirmation:'DISCARD_SHARED_TRANSFER'});
        dialog.overlay.remove(); if (state.modal === dialog) state.modal = null;
        notice('已清除本地副本任务；保留防重复创建信息，未删除任何妙手商品。');
        await refresh('jobs');
      } finally { clearing = false; }
    }));
  }
  function statusName(status, step) {
    if (status === 'running' && step === 'publish') return '正在发布';
    if (status === 'needs_review' && step === 'publish') return '发布结果需核查';
    if (status === 'failed' && step === 'publish') return '发布失败';
    if (status === 'canceled' && step === 'publish') return '已移出发布队列';
    return ({queued:'排队中',running:'正在保存',needs_input:'待你处理',needs_review:'需人工核查',interrupted:'任务已中断',failed:'保存失败',ready_publish:'已保存 · 待发布',done:'已提交发布',canceled:'已结束'})[status] || status;
  }
  function params(kind) {
    const prefix = kind === 'pool' ? 'sharedPool' : 'sharedJobs';
    const query = new URLSearchParams({page: state[kind].page});
    query.set('pageSize', state[kind].pageSize || 50);
    if (kind === 'jobs' && state.jobs.group) query.set('taskGroup',state.jobs.group);
    for (const [suffix, field] of [['Source', 'sourceAccountId'], ['Keyword','keyword'], ['Person','personId'], ['Account','accountId'], ['Status','status'], ['Partition','poolId'], ['Failure','failureCategory']]) {
      if ($(prefix + suffix)?.value) query.set(kind === 'jobs' && suffix === 'Person' ? 'personKey' : field, $(prefix + suffix).value);
    }
    const shop = $(prefix + 'Shop')?.value;
    if (shop) {const [account, id] = shop.split(':'); query.set('accountId', account); query.set('shopId', id);}
    return query;
  }
  async function refresh(kind = 'pool', options = {}) {
    if (!state.ready) return;
    if (kind === 'jobs' && (state.deleting || state.retrying || state.handlingFailures)) return;
    if (kind === 'pool' && !$('automation').classList.contains('active')) return;
    if (options.background && (state[kind].loading || state[kind].searchTimer)) return;
    if (!options.background && state[kind].searchTimer) {window.clearTimeout(state[kind].searchTimer);state[kind].searchTimer=null;}
    const revision = state.revisions[kind] = (state.revisions[kind] || 0) + 1;
    const activityRevision = state.activityRevision;
    const displayCache = typeof window === 'undefined' ? null : window.LocalViewCache;
    state[kind].controller?.abort();
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    state[kind].controller = controller;
    let timedOut = false;
    const timer = controller && typeof window.setTimeout === 'function'
      ? window.setTimeout(() => {timedOut = true;controller.abort();}, 12000) : null;
    state[kind].loading = true;
    state[kind].selectionBlocked = !options.background;
    const loadState = kind === 'jobs' ? $('sharedTaskLoadState') : null;
    if (loadState && (!options.background || !state.jobs.renderedQuery)) {
      loadState.hidden = false;
      loadState.textContent = '正在读取本地任务列表…';
    }
    try {
      const endpoint = kind === 'pool' ? 'pool' : state.kind;
      const query = endpoint + '?' + params(kind);
      const list=$(`shared${kind === 'pool' ? 'Pool' : 'Jobs'}List`);
      if (!state[kind].renderedQuery) void displayCache?.preview(state.cacheNamespace, query, list,
        ()=>revision===state.revisions[kind] && !state[kind].renderedQuery);
      const data = await api(query, undefined, controller ? {signal:controller.signal} : {});
      if (revision !== state.revisions[kind]) return;
      const partition = Number($(kind === 'pool' ? 'sharedPoolPartition' : 'sharedJobsPartition')?.value || 0);
      const capabilities = state.capabilities;
      const supportsPartition = capabilities
        ? capabilities.poolFiltering === true && capabilities.poolIds?.includes(partition)
        : Number.isSafeInteger(data.poolCount) && partition <= data.poolCount;
      if (partition && !supportsPartition)
        throw Error('运行中的后台暂不支持所选自动化池，请等待安全更新后重试；已保留当前列表。');
      if ((state[kind].pageSize || 50) !== (data.pageSize || 50))
        throw Error('后台尚未应用所选每页条数，请更新后台后重试；未显示旧分页数据。');
      if (kind === 'jobs' && $('sharedJobsPerson')) {
        if ($('sharedJobsPerson').value && !Array.isArray(data.personOptions)) throw Error('后台尚不支持人员筛选，请安全更新后台；未显示未筛选的数据。');
      }
      state[kind].page = data.page;
      if (kind === 'jobs') updateTaskSummary(data);
      displayCache?.clearPreview(list);
      rows(kind, data, {reset:!options.background && state[kind].renderedQuery !== query});
      state[kind].renderedQuery = query;
      if (displayCache) void displayCache.put(state.cacheNamespace, query,
        (state[kind].rows || []).map(row=>({title:row.title,thumbnail:row.thumbnail,
          detailId:row.detailId || row.source_id,accountName:name(row.sourceAccountId || row.source_account || row.accountId)})));
      if (kind === 'jobs') {
        state.jobs.lastFreshAt=Date.now();state.jobs.activityDirty=state.activityRevision!==activityRevision;
        state.jobs.loadError = false;
        if (loadState) {loadState.textContent = '';loadState.hidden = true;}
        const badge = $('taskBadge'), total = data.totalAll ?? data.total; badge.textContent = total; badge.classList.toggle('hidden', total === 0);
      }
    } catch (error) { if (revision === state.revisions[kind]) {
      if (kind === 'jobs' && (!controller?.signal.aborted || timedOut)) {
        state.jobs.loadError=true;state.jobs.shopCountsStale=true;updateShopOptions();
        if (loadState) {
          loadState.hidden=false;
          loadState.textContent=`本地任务列表${timedOut ? '读取超时' : '读取失败：'+error.message}；${state.jobs.renderedQuery ? '保留上次成功数据，' : ''}10 秒后自动重试，也可点击查询／刷新。`;
        }
      }
      if (!controller?.signal.aborted || timedOut) notice(timedOut ? '读取超时，请稍后刷新。' : error.message);
    } }
    finally {
      if (timer !== null) window.clearTimeout(timer);
      if (revision === state.revisions[kind]) {state[kind].loading = false;state[kind].selectionBlocked = false;state[kind].controller = null;}
    }
  }
  async function config(force = false) {
    if (state.configPending && !force) return state.configPending;
    const pending = loadConfig(force);
    state.configPending = pending;
    try { return await pending; }
    finally { if (state.configPending === pending) state.configPending = null; }
  }
  async function loadConfig(force = false) {
    const revision = state.revisions.config = (state.revisions.config || 0) + 1;
    const response = await api('config' + (force ? '?refresh=1' : ''));
    if (revision !== state.revisions.config) return;
    state.config = response;
    ['sharedPoolSource', 'sharedJobsSource', 'sharedJobsAccount'].forEach(id => accountOptions($(id)));
    const selected = [...$('sharedTargets').selectedOptions].map(o => o.value);
    targetOptions($('sharedTargets'), selected);
    updateShopOptions();
    updatePersonOptions();
    const site = $('sharedSite'), previousSite = site.value || 'US'; site.replaceChildren();
    const sites = new Map(); state.config.targets.forEach(g => g.sites.forEach(s => sites.set(s.key, s.value || s.key)));
    sites.forEach((label, key) => option(site, key, `${label} (${key})`)); site.value = sites.has(previousSite) ? previousSite : (sites.keys().next().value || '');
    if (state.config.errors.length) notice(state.config.errors.map(e => `${name(e.accountId)}：${e.message}`).join('；'));
    else $('sharedPoolNotice').textContent = '跨账号先创建副本；保存后等待你手动发布。原商品保留，各账号身份独立。';
    updateTaskControls();
  }
  async function open(page) {
    state.page = page;
    try { await initialize(); } catch (error) { notice(error.message); return; }
    // Local tasks and queue activity do not depend on remote shop metadata.
    const reads = [refresh(page === 'automation' ? 'pool' : 'jobs')];
    if (page === 'tasks' && state.capabilities?.taskSummary) reads.push(loadTaskSummary());
    if (page === 'tasks') reads.push(loadQueueActivity());
    if (!state.config?.targets?.length) reads.push(config().catch(e => notice(e.message)));
    await Promise.allSettled(reads);
  }
  async function preview() {
    const dialog = modal('跨账号分配预览'); dialog.message.textContent = '正在核对商品、人员归属与目标店铺…';
    const items = [...state.pool.selected.values()].map(r => ({sourceAccountId:r.sourceAccountId,detailId:r.detailId}));
    const plan = await api('preview', {items, targets:chosenTargets($('sharedTargets')), targetMode:$('sharedMode').value,
      site:$('sharedSite').value, priceMode:$('sharedPriceMode').value, price:$('sharedPrice').value,
      priceMultiplier:$('sharedMultiplier').value, editConcurrency:Number($('sharedConcurrency').value)});
    if (state.modal !== dialog) return;
    dialog.message.textContent = `将创建 ${plan.copyCount} 个副本、${plan.taskCount} 个保存任务，跳过 ${plan.skipped.length} 个商品／店铺组合。`;
    if (plan.copyCount) dialog.content.append(element('p', '确认后自动核对并补齐属性、复制到目标账号、认领店铺、编辑保存，完成后进入待发布队列。源商品不修改，不会自动发布；资料无法确认或创建结果不明时安全暂停。'));
    for (const row of plan.transfers) dialog.content.append(element('p', `${name(row.sourceAccountId)} / ${row.detailId}「${row.title}」 → ${row.shopIds.map(id => shopName(row.targetAccountId,id)).join('、')}（${row.needsCopy ? '创建副本' : '复用商品'}）`));
    for (const row of plan.skipped) dialog.content.append(element('p', `跳过 ${name(row.sourceAccountId)} / ${row.detailId}：${row.reason}`, 'shared-error'));
    const submit = button('确认分配并提交待发布队列', async () => {
      const data = await api('run', {previewId:plan.previewId, confirmation:'SAVE_SHARED_AUTOMATION_BATCH'});
      dialog.overlay.remove(); state.modal = null; state.pool.selected.clear(); updateCount('pool');
      await refresh('pool');
      notice(data.paused ? `批次 ${data.batchId} 已排队，自动处理当前暂停；恢复后会继续保存到待发布队列。`
        : `批次 ${data.batchId} 已提交，后台自动处理，完成后进入待发布队列。可继续选择商品，稍后到任务中心查看。`);
    }, true);
    submit.disabled = !plan.taskCount || state.config.mode !== 'live';
    const actions = element('div', '', 'shared-preview-actions');
    actions.append(submit, dialog.message);
    dialog.content.prepend(actions);
    if (state.config.mode !== 'live') dialog.content.append(element('p', '当前为只读模式：可以核对分配，不会创建、保存或发布商品。'));
  }
  async function bindPeople() {
    if (!state.config?.targets?.length) await config();
    const dialog = modal('人员店铺绑定（按来源账号区分）');
    const account = select('', []); accountOptions(account, false);
    if ($('sharedPoolSource').value) account.value = $('sharedPoolSource').value;
    const people = select('', []), targets = select('', []); targets.multiple = true; targets.size = 8;
    const note = element('p'); let result = null, revision = 0;
    function showPerson() {
      const person = result?.people.find(p => p.id === people.value);
      targetOptions(targets, (person?.targets || []).map(t => `${t.targetAccountId}:${t.shopId}`));
    }
    async function load(scan = false) {
      const current = ++revision, id = account.value;
      let response, first = true;
      do {
        response = await api(`people?sourceAccountId=${id}${scan ? '&scan=1' + (first ? '&refresh=1' : '') : ''}`);
        first = false;
        if (state.modal !== dialog || current !== revision) return;
        const scanLabel = ({unavailable:'待刷新',expired:'已过期，请刷新',loading:'读取中',ready:'已核对',failed:'读取失败，请重试'})[response.scan.status] || response.scan.status;
        note.textContent = `人员归属：${scanLabel} · 已核对 ${response.scan.scanned || 0} 件；同名人员仍按账号 ID 区分。`;
      } while (scan && response.scan.status === 'loading');
      result = response; people.replaceChildren();
      result.people.forEach(p => option(people, p.id, `${p.alias || p.name || '未匹配人员'}（${p.id}）`));
      showPerson();
    }
    account.addEventListener('change', () => {targets.replaceChildren(); people.replaceChildren(); load().catch(e=>notice(e.message));});
    people.addEventListener('change', showPerson);
    dialog.content.append(field('来源采集箱', account), button('刷新人员归属', () => load(true)), note,
      field('所属人员', people), field('目标店铺（可跨账号多选；全不选即解除绑定）', targets),
      button('保存当前人员绑定', async () => {
        await api('bindings', {sourceAccountId:Number(account.value),scope:result?.scope,personId:people.value,targets:chosenTargets(targets)});
        dialog.message.textContent = '绑定已保存在本机，不修改妙手人员归属。'; await load();
      }, true));
    await load();
  }
  async function jobAction(row, action, extra = {}) {
    const result = await api('action', {accountId:row.accountId,jobId:row.jobId,action,confirmation:'SHARED_JOB_ACTION',...extra});
    if (result?.status === 'queued') {
      state.jobs.selected.delete(row.key);
      if (state.modal) {state.modal.overlay.remove();state.modal=null;}
      notice('已重新加入编辑队列，可在“处理中”查看进度；保存完成后仍需手动发布。');
    } else {
      notice('操作已提交；各账号分别处理，保存不会自动发布。');
    }
    await refresh('jobs');
  }
  async function retrySelected() {
    if (state.retrying || state.handlingFailures) return;
    const selected = [...state.jobs.selected.values()];
    if (!selected.length) throw Error('请勾选任务');
    if (selected.length > 500) throw Error('每次最多重试 500 条任务');
    if (selected.some(row=>row.type === 'transfer' || !['failed','needs_review','interrupted'].includes(row.status) || !row.available))
      throw Error('仅可批量重试账号可用的失败或中断保存任务');
    state.retrying=true;updateTaskControls();
    const errors=[];let completed=0;
    try {
      for (const row of selected) {
        try {
          await api('action',{accountId:row.accountId,jobId:row.jobId,action:'retry',confirmation:'SHARED_JOB_ACTION'});
          state.jobs.selected.delete(row.key);
        } catch (error) {errors.push(`${row.key}: ${error.message}`);}
        completed++;
        notice(`正在提交重试：${completed}/${selected.length} 条${errors.length ? ` · 失败 ${errors.length} 条` : ''}`);
      }
    } finally {
      state.retrying=false;syncSelection('jobs');
      await refresh('jobs');
    }
    notice(errors.length ? `已提交 ${selected.length-errors.length} 条；失败 ${errors.length} 条：${errors.join('；')}` : '重试已全部提交，完成后等待手动发布');
  }
  async function handleKnownFailures() {
    if (state.handlingFailures || state.retrying || state.deleting) return;
    const eligible = (state.jobs.rows || []).filter(row=>row.safeBatchEligible);
    if (!eligible.length) throw Error('本页没有可安全自动处理的任务；远端结果不明的记录仍需核查。');
    if (eligible.length > 500) throw Error('每批最多处理 500 条任务');
    if (!window.confirm(`按原因处理本页 ${eligible.length} 条可恢复任务？将同步模板或安全续队，远端结果不明的任务不会自动重放。保存成功后仍需手动发布。`)) return;
    state.handlingFailures = true; updateTaskControls();
    let processed=0, failed=0, skipped=0, completed=0;
    const errors=[];
    try {
      // Keep the existing sequential submission pace, and revalidate each
      // record on the server immediately before acting on it.
      for (const row of eligible) {
        const entry = row.type === 'transfer' ? {transferId:row.transferId || row.transfer_id} : {accountId:row.accountId,jobId:row.jobId};
        try {
          const result = await api('failure-batch',{entries:[entry],confirmation:'HANDLE_SHARED_FAILURES'});
          processed += result.counts.processed; failed += result.counts.failed; skipped += result.counts.skipped;
          if (result.counts.processed) state.jobs.selected.delete(row.key);
          for (const item of result.failed || []) if (errors.length < 5) errors.push(item.reason || item.error || item.message || '处理失败');
        } catch (error) {failed++;if(errors.length<5)errors.push(error.message);}
        completed++;
        notice(`分类处理 ${completed}/${eligible.length} · 已接受 ${processed} · 失败 ${failed} · 跳过 ${skipped}`);
      }
    } finally {
      state.handlingFailures=false;syncSelection('jobs');
      await refresh('jobs');updateTaskControls();
    }
    notice(`分类处理完成：已接受 ${processed} 条，失败 ${failed} 条，跳过 ${skipped} 条。已接受不等于远端保存成功；不会自动发布。${errors.length?' '+errors.join('；'):''}`);
  }
  function canDeleteJob(row) {
    return ['queued','needs_input','done','failed','needs_review','interrupted','canceled'].includes(row.status);
  }
  function canDeleteRecord(row) {
    // A transfer may be removed only after it has stopped and requires review.
    // This keeps unknown remote outcomes and dedupe evidence while never
    // deleting a queued or running cross-account copy operation.
    return row.type === 'transfer'
      ? ['needs_review','discarded'].includes(row.status)
      : canDeleteJob(row);
  }
  function canSelectRecord(kind, row) {
    return !!row.available || (kind === 'jobs' && canDeleteRecord(row));
  }
  function deleteButton(text, action) {
    const node = button(text, action); node.classList.add('shared-delete'); return node;
  }
  async function deleteRecords(entries = [...state.jobs.selected.values()]) {
    if (state.deleting) return;
    const selected = [...new Map(entries.map(row => [row.type === 'transfer'
      ? `transfer:${row.transferId || row.transfer_id}` : `${row.accountId}:${row.jobId}`, row])).values()];
    if (!selected.length) throw Error('请先勾选要删除的任务记录');
    if (selected.length > 500) throw Error('每次最多删除 500 条任务记录');
    const dialog = modal('删除本地任务记录');
    dialog.content.append(element('p',`将删除 ${selected.length} 条本地记录。排队中和待处理的任务会一并结束。`),
      element('p','不删除妙手商品、自动化池商品或副本映射。跨账号副本仅能清除“需人工核查”的本地记录；正在运行的任务会跳过，待发布任务需先移出发布队列。'));
    const confirm = deleteButton(`确认删除 ${selected.length} 条记录`,()=>performDeleteRecords(selected,dialog));
    confirm.id = 'sharedConfirmDelete';
    dialog.content.append(button('取消，保留记录',()=>{dialog.overlay.remove();if(state.modal===dialog)state.modal=null;}),confirm);
    dialog.message.textContent = '尚未删除任何记录，请核对后再确认。';
  }
  async function performDeleteRecords(selected, dialog) {
    if (state.deleting || state.modal !== dialog) return;
    state.deleting = true; state.revisions.jobs = (state.revisions.jobs || 0) + 1;
    try {
      const result = await api('delete-records', {jobs:selected.map(row=>row.type === 'transfer'
        ? {transferId:row.transferId || row.transfer_id} : {accountId:row.accountId,jobId:row.jobId}),confirmation:'DELETE_SHARED_JOB_RECORDS'});
      for (const row of result.deleted) state.jobs.selected.delete(row.transferId
        ? `transfer:${row.transferId}` : `${row.accountId}:${row.jobId}`);
      const displayRef = row => row.transferId ? `副本任务 ${row.transferId}` : `${name(row.accountId)} / ${row.jobId}`;
      notice(`已删除 ${result.deleted.length} 条本地记录${result.errors.length ? `，${result.errors.length} 条未删除：` + result.errors.map(e=>`${displayRef(e)}：${e.message}`).join('；') : '；妙手商品保持不变。'}`);
      if (dialog && state.modal === dialog && !result.errors.length) {dialog.overlay.remove();state.modal=null;}
    } finally {
      state.deleting = false; updateCount('jobs'); await refresh('jobs');
    }
  }
  function taskEventTime(value) {
    if (!value) return '时间未记录';
    const numeric = typeof value === 'number' || (typeof value === 'string' && /^\d+(?:\.\d+)?$/.test(value));
    const date = new Date(numeric ? Number(value) * 1000 : value);
    if (Number.isNaN(date.getTime())) return String(value);
    const parts = new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
    }).formatToParts(date);
    const fields = Object.fromEntries(parts.map(part => [part.type, part.value]));
    return `${fields.year}-${fields.month}-${fields.day} ${fields.hour}:${fields.minute}:${fields.second}`;
  }
  async function jobLogistics(dialog, row, job, message) {
    const choices = select('', [['', '请选择店铺配置']]);
    const status = element('p', '正在同步店铺最新配置…');
    let current = job, syncing = false, fresh = false;
    const canResume = () => current.status !== 'queued' && fresh && !syncing && choices.value && row.available && state.config?.mode === 'live';
    const resume = button('已处理问题，继续保存', () => {
      if (canResume()) return jobAction(row,'resume',{selectedId:choices.value});
    }, true);
    function render() {
      choices.replaceChildren(); option(choices, '', '请选择店铺配置');
      const choice = current.choices?.[current.step] || {}, options = choice.options || [];
      options.forEach(o => option(choices,o.id,o.name));
      const defaults = options.filter(o => o.name.trim() === (current.step === 'freight' ? '通用' : '发货'));
      choices.value = options.some(o=>o.id === choice.selectedId) ? choice.selectedId :
        (defaults.length === 1 ? defaults[0].id : '');
      choices.disabled = syncing || !options.length;
      resume.disabled = !canResume();
    }
    async function sync() {
      if (syncing) return;
      syncing = true; fresh = false; refreshButton.disabled = true; render();
      status.textContent = '正在同步店铺最新配置…';
      try {
        const updated = await api('action', {accountId:row.accountId,jobId:row.jobId,
          action:'refresh-logistics',confirmation:'SHARED_JOB_ACTION'});
        if (state.modal !== dialog) return;
        current = updated; fresh = true; message.textContent = current.message;
        status.textContent = current.autoQueuedJobs
          ? `店铺配置已同步，已将 ${current.autoQueuedJobs} 条同店铺任务重新加入编辑队列。`
          : `店铺配置已同步 · 北京时间 ${taskEventTime(current.choices?.[current.step]?.refreshedAt)}`;
      } catch (error) {
        if (state.modal === dialog) status.textContent = `同步失败：${error.message}。保留原选项，请重新刷新。`;
      } finally {
        syncing = false;
        if (state.modal === dialog) {refreshButton.disabled = !row.available || current.status === 'queued'; render();}
      }
    }
    const refreshButton = button('刷新店铺配置', sync);
    choices.addEventListener('change', () => {resume.disabled = !canResume();});
    dialog.content.append(choices, refreshButton, status, resume);
    render();
    if (row.available) await sync();
    else {refreshButton.disabled = true; status.textContent = '账号当前不可用，无法同步店铺配置。';}
  }
  async function jobDetails(row) {
    const dialog = modal('任务详情与处理');
    const job = await api(`job?targetAccountId=${row.accountId}&jobId=${row.jobId}`);
    if (state.modal !== dialog) return;
    dialog.content.append(element('p', `${name(row.sourceAccountId)} / ${row.sourceDetailId} → ${shopName(row.accountId,row.shopId)}`));
    const message = element('p', job.message); dialog.content.append(message);
    const logistics = job.status === 'needs_input' && ['freight','warehouse'].includes(job.step);
    const logisticsRefresh = logistics ? jobLogistics(dialog, row, job, message) : null;
    if (job.status === 'needs_input' && !logistics) {
      const choices = select('', []);
      (job.choices?.[job.step]?.options || []).forEach(o => option(choices,o.id,o.name));
      if (choices.options.length) dialog.content.append(choices);
      const resume = button('已处理问题，继续保存', () => jobAction(row,'resume',{selectedId:choices.value || ''}), true);
      resume.disabled = !row.available || state.config?.mode !== 'live'; dialog.content.append(resume);
    }
    if (['failed','needs_review','interrupted'].includes(job.status)) {
      const retry = button('重试保存',()=>jobAction(row,'retry'),true); retry.disabled = !row.available || state.config?.mode !== 'live'; dialog.content.append(retry);
    }
    if (state.config?.mode !== 'live') dialog.content.append(element('p','当前只读模式：可查看或删除本地记录，不能继续保存或发布。'));
    if (['needs_input','queued','running'].includes(job.status)) dialog.content.append(button('结束编辑任务',()=>jobAction(row,'cancel')));
    if (canDeleteJob(job)) dialog.content.append(deleteButton('删除记录',()=>deleteRecords([row])));
    if (job.preparationNotes?.length) {
      dialog.content.append(element('h3','跨账号创建前属性处理记录'));
      const notes = element('ul'); job.preparationNotes.forEach(note=>notes.append(element('li',note)));
      dialog.content.append(notes);
    }
    dialog.content.append(element('p', '处理记录（北京时间 UTC+8）'));
    const events = element('ol'); job.events.forEach(e=>events.append(element('li',`${taskEventTime(e.createdAt)} · ${e.step} · ${e.message}`)));
    dialog.content.append(events);
    if (logisticsRefresh) await logisticsRefresh;
  }
  async function editJob(row) {
    const job = await api(`editor?targetAccountId=${row.accountId}&jobId=${row.jobId}`);
    if (Number(window.collectionAccountContext.id) !== row.accountId) {
      window.collectionAccountContext.switchTo(row.accountId, {sharedEditJob:row.jobId}); return;
    }
    window.openEditorForAutomationTask(job);
  }
  async function reconcile(row) {
    const dialog = modal('核查跨账号副本');
    if (/database is (locked|busy)/i.test(row.message || '')) {
      dialog.content.append(element('p','这是本机数据库暂时被占用，未要求填写目标商品 ID。系统会直接重新排队，并自动继续复制与保存。'),
        button('自动继续', async () => {
          await api('resume-transfer',{transferId:row.transfer_id,confirmation:'RESUME_SHARED_TO_QUEUE'});
          dialog.overlay.remove(); state.modal=null; await refresh('jobs');
        }, true));
      return;
    }
    const target = input('', row.target_id || '', 'number');
    const sizeChartValidation = /groupIdAndValueMap.*(?:必填|required)/i.test(row.message || '');
    const canDirectResubmit = /产品属性【.+】必填|承诺发货时效错误|英文标题不能超过250个字符/.test(row.message || '') || sizeChartValidation;
    dialog.content.append(element('p', `${name(row.payload.sourceAccountId)} / ${row.payload.detailId} → ${name(row.payload.targetAccountId)}`),
      element('p', '若目标采集箱已生成商品，请填写其 ID 后核对标题、类目、图片和规格；不匹配不会关联。创建结果不明时不会自动重复创建。'),
      field('目标采集箱商品 ID',target),button('核对已有副本并继续保存',async()=>{
        await api('reconcile',{transferId:row.transfer_id,targetDetailId:Number(target.value),confirmation:'RECONCILE_SHARED_COPY'});
        dialog.overlay.remove();state.modal=null;await refresh('jobs');
      },true));
    if (canDirectResubmit) dialog.content.append(button(sizeChartValidation ? '自动补齐尺码表并重试创建' : '同步源属性并重试创建',async()=>{
      await api('reconcile',{transferId:row.transfer_id,confirmation:'RECONCILE_SHARED_COPY'});
      dialog.overlay.remove();state.modal=null;await refresh('jobs');
    },true),element('p',sizeChartValidation ? '妙手已明确拒绝本次创建，目标商品未生成。系统会补齐空的尺码表分组字段后重新创建，不需要填写目标商品 ID。' : '仅当妙手明确返回校验错误时可用。包含的组件优先按源资料映射到目标类目；缺失或无法匹配时提示人工处理，不猜填。英文标题超长时按完整词精简至250字符内；源商品不变，图片、SKU和发货时效保持原资料。'));
  }
  async function resumeLockedTransfer(row) {
    await api('resume-transfer',{transferId:row.transfer_id,confirmation:'RESUME_SHARED_TO_QUEUE'});
    notice('本机数据库占用任务已重新排队，正在自动继续处理。');
    await refresh('jobs');
  }
  function publishConfirmation(dialog, payload, count) {
    const release = button(`确认发布 ${count} 件`,async()=>{
      if(state.modal!==dialog || state.publishing)return;
      if(state.config?.mode!=='live')throw Error('当前为只读模式，不能提交真实发布');
      state.publishing=true;updateTaskControls();
      try {
        if(!payload.previewId) dialog.publishRequestId ||= crypto.randomUUID();
        let result;
        try {
          result=await api('publish',{...payload,...(payload.previewId?{}:{requestId:dialog.publishRequestId}),confirmation:'PUBLISH_SHARED_READY_JOBS'});
        } catch(error) {
          if(!state.capabilities?.publishReceipts)throw error;
          const receipt=await api('publish-receipt?requestId='+encodeURIComponent(payload.previewId || dialog.publishRequestId));
          if(receipt.status==='submitted')result=receipt.result;
          else throw Error(`发布申请已保留回执，已入队 ${Number(receipt.result?.submitted)||0} 件；未自动重复提交，请刷新核对后重新预览。申请编号：${receipt.request_id}`);
        }
        state.jobs.selected.clear();dialog.overlay.remove();if(state.modal===dialog)state.modal=null;
        notice(result.joinedExisting
          ? `已将 ${result.submitted} 件加入当前发布队列，将在正在提交的商品后依次发布。`
          : `已提交 ${result.submitted} 件发布任务；最终上架结果请到发布记录核对。`);
        await refresh('jobs');
      } finally {state.publishing=false;updateTaskControls();}
    },true);
    release.disabled = state.config?.mode !== 'live';dialog.content.append(release);
    if(release.disabled)dialog.content.append(element('p','当前为只读模式，仅供预览，不能提交真实发布。'));
    dialog.content.append(element('p','提交妙手发布任务不等于 TEMU 最终上架成功，请到对应账号发布记录核对。'));
  }
  async function publishByCount() {
    if (state.publishPreviewLoading) {notice('正在生成发布预览，请等待当前请求完成或关闭确认窗口。');return;}
    const count=Number($('sharedPublishCount').value),interval=Number($('sharedPublishInterval').value);
    if(!Number.isInteger(count)||count<1)throw Error('本次发布数量应为大于 0 的整数');
    if(!Number.isInteger(interval)||interval<1||interval>600)throw Error('发布间隔应为 1 到 600 的整数秒');
    const filters=Object.fromEntries(params('jobs'));delete filters.page;delete filters.pageSize;delete filters.taskGroup;
    const dialog=modal('按数量发布 · 确认明细');
    await loadPublishPreview(dialog,count,interval,filters);
  }
  async function updateRunningPublishInterval() {
    const interval=Number($('sharedPublishInterval').value);
    if(!Number.isInteger(interval)||interval<1||interval>600)throw Error('发布间隔应为 1 到 600 的整数秒');
    if(!window.confirm(`将正在发布队列的后续发布间隔调整为 ${interval} 秒。当前正在提交的商品不会中断；从下一件开始按新间隔执行。继续吗？`))return;
    const result=await api('publish-interval',{intervalSeconds:interval,confirmation:'UPDATE_SHARED_PUBLISH_INTERVAL'});
    notice(`已更新 ${result.batches.length} 个运行中发布批次的间隔为 ${result.intervalSeconds} 秒；当前请求不受中断，下一件起生效。`);
    await loadQueueActivity();await refresh('jobs');
  }
  async function loadPublishPreview(dialog,count,interval,filters,shopCounts=null) {
    if (state.publishPreviewLoading && state.publishPreviewLoading !== dialog)
      throw Error('正在生成发布预览，请等待当前请求完成或关闭确认窗口。');
    state.publishPreviewLoading = dialog;
    dialog.message.textContent='正在从本机待发布队列生成预览…';
    const abortController=dialog.abortController;
    const canScheduleTimeout=typeof window !== 'undefined' && typeof window.setTimeout === 'function';
    const timeout=canScheduleTimeout ? window.setTimeout(()=>{dialog.previewTimedOut=true;abortController?.abort();},30000) : null;
    let plan;
    try {
      plan=await api('publish-preview',{publishCount:count,intervalSeconds:interval,filters,...(shopCounts?{shopCounts}:{})},
        abortController ? {signal:abortController.signal} : {});
    } catch(error) {
      if (dialog.closed) return;
      if (error?.name === 'AbortError' && dialog.previewTimedOut)
        throw Error('生成发布预览超时，未提交任何发布任务；请稍后重试。');
      throw error;
    } finally {
      if (timeout !== null) window.clearTimeout(timeout);
      if (state.publishPreviewLoading === dialog) state.publishPreviewLoading = null;
    }
    if(state.modal!==dialog || dialog.closed)return;
    dialog.message.textContent='';
    if(dialog.heading)dialog.heading.textContent=filters.shops?'多店铺发布 · 确认明细':'按数量发布 · 确认明细';
    dialog.content.replaceChildren();
    dialog.content.append(element('p',`当前筛选共有 ${plan.total} 件待发布；本次请求 ${plan.requestedCount} 件，实际发布 ${plan.count} 件。发布间隔 ${plan.intervalSeconds} 秒。`));
    if(plan.count<plan.requestedCount)dialog.content.append(element('p','剩余数量不足，已按实际待发布数量生成预览。'));
    dialog.content.append(element('p',plan.perShopCounts
      ? '已按每家店铺填写的数量分别取数；本次明细已固定，不受当前勾选影响。'
      : '按当前列表顺序从第一条跨页取数，数量为所有目标店铺合计，不是每店各发此数量；不受勾选影响。'));
    if(filters.keyword)dialog.content.append(element('p',`商品搜索：${filters.keyword}`));
    if(filters.sourceAccountId)dialog.content.append(element('p',`来源：${name(filters.sourceAccountId)}`));
    if(filters.personKey)dialog.content.append(element('p',`所属人员：${personLabel(filters.personKey)}`));
    for(const shop of plan.shops)dialog.content.append(element('p',plan.perShopCounts
      ? `${shopName(shop.accountId,shop.shopId)} · 填写 ${shop.requestedCount} 件 · 可发布 ${shop.availableCount} 件 · 本次 ${shop.count} 件`
      : `${shopName(shop.accountId,shop.shopId)} · ${shop.count} 件`));
    const details=element('details'),summary=element('summary',`查看商品明细（展示前 ${plan.sample.length} 件）`),list=element('ol');
    for(const job of plan.sample)list.append(element('li',`${job.title || job.sourceDetailId} · ${name(job.sourceAccountId)} / ${job.sourceDetailId} → ${shopName(job.accountId,job.shopId)}`));
    details.append(summary,list);dialog.content.append(details,element('p','本次明细已固定，10 分钟内有效。新进入队列的商品不会补入；任务状态或账号凭据变化时需重新预览。'));
    publishConfirmation(dialog,{previewId:plan.previewId},plan.count);
  }
  function publishShopChoices() {
    const choices=new Map(),counts=state.jobs.shopPendingCounts;
    for(const group of state.config?.targets || [])for(const shop of group.shops) {
      const accountId=Number(group.accountId),shopId=Number(shop.shopId),key=`${accountId}:${shopId}`;
      choices.set(key,{key,accountId,shopId});
    }
    for(const key of counts?.keys() || [])if(!choices.has(key)) {
      const [accountId,shopId]=key.split(':').map(Number);choices.set(key,{key,accountId,shopId});
    }
    return [...choices.values()].map(shop=>{
      const account=state.config?.accounts.find(a=>a.id===shop.accountId);
      return {...shop,count:counts ? (counts.get(shop.key)||0) : null,
        available:!!account?.configured && !account.blocked};
    });
  }
  function publishMultipleShops() {
    const dialog=modal('多店铺发布 · 选择店铺');
    const filters=Object.fromEntries(params('jobs'));
    for(const key of ['page','pageSize','taskGroup','accountId','shopId'])delete filters[key];
    dialog.content.append(element('p','可跨账号勾选店铺，并分别填写每家店铺的发布数量；沿用当前商品搜索、来源和所属人员筛选。店铺待发布数仍为全店总数，符合筛选的实际数量以预览为准。'));
    if(filters.keyword)dialog.content.append(element('p',`商品搜索：${filters.keyword}`));
    if(filters.sourceAccountId)dialog.content.append(element('p',`来源：${name(filters.sourceAccountId)}`));
    if(filters.personKey)dialog.content.append(element('p',`所属人员：${personLabel(filters.personKey)}`));
    const settings=element('div','','shared-multi-publish-settings');
    const interval=input('', $('sharedPublishInterval').value,'number');interval.min='1';interval.max='600';interval.step='1';
    settings.append(field('发布间隔（秒）',interval));dialog.content.append(settings);
    const list=element('div','','shared-publish-shops'),choices=publishShopChoices(),checks=[];
    let account;
    for(const shop of choices) {
      if(account!==shop.accountId){account=shop.accountId;list.append(element('h3',name(account)));}
      const check=input('','','checkbox');check.disabled=!shop.available;
      check.checked=shop.available && shop.key===$('sharedJobsShop').value;
      const quantity=input('',String(Math.min(Number($('sharedPublishCount').value)||50,shop.count||1)),'number');
      quantity.min='1';quantity.step='1';quantity.disabled=!check.checked||!shop.available;
      const label=element('label','','shared-publish-shop');
      const quantityField=element('span','','shared-publish-quantity');
      quantityField.append(element('span','发布数量'),quantity);
      label.append(check,element('span',`${shopName(shop.accountId,shop.shopId)} · 待发布 ${shop.count ?? '—'}${shop.available?'':'（账号不可用）'}`),quantityField);
      checks.push({check,quantity,shop});list.append(label);
    }
    const summary=element('p'),actions=element('div','','shared-actions');
    const preview=button('预览所选店铺发布',async()=>{
      const selected=checks.filter(r=>r.check.checked&&!r.check.disabled).map(r=>({accountId:r.shop.accountId,shopId:r.shop.shopId,publishCount:Number(r.quantity.value)}));
      const n=selected.reduce((sum,shop)=>sum+shop.publishCount,0),seconds=Number(interval.value);
      if(!selected.length||selected.length>100)throw Error('请选择 1 到 100 家发布店铺');
      if(selected.some(shop=>!Number.isInteger(shop.publishCount)||shop.publishCount<1))throw Error('每个店铺的发布数量应为大于 0 的整数');
      if(!Number.isInteger(seconds)||seconds<1||seconds>600)throw Error('发布间隔应为 1 到 600 的整数秒');
      await loadPublishPreview(dialog,n,seconds,filters,selected);
    },true);
    const update=()=>{
      const selected=checks.filter(r=>r.check.checked&&!r.check.disabled),total=selected.reduce((sum,r)=>sum+Number(r.quantity.value),0);
      const valid=selected.every(r=>Number.isInteger(Number(r.quantity.value))&&Number(r.quantity.value)>=1);
      summary.textContent=`已选 ${selected.length} 家店铺 · 发布数量合计 ${Number.isFinite(total)?total:'—'} 件`;
      preview.disabled=!selected.length||selected.length>100||!valid;
    };
    actions.append(button('选择有待发布的店铺',()=>{checks.forEach(r=>{r.check.checked=!r.check.disabled&&r.shop.count>0;r.quantity.disabled=!r.check.checked;});update();}),
      button('清空店铺选择',()=>{checks.forEach(r=>{r.check.checked=false;r.quantity.disabled=true;});update();}));
    checks.forEach(r=>{r.check.addEventListener('change',()=>{r.quantity.disabled=!r.check.checked;update();});r.quantity.addEventListener('input',update);});update();
    dialog.content.append(actions,list,summary,preview);
  }
  async function publishSelected() {
    const jobs = [...state.jobs.selected.values()];
    if (!jobs.length || jobs.some(j=>j.status !== 'ready_publish')) throw Error('请只勾选已保存、待发布的任务');
    const dialog = modal('确认跨账号发布');
    jobs.forEach(j=>dialog.content.append(element('p',`${name(j.sourceAccountId)} / ${j.sourceDetailId} → ${shopName(j.accountId,j.shopId)} / ${j.site}`)));
    publishConfirmation(dialog,{jobs:jobs.map(j=>({accountId:j.accountId,jobId:j.jobId})),intervalSeconds:Number($('sharedPublishInterval').value)},jobs.length);
  }
  function updateMoreFilters() {
    const more = $('sharedMoreFilters'); if (!more) return;
    const extra = ['Source','Keyword','Status','Failure'].filter(key=>$('sharedJobs'+key)?.value.trim()).length;
    more.textContent = extra ? `更多筛选 (${extra})` : '更多筛选';
  }
  function toolbar(kind) {
    const prefix = kind === 'pool' ? 'sharedPool' : 'sharedJobs', node = element('div','','shared-toolbar');
    const source = select(prefix+'Source',[['','全部来源采集箱']]);
    const keyword = input(prefix+'Keyword'); keyword.placeholder = '商品名称或 ID';
    if (kind === 'pool') {
      node.append(field('来源采集箱',source),field('商品搜索',keyword));
      const person = select(prefix+'Person',[['','全部人员']]);node.append(field('所属人员',person));
      source.addEventListener('change',async()=>{
        person.replaceChildren();option(person,'','全部人员');
        const current = source.value;
        if (current) try {const data=await api(`people?sourceAccountId=${current}`);
          if(source.value===current)data.people.forEach(p=>option(person,p.id,`${p.alias||p.name||'未匹配人员'}（${p.id}）`));
        } catch(error){notice(error.message);}
      });
    } else {
      node.classList.add('shared-job-filters');
      node.append(field('目标账号',select(prefix+'Account',[['','全部账号']])));
      const content = element('div','','shared-filter-fields'); content.id='sharedExtraFilters'; content.hidden=true;
      const more = button('更多筛选',()=>{content.hidden=!content.hidden;more.setAttribute('aria-expanded',String(!content.hidden));});
      more.id='sharedMoreFilters';
      more.classList.add('shared-more-filters');more.setAttribute('aria-expanded','false');more.setAttribute('aria-controls',content.id);
      const shop = select(prefix+'Shop',[['','全部店铺']]);
      const shopHint = element('small','','shared-shop-count-hint');shopHint.id='sharedShopPendingHint';shopHint.hidden=true;
      shop.setAttribute('aria-label','目标店铺');
      shop.setAttribute('aria-describedby',shopHint.id);
      node.append(field('目标店铺',shop));
      const person = select(prefix+'Person',[['','全部人员'],['unknown','未知归属']]);person.disabled=true;
      const personField = field('所属人员',person), personHint=element('small','','shared-person-hint');
      personHint.id='sharedPersonHint';personHint.hidden=true;person.setAttribute('aria-describedby',personHint.id);
      personField.append(personHint);node.append(personField);
      content.append(field('来源采集箱',source),field('搜索商品',keyword),
        field('具体状态',select(prefix+'Status',[['','本组全部状态'],...taskGroups[state.jobs.group].statuses.map(s=>[s,statusName(s)])])));
      content.append(field('问题类型',select(prefix+'Failure',[['','全部问题'],['template','运费模板 / 仓库'],['size_chart','尺码表'],['attributes','商品属性'],['database','数据库占用'],['transient','限流 / 超时'],['unknown','其他待核查']])));
      node.append(more,shopHint,content);
    }
    node.append(field('自动化池',select(prefix+'Partition',[['','三个池汇总'],['1','池 1（原池）'],['2','池 2'],['3','池 3（新增）']])));
    const updateMore=()=>{if(kind === 'jobs')updateMoreFilters();};
    const search=async()=>{
      if (state[kind].searchTimer) window.clearTimeout(state[kind].searchTimer);
      state[kind].searchTimer=null;updateMore();state[kind].page=1;state[kind].selected.clear();updateCount(kind);await refresh(kind);
    };
    const queueSearch=()=>{
      if (state[kind].searchTimer) window.clearTimeout(state[kind].searchTimer);
      state[kind].controller?.abort();state.revisions[kind]=(state.revisions[kind] || 0)+1;
      state[kind].loading=false;state[kind].selectionBlocked=true;
      state[kind].searchTimer=window.setTimeout(()=>search().catch(error=>notice(error.message)),250);
    };
    node.append(button('查询／刷新',search));keyword.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing&&!composing)search();});
    let composing=false;
    keyword.addEventListener('compositionstart',()=>{
      composing=true;if(state[kind].searchTimer)window.clearTimeout(state[kind].searchTimer);state[kind].searchTimer=null;
    });
    keyword.addEventListener('compositionend',()=>{composing=false;queueSearch();});
    keyword.addEventListener('input',event=>{updateMore();if(!composing&&!event.isComposing)queueSearch();});
    node.querySelectorAll('select').forEach(s=>s.addEventListener('change',()=>{
      if (kind === 'jobs' && s.id === 'sharedJobsAccount') $('sharedJobsShop').value = '';
      if (kind === 'jobs' && s.id === 'sharedJobsShop' && s.value) $('sharedJobsAccount').value = s.value.split(':')[0];
      if (kind === 'jobs' && s.id === 'sharedJobsSource') {
        const person = $('sharedJobsPerson');
        if (s.value && person.value && person.value !== 'unknown' && person.value.split(':')[0] !== s.value) person.value='';
        updatePersonOptions();
      }
      queueSearch();
    }));return node;
  }
  function selectBar(kind) {
    const node=element('div','','shared-actions');
    node.append(button('选择本页全部可操作记录（替换当前）',()=>{
      if (state[kind].selectionBlocked) { notice('正在切换列表，请稍后选择'); return; }
      // Each click replaces the prior page selection, so the current page can
      // be fully selected even when it is configured to display 500 records.
      state[kind].selected.clear();
      (state[kind].rows || []).filter(r=>canSelectRecord(kind, r)).forEach(r=>state[kind].selected.set(r.key,r));
      syncSelection(kind);
    }),button('清空选择',()=>{state[kind].selected.clear();syncSelection(kind);}));
    const count=element('span','已选 0 件');count.id=kind==='pool'?'sharedPoolCount':'sharedJobsCount';node.append(count);return node;
  }
  function mount() {
    const preferences = window.automationPreferences?.() || {};
    const pool=element('section','','shared-root');pool.id='sharedAutomationRoot';
    pool.append(element('h1','共享自动化池'),element('p','所有采集箱统一管理 · 来源账号与商品 ID 独立 · 可一次选择当前页全部可操作记录'));
    const noticeNode=element('p','','shared-message');noticeNode.id='sharedPoolNotice';noticeNode.setAttribute('role','status');pool.append(noticeNode);
    const controls=element('div','','shared-config');
    const mode=select('sharedMode',[['manual','手动跨账号多选店铺'],['personnel','按来源人员绑定店铺']]);
    const targets=select('sharedTargets',[]);targets.multiple=true;targets.size=7;
    controls.append(field('保存方式',mode),button('人员店铺绑定／刷新归属',bindPeople),button('刷新账号与店铺',()=>config(true)),field('目标店铺（按住 Ctrl 多选）',targets),
      field('发布站点',select('sharedSite',[['US','美国站 (US)']])),
      field('申报价方式',select('sharedPriceMode',[['floor','固定下限 X ～ X+20'],['source_multiplier','原始 SKU 价格倍数']])),
      field('申报价下限 X',input('sharedPrice',preferences.price || $('automationSkuPrice')?.value || '160','number')),
      field('原价倍数',input('sharedMultiplier',$('automationPriceMultiplier')?.value || '1.5','number')),
      field('同时编辑任务数（全账号共享）',input('sharedConcurrency',$('automationEditConcurrency')?.value || '8','number')),
      button('预览分配并批量保存',preview,true),button('移出选中商品',async()=>{
        const items=[...state.pool.selected.values()].map(r=>({sourceAccountId:r.sourceAccountId,detailId:r.detailId}));
        if(!window.confirm('仅从本地共享池移出所选商品，不删除妙手商品，确定吗？'))return;
        const result=await api('remove',{items,confirmation:'REMOVE_SHARED_POOL_ITEMS'});state.pool.selected.clear();
        notice(`已移出 ${result.removed.length} 件。${result.errors.map(e=>`${e.sourceAccountId}:${e.detailId} ${e.message}`).join('；')}`);await refresh('pool');
      }));
    mode.addEventListener('change',()=>{targets.disabled=mode.value==='personnel';});
    pool.append(controls,toolbar('pool'));
    const poolTools=element('div','','shared-pool-list-tools');
    const poolPager=element('div','','shared-pager');poolPager.id='sharedPoolPager';
    poolTools.append(selectBar('pool'),poolPager);
    const poolList=element('div','','shared-list');poolList.id='sharedPoolList';pool.append(poolTools,poolList);
    $('automation').append(pool);
    $('sharedPriceMode').value = $('automationPriceMode')?.value || 'floor';
    const tasks=element('section','','shared-root');tasks.id='sharedTasksRoot';tasks.append(element('h1','任务中心'),
      element('p','保存商品 → 处理问题 → 手动发布。所有账号统一查看，商品仍按目标账号独立处理。','shared-task-intro'));
    const tabs=element('div','','shared-task-tabs'); tabs.setAttribute('aria-label','按任务进度查看');
    for(const [key,group] of Object.entries(taskGroups)) {
      const tab=button('',()=>switchTaskGroup(key)); tab.id='sharedTab'+key;
      tab.append(element('span',group.label),element('b','—'));tab.setAttribute('aria-pressed',String(key===state.jobs.group)); tabs.append(tab);
    }
    const view=element('h2','需要处理');view.id='sharedJobsView';
    const hint=element('p',taskGroups.attention.hint,'shared-view-hint');hint.id='sharedJobsHint';
    const total=element('span','','shared-jobs-total');total.id='sharedJobsTotal';
    const heading=element('div','','shared-view-heading');heading.append(view,total);
    const activity = element('p','队列状态读取中…','shared-queue-activity');activity.id='sharedQueueActivity';activity.setAttribute('role','status');
    const poolDetails=element('details','','shared-queue-activity');
    poolDetails.append(element('summary','查看各账号独立池和队列状态'));
    const poolActivity=element('div');poolActivity.id='sharedPoolActivity';poolDetails.append(poolActivity);
    const todayStats = element('section','','shared-today-store-stats');todayStats.id='sharedTodayStoreStats';todayStats.hidden=true;
    tasks.append(activity,poolDetails,todayStats);
    window.WorkbenchDiagnostics?.mount(tasks,name,()=>state.config?.accounts || []);
    const loadState=element('p','正在读取本地任务…','shared-message');loadState.id='sharedTaskLoadState';loadState.setAttribute('role','status');
    tasks.append(tabs,loadState,toolbar('jobs'),heading,hint);
    const info=element('p','','shared-message');info.id='sharedJobsNotice';info.setAttribute('role','status');tasks.append(info);
    const actionPanel=element('div','','shared-task-actions');actionPanel.append(selectBar('jobs'));
    const release=element('div','','shared-actions'); release.id='sharedJobActions';
    const settings=field('发布间隔（秒）',input('sharedPublishInterval',$('publishQueuePublishInterval')?.value || '1','number')); settings.id='sharedPublishSettings';
    const publishCount=input('sharedPublishCount','50','number');publishCount.min='1';publishCount.step='1';
    publishCount.addEventListener('input',updateTaskControls);
    const countField=field('本次发布数量（件）',publishCount);countField.id='sharedPublishCountField';
    const countPreview=button('预览发布前 50 件',publishByCount,true);countPreview.id='sharedPublishCountPreview';
    const multi=button('多店铺发布',publishMultipleShops);multi.id='sharedPublishMulti';
    const publish=button('发布选中商品',publishSelected);publish.id='sharedPublishSelected';
    const adjust=button('调整运行中发布间隔',updateRunningPublishInterval);adjust.id='sharedPublishIntervalAdjust';
    const pause=button('暂停自动处理',setAutomationPause);pause.id='sharedAutomationPause';
    const reload=button('一键重新加载排队任务',reloadQueuedWork);reload.id='sharedReloadQueued';
    const retry=button('重试选中失败任务',retrySelected);retry.id='sharedRetrySelected';
    const known=button('批量处理本页可恢复问题',handleKnownFailures);known.id='sharedKnownFailures';
    const remove=deleteButton('删除选中记录',()=>deleteRecords());remove.id='sharedDeleteSelected';
    const selectionHint=element('p','','shared-selection-hint');selectionHint.id='sharedSelectionHint';
    release.append(settings,countField,countPreview,multi,publish,adjust,pause,reload,retry,known,remove);actionPanel.append(release,selectionHint);tasks.append(actionPanel);
    for(const [id,cls] of [['sharedTransfers','shared-transfers'],['sharedJobsList','shared-list'],['sharedJobsPager','shared-pager']]){const n=element('div','',cls);n.id=id;tasks.append(n);}
    $('tasks').append(tasks);
    updateTaskControls();
    // Retain the existing tiny settings record; no product lists or snapshots
    // are stored in the browser. Each request still sends explicit targets.
    for (const [shared, original] of [['sharedPrice','automationSkuPrice'],['sharedPriceMode','automationPriceMode'],
      ['sharedMultiplier','automationPriceMultiplier'],['sharedConcurrency','automationEditConcurrency'],
      ['sharedPublishInterval','publishQueuePublishInterval']]) {
      $(shared).addEventListener('change',()=>{$(original).value=$(shared).value;window.saveAutomationPreferences?.();});
    }
  }
  function accountsChanged(data) {
    if (!state.config) return;
    state.revisions.config = (state.revisions.config || 0) + 1;
    state.config.accounts = [...data.accounts, ...(data.removedAccounts || [])];
    state.config.targets = []; // Reload target shops on the next open, never reuse removed targets.
    const removed = new Set((data.removedAccounts || []).map(a => Number(a.id)));
    for (const kind of ['pool','jobs']) {
      state.revisions[kind] = (state.revisions[kind] || 0) + 1;
      state[kind].controller?.abort();
      if(state[kind].searchTimer)window.clearTimeout(state[kind].searchTimer);
      state[kind].searchTimer=null;
      for (const [key, row] of state[kind].selected) if (removed.has(row.sourceAccountId) || removed.has(row.accountId)) state[kind].selected.delete(key);
      state[kind].loading = false;
    }
    ['sharedPoolSource','sharedJobsSource','sharedJobsAccount'].forEach(id=>accountOptions($(id)));
    targetOptions($('sharedTargets')); updateShopOptions(); updateTaskControls();
  }
  window.sharedAutomation={open,refresh,accountsChanged,consumeActivity};
  async function initialize() {
    if (state.ready) return;
    if (state.initializing) return state.initializing;
    const pending = (async () => {
      const controller = new AbortController();
      const timer = window.setTimeout(() => controller.abort(), 12000);
      let health;
      try {
        const response = await fetch('/api/health', {signal:controller.signal});
        if (!response.ok) throw Error('后台状态暂时无法读取，将自动重试。');
        health = await response.json();
      } finally { window.clearTimeout(timer); }
      window.updateApplicationVersion?.(health);
      if(!health.sharedAutomation)throw Error('共享自动化需要 V1.2.94 后台，请先重启后台再刷新页面。');
      const accounts = await api('accounts');
      state.config={...accounts,targets:[],mode:health.mode,errors:[]};
      state.capabilities=health.capabilities || null;
      state.cacheNamespace=health.viewCacheNamespace || null;
      window.WorkbenchLiveStatus?.configure(state.capabilities);
      if (Array.isArray(state.capabilities?.poolIds)) {
        for (const id of ['sharedPoolPartition','sharedJobsPartition']) {
          const node=$(id);if(!node?.replaceChildren)continue;
          const previous=node.value;node.replaceChildren();option(node,'','全部自动化池');
          state.capabilities.poolIds.forEach(poolId=>option(node,String(poolId),`池 ${poolId}`));
          if (state.capabilities.poolIds.includes(Number(previous))) node.value=previous;
        }
      }
      ['sharedPoolSource','sharedJobsSource','sharedJobsAccount'].forEach(id=>accountOptions($(id)));
      state.ready=true;
    })();
    state.initializing = pending;
    try { await pending; }
    finally { if (state.initializing === pending) state.initializing = null; }
  }
  document.addEventListener('DOMContentLoaded',()=>{
    mount();document.body.classList.add('shared-automation-enabled');
    async function poll(){
      try{
        await initialize();
        if(!document.hidden&&!state.modal&&$('tasks').classList.contains('active') &&
           (!window.WorkbenchLiveStatus?.isLive() || state.jobs.loadError || state.jobs.activityDirty || !state.jobs.renderedQuery ||
            Date.now()-(state.jobs.lastFreshAt || 0)>60000))await refresh('jobs',{background:true});
      }catch(error){
        notice('后台连接暂时未完成，将自动重试。' + (error.name === 'AbortError' ? '' : error.message));
      // A timed-out full query can still be unwinding in SQLite after the
      // browser aborts.  Leave a larger gap before the next background list
      // refresh so it cannot stack more readers behind active save writes.
      }finally{window.setTimeout(poll,10000);}
    }
    poll();
    function activityPollDelay(){
      if (document.hidden || !$('tasks')?.classList.contains('active')) return 30000;
      if(!state.queueActivity && state.capabilities?.activityEvents)return 1500;
      const activity = state.queueActivity;
      return activity && (activity.running || activity.queued || activity.editing || activity.publishWorker || activity.publishing)
        ? 4000 : 10000;
    }
    async function activityPoll(){try{await loadQueueActivity();}finally{window.setTimeout(activityPoll,activityPollDelay());}}
    activityPoll();
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)loadQueueActivity();});
    const editId=new URLSearchParams(location.search).get('sharedEditJob');
    if(editId) initialize().then(async()=>{
      const job=await api(`editor?targetAccountId=${window.collectionAccountContext.id}&jobId=${encodeURIComponent(editId)}`);
      const url=new URL(location.href);url.searchParams.delete('sharedEditJob');history.replaceState(null,'',url);window.openEditorForAutomationTask(job);
    }).catch(error=>notice(error.message));
  });
})();


