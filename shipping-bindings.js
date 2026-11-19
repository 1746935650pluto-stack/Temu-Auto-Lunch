/* One local backend preference controls site choices in every workbench view. */
(() => {
  if (!document.getElementById('visibleSitesOptions')) return;
  let selected = null, available = [], readyOk = false, revision = 0;
  const byId = id => document.getElementById(id);
  async function request(url, init) {
    const response = await fetch(url, init), data = await response.json();
    if (!response.ok) throw Error(data.error || '站点设置读取失败');
    return data.data;
  }
  const ready = request('/api/settings/visible-sites', {cache:'no-store'}).then(data=>{
    selected = data.sites === null ? null : new Set(data.sites);readyOk=true;
  }).catch(()=>{const status=byId('visibleSitesStatus');if(status)status.textContent='后台尚未更新或站点配置读取失败，暂时显示全部站点。';});
  function allowed(key){return selected===null || selected.has(String(key).toUpperCase());}
  function apply() {
    for(const id of ['liveSite','editSite','automationPublishSite','autoTaskSite','sharedSite','shippingSite']) {
      const select=byId(id);if(!select)continue;
      for(const option of select.options) {
        const hidden=!!option.value && !allowed(option.value);
        option.hidden=hidden;option.disabled=hidden;
      }
      if(select.value && !allowed(select.value)) {select.value='';select.dispatchEvent(new Event('change',{bubbles:true}));}
    }
  }
  async function open() {
    await ready;const box=byId('visibleSitesOptions'),status=byId('visibleSitesStatus');if(!box)return;
    if(!readyOk){box.textContent='站点总控将在后台更新后可用。';return;}
    try {
      const response=await request('/api/sites',{cache:'no-store'});
      available=response.data?.publishSiteOptions || [];
      for (const key of selected || []) if(!available.some(row=>String(row.key).toUpperCase()===key)) available.push({key,value:key});box.replaceChildren();
      if(!available.length){box.textContent='未读取到可用站点，请检查采集箱连接。';return;}
      for(const row of available){const label=document.createElement('label'),check=document.createElement('input');
        check.type='checkbox';check.value=String(row.key).toUpperCase();check.checked=allowed(check.value);
        label.append(check,document.createTextNode(`${row.value || row.key} (${row.key})`));box.append(label);}
      status.textContent='勾选需要的站点后保存。';
    } catch(error){box.textContent='站点读取失败';status.textContent=error.message;}
  }
  byId('refreshVisibleSites')?.addEventListener('click',open);
  byId('selectAllVisibleSites')?.addEventListener('click',()=>byId('visibleSitesOptions').querySelectorAll('input').forEach(input=>input.checked=true));
  byId('saveVisibleSites')?.addEventListener('click',async()=>{
    const status=byId('visibleSitesStatus'),sites=[...byId('visibleSitesOptions').querySelectorAll('input')].filter(input=>input.checked).map(input=>input.value);
    if(!readyOk){status.textContent='后台尚未更新或站点配置读取失败。';return;}
    if(!sites.length){status.textContent='请至少选择一个站点。';return;}
    const token=++revision;
    try {const result=await request('/api/settings/visible-sites',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sites})});
      if(token!==revision)return;selected=new Set(result.sites);apply();status.textContent='已保存，站点选择已更新。';
    } catch(error){status.textContent='保存失败：'+error.message;}
  });
  const ids=new Set(['liveSite','editSite','automationPublishSite','autoTaskSite','sharedSite','shippingSite']);
  const observer=new MutationObserver(records=>{if(records.some(record=>ids.has(record.target.id) || ids.has(record.target.parentNode?.id)))apply();});
  observer.observe(document.documentElement,{childList:true,subtree:true});
  async function refresh() {
    if(!readyOk)return;
    const token=++revision;
    try {const result=await request('/api/settings/visible-sites',{cache:'no-store'});
      if(token!==revision)return;selected=result.sites===null?null:new Set(result.sites);apply();
    } catch (_) { /* Keep the last confirmed choices on a transient read failure. */ }
  }
  window.addEventListener?.('focus', refresh);
  window.SitePreferences={ready,allowed,apply,open};ready.then(apply);
})();

