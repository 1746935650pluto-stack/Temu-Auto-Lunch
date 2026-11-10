(() => {
  'use strict';
  const pages = new Set(['workbench','collectionAccount','dataStatistics','orders','zhihou','pricing',
    'collectionAutomation','automation','automaticTasks','tasks','publishRecords','settings','products','templates']);
  let handlers = null, initialized = false, requested = false, applying = false;
  let domReady = document.readyState !== 'loading', current = null, pending = null;
  const pageNames = {workbench:'采集箱',collectionAccount:'采集箱',dataStatistics:'数据统计',orders:'订单明细',
    zhihou:'智猴物流',pricing:'自动核价',collectionAutomation:'自动化采集',automation:'自动化池',
    automaticTasks:'自动任务',tasks:'任务中心',publishRecords:'发布记录',settings:'接口设置',products:'商品',templates:'模板'};
  function desktopState(route) {
    const raw = new URL(window.location.href).searchParams.get('collectionAccount') || '1';
    const collectionAccountId = /^[1-9]\d?$/.test(raw) && Number(raw) <= 20 ? Number(raw) : 1;
    let title = pageNames[route.page];
    if (route.page === 'orders') title += ' / ' + (document.querySelector?.('#orderCenterTitle')?.textContent?.split(' / ').slice(1).join(' / ') || route.orderAccountId);
    else if (route.page === 'workbench' || route.page === 'collectionAccount') {
      title += ' / ' + (document.querySelector?.(`#miaoshouCollectionChildren [data-account-id="${collectionAccountId}"] .collection-nav-name`)?.textContent || collectionAccountId);
    }
    window.temuDesktop?.navigationChanged?.({...route,collectionAccountId,title});
  }
  function normalize(route) {
    const raw = String(route?.orderAccountId || '1');
    return {page: pages.has(route?.page) ? route.page : 'workbench',
      orderAccountId: /^[1-9]\d?$/.test(raw) && Number(raw) <= 20 ? Number(raw) : 1};
  }
  function fromUrl() {
    const params = new URL(window.location.href).searchParams;
    return normalize({page: params.get('page'), orderAccountId: params.get('orderAccount')});
  }
  function key(route) { return route.page + (route.page === 'orders' ? ':' + route.orderAccountId : ''); }
  function commit(route, replace) {
    route = normalize(route);
    const url = new URL(window.location.href); url.searchParams.set('page', route.page);
    if (route.page === 'orders') url.searchParams.set('orderAccount', String(route.orderAccountId));
    else url.searchParams.delete('orderAccount');
    if (route.page !== 'workbench') url.searchParams.delete('sharedEditJob');
    const state = {...(window.history.state || {}), temuNavigation: route};
    if (replace || !current || key(route) === key(current)) window.history.replaceState(state, '', url.href);
    else window.history.pushState(state, '', url.href);
    current = route;
    desktopState(route);
  }
  function restore(route) {
    applying = true;
    try { handlers.navigate(normalize(route)); }
    finally { applying = false; }
    commit(handlers.current(), true);
  }
  function start() {
    if (!requested || !domReady || !handlers || initialized) return;
    initialized = true; restore(pending || fromUrl());
  }
  function record(page, orderAccountId) {
    const route = normalize({page, orderAccountId});
    if (applying) return;
    if (!initialized) {
      if (!['workbench','collectionAccount'].includes(route.page)) pending = route;
      return;
    }
    commit(route, false);
  }
  window.addEventListener('popstate', () => { if (initialized && handlers) restore(fromUrl()); });
  document.addEventListener('DOMContentLoaded', () => { domReady = true; start(); });
  window.workbenchNavigation = {record,
    open(value) {
      if (!initialized || !value || !pages.has(value.page)) return false;
      const route = normalize(value), here = new URL(window.location.href);
      const raw = String(value.collectionAccountId || '1');
      if (!/^[1-9]\d?$/.test(raw) || Number(raw) > 20) return false;
      if (Number(here.searchParams.get('collectionAccount') || 1) !== Number(raw)) {
        return window.collectionAccountContext?.switchTo(Number(raw),{page:route.page,orderAccountId:route.orderAccountId}) === true;
      } else { applying = true; try { handlers.navigate(route); } finally { applying = false; } commit(handlers.current(),false); return true; }
    },
    configure(value) { handlers = value; start(); },
    ready() { requested = true; start(); return true; }};
})();
