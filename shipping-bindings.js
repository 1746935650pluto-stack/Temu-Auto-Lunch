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
      const data = await request('/api/settings/shipping?' + query, {signal: controller.signal});
      if (token !== version) return;
      if (level === 'accounts') {
        options('shippingAccount', data.accounts.filter(row => row.configured), '请选择账号');
        $('shippingBindingStatus').textContent = '选择账号后读取店铺与站点。';
      } else {
        if (String(data.accountId) !== account) throw new Error('返回账号不一致，请重新读取');
        if (level === 'account') {
          options('shippingShop', data.shops, '请选择店铺');
          options('shippingSite', data.sites, '请选择站点', data.sites.some(row => row.id === 'US') ? 'US' : '');
        }
        if (level === 'selection' && shop && site) {
          if (String(data.shopId) !== shop || data.site !== site || !data.binding) throw new Error('返回店铺或站点不一致，请重新读取');
          options('shippingFreight', data.freight, '请选择运费模板', data.binding.freightId);
          options('shippingWarehouse', data.warehouses, '请选择发货仓库', data.binding.warehouseId);
          model = data;
          const missing = (data.binding.freightId && !$('shippingFreight').value) || (data.binding.warehouseId && !$('shippingWarehouse').value);
          $('shippingBindingStatus').textContent = missing ? '已绑定的物流选项失效，请重新选择并保存；自动任务不会改用其他物流。'
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
