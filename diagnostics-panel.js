/* Read-only runtime metrics and explicit, preview-first local log archiving. */
(() => {
  const node = (tag, text = '', cls = '') => {const value=document.createElement(tag);value.textContent=text;value.className=cls;return value;};
  function mount(parent, accountName, configuredAccounts) {
    const panel=node('details','','shared-diagnostics');
    panel.append(node('summary','运行性能与历史日志'));
    const info=node('p','展开后每 5 秒更新；统计最近 5 分钟，本次启动后重新计数。','muted');
    const message=node('p','','shared-message');message.setAttribute('role','status');
    const refresh=node('button','刷新性能','button subtle');refresh.type='button';
    const tableWrap=node('div','','shared-diagnostics-table');
    let timer=null,controller=null,loading=false,disposed=false;
    async function load() {
      if (loading || disposed || !panel.open) return;
      if (document.hidden || !parent.closest('section.active')) {
        if(timer)window.clearTimeout(timer);
        timer=window.setTimeout(load,5000);return;
      }
      loading=true;refresh.disabled=true;controller=new AbortController();
      const timeout=window.setTimeout(()=>controller?.abort(),10000);
      try {
        const response=await fetch('/api/performance',{signal:controller.signal});const data=await response.json();
        if (!response.ok) throw Error(data.error || '性能信息读取失败');
        if (!panel.open || disposed) return;
        const table=node('table'),head=node('thead'),headRow=node('tr');
        for(const label of ['账号','远端请求占用 / 当前上限','等待请求','请求数','请求 P95','限流 / 超时','SQLite 门锁等待','编辑 / 发布提交（每分钟）','限流标志（近 1 分钟）'])headRow.append(node('th',label));
        head.append(headRow);table.append(head);
        const rowsById=new Map();
        for (const candidate of data.accounts || []) {
          const accountId=Number(candidate?.accountId);
          if (Number.isSafeInteger(accountId) && accountId > 0) rowsById.set(accountId,candidate);
        }
        // Metrics are process-local and can briefly omit a newly configured
        // account while configuration is reloading. Keep the table stable by
        // merging the authoritative account list supplied by the shared view.
        for (const account of (typeof configuredAccounts === 'function' ? configuredAccounts() : [])) {
          const accountId=Number(account?.id);
          if (Number.isSafeInteger(accountId) && accountId > 0 && !rowsById.has(accountId)) {
            rowsById.set(accountId,{accountId,unavailable:true});
          }
        }
        const rows=[...rowsById.values()].sort((left,right)=>Number(left.accountId)-Number(right.accountId));
        const body=node('tbody');
        for(const row of rows) {
          const tr=node('tr');
          const rateLimitedCount=Number(row.rateLimitedCount || 0);
          const recentKnown=Number.isFinite(row.rateLimitedRecentCount);
          const recentRateLimitedCount=recentKnown ? row.rateLimitedRecentCount : 0;
          const rateFlagUnavailable=row.unavailable || !recentKnown;
          const values=row.unavailable ? [accountName(row.accountId),'未返回','—','—','未返回','—','—','—'] :
            [accountName(row.accountId),`${row.active} / ${row.limit}`,row.queued || 0,row.requestCount,
            row.latencyP95Ms == null ? '暂无样本' : `${Math.round(row.latencyP95Ms)} ms`,
            `${rateLimitedCount} / ${row.timeoutCount}`,
            `${row.sqliteWaitCount} 次 / ${Math.round(row.sqliteWaitMs)} ms`,
            `${Number(row.editPerMinute || 0).toFixed(1)} / ${Number(row.publishPerMinute || 0).toFixed(1)}`];
          values.forEach(value=>tr.append(node('td',String(value))));
          const rateFlag=node('span',rateFlagUnavailable ? '未获取' : recentRateLimitedCount > 0 ? `已限流（${recentRateLimitedCount} 次）` : '正常',
            `shared-rate-flag ${rateFlagUnavailable ? 'unknown' : recentRateLimitedCount > 0 ? 'limited' : 'normal'}`);
          rateFlag.title=rateFlagUnavailable ? '本次响应未提供此账号最近 1 分钟的限流统计，等待后台更新或下次刷新；不代表零请求。' :
            '仅统计最近 1 分钟妙手返回的限流；超时不会显示为限流。';
          const rateCell=node('td');rateCell.append(rateFlag);tr.append(rateCell);body.append(tr);
        }
        if (!rows.length) {const row=node('tr'),empty=node('td','暂无请求样本，任务开始后会自动显示。');empty.colSpan=9;row.append(empty);body.append(row);}
        table.append(body);tableWrap.replaceChildren(table);
        const sampled=new Date(data.sampledAt).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
        info.textContent=`最近 ${Math.round(data.windowSeconds / 60)} 分钟 · 北京时间 ${sampled} · 全局请求占用 ${data.global?.active || 0} / ${data.limits?.globalCap || 24}。每账号最多 ${data.limits?.perAccountCap || 6} 路，按接口表现自动调整。SQLite 等待仅指本机池连接门锁；发布计数为已提交妙手，非最终上架。限流标志仅统计最近 1 分钟。统计在后台重启后重新开始。`;
        message.textContent='';
      } catch(error) {if(panel.open && !disposed)message.textContent=controller?.signal.aborted?'性能读取超时，请稍后重试。':error.message;}
      finally {
        window.clearTimeout(timeout);controller=null;loading=false;refresh.disabled=false;
        if(timer)window.clearTimeout(timer);
        if(panel.open&&!disposed)timer=window.setTimeout(load,5000);
      }
    }
    refresh.addEventListener('click',()=>load());
    panel.addEventListener('toggle',()=>{if(panel.open)load();else {if(timer)window.clearTimeout(timer);controller?.abort();}});
    const visible=()=>{if(!document.hidden&&panel.open)load();};
    document.addEventListener('visibilitychange',visible);
    const archive=node('div','','shared-archive-tools');
    const retention=node('select');retention.setAttribute('aria-label','成功任务日志保留时间');
    for(const days of [7,30,90,180,365]){const option=node('option',`保留最近 ${days} 天`);option.value=String(days);retention.append(option);}retention.value='7';
    const preview=node('button','预览可归档日志','button subtle'),apply=node('button','归档预览中的成功日志','button subtle');
    preview.type=apply.type='button';apply.disabled=true;
    const result=node('p','仅归档保留期之前已明确完成的成功任务日志；任务和历史明细仍可查询。','muted');result.setAttribute('role','status');
    let approvedPreview=null,archiveBusy=false;
    const clearPreview=()=>{approvedPreview=null;apply.disabled=true;};retention.addEventListener('change',clearPreview);
    async function archiveRequest(write) {
      if(archiveBusy || (write && !approvedPreview))return;
      const days=Number(retention.value);
      if(write&&!window.confirm(`将归档预览中 ${approvedPreview.events} 条成功任务日志，保留最近 ${days} 天。归档后的历史明细仍可查看。确认继续？`))return;
      archiveBusy=true;preview.disabled=true;apply.disabled=true;retention.disabled=true;
      try {
        const options=write?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({retentionDays:days,previewToken:approvedPreview.token,confirmation:'ARCHIVE_SUCCESS_EVENTS'})}:{};
        const response=await fetch(`/api/maintenance/event-archive?retentionDays=${days}`,options);const payload=await response.json();
        if(!response.ok)throw Error((payload.error || '日志归档操作失败')+(payload.data ? `；已移出热库 ${payload.data.deletedEvents || 0} 条，请重新预览剩余日志。` : ''));const data=payload.data;
        if(write){clearPreview();result.textContent=`已归档 ${data.archivedEvents || 0} 条日志，从当前任务库移出 ${data.deletedEvents || 0} 条。${data.errors?.length?'部分池未完成：'+data.errors.map(row=>row.error || row.message || String(row)).join('；'):'历史明细可继续查询。'}`;}
        else {approvedPreview=data;result.textContent=`可归档 ${data.events} 条成功日志，涉及 ${data.jobs} 个任务；其他任务保留。`;}
      } catch(error){clearPreview();result.textContent=error.message;}
      finally {archiveBusy=false;preview.disabled=false;retention.disabled=false;apply.disabled=!(approvedPreview?.events>0);}
    }
    preview.addEventListener('click',()=>archiveRequest(false));apply.addEventListener('click',()=>archiveRequest(true));
    archive.append(retention,preview,apply,result);panel.append(info,refresh,message,tableWrap,archive);parent.append(panel);
    return {panel,dispose(){disposed=true;if(timer)window.clearTimeout(timer);controller?.abort();document.removeEventListener('visibilitychange',visible);}};
  }
  window.WorkbenchDiagnostics={mount};
})();
