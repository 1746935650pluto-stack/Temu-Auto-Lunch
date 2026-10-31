(() => {
  'use strict';
  const LOCAL_ORIGIN = window.location.origin;
  const BRIDGE_SOURCE = 'temu-pricing-workbench';
  const defaults = Object.freeze({ formula: 'price * 0.9 * 0.95 - price * 0.95 / 12 - rate * weight - cost - fixed', rate: '65', fixed: '4', minProfit: '3', minMargin: '13', minQuote: '15', passAction: '申请调整更新申报价格', mixedAction: '' });
  const fields = Object.freeze({ formula: 'pricingConfigFormula', rate: 'pricingConfigRate', fixed: 'pricingConfigFixed', minProfit: 'pricingConfigMinProfit', minMargin: 'pricingConfigMinMargin', minQuote: 'pricingConfigMinQuote', passAction: 'pricingConfigPassAction', mixedAction: 'pricingConfigMixedAction' });
  let bridgeTimer = null;
  const $ = (id) => document.getElementById(id);
  const isWorkbenchUrl = (value) => { try { const url = new URL(value); return url.protocol === 'chrome-extension:' && /\/workbench\.html$/.test(url.pathname); } catch { return false; } };
  const setStatus = (text, kind = 'neutral') => { const state = $('pricingWorkbenchState'); if (state) { state.textContent = text; state.className = `status-pill ${kind}`; } };
  const setConfigStatus = (text, error = false) => { const status = $('pricingConfigStatus'); if (status) { status.textContent = text; status.classList.toggle('is-error', error); } };
  const fillSettings = (settings = {}) => { const values = { ...defaults, ...settings }; for (const [key, id] of Object.entries(fields)) if ($(id)) $(id).value = values[key] ?? ''; };
  const readSettings = () => Object.fromEntries(Object.entries(fields).map(([key, id]) => [key, String($(id)?.value ?? '').trim()]));
  const send = (type, extra = {}) => window.postMessage({ source: BRIDGE_SOURCE, type, ...extra }, LOCAL_ORIGIN);
  const showFallback = (text) => { const embed = $('pricingWorkbenchEmbed'), frame = $('pricingWorkbenchFrame'); if (embed) embed.classList.remove('pricing-frame-ready'); if (frame) { frame.hidden = true; frame.removeAttribute('src'); } setStatus(text, 'neutral'); };
  const mountWorkbench = (value, version) => {
    if (!isWorkbenchUrl(value)) return;
    window.clearTimeout(bridgeTimer);
    const embed = $('pricingWorkbenchEmbed'), frame = $('pricingWorkbenchFrame');
    if (!embed || !frame) return;
    const url = new URL(value); url.searchParams.set('embedded', '1');
    if (frame.src !== url.href) frame.src = url.href;
    frame.hidden = false; embed.classList.add('pricing-frame-ready'); setStatus(`已连接核价扩展 v${version || '未知版本'}`, 'success');
  };
  const requestBridge = () => { if (!$('pricingWorkbenchFrame')) return; if(window.temuDesktop){showFallback('请在 Chrome 中连接核价扩展');return;} window.clearTimeout(bridgeTimer); setStatus('正在连接核价扩展…', 'neutral'); send('temuPricingWorkbenchHostReady'); bridgeTimer = window.setTimeout(() => showFallback('未检测到核价扩展'), 900); };
  const requestSettings = () => { if(window.temuDesktop){setConfigStatus('核价配置保存在 Chrome 扩展中，请点击下方按钮打开 Chrome 配置。');return;} setConfigStatus('正在读取扩展本地核价配置…'); send('temuPricingWorkbenchSettingsRead'); };
  const saveSettings = () => { setConfigStatus('正在保存到核价扩展本地存储…'); send('temuPricingWorkbenchSettingsSave', { settings: readSettings() }); };
  window.addEventListener('message', (event) => {
    const data = event.data;
    if (event.source !== window || event.origin !== LOCAL_ORIGIN || data?.source !== BRIDGE_SOURCE) return;
    if (data.type === 'temuPricingWorkbenchExtensionReady') { mountWorkbench(data.url, data.version); requestSettings(); }
    else if (data.type === 'temuPricingWorkbenchSettingsSnapshot') { fillSettings(data.settings); setConfigStatus('已读取扩展本地核价配置。'); }
    else if (data.type === 'temuPricingWorkbenchSettingsSaved') { fillSettings(data.settings); setConfigStatus('核价配置已保存到浏览器扩展本地存储。'); }
    else if (data.type === 'temuPricingWorkbenchSettingsError') setConfigStatus(data.message || '核价配置操作失败。', true);
  });
  window.pricingWorkbench = { open: requestBridge, settingsOpen: requestSettings };
  document.addEventListener('DOMContentLoaded', () => {
    fillSettings(); $('pricingWorkbenchOpen')?.addEventListener('click', requestBridge);
    $('pricingConfigSuggested')?.addEventListener('click', () => { fillSettings(); setConfigStatus('已恢复推荐值；点击“保存核价配置”后生效。'); });
    $('pricingConfigForm')?.addEventListener('submit', (event) => { event.preventDefault(); saveSettings(); }); requestSettings();
  });
})();