/* Explicit local logistics defaults, isolated by account, shop and site. */
(() => {
  const $ = id => document.getElementById(id);
  if (!$('shippingBindingForm')) return;
  let version = 0, controller, model = null, busy = false, loading = false, reopen = false;
  const fields = ['shippingAccount', 'shippingShop', 'shippingSite', 'shippingFreight', 'shippingWarehouse'];
  function controls() {
    fields.forEach(id => { $(id).disabled = busy || loading || !$(id).options.length; });
    $('saveShippingBinding').disabled = busy || loading || !model || !$('shippingFreight').value || !$('shippingWarehouse').value;
    $('resetShippingBinding').disabled = busy || loading || !model || !(model.binding.freightId || model.binding.warehouseId);
    $('refreshShippingBinding').disabled = busy || loading;
  }
  function options(id, rows, prompt, value = '') {
    const select = $(id); select.replaceChildren();
    const placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = prompt; select.append(placeholder);
    for (const row of rows || []) {
      const option = document.createElement('option'); option.value = String(row.id); option.textContent = row.name; select.append(option);
    }
    select.value = String(value);
    if (!select.value) select.value = '';
  }
  async function request(url, init = {}) {
    const response = await fetch(url, init), body = await response.json();
    if (!response.ok) throw new Error(body.error || '物流配置请求失败');
    return body.data;
  }
  async function read(level = 'selection', refresh = false) {
    if (busy) return;
    const token = ++version;
    controller?.abort(); controller = new AbortController();
    const account = $('shippingAccount').value, shop = $('shippingShop').value, site = $('shippingSite').value;
    model = null; loading = true;
    if (level === 'accounts' || level === 'account') {
      options('shippingShop', [], '请选择店铺'); options('shippingSite', [], '请选择站点');
    }
    options('shippingFreight', [], '请先选择账号、店铺和站点');
    options('shippingWarehouse', [], '请先选择账号、店铺和站点');
    controls(); $('shippingBindingStatus').textContent = '正在读取物流配置…';
    try {
      if (level !== 'accounts' && !account) {
        $('shippingBindingStatus').textContent = '请选择要绑定的账号。'; return;
      }
      const query = new URLSearchParams();
      if (level !== 'accounts' && account) query.set('accountId', account);
      if (level === 'selection' && account && shop && site) { query.set('shopId', shop); query.set('site', site); }
      if (refresh) query.set('refresh', '1');
      if (window.SitePreferences) await window.SitePreferences.ready;
      const data = await request('/api/settings/shipping?' + query, {signal: controller.signal});
      if (token !== version) return;
      if (level === 'accounts') {
        options('shippingAccount', data.accounts.filter(row => row.configured), '请选择账号');
        $('shippingBindingStatus').textContent = '选择账号后读取店铺与站点。';
      } else {
        if (String(data.accountId) !== account) throw new Error('返回账号不一致，请重新读取');
        if (level === 'account') {
          options('shippingShop', data.shops, '请选择店铺');
          const sites = data.sites.filter(row => !window.SitePreferences || window.SitePreferences.allowed(row.id));
          options('shippingSite', sites, '请选择站点', sites.some(row => row.id === 'US') ? 'US' : '');
        }
        if (level === 'selection' && shop && site) {
          if (String(data.shopId) !== shop || data.site !== site || !data.binding) throw new Error('返回店铺或站点不一致，请重新读取');
          options('shippingFreight', data.freight, '请选择运费模板', data.binding.freightId);
          options('shippingWarehouse', data.warehouses, '请选择发货仓库', data.binding.warehouseId);
          model = data;
          const missing = (data.binding.freightId && !$('shippingFreight').value) || (data.binding.warehouseId && !$('shippingWarehouse').value);
          const unavailable = [!data.freight.length ? '运费模板' : '', !data.warehouses.length ? '发货仓库' : ''].filter(Boolean);
          $('shippingBindingStatus').textContent = missing ? '已绑定的物流选项失效，请重新选择并保存；自动任务不会改用其他物流。'
            : unavailable.length ? `妙手接口未返回当前店铺和站点的${unavailable.join('及')}。请确认已在妙手同步对应站点的配置，然后点击“重新读取”。`
            : data.binding.freightId ? '已手动绑定，后续编辑保存优先使用此配置。' : '尚未手动绑定，当前使用“通用／发货”自动匹配规则。';
        } else $('shippingBindingStatus').textContent = '请选择店铺与站点后读取可绑定的物流。';
      }
    } catch (error) {
      if (token === version && error.name !== 'AbortError') $('shippingBindingStatus').textContent = '读取失败：' + error.message;
    } finally { if (token === version) { loading = false; controls(); } }
  }
  async function save(reset = false) {
    if (busy || loading || !model || (!reset && (!$('shippingFreight').value || !$('shippingWarehouse').value))) return;
    const snapshot = model, token = version;
    busy = true; controls(); $('shippingBindingStatus').textContent = '正在保存本机绑定…';
    try {
      const data = await request('/api/settings/shipping', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({accountId: snapshot.accountId, shopId: snapshot.shopId, site: snapshot.site,
          scope: snapshot.scope, revision: snapshot.binding.revision,
          freightId: reset ? '' : $('shippingFreight').value, warehouseId: reset ? '' : $('shippingWarehouse').value,
          confirmation: reset ? 'RESET_SHIPPING_BINDING' : 'SAVE_SHIPPING_BINDING'})});
      if (token !== version) return;
      if (data.accountId !== snapshot.accountId || data.shopId !== snapshot.shopId || data.site !== snapshot.site) throw new Error('保存回执不一致，请刷新核对');
      model = data;
      options('shippingFreight', data.freight, '请选择运费模板', data.binding.freightId);
      options('shippingWarehouse', data.warehouses, '请选择发货仓库', data.binding.warehouseId);
      $('shippingBindingStatus').textContent = reset ? '已恢复自动匹配规则，不改动已运行任务或商品。' : '绑定已保存；后续编辑保存优先使用，不自动编辑或发布商品。';
    } catch (error) { if (token === version) { model = null; $('shippingBindingStatus').textContent = '保存未确认：' + error.message + '。请重新读取后核对。'; } }
    finally { busy = false; controls(); if (reopen) { reopen = false; read('accounts'); } }
  }
  $('shippingBindingForm').addEventListener('submit', event => { event.preventDefault(); save(); });
  $('shippingAccount').addEventListener('change', () => read('account'));
  for (const id of ['shippingShop', 'shippingSite']) $(id).addEventListener('change', () => read());
  for (const id of ['shippingFreight', 'shippingWarehouse']) $(id).addEventListener('change', controls);
  $('refreshShippingBinding').addEventListener('click', () => read(!$('shippingAccount').value ? 'accounts' : $('shippingShop').value && $('shippingSite').value ? 'selection' : 'account', true));
  $('resetShippingBinding').addEventListener('click', () => save(true));
  window.shippingBindings = {open() { if (busy) reopen = true; else read('accounts'); }, close() { ++version; controller?.abort(); loading = false; model = null; reopen = false; controls(); }};
  controls();
})();
