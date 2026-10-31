// One document is pinned to one account. Reloading on account switches discards
// old editors, selections and asynchronous responses instead of reusing them.
(() => {
  const raw = new URLSearchParams(window.location.search).get('collectionAccount') || '1';
  const id = /^[1-9]\d?$/.test(raw) && Number(raw) <= 20 ? Number(raw) : 1;
  const nativeFetch = window.fetch.bind(window);
  let pendingWrites = 0;
  window.collectionAccountContext = {
    id,
    switchTo(nextId, params = {}) {
      if (pendingWrites) { window.alert('当前操作仍在提交，请完成后再切换采集箱。'); return; }
      const editor = document.querySelector('#liveEditor');
      if (editor && !editor.classList.contains('hidden') &&
          !window.confirm('切换采集箱会关闭当前编辑器并丢弃未保存内容，是否继续？')) return;
      const url = new URL(window.location.href);
      url.searchParams.set('collectionAccount', String(nextId));
      url.searchParams.delete('sharedEditJob');
      if (params.sharedEditJob) url.searchParams.set('sharedEditJob', params.sharedEditJob);
      window.location.assign(url.href);
    },
  };
  window.fetch = async (input, options = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, window.location.href);
    const local = url.origin === window.location.origin && url.pathname.startsWith('/api/');
    // Diagnostics are global unless their caller explicitly asks for an
    // account. Do not silently filter the all-account performance table.
    const scoped = local && !/^\/api\/(orders|miaoshou-accounts|settings|performance|automation\/shared)(\/|$)/.test(url.pathname);
    const headers = new Headers(options.headers || (typeof input !== 'string' ? input.headers : undefined));
    if (scoped) headers.set('X-Miaoshou-Account-Id', String(id));
    const writing = local && !['GET', 'HEAD'].includes((options.method || input.method || 'GET').toUpperCase());
    if (writing) pendingWrites++;
    try { return await nativeFetch(input, {...options, headers}); }
    finally { if (writing) pendingWrites--; }
  };
})();
