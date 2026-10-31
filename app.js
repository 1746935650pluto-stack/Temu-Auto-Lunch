const products = [
  {
    id: 'MS-240928-001',
    title: 'Pet Grooming Bathing Stand for Dogs, Adjustable Non-Slip Tub Rack',
    image: 'https://images.unsplash.com/photo-1558788353-f76d92427f16?auto=format&fit=crop&w=320&q=80',
    source: '妙手货源 · 1688', cost: 12.8, weight: 1.45, dimensions: '56 × 17 × 8', category: 'Pet Supplies',
    tags: ['可调节', '宠物洗护', '3 个变体'],
    skus: [{ name: 'Black / Small', source: 'MS-PET-001-BS', cost: 12.8, stock: 1537 }, { name: 'Blue / Small', source: 'MS-PET-001-US', cost: 12.8, stock: 826 }, { name: 'Pink / Small', source: 'MS-PET-001-PS', cost: 12.8, stock: 441 }]
  },
  {
    id: 'MS-240928-002',
    title: 'Foldable Silicone Drain Basket for Kitchen Sink, 2 Pack',
    image: 'https://images.unsplash.com/photo-1584990347449-a8547413f4cf?auto=format&fit=crop&w=320&q=80',
    source: '妙手货源 · 1688', cost: 4.35, weight: .38, dimensions: '26 × 18 × 7', category: 'Home & Kitchen',
    tags: ['厨房收纳', '硅胶材质', '2 个变体'],
    skus: [{ name: 'Gray / 2 Pack', source: 'MS-KIT-102-GY', cost: 4.35, stock: 908 }, { name: 'Green / 2 Pack', source: 'MS-KIT-102-GN', cost: 4.35, stock: 678 }]
  },
  {
    id: 'MS-240928-003',
    title: 'Portable Travel Jewelry Case with Mirror, Mini Organizer Box',
    image: 'https://images.unsplash.com/photo-1617038220319-276d3cfab638?auto=format&fit=crop&w=320&q=80',
    source: '妙手货源 · 1688', cost: 6.2, weight: .26, dimensions: '11 × 10 × 5', category: 'Beauty & Health',
    tags: ['旅行收纳', '带镜子', '4 个变体'],
    skus: [{ name: 'Pink', source: 'MS-JEW-203-PK', cost: 6.2, stock: 551 }, { name: 'Cream', source: 'MS-JEW-203-CR', cost: 6.2, stock: 420 }, { name: 'Blue', source: 'MS-JEW-203-BL', cost: 6.2, stock: 302 }, { name: 'Black', source: 'MS-JEW-203-BK', cost: 6.2, stock: 764 }]
  }
];

const state = { product: null, stage: 1, generated: false, validated: false, history: JSON.parse(localStorage.getItem('temuDemoHistory') || '[]') };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
let miaoshouCollectionAccounts = [
  { id: 1, name: '妙手采集箱', configured: false, active: true },
  { id: 2, name: '妙手采集箱 2', configured: false, active: false },
];
let activeMiaoshouCollectionAccountId = window.collectionAccountContext?.id || 1;
let miaoshouCollectionExpanded = false;
let collectionAccountMutationPending = false;
let collectionAccountRevision = 0;

function showToast(text) { const toast = $('#toast'); toast.textContent = text; toast.classList.remove('hidden'); window.clearTimeout(showToast.timer); showToast.timer = window.setTimeout(() => toast.classList.add('hidden'), 2600); }
function priceFromCost(cost, margin) { return (Number(cost) / (1 - Number(margin) / 100)).toFixed(2); }
function generateCopy(product) {
  const firstTag = product.tags[0];
  return {
    title: `${product.title.replace(', Adjustable', '').replace(', 2 Pack', '')} — Durable ${firstTag} Essential`,
    bullets: `• Ready for everyday use with a stable, practical design\n• Selected for ${firstTag} needs and easy storage\n• Multiple styles available for simple product matching\n• Please check size details before ordering`,
    description: `Designed for daily use, this product combines practical function with a compact, easy-to-store form. The listing content has been drafted from the product data synchronized through MiaoShou. Please confirm product materials, dimensions and local compliance information before submitting to TEMU.`
  };
}

function renderProductOptions(filter = '') {
  const key = filter.trim().toLowerCase();
  const found = products.filter((p) => `${p.title} ${p.id} ${p.skus.map((s) => s.source).join(' ')}`.toLowerCase().includes(key));
  $('#productOptions').innerHTML = found.length ? found.map((p) => `<button class="product-option" data-product-id="${p.id}"><img src="${p.image}" alt="" /><div><h3>${p.title}</h3><p>${p.id} · 成本 $${p.cost.toFixed(2)} · ${p.skus.length} 个 SKU</p></div><b>选择</b></button>`).join('') : '<p class="empty-search">没有匹配的演示商品</p>';
  $$('.product-option').forEach((button) => button.addEventListener('click', () => selectProduct(products.find((p) => p.id === button.dataset.productId))));
}

function renderSelectedProduct() {
  const p = state.product;
  $('#sourceEmpty').classList.add('hidden');
  $('#selectedProduct').classList.remove('hidden');
  $('#selectedProduct').innerHTML = `<img src="${p.image}" alt="${p.title}" /><div><h3>${p.title}</h3><div class="product-meta">${p.id}<span>·</span>${p.source}<span>·</span>成本 $${p.cost.toFixed(2)}</div><div class="product-tags">${p.tags.map((tag) => `<b>${tag}</b>`).join('')}</div></div>`;
  $('#crumbProduct').textContent = p.id;
  $('#editorLocked').classList.add('hidden'); $('#listingForm').classList.remove('hidden');
  $('#generateContent').disabled = false; $('#validateListing').disabled = false;
  $('#cost').value = p.cost; $('#weight').value = p.weight; $('#dimensions').value = p.dimensions; $('#category').value = p.category;
  $('#price').value = priceFromCost(p.cost, $('#margin').value);
  $('#skuList').innerHTML = `<div class="sku-heading"><span>变体</span><span>货源 SKU</span><span>成本</span><span>库存</span></div>${p.skus.map((sku) => `<div class="sku-row"><span>${sku.name}</span><em>${sku.source}</em><span>$${sku.cost.toFixed(2)}</span><span>${sku.stock}</span></div>`).join('')}`;
}

function setStage(stage) {
  state.stage = stage;
  const names = ['商品资料已同步', '内容待生成', '待校验半托管配置', '可通过妙手提交半托管发布'];
  $('#pipelineState').textContent = names[stage - 1];
  $$('.pipeline-step').forEach((el) => { const n = Number(el.dataset.step); el.classList.toggle('active', n === stage); el.classList.toggle('done', n < stage); });
  $$('.pipeline-line').forEach((el, index) => el.classList.toggle('done', index + 1 < stage));
  renderTasks();
}

function renderTasks() {
  const values = [
    { title: '读取商品资料', note: state.product ? `${state.product.skus.length} 个 SKU 已同步` : '等待商品选择', ready: state.product },
    { title: 'AI 生成商品内容', note: state.generated ? '标题、卖点和描述已生成' : '等待资料完整', ready: state.generated },
    { title: '校验半托管配置', note: state.validated ? '基础字段校验通过' : '等待内容生成', ready: state.validated },
    { title: '提交半托管发布', note: state.stage === 4 ? '等待人工确认提交' : '由妙手执行', ready: false }
  ];
  $('#taskList').innerHTML = values.map((item, index) => `<div class="task-row"><span class="task-index">${index + 1}</span><div><b>${item.title}</b><small>${item.note}</small></div><span class="task-result ${item.ready ? 'ok' : 'pending'}">${item.ready ? '✓ 已完成' : '—'}</span></div>`).join('');
  const status = $('#taskStatus');
  if (!state.product) { status.textContent = '未开始'; status.className = 'status-pill neutral'; }
  else if (state.stage === 4) { status.textContent = '可提交'; status.className = 'status-pill success'; }
  else { status.textContent = '进行中'; status.className = 'status-pill running'; }
}

function selectProduct(product) { state.product = product; state.generated = false; state.validated = false; renderSelectedProduct(); setStage(2); closeModal('productModal'); showToast('商品资料已从妙手演示数据同步'); }
function generateContent() { if (!state.product) return; const copy = generateCopy(state.product); $('#listingTitle').value = copy.title; $('#bulletPoints').value = copy.bullets; $('#description').value = copy.description; updateTitleCount(); state.generated = true; state.validated = false; setStage(3); showToast('AI 已生成英文标题、卖点和商品描述'); }
function updateTitleCount() { $('#titleCount').textContent = `${$('#listingTitle').value.length} / 180`; }
function validateListing() {
  if (!state.product) return;
  const required = [['listingTitle', '英文商品标题'], ['bulletPoints', '商品亮点'], ['description', '商品描述'], ['category', 'TEMU 类目'], ['price', '建议售价'], ['dimensions', '包装尺寸']];
  const missing = required.find(([id]) => !String($(`#${id}`).value).trim());
  if (missing) { showToast(`请补充：${missing[1]}`); $(`#${missing[0]}`).focus(); return; }
  const cost = Number($('#cost').value), price = Number($('#price').value);
  if (price <= cost) { showToast('建议售价需高于采购成本'); $('#price').focus(); return; }
  state.validated = true; setStage(4); showToast('基础校验已通过，可创建 TEMU 草稿');
  $('#validateListing').textContent = '创建 TEMU 草稿'; $('#validateListing').onclick = createDraft;
}
function createDraft() {
  const draftId = `TD-${new Date().toISOString().slice(0,10).replaceAll('-','')}-${Math.floor(1000 + Math.random() * 9000)}`;
  const item = { draftId, product: $('#listingTitle').value, date: new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date()), status: '草稿已创建（演示）' };
  state.history.unshift(item); localStorage.setItem('temuDemoHistory', JSON.stringify(state.history)); renderHistory();
  $('#resultTitle').textContent = '半托管发布任务已创建'; $('#resultBody').textContent = `任务编号：${draftId}。当前为演示模式，尚未向妙手发送任何真实请求。`;
  $('#resultModal').classList.remove('hidden'); showToast('草稿已保存到任务中心');
}
function renderHistory() {
  $('#demoTaskHistorySection').classList.toggle('hidden', !state.history.length);
  $('#taskHistory').innerHTML = state.history.map((item) => `<div class="history-item"><div><b>${item.product}</b><small>${item.draftId} · ${item.date}</small></div><span class="status-pill success">${item.status}</span></div>`).join('');
}
function closeModal(id) {
  if (id === 'personnelBindingModal') { closePersonnelBindings(); return; }
  if (id === 'personnelAliasModal') { closePersonnelAliases(); return; }
  $(`#${id}`).classList.add('hidden');
}
function resetDemo() { state.product = null; state.stage = 1; state.generated = false; state.validated = false; $('#sourceEmpty').classList.remove('hidden'); $('#selectedProduct').classList.add('hidden'); $('#editorLocked').classList.remove('hidden'); $('#listingForm').classList.add('hidden'); $('#generateContent').disabled = true; $('#validateListing').disabled = true; $('#validateListing').textContent = '校验并继续'; $('#validateListing').onclick = validateListing; $('#crumbProduct').textContent = '新建 TEMU 半托管上架任务'; setStage(1); showToast('当前任务已重置'); }

const liveState = { mode: 'demo', page: 1, pageSize: readLivePageSize(), products: [], selected: null, poolSelected: new Map(), poolTransferring: false, readiness: null, checkVersion: 0, editing: false, editorStage: 2, pageSummary: null, pageSummaryTimer: null, pageSummaryRequest: 0,
  collectorId: '', collectorAccountId: activeMiaoshouCollectionAccountId, searchText: '', people: new Map(), productRequest: 0, productsLoading: false,
  personnelRequest: 0, personnelTimer: null, productTimer: null, filterProgress: null,
  aliasRevision: 0, aliasSaving: false, aliasDialog: null };
function renderLiveFlow(publishing = false) {
  let stage = 1;
  let stateText = '等待选择商品';
  if (liveState.selected && !liveState.readiness) {
    stage = 2; stateText = '请选择认领店铺';
  }
  if (liveState.readiness && !liveState.readiness.ready) {
    stage = 6; stateText = '请补齐规则资料';
  }
  if (liveState.readiness?.ready) {
    stage = 7; stateText = publishing ? '正在提交发布' : '可提交发布';
  }
  if (liveState.editing) {
    stage = liveState.editorStage || 2;
    stateText = stage === 2 ? '正在认领目标店铺' : stage === 4 ? '正在编辑标题与商品图片' : stage === 5 ? '正在编辑 SKU 变体' : '正在编辑上架资料';
  }
  $('#liveFlowState').textContent = stateText;
  $$('.live-flow-step').forEach((element) => {
    const number = Number(element.dataset.flowStep);
    element.classList.toggle('done', number < stage);
    element.classList.toggle('active', number === stage);
  });
  $$('.live-flow-line').forEach((element, index) => element.classList.toggle('done', index + 1 < stage));
}
function scrollToLiveStep(targetId) {
  document.getElementById(targetId)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function navigateLiveFlow(stage) {
  if (stage === 1) { scrollToLiveStep('liveSourceStep'); return; }
  if (!liveState.selected) {
    showToast('请先在第 1 步选择一个商品');
    scrollToLiveStep('liveSourceStep');
    return;
  }
  if (stage >= 2 && stage <= 5) {
    window.dispatchEvent(new CustomEvent('open-live-editor-request', { detail: { stage } }));
    return;
  }
  scrollToLiveStep(stage === 6 ? 'liveValidationStep' : 'livePublishStep');
}
function activatePage(pageId) {
  if (pageId !== 'collectionAccount') clearCollectionCredentialInputs();
  if (pageId === 'settings') { loadCollectionAccountManager(); loadDeepSeekSettings(); loadDeepSeekProfiles(); window.pricingWorkbench?.settingsOpen(); }
  else clearDeepSeekInput();
  const displayPage = pageId;
  document.body.classList.toggle('workbench-active', displayPage === 'workbench');
  document.body.classList.toggle('zhihou-active', pageId === 'zhihou');
  document.body.classList.toggle('pricing-active', pageId === 'pricing');
  $$('.nav-item').forEach((item) => {
    if (item.id === 'miaoshouCollectionToggle') {
      item.classList.toggle('active', pageId === 'workbench' || pageId === 'collectionAccount');
      return;
    }
    if (item.id === 'orderAccountsToggle') {
      item.classList.toggle('active', pageId === 'orders');
      return;
    }
    if (item.dataset.orderAccountId) {
      item.classList.toggle('active', pageId === 'orders' && Number(item.dataset.orderAccountId) === activeOrderAccountId);
      return;
    }
    const accountId = Number(item.dataset.accountId || 0);
    const isAccountMatch = !accountId || (pageId === 'workbench' && accountId === activeMiaoshouCollectionAccountId)
      || (pageId === 'collectionAccount' && accountId === activeMiaoshouCollectionAccountId);
    item.classList.toggle('active', item.dataset.page === pageId && isAccountMatch);
  });
  $$('.page').forEach((page) => page.classList.toggle('active', page.id === displayPage));
  if (pageId === 'publishRecords' && liveState.mode !== 'demo') loadLiveRecords();
  if (pageId === 'orders') loadOrders();
  if (pageId === 'zhihou') window.zhiHouModule?.refresh();
  if (pageId === 'pricing') window.pricingWorkbench?.open();
  if (pageId === 'automaticTasks') {
    if (window.automaticTasks) window.automaticTasks.open();
    else if ($('#autoTaskMessage')) $('#autoTaskMessage').textContent = '自动任务模块尚未加载。若刚升级，请等后台同步结束后安全重启，再刷新页面；不会中断当前任务。';
  }
  if (window.sharedAutomation && ['automation', 'tasks'].includes(pageId)) { window.sharedAutomation.open(pageId); return; }
  if (pageId === 'automation') { loadAutomationPool(); loadAutomationShops(); loadAutomationSites(); loadAutomationJobs(); loadAutomationPersonnel(); }
  if (pageId === 'tasks') loadAutomationJobs();
}
function accountById(accountId) {
  return miaoshouCollectionAccounts.find((account) => Number(account.id) === Number(accountId));
}
function updateCurrentCollectionAccountName() {
  const account = accountById(activeMiaoshouCollectionAccountId);
  const name = account?.name && account.name !== '妙手采集箱'
    ? account.name : `妙手采集箱 ${activeMiaoshouCollectionAccountId}`;
  if ($('#currentCollectionAccount')) $('#currentCollectionAccount').textContent = `当前账号：${name}`;
}
function openMiaoshouCollectionAccount(accountId) {
  clearCollectionCredentialInputs();
  const account = accountById(accountId) || { id: Number(accountId), name: `妙手采集箱 ${accountId}`, configured: false };
  if (Number(account.id) > 1 && account.configured && !window.collectionAccountContext) {
    showToast('请先重启更新后的后台并刷新页面，再进入此采集箱'); return;
  }
  const accountChanged = activeMiaoshouCollectionAccountId !== Number(account.id);
  if (accountChanged && window.collectionAccountContext) {
    window.collectionAccountContext.switchTo(Number(account.id));
    return;
  }
  activeMiaoshouCollectionAccountId = Number(account.id);
  updateCurrentCollectionAccountName();
  if (accountChanged) resetLiveCollectorAccount(activeMiaoshouCollectionAccountId);
  setMiaoshouCollectionExpanded(true);
  if (activeMiaoshouCollectionAccountId === 1 || account.configured) {
    activatePage('workbench');
    updateCurrentCollectionAccountName();
    if (accountChanged && liveState.mode !== 'demo') loadLiveProducts();
    return;
  }
  const name = account.name || `妙手采集箱 ${account.id}`;
  $('#collectionAccountTitle').textContent = name;
  $('#collectionAccountCardTitle').textContent = name;
  $('#collectionAccountLogo').textContent = String(account.id);
  $('#deleteEmptyCollection').hidden = account.deletable !== true;
  $('#deleteEmptyCollection').disabled = collectionAccountMutationPending;
  $('#collectionAccountDescription').textContent = `为“${name}”配置独立 API 后，即可同步商品、店铺和订单，使用与采集箱 1 相同的工作流。各账号数据独立。`;
  $('#collectionAccountCardDescription').textContent = account.configured
    ? '独立账号凭证已登记，可同步信息。'
    : '尚未接入独立妙手开放平台 API。';
  const status = $('#collectionAccountStatus');
  status.textContent = account.configured ? '已配置' : '待接入';
  status.className = `status-pill ${account.configured ? 'running' : 'neutral'}`;
  $('#collectionCredentialForm').hidden = account.deletable !== true;
  $('#collectionCredentialStatus').textContent = '';
  $('#collectionAccountGuide').textContent = account.deletable === true
    ? `在上方填写此采集箱的独立 API，后台会保存到本机 .env 的 MIAOSHOU_ACCOUNT_${account.id}_APP_KEY 和 MIAOSHOU_ACCOUNT_${account.id}_APP_SECRET。保存后点击同步信息；不会保存或发布商品。`
    : '此账号已有部分凭据或历史数据。为保护账号身份，请先在本机 .env 核对配置，再重启后台并同步信息。';
  updateCollectionAccountControls();
  activatePage('collectionAccount');
}
function setMiaoshouCollectionExpanded(expanded) {
  miaoshouCollectionExpanded = Boolean(expanded);
  const children = $('#miaoshouCollectionChildren');
  if (children) children.hidden = !miaoshouCollectionExpanded;
  const parent = $('#miaoshouCollectionToggle');
  if (parent) parent.setAttribute('aria-expanded', String(miaoshouCollectionExpanded));
}
function bindCollectionAccountNavigation(button) {
  button.addEventListener('click', () => openMiaoshouCollectionAccount(Number(button.dataset.accountId)));
}
function bindCollectionFolderNavigation(button) {
  button.addEventListener('click', () => setMiaoshouCollectionExpanded(!miaoshouCollectionExpanded));
}
function updateCollectionAccountControls() {
  const add = $('#addEmptyCollection');
  if (add) add.disabled = collectionAccountMutationPending;
  const remove = $('#deleteEmptyCollection');
  if (remove) remove.disabled = collectionAccountMutationPending;
  const canConfigure = accountById(activeMiaoshouCollectionAccountId)?.deletable === true;
  ['#saveCollectionCredentials', '#collectionAppKey', '#collectionAppSecret'].forEach(selector => {
    const field = $(selector);
    if (field) field.disabled = collectionAccountMutationPending || !canConfigure;
  });
  const sync = $('#configureCollectionSync');
  if (sync) sync.disabled = collectionAccountMutationPending;
  $$('#collectionAccountManager button').forEach(button => { button.disabled = collectionAccountMutationPending; });
  const refresh = $('#refreshCollectionAccountManager');
  if (refresh) refresh.disabled = collectionAccountMutationPending;
}
function clearCollectionCredentialInputs() {
  ['#collectionAppKey', '#collectionAppSecret'].forEach(selector => {
    const field = $(selector);
    if (field) field.value = '';
  });
}
async function saveCollectionCredentials(event) {
  event?.preventDefault();
  if (collectionAccountMutationPending) return;
  const accountId = activeMiaoshouCollectionAccountId;
  if (accountId <= 1 || accountById(accountId)?.deletable !== true) return;
  const status = $('#collectionCredentialStatus');
  let appKey = $('#collectionAppKey').value.trim();
  let appSecret = $('#collectionAppSecret').value.trim();
  if (!appKey || !appSecret) { status.textContent = '请同时填写 App Key 和 App Secret'; return; }
  collectionAccountMutationPending = true;
  collectionAccountRevision += 1;
  updateCollectionAccountControls();
  status.textContent = `正在保存采集箱 ${accountId} 的 API 配置…`;
  try {
    const response = await fetch(`/api/miaoshou-accounts/${accountId}/credentials`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, cache: 'no-store',
      body: JSON.stringify({appKey, appSecret, confirmation: 'SAVE_COLLECTION_API_CREDENTIALS'}),
    });
    appKey = ''; appSecret = '';
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || '保存失败，请检查后台是否已更新');
    if (body.data?.id !== accountId || !body.data.configured || !Array.isArray(body.data.accounts)
        || !body.data.accounts.some(account => Number(account.id) === accountId && account.configured)
        || !body.data.accounts.some(account => Number(account.id) === 1))
      throw new Error('后台未返回配置确认，请刷新检查；不要重复新增采集箱');
    miaoshouCollectionAccounts = body.data.accounts;
    renderMiaoshouCollectionAccounts(miaoshouCollectionAccounts);
    if (activeMiaoshouCollectionAccountId === accountId) {
      $('#collectionCredentialForm').hidden = true;
      $('#collectionAccountStatus').textContent = '已配置';
      $('#collectionAccountStatus').className = 'status-pill running';
      $('#collectionAccountCardDescription').textContent = '独立 API 已保存并生效，尚未验证连接。';
      $('#collectionAccountGuide').textContent = '配置已保存到本机 .env。点击下方同步信息验证连接并读取商品、店铺和订单；不会保存或发布商品。';
      status.textContent = '保存成功，无需重启。现在可以同步信息。';
    }
    showToast(`采集箱 ${accountId} 的 API 已保存并生效`);
  } catch (error) {
    if (activeMiaoshouCollectionAccountId === accountId) status.textContent = error.message;
  } finally {
    appKey = ''; appSecret = '';
    clearCollectionCredentialInputs();
    collectionAccountMutationPending = false;
    collectionAccountRevision += 1;
    updateCollectionAccountControls();
  }
}
async function mutateEmptyCollection(accountId = null) {
  if (collectionAccountMutationPending) return;
  const deleting = accountId !== null;
  if (deleting) {
    const account = accountById(accountId);
    if (!account?.deletable || Number(accountId) === 1) { showToast('只能删除未配置 API 的空采集箱'); return; }
    if (!window.confirm(`删除空采集箱“${account.name}”？只移除本机账号入口，不会删除妙手商品。`)) return;
  }
  collectionAccountMutationPending = true;
  collectionAccountRevision += 1;
  updateCollectionAccountControls();
  try {
    const response = await fetch(deleting ? `/api/miaoshou-accounts/${encodeURIComponent(accountId)}` : '/api/miaoshou-accounts', {
      method: deleting ? 'DELETE' : 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({confirmation: deleting ? 'DELETE_EMPTY_COLLECTION' : 'ADD_EMPTY_COLLECTION'}),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(body.error || '采集箱目录保存失败');
    if (!Array.isArray(body.data?.accounts) || !body.data.accounts.some(account => Number(account.id) === 1)) throw Error('后台返回的采集箱目录不完整，请刷新重试');
    miaoshouCollectionAccounts = body.data.accounts;
    renderMiaoshouCollectionAccounts(miaoshouCollectionAccounts);
    openMiaoshouCollectionAccount(deleting ? 1 : body.data.id);
    showToast(deleting ? '空采集箱入口已删除，未删除任何妙手商品；可重新新增' : '已新增空采集箱，后续可配置独立 API');
  } catch (error) { showToast(`${deleting ? '删除' : '新增'}失败：${error.message}`); }
  finally { collectionAccountMutationPending = false; collectionAccountRevision += 1; updateCollectionAccountControls(); }
}
function renderMiaoshouCollectionAccounts(accounts) {
  const container = $('#miaoshouCollectionNav');
  if (!container || !Array.isArray(accounts) || !accounts.length) return;
  container.replaceChildren();
  const parent = document.createElement('button');
  parent.id = 'miaoshouCollectionToggle'; parent.type = 'button'; parent.className = 'nav-item';
  parent.setAttribute('aria-controls', 'miaoshouCollectionChildren');
  const folderIcon = document.createElement('span'); folderIcon.textContent = '⌘';
  const arrow = document.createElement('span');
  arrow.className = 'collection-nav-chevron'; arrow.textContent = '›';
  arrow.setAttribute('aria-hidden', 'true');
  parent.append(folderIcon, document.createTextNode('妙手采集箱'), arrow);
  bindCollectionFolderNavigation(parent);
  container.append(parent);
  const children = document.createElement('div');
  children.id = 'miaoshouCollectionChildren';
  children.className = 'miaoshou-collection-children';
  children.setAttribute('role', 'group');
  children.setAttribute('aria-label', '妙手采集箱账号');
  let displayNumber = 0;
  accounts.forEach((account) => {
    const id = Number(account.id);
    if (!Number.isSafeInteger(id) || id <= 0) return;
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'nav-item';
    button.dataset.page = id === 1 || account.configured ? 'workbench' : 'collectionAccount';
    button.dataset.accountId = String(id);
    const icon = document.createElement('span'); icon.textContent = String(++displayNumber);
    const name = id === 1 && account.name === '妙手采集箱' ? '妙手采集箱 1' : account.name || `妙手采集箱 ${id}`;
    button.append(icon, document.createTextNode(name));
    if (id > 1 && !account.configured) button.title = '独立妙手 API 尚未接入';
    bindCollectionAccountNavigation(button);
    children.append(button);
  });
  const add = document.createElement('button');
  add.id = 'addEmptyCollection'; add.type = 'button'; add.className = 'collection-nav-add';
  add.textContent = '＋ 新增空采集箱'; add.disabled = collectionAccountMutationPending;
  add.addEventListener('click', () => mutateEmptyCollection());
  children.append(add);
  container.append(children);
  setMiaoshouCollectionExpanded(miaoshouCollectionExpanded);
  const selectedPage = activeMiaoshouCollectionAccountId === 1 || accountById(activeMiaoshouCollectionAccountId)?.configured ? 'workbench' : 'collectionAccount';
  const currentPage = $$('.page').find((page) => page.classList.contains('active'))?.id;
  if (currentPage === 'collectionAccount') {
    $('#deleteEmptyCollection').hidden = accountById(activeMiaoshouCollectionAccountId)?.deletable !== true;
  }
  if (currentPage === selectedPage) activatePage(selectedPage);
  renderOrderAccounts(accounts);
  updateCurrentCollectionAccountName();
}
async function loadMiaoshouCollectionAccounts() {
  const revision = collectionAccountRevision;
  try {
    const response = await apiGet('/api/miaoshou-accounts');
    if (revision !== collectionAccountRevision || collectionAccountMutationPending) return;
    const accounts = response.accounts;
    if (!Array.isArray(accounts)) return;
    const valid = accounts.filter((account) => Number.isSafeInteger(Number(account?.id)) && Number(account.id) > 0);
    if (!valid.some((account) => Number(account.id) === 1)) return;
    miaoshouCollectionAccounts = valid.sort((left, right) => Number(left.id) - Number(right.id));
    renderMiaoshouCollectionAccounts(miaoshouCollectionAccounts);
    if (!accountById(activeMiaoshouCollectionAccountId) && window.collectionAccountContext) {
      showToast('当前采集箱已删除，返回采集箱 1');
      window.collectionAccountContext.switchTo(1);
      return;
    }
    if (window.collectionAccountContext && !loadMiaoshouCollectionAccounts.initialized) {
      loadMiaoshouCollectionAccounts.initialized = true;
      openMiaoshouCollectionAccount(activeMiaoshouCollectionAccountId);
    }
  } catch (_) {
    // Keep the two built-in navigation slots visible when the backend is not
    // running yet or is still an older version during a rolling restart.
  }
}
function renderCollectionAccountManager(data) {
  const list = $('#collectionAccountManager');
  if (!list) return;
  list.replaceChildren();
  data.accounts.forEach(account => {
    const row = document.createElement('div'); row.className = 'collection-manager-row';
    const details = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = Number(account.id) === 1 && account.name === '妙手采集箱' ? '妙手采集箱 1' : account.name;
    const status = document.createElement('p');
    status.textContent = account.configured ? 'API 已配置 · 各账号数据独立' : 'API 未配置完整';
    details.append(title, status); row.append(details);
    const rename = document.createElement('button'); rename.type = 'button';
    rename.className = 'button subtle'; rename.textContent = '重命名采集箱';
    rename.setAttribute('aria-label', `重命名${account.name}`);
    rename.disabled = collectionAccountMutationPending;
    rename.addEventListener('click', () => renameManagedCollection(Number(account.id)));
    row.append(rename);
    if (Number(account.id) === 1) {
      const protectedLabel = document.createElement('span'); protectedLabel.className = 'status-pill neutral';
      protectedLabel.textContent = '默认账号 · 保留'; row.append(protectedLabel);
    }
    if (Number(account.id) !== 1) {
      const remove = document.createElement('button'); remove.type = 'button';
      remove.className = 'button subtle collection-manager-delete'; remove.textContent = '删除采集箱';
      remove.setAttribute('aria-label', `删除${account.name}的本机配置`);
      remove.disabled = collectionAccountMutationPending;
      remove.addEventListener('click', () => removeManagedCollection(Number(account.id))); row.append(remove);
    }
    list.append(row);
  });
  const removed = $('#removedCollectionAccountList'); removed.replaceChildren();
  (data.removedAccounts || []).forEach(account => {
    const row = document.createElement('div'); row.className = 'collection-manager-row';
    const label = document.createElement('p'); label.textContent = `${account.name} · 本地历史尚未清理（编号 ${account.id} 不复用）`;
    const clear = document.createElement('button'); clear.type = 'button';
    clear.className = 'button subtle collection-manager-delete'; clear.textContent = '清空本地历史';
    clear.disabled = collectionAccountMutationPending;
    clear.addEventListener('click', () => removeManagedCollection(Number(account.id), account));
    row.append(label, clear);
    removed.append(row);
  });
  $('#removedCollectionAccounts').hidden = !(data.removedAccounts || []).length;
}
async function loadCollectionAccountManager() {
  if (collectionAccountMutationPending) return;
  const revision = collectionAccountRevision;
  const request = loadCollectionAccountManager.request = (loadCollectionAccountManager.request || 0) + 1;
  const status = $('#collectionAccountManagerStatus');
  status.textContent = '正在读取采集箱…';
  try {
    const data = await apiGet('/api/miaoshou-accounts/manage');
    if (revision !== collectionAccountRevision || request !== loadCollectionAccountManager.request || collectionAccountMutationPending) return;
    if (!Array.isArray(data.accounts) || !data.accounts.some(account => Number(account.id) === 1))
      throw new Error('账号列表不完整，请刷新后重试');
    miaoshouCollectionAccounts = data.accounts;
    renderMiaoshouCollectionAccounts(data.accounts);
    renderCollectionAccountManager(data);
    status.textContent = '有运行、排队或同步任务时不能删除。';
  } catch (error) {
    if (revision === collectionAccountRevision && request === loadCollectionAccountManager.request)
      status.textContent = `读取失败：${error.message}`;
  }
}
async function renameManagedCollection(accountId) {
  if (collectionAccountMutationPending) return;
  const account = accountById(accountId);
  if (!account) return;
  const nextName = window.prompt(`重命名“${account.name}”`, account.name);
  if (nextName === null || nextName === account.name) return;
  collectionAccountMutationPending = true;
  collectionAccountRevision += 1;
  updateCollectionAccountControls();
  const status = $('#collectionAccountManagerStatus'); status.textContent = `正在保存${account.name}的新名称…`;
  try {
    const response = await fetch(`/api/miaoshou-accounts/${accountId}/name`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: nextName, confirmation: 'RENAME_COLLECTION_ACCOUNT'}),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || '重命名失败，请刷新检查后重试');
    if (body.data?.id !== accountId || typeof body.data.name !== 'string' || !Array.isArray(body.data.accounts))
      throw new Error('后台未返回重命名确认，请刷新检查');
    miaoshouCollectionAccounts = body.data.accounts;
    renderMiaoshouCollectionAccounts(body.data.accounts);
    renderCollectionAccountManager({accounts: body.data.accounts, removedAccounts: []});
    window.sharedAutomation?.accountsChanged?.({accounts: body.data.accounts, removedAccounts: []});
    status.textContent = `已将“${account.name}”重命名为“${body.data.name}”。`;
    showToast(status.textContent);
  } catch (error) { status.textContent = error.message; }
  finally {
    collectionAccountMutationPending = false;
    collectionAccountRevision += 1;
    updateCollectionAccountControls();
  }
}
async function removeManagedCollection(accountId, removedAccount = null) {
  if (collectionAccountMutationPending) return;
  const account = accountById(accountId) || removedAccount;
  if (!account || accountId === 1) return;
  const message = `删除“${account.name}”及其本地历史？\n\n先备份，再清除该账号的 App Key / App Secret、侧栏入口、本地订单、自动化池和任务（含待发布）。\n不会删除妙手商品、店铺或其他采集箱的订单任务。账号编号不复用；其他账号副本的防重复映射仍保留。`
    + (accountId === activeMiaoshouCollectionAccountId ? '\n\n当前采集箱会关闭，请先保存未完成的编辑。' : '');
  if (!window.confirm(message)) return;
  collectionAccountMutationPending = true;
  collectionAccountRevision += 1;
  updateCollectionAccountControls();
  const status = $('#collectionAccountManagerStatus'); status.textContent = `正在删除${account.name}的本机配置…`;
  let switchAccount = false;
  try {
    const response = await fetch(`/api/miaoshou-accounts/${accountId}/remove`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({confirmation: 'REMOVE_COLLECTION_ACCOUNT_AND_HISTORY'}),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || '删除失败，请刷新检查后重试');
    if (body.data?.id !== accountId || !Array.isArray(body.data.accounts)
        || !body.data.accounts.some(item => Number(item.id) === 1)
        || body.data.accounts.some(item => Number(item.id) === accountId))
      throw new Error('后台未返回删除确认，请刷新检查');
    miaoshouCollectionAccounts = body.data.accounts;
    renderMiaoshouCollectionAccounts(body.data.accounts);
    renderCollectionAccountManager(body.data);
    window.sharedAutomation?.accountsChanged?.(body.data);
    status.textContent = `${account.name}的本机 API、入口、本地订单和任务历史已清除，清理前的备份保存在本机 backups 目录；未删除任何妙手商品。`;
    showToast(status.textContent);
    switchAccount = accountId === activeMiaoshouCollectionAccountId;
  } catch (error) { status.textContent = error.message; }
  finally {
    collectionAccountMutationPending = false;
    collectionAccountRevision += 1;
    updateCollectionAccountControls();
  }
  if (switchAccount) openMiaoshouCollectionAccount(1);
}
function clearDeepSeekInput() {
  if ($('#deepSeekApiKey')) $('#deepSeekApiKey').value = '';
  if ($('#deepSeekProfileKey')) $('#deepSeekProfileKey').value = '';
}
function updateMiaoshouShopRefreshControl() {
  const button = $('#refreshMiaoshouShops');
  if (button) button.disabled = Boolean(refreshMiaoshouShops.busy);
}
async function refreshMiaoshouShops() {
  if (refreshMiaoshouShops.busy) return;
  const button = $('#refreshMiaoshouShops'), status = $('#miaoshouShopRefreshStatus');
  if (!button || !status) return;
  refreshMiaoshouShops.busy = true;
  updateMiaoshouShopRefreshControl();
  status.textContent = '正在从妙手同步所有已配置采集箱的店铺、发货仓和运费模板…';
  try {
    const response = await fetch('/api/settings/miaoshou-shops', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({confirmation: 'REFRESH_MIAOSHOU_SHOPS'})});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || '店铺拉取失败');
    const data = body.data;
    if (!Array.isArray(data?.accounts) || !Array.isArray(data?.errors) || !Array.isArray(data?.warnings || [])) throw new Error('后台未确认店铺拉取结果');
    const succeeded = data.accounts.map(row => {
      const name = row.name || `采集箱 ${row.accountId}`;
      if (!Number.isSafeInteger(row.syncedShopCount)) return `${name} ${row.shopCount} 家`;
      return `${name} ${row.shopCount} 家店铺，物流 ${row.syncedShopCount}/${row.shopCount} 家（运费模板 ${row.freightTemplateCount || 0}、仓库 ${row.warehouseCount || 0}）`;
    });
    const failed = data.errors.map(row => `${row.name || `采集箱 ${row.accountId}`}：${row.message}`);
    const warnings = (data.warnings || []).map(row => `${row.name || `采集箱 ${row.accountId}`}：${row.message}`);
    status.textContent = `已更新 ${data.accounts.length} 个采集箱${succeeded.length ? `（${succeeded.join('、')}）` : ''}${failed.length ? `；失败 ${failed.length} 个：${failed.join('；')}` : ''}${warnings.length ? `；物流缓存已保留，${warnings.length} 项未更新：${warnings.slice(0, 3).join('；')}${warnings.length > 3 ? '…' : ''}` : ''}。`;
    await loadMiaoshouCollectionAccounts();
    showToast(failed.length || warnings.length ? '部分妙手资料未同步成功，旧缓存已保留，请查看接口设置。' : '妙手店铺、仓库和运费模板已更新。');
  } catch (error) {
    status.textContent = `拉取未完成：${error.message}。现有店铺缓存已保留。`;
  } finally {
    refreshMiaoshouShops.busy = false;
    updateMiaoshouShopRefreshControl();
  }
}
function updateDeepSeekControls() {
  const busy = Boolean(saveDeepSeekSettings.busy);
  ['#deepSeekApiKey', '#saveDeepSeekSettings', '#refreshDeepSeekSettings'].forEach(id => { if ($(id)) $(id).disabled = busy; });
  if ($('#removeDeepSeekSettings')) $('#removeDeepSeekSettings').disabled = busy || !loadDeepSeekSettings.configured;
}
function renderDeepSeekSettings(data) {
  if (typeof data.configured !== 'boolean') throw new Error('后台未返回 DeepSeek 配置状态');
  loadDeepSeekSettings.configured = data.configured;
  $('#deepSeekConfigured').textContent = data.configured ? '已配置' : '未配置';
  $('#deepSeekConfigured').className = `status-pill ${data.configured ? 'success' : 'neutral'}`;
  $('#deepSeekModel').textContent = `当前模型：${data.model || '沿用后台配置'}。此处仅管理密钥，不修改模型或接口地址。`;
  const usage = data.usageToday;
  if (usage && Number.isFinite(usage.requests)) {
    $('#deepSeekModel').textContent += ` 今日记录 ${usage.requests} 次请求、${usage.totalTokens.toLocaleString()} tokens（输入 ${usage.promptTokens.toLocaleString()} / 输出 ${usage.completionTokens.toLocaleString()}，缓存命中 ${usage.cacheHitTokens.toLocaleString()}）；标题首轮 ${usage.byPurpose?.title_initial || 0} 次、修正 ${usage.byPurpose?.title_repair || 0} 次、属性 ${usage.byPurpose?.attribute || 0} 次。仅统计新版启用后，点击刷新更新。`;
    if (usage.knownUsageRequests < usage.requests || usage.recordErrors) $('#deepSeekModel').textContent += ' 部分请求用量未返回或记录不完整，实际消耗以DeepSeek账单为准。';
  }
  updateDeepSeekControls();
}
async function loadDeepSeekSettings() {
  if (saveDeepSeekSettings.busy) return;
  const revision = loadDeepSeekSettings.revision || 0;
  const request = loadDeepSeekSettings.request = (loadDeepSeekSettings.request || 0) + 1;
  try {
    const data = await apiGet('/api/settings/deepseek');
    if (revision !== (loadDeepSeekSettings.revision || 0) || request !== loadDeepSeekSettings.request || saveDeepSeekSettings.busy) return;
    renderDeepSeekSettings(data);
    $('#deepSeekSettingsStatus').textContent = data.configured ? '密钥已保存在后台，不回显；此状态不代表已验证远端连接。' : '请填写 API Key 后保存。';
  } catch (error) {
    if (revision === (loadDeepSeekSettings.revision || 0) && request === loadDeepSeekSettings.request)
      $('#deepSeekSettingsStatus').textContent = `状态读取失败：${error.message}`;
  }
}
async function saveDeepSeekSettings(event, remove = false) {
  event?.preventDefault();
  if (saveDeepSeekSettings.busy || collectionAccountMutationPending) return;
  const status = $('#deepSeekSettingsStatus');
  let apiKey = $('#deepSeekApiKey').value.trim();
  if (!remove && !apiKey) { status.textContent = '请填写 DeepSeek API Key'; return; }
  const message = remove
    ? '删除本机 DeepSeek API 配置？标题润色和 AI 属性识别将不可用。不会删除妙手 API、商品或历史任务。'
    : '保存 DeepSeek API Key 到本机 .env？如已有密钥将替换，所有采集箱共用。不会立即调用 AI，也不会发布商品。';
  if (!window.confirm(message)) return;
  saveDeepSeekSettings.busy = true;
  loadDeepSeekSettings.revision = (loadDeepSeekSettings.revision || 0) + 1;
  updateDeepSeekControls();
  status.textContent = remove ? '正在删除 DeepSeek 配置…' : '正在保存 DeepSeek 配置…';
  try {
    const response = await fetch('/api/settings/deepseek', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(remove ? {confirmation: 'REMOVE_DEEPSEEK_API'} : {apiKey, confirmation: 'SAVE_DEEPSEEK_API'})});
    apiKey = '';
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || '请求失败');
    if (body.data?.configured !== !remove) throw new Error('后台未确认配置结果');
    renderDeepSeekSettings(body.data);
    status.textContent = remove ? 'DeepSeek API 配置已删除。未修改妙手账号或商品。' : 'DeepSeek API 已保存并生效，无需重启；未发起 AI 请求。';
    showToast(status.textContent);
  } catch (error) { status.textContent = `操作未确认：${error.message}。请刷新状态核对后重试。`; }
  finally {
    apiKey = ''; clearDeepSeekInput();
    saveDeepSeekSettings.busy = false;
    loadDeepSeekSettings.revision = (loadDeepSeekSettings.revision || 0) + 1;
    updateDeepSeekControls();
  }
}
function deepSeekProfileId() {
  return `api-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}
function updateDeepSeekProfileControls() {
  const busy = Boolean(saveDeepSeekProfileAction.busy);
  ['#deepSeekProfileName','#deepSeekProfileKey','#saveDeepSeekProfile','#refreshDeepSeekProfiles','#cancelDeepSeekProfileEdit']
    .forEach(id=>{if($(id))$(id).disabled=busy;});
  for (const root of ['#deepSeekProfileList','#deepSeekAccountAssignments'])
    $(root)?.querySelectorAll?.('button,select').forEach(node=>{node.disabled=busy;});
  const submit=$('#saveDeepSeekProfile');
  if(submit)submit.textContent=saveDeepSeekProfile.editId?'替换此 API 密钥':'新增 API 配置';
}
function editDeepSeekProfile(profile = null) {
  if(saveDeepSeekProfileAction.busy)return;
  saveDeepSeekProfile.editId=profile?.id || '';
  saveDeepSeekProfile.pendingId=null;
  $('#deepSeekProfileName').value=profile?.name || '';
  $('#deepSeekProfileKey').value='';
  let cancel=$('#cancelDeepSeekProfileEdit');
  if(!cancel){
    cancel=document.createElement('button');cancel.id='cancelDeepSeekProfileEdit';cancel.type='button';cancel.className='button subtle';
    cancel.textContent='取消替换';cancel.addEventListener('click',()=>editDeepSeekProfile());$('#deepSeekProfileForm').append(cancel);
  }
  cancel.hidden=!profile;
  $('#deepSeekProfileStatus').textContent=profile?`正在替换“${profile.name}”的密钥；现有妙手分配保持不变。`:'填写名称与密钥以新增 API 配置。';
  updateDeepSeekProfileControls();$('#deepSeekProfileKey').focus?.();
}
function renderDeepSeekProfiles(data) {
  if (!Array.isArray(data.profiles) || !data.profiles.every(row => row && typeof row.id === 'string' && typeof row.name === 'string')) throw new Error('后台返回的 DeepSeek API 列表无效');
  if(Number.isSafeInteger(data.revision) && Number.isSafeInteger(loadDeepSeekProfiles.data?.revision)
      && data.revision < loadDeepSeekProfiles.data.revision)return false;
  loadDeepSeekProfiles.data=data;
  const profiles = data.profiles, assignments = data.assignments || {};
  const list = $('#deepSeekProfileList'), mappings = $('#deepSeekAccountAssignments');
  list.replaceChildren();
  if (!profiles.length) list.textContent = '尚未新增专用 API；各妙手采集箱将沿用默认 API。';
  profiles.forEach(profile => {
    const row = document.createElement('div'); row.className = 'deepseek-profile-row';
    const label = document.createElement('strong'); label.textContent = profile.name;
    const hint = document.createElement('span'); hint.textContent = `已配置 · 标识 ${profile.id}`;
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'button subtle collection-manager-delete'; remove.textContent = '删除 API';
    remove.addEventListener('click', async () => {
      if (!window.confirm(`删除“${profile.name}”？使用它的妙手采集箱将自动回退到默认 API。不会调用 AI 或修改商品。`)) return;
      await saveDeepSeekProfileAction({action:'delete', id:profile.id, confirmation:'DELETE_DEEPSEEK_PROFILE'}, '正在删除 API 配置…');
    });
    const replace=document.createElement('button');replace.type='button';replace.className='button subtle';replace.textContent='替换密钥';
    replace.addEventListener('click',()=>editDeepSeekProfile(profile));
    row.append(label, hint, replace, remove); list.append(row);
  });
  mappings.replaceChildren();
  const accounts = (miaoshouCollectionAccounts || []).filter(account => account.configured && !account.removed);
  if (!accounts.length) { mappings.textContent = '尚无已配置的妙手采集箱。'; updateDeepSeekProfileControls(); return true; }
  const heading = document.createElement('h3'); heading.textContent = '妙手采集箱使用的 API'; mappings.append(heading);
  accounts.forEach(account => {
    const row = document.createElement('label'); row.className = 'deepseek-account-assignment';
    const text = document.createElement('span'); text.textContent = account.name || `妙手采集箱 ${account.id}`;
    const select = document.createElement('select');
    const fallback = document.createElement('option'); fallback.value = ''; fallback.textContent = data.default?.configured ? '默认 API（现有配置）' : '默认 API（未配置）'; select.append(fallback);
    profiles.forEach(profile => { const option = document.createElement('option'); option.value = profile.id; option.textContent = profile.name; select.append(option); });
    select.value = assignments[String(account.id)] || '';
    select.addEventListener('change', async () => {
      if(saveDeepSeekProfileAction.busy){select.value=assignments[String(account.id)] || '';return;}
      const choice = profiles.find(profile => profile.id === select.value), target = choice?.name || '默认 API';
      if (!window.confirm(`将“${account.name || `妙手采集箱 ${account.id}`}”指定为使用“${target}”？不会立即调用 AI 或影响已运行任务。`)) { select.value = assignments[String(account.id)] || ''; return; }
      const saved=await saveDeepSeekProfileAction({action:'assign', accountId:Number(account.id), profileId:select.value, confirmation:'ASSIGN_DEEPSEEK_PROFILE'}, '正在保存妙手 API 分配…');
      if(!saved)select.value=loadDeepSeekProfiles.data?.assignments?.[String(account.id)] || '';
    });
    row.append(text, select); mappings.append(row);
  });
  updateDeepSeekProfileControls();return true;
}
async function loadDeepSeekProfiles() {
  const status = $('#deepSeekProfileStatus'); if (!status) return;
  if(saveDeepSeekProfileAction.busy)return;
  const revision=loadDeepSeekProfiles.revision || 0;
  const request=loadDeepSeekProfiles.request=(loadDeepSeekProfiles.request || 0)+1;
  const current=()=>revision===(loadDeepSeekProfiles.revision || 0) && request===loadDeepSeekProfiles.request && !saveDeepSeekProfileAction.busy;
  try {
    const data = await apiGet('/api/settings/deepseek/profiles');
    if(!current() || renderDeepSeekProfiles(data)===false)return;
    status.textContent = '专用 API 与妙手采集箱分配已从本机读取；密钥不会显示。';
  } catch (error) { if(current())status.textContent = `API 管理读取失败：${error.message}`; }
}
async function saveDeepSeekProfileAction(body, progress) {
  if(saveDeepSeekProfileAction.busy)return false;
  saveDeepSeekProfileAction.busy=true;
  loadDeepSeekProfiles.revision=(loadDeepSeekProfiles.revision || 0)+1;
  updateDeepSeekProfileControls();
  const status = $('#deepSeekProfileStatus'); status.textContent = progress;
  try {
    const response = await fetch('/api/settings/deepseek/profiles', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const result = await response.json().catch(() => ({})); if (!response.ok) throw new Error(result.error || '保存失败');
    if(renderDeepSeekProfiles(result.data)===false)throw new Error('返回配置已过期，请刷新列表核对');
    status.textContent = body.action==='create'?'新 API 已添加，可在下方分配给妙手采集箱。':body.action==='replace'?'此 API 密钥已替换，现有妙手分配保持不变。':'DeepSeek API 配置已保存。';
    showToast(status.textContent);return true;
  } catch (error) {
    if(loadDeepSeekProfiles.data)renderDeepSeekProfiles(loadDeepSeekProfiles.data);
    status.textContent = `操作未确认：${error.message}。请刷新列表核对后重试。`;return false;
  } finally {
    if(body.apiKey)body.apiKey='';clearDeepSeekInput();
    saveDeepSeekProfileAction.busy=false;
    loadDeepSeekProfiles.revision=(loadDeepSeekProfiles.revision || 0)+1;
    updateDeepSeekProfileControls();
  }
}
async function saveDeepSeekProfile(event) {
  event.preventDefault();
  if(saveDeepSeekProfileAction.busy)return;
  const name = $('#deepSeekProfileName').value.trim(), apiKey = $('#deepSeekProfileKey').value.trim(), status = $('#deepSeekProfileStatus');
  if (!name || !apiKey) { status.textContent = '请填写 API 名称和 API Key'; return; }
  const replacing=Boolean(saveDeepSeekProfile.editId);
  if (!window.confirm(replacing?`替换“${name}”的 API 密钥？此配置关联的妙手采集箱将使用新密钥。`:`保存新的 DeepSeek API“${name}”？密钥只保存在本机，不会立即调用 AI。`)) return;
  const id=saveDeepSeekProfile.editId || (saveDeepSeekProfile.pendingId ||= deepSeekProfileId());
  const saved=await saveDeepSeekProfileAction({action:replacing?'replace':'create', id, name, apiKey,
    confirmation:replacing?'REPLACE_DEEPSEEK_PROFILE':'CREATE_DEEPSEEK_PROFILE'}, replacing?'正在替换 API 密钥…':'正在新增 API 配置…');
  if(saved){
    $('#deepSeekProfileName').value='';saveDeepSeekProfile.editId='';saveDeepSeekProfile.pendingId=null;
    if($('#cancelDeepSeekProfileEdit'))$('#cancelDeepSeekProfileEdit').hidden=true;
    updateDeepSeekProfileControls();
  }
}
function organizeLiveModules() {
  const collector = $('#livePanel');
  if (!collector || collector.dataset.organized === 'true') return;
  const detail = collector.querySelector('.live-detail');
  const editor = $('#liveEditor');
  const main = collector.querySelector('.live-main');
  if (!detail || !editor || !main) return;
  const workspace = document.createElement('section');
  workspace.className = 'live-edit-workspace';
  workspace.id = 'liveEditWorkspace';
  // Keep the collector and editor visible together: source selection stays
  // on the left while every editing step remains in the right workspace.
  workspace.append(editor, detail);
  main.append(workspace);
  collector.dataset.organized = 'true';
}
const PANE_WIDTHS_KEY = 'temuWorkbenchPaneWidths';
function clamp(value, min, max) { return Math.min(Math.max(value, min), max); }
function paneWidth(name, fallback) {
  const value = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name));
  return Number.isFinite(value) ? value : fallback;
}
function savePaneWidths() {
  try {
    localStorage.setItem(PANE_WIDTHS_KEY, JSON.stringify({
      sidebar: paneWidth('--sidebar-width', 220),
      source: paneWidth('--source-pane-width', 430),
    }));
  } catch { /* 浏览器隐私模式下无法保存时仍可在当前页面拖动。 */ }
}
function restorePaneWidths() {
  try {
    const saved = JSON.parse(localStorage.getItem(PANE_WIDTHS_KEY) || '{}');
    if (Number.isFinite(saved.sidebar)) document.documentElement.style.setProperty('--sidebar-width', `${clamp(saved.sidebar, 180, 420)}px`);
    if (Number.isFinite(saved.source)) document.documentElement.style.setProperty('--source-pane-width', `${clamp(saved.source, 320, 900)}px`);
  } catch { /* 忽略失效的本地布局数据。 */ }
}
function bindPaneResizer(id, variable, fallback, bounds) {
  const handle = document.getElementById(id);
  if (!handle || !matchMedia('(pointer: fine)').matches) return;
  handle.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    const initialX = event.clientX;
    const initialWidth = paneWidth(variable, fallback);
    handle.setPointerCapture(event.pointerId);
    document.body.classList.add('pane-resizing');
    const move = (moveEvent) => {
      const { min, max } = bounds();
      const width = clamp(initialWidth + moveEvent.clientX - initialX, min, max);
      document.documentElement.style.setProperty(variable, `${Math.round(width)}px`);
    };
    const finish = () => {
      document.body.classList.remove('pane-resizing');
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', finish);
      window.removeEventListener('pointercancel', finish);
      savePaneWidths();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', finish);
    window.addEventListener('pointercancel', finish);
  });
}
function setupPaneResizers() {
  restorePaneWidths();
  bindPaneResizer('appSidebarResizer', '--sidebar-width', 220, () => ({ min: 180, max: Math.max(180, Math.min(420, window.innerWidth - 520)) }));
  bindPaneResizer('sourcePaneResizer', '--source-pane-width', 430, () => {
    const panel = document.getElementById('livePanel');
    const width = panel?.getBoundingClientRect().width || window.innerWidth;
    return { min: 320, max: Math.max(320, width - 480) };
  });
  setupTaskColumnResizer();
}
function taskColumnBounds(width) {
  const available = Math.max(1, width - 20); // handle and two 4px gaps
  const min = Math.min(0.5, Math.max(0.3, 320 / available));
  return { available, min, max: 1 - min };
}
function setupTaskColumnResizer() {
  const panel = $('.task-center-columns');
  const handle = $('#taskColumnResizer');
  if (!panel || !handle) return;
  const storageKey = 'temuWorkbenchTaskSplit';
  let requested = 0.5, effective = 0.5, drag = null;
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
    if (Number.isFinite(saved)) requested = clamp(saved, 0.3, 0.7);
  } catch { /* Ignore unavailable or invalid saved layout. */ }
  const bounds = () => taskColumnBounds(panel.getBoundingClientRect().width);
  const apply = () => {
    if (!panel.getBoundingClientRect().width || window.innerWidth <= 1180) return;
    const { min, max } = bounds();
    effective = clamp(requested, min, max);
    panel.style.setProperty('--task-left-track', `${effective}fr`);
    panel.style.setProperty('--task-right-track', `${1 - effective}fr`);
    handle.setAttribute('aria-valuemin', String(Math.ceil(min * 100)));
    handle.setAttribute('aria-valuemax', String(Math.floor(max * 100)));
    handle.setAttribute('aria-valuenow', String(Math.round(effective * 100)));
    handle.setAttribute('aria-valuetext', `任务记录 ${Math.round(effective * 100)}%，发布队列 ${Math.round((1 - effective) * 100)}%`);
  };
  const save = () => {
    try { localStorage.setItem(storageKey, JSON.stringify(requested)); } catch { /* Drag still works without storage. */ }
  };
  const finish = (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    drag = null;
    handle.classList.remove('resizing');
    document.body.classList.remove('task-columns-resizing');
    if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
    save();
  };
  handle.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || drag || window.innerWidth <= 1180) return;
    event.preventDefault();
    apply();
    drag = { pointerId: event.pointerId, x: event.clientX, ratio: effective };
    handle.setPointerCapture(event.pointerId);
    handle.classList.add('resizing');
    document.body.classList.add('task-columns-resizing');
  });
  handle.addEventListener('pointermove', (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const { available, min, max } = bounds();
    requested = clamp(drag.ratio + (event.clientX - drag.x) / available, min, max);
    apply();
  });
  for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) handle.addEventListener(event, finish);
  handle.addEventListener('dblclick', () => { requested = 0.5; apply(); save(); });
  handle.addEventListener('keydown', (event) => {
    if (window.innerWidth <= 1180 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const { min, max } = bounds();
    const delta = (event.shiftKey ? 0.1 : 0.02) * (event.key === 'ArrowLeft' ? -1 : 1);
    requested = event.key === 'Home' ? min : event.key === 'End' ? max : clamp(effective + delta, min, max);
    apply(); save();
  });
  new ResizeObserver(apply).observe(panel);
  window.addEventListener('resize', apply);
  apply();
}
async function apiGet(path) {
  const response = await fetch(path, { cache: 'no-store' });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || '读取失败');
  return body.data;
}
function orderText(value) {
  return String(value || '').replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
}
function orderImageUrl(value) {
  if (!String(value || '').trim()) return '';
  try {
    const url = new URL(String(value || ''), window.location.origin);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
  } catch (_) { return ''; }
}
function orderStatusLabel(value) {
  const labels = { '平台处理中': '待处理', pending: '待处理' };
  return labels[String(value || '')] || value || '状态待返回';
}
function orderStatusTone(value) {
  const label = orderStatusLabel(value);
  if (['待处理', '待审核', '待发货', '未付款'].includes(label)) return 'pending';
  if (label === '已发货') return 'shipping';
  if (label === '已完成') return 'complete';
  return 'neutral';
}
let latestOrderData = null;
let orderSyncRelativeTimer = null;
let activeOrderAccountId = 1;
let orderAccountsExpanded = false;
let orderRequestRevision = 0;
const syncingOrderAccounts = new Set();
function setOrderAccountsExpanded(expanded) {
  orderAccountsExpanded = Boolean(expanded);
  $('#orderAccountsChildren').hidden = !orderAccountsExpanded;
  $('#orderAccountsToggle').setAttribute('aria-expanded', String(orderAccountsExpanded));
}
function orderAccountName(account) {
  const id = Number(account?.id);
  return account?.name && account.name !== '妙手采集箱'
    ? account.name : `妙手采集箱 ${id}`;
}
function renderOrderAccounts(accounts) {
  const container = $('#orderAccountsChildren');
  container.replaceChildren();
  let displayNumber = 0;
  accounts.forEach((account) => {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'nav-item';
    button.dataset.page = 'orders'; button.dataset.orderAccountId = String(account.id);
    const icon = document.createElement('span'); icon.textContent = String(++displayNumber);
    const name = orderAccountName(account);
    button.append(icon, document.createTextNode(name));
    button.title = `对应 ${name}`;
    button.addEventListener('click', () => openOrderAccount(Number(account.id)));
    button.classList.toggle('active', $('#orders').classList.contains('active') && Number(account.id) === activeOrderAccountId);
    container.append(button);
  });
  if (!accounts.some(account => Number(account.id) === activeOrderAccountId)) {
    activeOrderAccountId = 1; latestOrderData = null; orderRequestRevision += 1;
    if ($('#orders').classList.contains('active')) openOrderAccount(1);
  }
  setOrderAccountsExpanded(orderAccountsExpanded);
  if (Number(activeOrderAccountId) && accountById(activeOrderAccountId)) {
    const active = accountById(activeOrderAccountId);
    $('#orderCenterTitle').textContent = `订单中心 / ${orderAccountName(active)}`;
    $('#orderAccountDescription').textContent = `对应 ${orderAccountName(active)}；只读取该账号的订单，不修改或回写妙手。`;
  }
}
function openOrderAccount(accountId) {
  if (!accountById(accountId)) return;
  activeOrderAccountId = accountId;
  latestOrderData = null;
  $('#orderShopFilter').value = '';
  const name = orderAccountName(accountById(accountId));
  $('#orderCenterTitle').textContent = `订单中心 / ${name}`;
  $('#orderAccountDescription').textContent = `对应 ${name}；只读取该账号的订单，不修改或回写妙手。`;
  setOrderAccountsExpanded(true);
  activatePage('orders');
}
function renderOrderSyncTime(rawTime) {
  const target = $('#orderLastSynced');
  const syncedAt = new Date(rawTime || '');
  if (Number.isNaN(syncedAt.getTime())) { target.textContent = '尚未同步'; return; }
  const localTime = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(syncedAt).replace(/\//g, '-');
  const elapsedSeconds = Math.max(0, Math.floor((Date.now() - syncedAt.getTime()) / 1000));
  const relative = elapsedSeconds < 60 ? '刚刚' : elapsedSeconds < 3600 ? `${Math.floor(elapsedSeconds / 60)} 分钟前`
    : elapsedSeconds < 86400 ? `${Math.floor(elapsedSeconds / 3600)} 小时前` : `${Math.floor(elapsedSeconds / 86400)} 天前`;
  target.textContent = `最近同步：${localTime}（${relative}）`;
}
function orderAutoSyncText(autoSync) {
  if (!autoSync) return '自动同步：等待后台更新启用';
  if (!autoSync?.enabled) return '自动同步：未启用';
  if (autoSync.running) return '自动同步：正在同步订单（每 30 分钟）';
  const next = new Date(Number(autoSync.nextRunAt || 0) * 1000);
  if (Number.isNaN(next.getTime()) || next.getTime() <= 0) return '自动同步：每 30 分钟';
  const time = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', hour:'2-digit', minute:'2-digit', hourCycle:'h23'}).format(next);
  return `自动同步：每 30 分钟 · 下次 ${time}`;
}
function ensureOrderSyncClock() {
  if (orderSyncRelativeTimer) return;
  orderSyncRelativeTimer = window.setInterval(() => {
    if (latestOrderData?.summary?.lastSyncedAt) renderOrderSyncTime(latestOrderData.summary.lastSyncedAt);
  }, 30000);
}
function orderShopKey(order) {
  return String(order?.shopId || order?.shopName || '');
}
function populateOrderShopFilter(orders) {
  const select = $('#orderShopFilter');
  if (!select) return;
  const selected = select.value;
  const shops = new Map();
  orders.forEach((order) => {
    const key = orderShopKey(order);
    if (!key) return;
    const current = shops.get(key) || { name: order.shopName || '未命名店铺', count: 0 };
    current.count += 1;
    shops.set(key, current);
  });
  select.innerHTML = '<option value="">全部店铺</option>' + [...shops.entries()]
    .sort(([, left], [, right]) => left.name.localeCompare(right.name, 'zh-CN'))
    .map(([key, shop]) => `<option value="${orderText(key)}">${orderText(shop.name)}（${shop.count}）</option>`).join('');
  select.value = shops.has(selected) ? selected : '';
}
function renderOrders(data) {
  latestOrderData = data;
  const allOrders = Array.isArray(data?.orders) ? [...data.orders].sort((left, right) => {
    const leftTime = String(left?.orderedAt || '');
    const rightTime = String(right?.orderedAt || '');
    return rightTime.localeCompare(leftTime) || String(right?.recordKey || '').localeCompare(String(left?.recordKey || ''));
  }) : [];
  populateOrderShopFilter(allOrders);
  const selectedShop = $('#orderShopFilter')?.value || '';
  const orders = selectedShop ? allOrders.filter((order) => orderShopKey(order) === selectedShop) : allOrders;
  const summary = data?.summary || {};
  $('#orderSyncCount').textContent = `本地订单 ${Number(summary.count || 0)} 条`;
  renderOrderSyncTime(summary.lastSyncedAt);
  $('#orderAutoSyncSchedule').textContent = orderAutoSyncText(data?.autoSync);
  ensureOrderSyncClock();
  const syncButton = $('#syncOrders');
  const syncing = syncingOrderAccounts.has(activeOrderAccountId);
  syncButton.disabled = !data?.syncConfigured || syncing;
  syncButton.textContent = syncing ? '正在同步…' : '同步订单记录';
  syncButton.title = data?.syncConfigured ? '只读取妙手订单记录，不修改妙手数据' : (data?.message || '需先配置妙手包裹获取接口路径');
  const status = $('#orderSyncStatus');
  status.textContent = data?.syncConfigured ? '只读同步已配置' : (data?.message || '待配置包裹获取接口');
  status.className = `status-pill ${data?.syncConfigured ? 'success' : 'neutral'}`;
  $('#ordersList').innerHTML = orders.length ? orders.map((order) => {
    const imageUrl = orderImageUrl(order.imageUrl);
    const image = `<div class="order-thumb"><span class="order-thumb-empty">无图</span>${imageUrl ? `<img src="${orderText(imageUrl)}" alt="商品图片" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : ''}</div>`;
    const shop = order.shopName || '未命名店铺';
    const title = order.itemSummary || '妙手未返回商品摘要';
    const items = Array.isArray(order.itemDetails) ? order.itemDetails.filter(item => item && typeof item === 'object') : [];
    const details = items.map((item) => `<div class="order-item-detail">${items.length > 1 ? `<span class="order-item-name">${orderText(item.title || '商品')}</span>` : ''}<span><span class="order-field-label">规格</span>${orderText(item.specification || '—')}</span><span><span class="order-field-label">SKU</span>${orderText(item.skuId || '—')}</span></div>`).join('');
    return `<article class="order-record-row">
      <header class="order-record-head">
        <div class="order-record-identity"><span class="order-number"><span class="order-field-label">订单</span><strong>${orderText(order.orderNo || '订单号未返回')}</strong></span><span class="order-shop"><span class="order-field-label">店铺</span>${orderText(shop)}</span></div>
        <span class="order-status order-status-${orderStatusTone(order.status)}">${orderText(orderStatusLabel(order.status))}</span>
      </header>
      <div class="order-record-body">
        <div class="order-product">${image}<div class="order-product-info"><strong class="order-product-title" title="${orderText(title)}">${orderText(title)}</strong>${details}</div></div>
        <dl class="order-record-meta"><div><dt>下单</dt><dd>${orderText(order.orderedAt || '—')}</dd></div><div><dt>更新</dt><dd>${orderText(order.updatedAtRemote || order.syncedAt || '—')}</dd></div><div><dt>运单</dt><dd>${orderText(order.trackingNo || '—')}</dd></div></dl>
      </div>
    </article>`;
  }).join('') : `<p class="live-empty">${selectedShop ? '该店铺暂无已同步订单。' : '暂无本地订单记录。'}</p>`;
}
async function loadOrders() {
  const accountId = activeOrderAccountId;
  const revision = ++orderRequestRevision;
  latestOrderData = null;
  renderOrders({orders: [], summary: {}, syncConfigured: false, message: '正在读取该账号订单…'});
  try {
    const data = await apiGet(`/api/orders?accountId=${accountId}`);
    if (revision !== orderRequestRevision || accountId !== activeOrderAccountId) return;
    if (data?.accountId !== accountId) throw Error('后台尚未支持账号订单隔离，请更新并重启服务');
    renderOrders(data);
  } catch (error) {
    if (revision !== orderRequestRevision || accountId !== activeOrderAccountId) return;
    renderOrders({orders: [], summary: {}, syncConfigured: false, message: '读取失败'});
    $('#ordersList').innerHTML = `<p class="live-empty">${orderText(error.message || '订单记录读取失败')}</p>`;
  }
}
async function syncOrders() {
  const accountId = activeOrderAccountId;
  if (syncingOrderAccounts.has(accountId) || latestOrderData?.accountId !== accountId || !latestOrderData?.syncConfigured) return;
  syncingOrderAccounts.add(accountId);
  const revision = ++orderRequestRevision;
  const button = $('#syncOrders');
  button.disabled = true; button.textContent = '正在同步…';
  try {
    const response = await fetch('/api/orders/sync', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({accountId}) });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || '订单同步失败');
    if (body.data?.accountId !== accountId) throw Error('订单返回账号不匹配，请重启服务后重试');
    if (revision === orderRequestRevision && accountId === activeOrderAccountId) renderOrders(body.data);
    showToast(`订单中心 ${accountId} 已同步 ${Number(body.data?.imported || 0)} 条订单记录`);
  } catch (error) { showToast(error.message || '订单同步失败'); }
  finally {
    syncingOrderAccounts.delete(accountId);
    if (accountId === activeOrderAccountId) {
      if (revision !== orderRequestRevision) loadOrders();
      else if (latestOrderData) renderOrders(latestOrderData);
    }
  }
}
function poolTransferStatus(message, tone = 'info') {
  const target = $('#livePoolTransferStatus');
  target.textContent = message || '';
  target.className = `live-transfer-status ${tone}${message ? '' : ' hidden'}`;
}
const automationState = { items: [], poolPage: 1, selected: new Set(), selectedJobIds: new Set(), selectedFreightJobIds: new Set(), selectedWarehouseJobIds: new Set(), selectedPublishJobIds: new Set(), publishQueueShopId: '', removingPublishJobs: false, publishProgress: null, publishStarting: false, publishProgressStale: false, retryingJobIds: new Set(), resumingJobIds: new Set(), cancelingJobIds: new Set(), batchRetrying: false, batchRestoring: false, batchFreightUpdating: false, batchFreightTemplateId: '', batchWarehouseUpdating: false, batchWarehouseId: '', clearingJobs: false, jobs: [], jobsLoaded: false, jobsLoading: false, jobsRefreshQueued: false, submitting: false, shopNames: new Map(), shopNamesLoading: false, shopNamesLoaded: false, jobRecordExpanded: new Map() };
const automationPreferencesKey = 'temu-workbench.automation-preferences.v1' + (activeMiaoshouCollectionAccountId === 1 ? '' : `.account-${activeMiaoshouCollectionAccountId}`);
function automationPreferences() {
  try { return JSON.parse(localStorage.getItem(automationPreferencesKey) || '{}') || {}; }
  catch (_) { return {}; }
}
function saveAutomationPreferences() {
  try {
    localStorage.setItem(automationPreferencesKey, JSON.stringify({
      shopId: $('#automationClaimShop').value,
      shopIds: selectedAutomationShopIds(),
      site: $('#automationPublishSite').value,
      price: $('#automationSkuPrice').value,
      priceMode: $('#automationPriceMode').value,
      priceMultiplier: $('#automationPriceMultiplier').value,
      intervalSeconds: $('#publishQueuePublishInterval').value,
      publishIntervalDefaultVersion: 2,
      publishCount: $('#publishQueuePublishCount').value,
      publishQueueShopIds: selectedPublishShopIds(),
      editConcurrency: $('#automationEditConcurrency').value,
    }));
  } catch (_) { /* Browser storage is optional; the current page still works. */ }
}
function updateAutomationPriceMode() {
  const multiplierMode = $('#automationPriceMode').value === 'source_multiplier';
  $('#automationPriceFloorLabel').hidden = multiplierMode;
  $('#automationPriceMultiplierLabel').hidden = !multiplierMode;
  $('#automationSkuPrice').disabled = multiplierMode;
  $('#automationPriceMultiplier').disabled = !multiplierMode;
}
function automationClaimFeedback(message, tone = 'info') {
  const target = $('#automationClaimFeedback');
  target.textContent = message || '';
  target.className = `automation-claim-feedback ${tone}${message ? '' : ' hidden'}`;
}
function automationJobFeedback(message, tone = 'info') {
  const target = $('#automationJobFeedback');
  target.textContent = message || '';
  target.className = `automation-claim-feedback ${tone}${message ? '' : ' hidden'}`;
}
const automationTerminalStatuses = new Set(['done', 'failed', 'needs_review', 'interrupted', 'canceled']);
let automationSaveFilter = 'unsaved';
function automationJobSaved(job) {
  if (['ready_publish', 'done'].includes(job.status)) return true;
  const lastSave = (job.events || []).filter(event => event.step === 'save').at(-1);
  return ['verify', 'publish', 'complete'].includes(job.step) && lastSave?.status === 'done';
}
function isPublishingAutomationJob(job) {
  return job?.status === 'ready_publish' || (job?.status === 'running' && job?.step === 'publish');
}
function canCancelEditAutomationJob(job) {
  return ['queued', 'running'].includes(job?.status) && !isPublishingAutomationJob(job);
}
function clearableAutomationJobs() {
  return automationState.jobs.filter((job) => job && !isPublishingAutomationJob(job));
}
function visibleAutomationJobs() {
  return automationState.jobs.filter(job => job && automationJobSaved(job) === (automationSaveFilter === 'saved'));
}
function setAutomationSaveFilter(filter) {
  if (!['saved', 'unsaved'].includes(filter)) return;
  automationSaveFilter = filter;
  automationState.selectedJobIds.clear();
  automationState.selectedFreightJobIds.clear();
  automationState.selectedWarehouseJobIds.clear();
  renderAutomationJobs();
}
function batchRetryCandidates() {
  const activeKeys = new Set(automationState.jobs.filter(job => job && ['queued', 'running'].includes(job.status))
    .map(job => `${job.detailId}:${job.shopId}`));
  const seen = new Set();
  return [...automationState.jobs].filter(job => job && !automationJobSaved(job))
    .sort((a, b) => String(b.updatedAt || b.createdAt || '').localeCompare(String(a.updatedAt || a.createdAt || '')))
    .filter(job => {
      if (!['needs_input', 'failed', 'needs_review', 'interrupted'].includes(job.status)) return false;
      if (automationState.retryingJobIds.has(job.jobId) || automationState.resumingJobIds.has(job.jobId)) return false;
      if (job.status === 'needs_input' && job.choices?.[job.step]?.options?.length) return false;
      const key = `${job.detailId}:${job.shopId}`;
      if (activeKeys.has(key) || seen.has(key)) return false;
      seen.add(key); return true;
    });
}
function deletedCollectBoxRestoreCandidates() {
  return automationState.jobs.filter((job) => job && job.status === 'needs_input'
    && String(job.message || '').includes('采集箱中已被删除')
    && !automationState.resumingJobIds.has(job.jobId));
}
function batchFreightCandidates() {
  const resuming = automationState.resumingJobIds || new Set();
  return automationState.jobs.filter((job) => job && job.status === 'needs_input' && job.step === 'freight'
    && Array.isArray(job.choices?.freight?.options) && job.choices.freight.options.length
    && !resuming.has(job.jobId));
}
function selectedBatchFreightJobs() {
  const selected = automationState.selectedFreightJobIds || new Set();
  return batchFreightCandidates().filter((job) => selected.has(job.jobId));
}
function sharedBatchFreightOptions(jobs = selectedBatchFreightJobs()) {
  if (!jobs.length) return [];
  return (jobs[0].choices.freight.options || []).filter((option) => option?.id
    && jobs.every((job) => (job.choices?.freight?.options || []).some((candidate) => candidate?.id === option.id)));
}
function renderBatchFreightTemplateControls() {
  const panel = $('#batchFreightTemplatePanel');
  const select = $('#batchFreightTemplate');
  const submit = $('#batchFreightTemplateSubmit');
  const note = $('#batchFreightTemplateNote');
  if (!panel || !select || !submit || !note) return;
  const candidates = batchFreightCandidates();
  const candidateIds = new Set(candidates.map((job) => job.jobId));
  automationState.selectedFreightJobIds = new Set([...automationState.selectedFreightJobIds].filter((jobId) => candidateIds.has(jobId)));
  const jobs = selectedBatchFreightJobs();
  const options = sharedBatchFreightOptions(jobs);
  panel.classList.toggle('hidden', candidates.length === 0);
  select.replaceChildren();
  if (!jobs.length) appendOption(select, '', '请先勾选待处理商品');
  else if (!options.length) appendOption(select, '', '所选商品没有共同可用模板');
  else {
    appendOption(select, '', '请选择运费模板');
    options.forEach((option) => appendOption(select, option.id, option.name));
  }
  if (options.some((option) => option.id === automationState.batchFreightTemplateId)) select.value = automationState.batchFreightTemplateId;
  else automationState.batchFreightTemplateId = '';
  select.disabled = automationState.batchFreightUpdating || !options.length;
  submit.disabled = automationState.batchFreightUpdating || !jobs.length || !options.length || !select.value;
  submit.textContent = automationState.batchFreightUpdating ? '正在批量修改…' : jobs.length ? `确认批量修改（${jobs.length}）` : '确认批量修改';
  note.textContent = !jobs.length ? `可勾选 ${candidates.length} 件待选择运费模板的商品。`
    : !options.length ? '所选商品没有共同可用的运费模板，请按店铺或模板分开处理。'
      : `将为所选 ${jobs.length} 件商品设置同一模板，随后继续编辑保存；不会自动发布。`;
}
function batchWarehouseCandidates() {
  const resuming = automationState.resumingJobIds || new Set();
  return automationState.jobs.filter((job) => job && job.status === 'needs_input' && job.step === 'warehouse'
    && Array.isArray(job.choices?.warehouse?.options) && job.choices.warehouse.options.length
    && !resuming.has(job.jobId));
}
function selectedBatchWarehouseJobs() {
  const selected = automationState.selectedWarehouseJobIds || new Set();
  return batchWarehouseCandidates().filter((job) => selected.has(job.jobId));
}
function sharedBatchWarehouseOptions(jobs = selectedBatchWarehouseJobs()) {
  if (!jobs.length) return [];
  return (jobs[0].choices.warehouse.options || []).filter((option) => option?.id
    && jobs.every((job) => (job.choices?.warehouse?.options || []).some((candidate) => candidate?.id === option.id)));
}
function renderBatchWarehouseControls() {
  const panel = $('#batchWarehousePanel');
  const select = $('#batchWarehouse');
  const submit = $('#batchWarehouseSubmit');
  const note = $('#batchWarehouseNote');
  if (!panel || !select || !submit || !note) return;
  const candidates = batchWarehouseCandidates();
  const candidateIds = new Set(candidates.map((job) => job.jobId));
  automationState.selectedWarehouseJobIds = new Set([...automationState.selectedWarehouseJobIds].filter((jobId) => candidateIds.has(jobId)));
  const jobs = selectedBatchWarehouseJobs();
  const options = sharedBatchWarehouseOptions(jobs);
  panel.classList.toggle('hidden', candidates.length === 0);
  select.replaceChildren();
  if (!jobs.length) appendOption(select, '', '请先勾选待处理商品');
  else if (!options.length) appendOption(select, '', '所选商品没有共同可用仓库');
  else {
    appendOption(select, '', '请选择发货仓库');
    options.forEach((option) => appendOption(select, option.id, option.name));
  }
  if (options.some((option) => option.id === automationState.batchWarehouseId)) select.value = automationState.batchWarehouseId;
  else automationState.batchWarehouseId = '';
  select.disabled = automationState.batchWarehouseUpdating || !options.length;
  submit.disabled = automationState.batchWarehouseUpdating || !jobs.length || !options.length || !select.value;
  submit.textContent = automationState.batchWarehouseUpdating ? '正在批量选择…' : jobs.length ? `确认批量选择（${jobs.length}）` : '确认批量选择';
  note.textContent = !jobs.length ? `可勾选 ${candidates.length} 件待选择发货仓库的商品。`
    : !options.length ? '所选商品没有共同可用的发货仓库，请按店铺或仓库分开处理。'
      : `将为所选 ${jobs.length} 件商品设置同一发货仓库，随后继续编辑保存；不会自动发布。`;
}
function updateAutomationJobBatchControls() {
  const retry = $('#retryAutomationJobsBatch');
  const retryCount = batchRetryCandidates().length;
  retry.disabled = automationState.batchRetrying || automationState.batchRestoring || retryCount === 0;
  retry.textContent = automationState.batchRetrying ? '正在批量重试…' : retryCount ? `批量重试（${retryCount}）` : '批量重试';
  const restore = $('#restoreDeletedAutomationJobsBatch');
  const restoreCount = deletedCollectBoxRestoreCandidates().length;
  restore.disabled = automationState.batchRestoring || automationState.batchRetrying || restoreCount === 0;
  restore.textContent = automationState.batchRestoring ? '正在批量恢复…'
    : restoreCount ? `一键恢复到采集箱并重新保存（${restoreCount}）` : '一键恢复到采集箱并重新保存';
  const deletableJobIds = visibleAutomationJobs().filter((job) => automationTerminalStatuses.has(job.status))
    .map((job) => job.jobId);
  const selected = [...automationState.selectedJobIds].filter((jobId) =>
    deletableJobIds.includes(jobId));
  automationState.selectedJobIds = new Set(selected);
  const button = $('#deleteAutomationJobsBatch');
  button.disabled = selected.length === 0;
  button.textContent = selected.length ? `批量删除记录（${selected.length}）` : '批量删除记录';
  const clear = $('#clearAutomationJobs');
  clear.disabled = automationState.clearingJobs || clearableAutomationJobs().length === 0;
  clear.textContent = automationState.clearingJobs ? '正在停止并清空…' : '停止并清空列表';
  const selectAll = $('#selectAllAutomationJobs');
  const freightJobs = automationSaveFilter === 'unsaved' ? batchFreightCandidates() : [];
  const warehouseJobs = automationSaveFilter === 'unsaved' ? batchWarehouseCandidates() : [];
  const configurationJobs = [...freightJobs, ...warehouseJobs];
  const configurationSelected = configurationJobs.length > 0
    && freightJobs.every((job) => automationState.selectedFreightJobIds.has(job.jobId))
    && warehouseJobs.every((job) => automationState.selectedWarehouseJobIds.has(job.jobId));
  const allSelected = deletableJobIds.length > 0 && selected.length === deletableJobIds.length;
  selectAll.disabled = automationSaveFilter === 'unsaved' ? configurationJobs.length === 0 : deletableJobIds.length === 0;
  selectAll.textContent = automationSaveFilter === 'unsaved'
    ? (configurationSelected ? '取消全选待选择配置' : `全选待选择配置（${configurationJobs.length}）`)
    : (allSelected ? '取消全选' : '全选已结束记录');
  renderBatchFreightTemplateControls();
  renderBatchWarehouseControls();
}
function toggleSelectAllAutomationJobs() {
  if (automationSaveFilter === 'unsaved') {
    const freightJobs = batchFreightCandidates();
    const warehouseJobs = batchWarehouseCandidates();
    const configurationJobs = [...freightJobs, ...warehouseJobs];
    const allSelected = configurationJobs.length > 0
      && freightJobs.every((job) => automationState.selectedFreightJobIds.has(job.jobId))
      && warehouseJobs.every((job) => automationState.selectedWarehouseJobIds.has(job.jobId));
    automationState.selectedFreightJobIds = allSelected ? new Set() : new Set(freightJobs.map((job) => job.jobId));
    automationState.selectedWarehouseJobIds = allSelected ? new Set() : new Set(warehouseJobs.map((job) => job.jobId));
    renderAutomationJobs();
    return;
  }
  const deletableJobIds = visibleAutomationJobs().filter((job) => automationTerminalStatuses.has(job.status))
    .map((job) => job.jobId);
  const allSelected = deletableJobIds.length > 0 && deletableJobIds.every((jobId) => automationState.selectedJobIds.has(jobId));
  automationState.selectedJobIds = allSelected ? new Set() : new Set(deletableJobIds);
  renderAutomationJobs();
}
function automationShopName(shopId) {
  return automationState.shopNames.get(String(shopId)) || '未知店铺';
}
async function loadAutomationJobShopNames() {
  if (automationState.shopNamesLoading || automationState.shopNamesLoaded) return;
  automationState.shopNamesLoading = true;
  try {
    const response = await apiGet('/api/shops');
    const names = new Map((response.data?.shopList || []).map((shop) => [
      String(shop.shopId), shop.shopNick || shop.platformShopName || `店铺 ${shop.shopId}`,
    ]));
    automationState.shopNames = names;
    automationState.shopNamesLoaded = true;
    if (automationState.jobsLoaded) renderAutomationJobs();
  } catch (_) {
    // Keep task history readable even if the current shop list cannot be read.
    automationState.shopNamesLoaded = true;
  } finally { automationState.shopNamesLoading = false; }
}
const AUTOMATION_POOL_PAGE_SIZE = 50;
let automationPoolClaimFilter = 'all';
function automationPersonnelReady() {
  return automationState.routing?.scan?.status === 'ready';
}
function automationPersonnelLabel(id) {
  if (!id) return '未读取到所属人员';
  const person = automationState.routing?.people?.find(row => row.id === id);
  const name = person?.alias || person?.name || person?.account || '未匹配人员';
  return `${name}（账号 ID ${id}）`;
}
function automationItemShopIds(detailId) {
  if (automationState.targetMode !== 'personnel') return selectedAutomationShopIds();
  if (!automationPersonnelReady()) return [];
  const owner = automationState.routing.owners?.[detailId];
  return owner ? [...new Set(automationState.routing.bindings?.[owner] || [])].sort((a, b) => a - b) : [];
}
function automationExpectedRouting(detailIds) {
  return detailIds.map(detailId => ({detailId, collectorId: automationState.routing?.owners?.[detailId] || '',
    shopIds: automationItemShopIds(detailId)}));
}
function renderAutomationPersonnelStatus() {
  const scan = automationState.routing?.scan;
  const status = $('#automationPersonnelStatus');
  status.textContent = automationState.routingError || (automationState.routingLoading || scan?.status === 'loading'
    ? `正在只读核对人员归属，已读取 ${scan?.scanned || 0} 件…`
    : scan?.status === 'ready' ? `人员归属已核对 ${scan.scanned} 件。按绑定保存仅处理当前勾选商品；未知归属或未绑定会跳过。缓存有效期 1 周；需要立即更新可手动刷新。`
    : ['error', 'failed'].includes(scan?.status) ? (scan.message || '人员归属读取失败，请刷新重试')
    : `对应当前妙手采集箱；按绑定保存前请刷新人员归属。未绑定商品不会套用其他店铺。`);
  $('#automationPersonnelRefresh').disabled = Boolean(automationState.routingLoading || automationState.bindingSaving || automationState.submitting);
}
async function loadAutomationPersonnel(scan = false, refresh = false) {
  if (automationState.bindingSaving) return;
  if (automationState.routingLoading) {
    if (scan) { automationState.routingScanQueued = true; automationState.routingRefreshQueued ||= refresh; }
    return;
  }
  clearTimeout(automationState.routingTimer);
  const serial = (automationState.routingSerial || 0) + 1;
  automationState.routingSerial = serial;
  automationState.routingLoading = true;
  automationState.routingError = '';
  if (refresh && automationState.routing) automationState.routing.scan = {status: 'loading', scanned: 0};
  renderAutomationPersonnelStatus(); updateAutomationControls();
  try {
    const result = await apiGet(`/api/automation/personnel-routing?accountId=${activeMiaoshouCollectionAccountId}${scan ? '&scan=1' : ''}${refresh ? '&refresh=1' : ''}`);
    if (serial !== automationState.routingSerial) return;
    if (result.accountId !== activeMiaoshouCollectionAccountId || !result.scope) throw new Error('采集箱账号返回不一致，请重新加载');
    // apiGet unwraps the local envelope once; no liveState account data is used here.
    if (automationState.routing?.scope && result.scope !== automationState.routing.scope) closePersonnelBindings();
    automationState.routing = result;
    if ((scan || automationState.targetMode === 'personnel') && result.scan?.status === 'loading') {
      automationState.routingTimer = setTimeout(() => loadAutomationPersonnel(true), 300);
    }
  } catch (error) {
    if (serial !== automationState.routingSerial) return;
    automationState.routingError = `人员归属读取失败：${error.message}`;
    if (automationState.routing) automationState.routing.scan = {status: 'error'};
  } finally {
    if (serial === automationState.routingSerial) {
      automationState.routingLoading = false;
      renderAutomationPersonnelStatus(); renderAutomationPool();
      if (automationState.routingScanQueued) {
        const fresh = automationState.routingRefreshQueued;
        automationState.routingScanQueued = false; automationState.routingRefreshQueued = false;
        clearTimeout(automationState.routingTimer);
        automationState.routingTimer = setTimeout(() => loadAutomationPersonnel(true, fresh), 0);
      }
    }
  }
}
function closePersonnelBindings() {
  if (automationState.bindingSaving) return;
  automationState.bindingOpen = false;
  $('#personnelBindingModal').classList.add('hidden');
  $('#automationPersonnelBindings').focus();
}
function renderPersonnelBindingShops() {
  const routing = automationState.routing;
  const personId = $('#personnelBindingPerson').value;
  const selected = new Set(routing?.bindings?.[personId] || []);
  const shops = routing?.shops || [];
  const list = $('#personnelBindingShops'); list.replaceChildren();
  shops.forEach(shop => {
    const label = document.createElement('label');
    const input = document.createElement('input'); input.type = 'checkbox'; input.value = String(shop.shopId);
    input.checked = selected.has(shop.shopId);
    const text = document.createElement('span'); text.textContent = `${shop.name}（${shop.shopId}）`;
    label.append(input, text); list.append(label);
  });
  const unavailable = [...selected].filter(id => !shops.some(shop => shop.shopId === id));
  $('#personnelBindingStatus').textContent = unavailable.length
    ? `原绑定中的店铺 ${unavailable.join('、')} 已不可用。保存将以当前勾选为准。`
    : personId ? '勾选店铺后点击“保存绑定”；切换人员前请先保存。' : '尚未发现人员，请关闭后点击“刷新人员归属”。';
  $('#personnelBindingSave').disabled = !personId || !routing?.scope;
}
function renderPersonnelBindingSummary() {
  const routing = automationState.routing;
  const list = $('#personnelBindingSummary'); list.replaceChildren();
  Object.entries(routing?.bindings || {}).forEach(([id, shops]) => {
    const row = document.createElement('li');
    row.textContent = `${automationPersonnelLabel(id)} → ${shops.map(shopId =>
      routing.shops?.find(shop => shop.shopId === shopId)?.name || `不可用店铺 ${shopId}`).join('、')}`;
    list.append(row);
  });
  if (!list.children.length) { const row = document.createElement('li'); row.textContent = '暂无绑定'; list.append(row); }
}
async function openPersonnelBindings() {
  if (automationState.submitting || automationState.bindingSaving) return;
  await loadAutomationPersonnel();
  const routing = automationState.routing;
  if (!routing?.scope || automationState.routingError) {
    automationClaimFeedback(automationState.routingError || '请等待人员读取完成后再打开绑定设置。', 'error'); return;
  }
  const select = $('#personnelBindingPerson'); select.replaceChildren();
  (routing.people || []).forEach(person => appendOption(select, person.id, automationPersonnelLabel(person.id)));
  automationState.bindingOpen = true;
  automationState.bindingScope = routing.scope;
  $('#personnelBindingModal').classList.remove('hidden');
  renderPersonnelBindingShops(); renderPersonnelBindingSummary(); select.focus();
}
async function savePersonnelBinding() {
  if (!automationState.bindingOpen || automationState.bindingSaving) return;
  const scope = automationState.bindingScope;
  const personId = $('#personnelBindingPerson').value;
  const shopIds = [...$('#personnelBindingShops').querySelectorAll('input:checked')].map(input => Number(input.value));
  automationState.bindingSaving = true;
  clearTimeout(automationState.routingTimer);
  automationState.routingSerial = (automationState.routingSerial || 0) + 1;
  automationState.routingLoading = false;
  const fields = [...$('#personnelBindingModal').querySelectorAll('button, select, input')];
  fields.forEach(field => { field.disabled = true; });
  $('#personnelBindingStatus').textContent = '正在保存本地绑定…';
  try {
    const response = await fetch('/api/automation/personnel-bindings', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({accountId: activeMiaoshouCollectionAccountId, scope, personId, shopIds})});
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || '保存失败');
    if (scope !== automationState.routing?.scope || body.data?.scope !== scope) throw new Error('账号已变更，请关闭后重新打开设置');
    automationState.routing.bindings = body.data.bindings;
    automationState.routing.shops = body.data.shops;
    $('#personnelBindingStatus').textContent = shopIds.length ? `已保存：该人员绑定 ${shopIds.length} 家店铺。未执行任何商品任务。` : '已解除该人员的店铺绑定。';
    renderPersonnelBindingSummary(); renderAutomationPool();
  } catch (error) { $('#personnelBindingStatus').textContent = `保存失败：${error.message}；当前勾选已保留，可重试。`; }
  finally {
    automationState.bindingSaving = false;
    fields.forEach(field => { field.disabled = false; });
    renderAutomationPersonnelStatus(); updateAutomationControls();
    if (automationState.routing?.scan?.status === 'loading' && automationState.targetMode === 'personnel') {
      automationState.routingTimer = setTimeout(() => loadAutomationPersonnel(true), 300);
    }
  }
}
function selectedAutomationShopIds() {
  return [...$('#automationClaimShop').selectedOptions].map(option => Number(option.value))
    .filter(id => Number.isSafeInteger(id) && id > 0);
}
function automationItemClaimStatus(item) {
  const claims = Array.isArray(item?.claims) ? item.claims : [];
  const targetShopIds = automationItemShopIds(item.detailId);
  if (automationState.targetMode === 'personnel' && !targetShopIds.length) return 'pending';
  return (targetShopIds.length ? targetShopIds.every(id => claims.some(entry => Number(entry.shopId) === id && entry.status === 'claimed'))
    : claims.some(entry => entry.status === 'claimed')) ? 'claimed' : 'pending';
}
function filteredAutomationPoolItems() {
  return automationState.items.filter(item => automationPoolClaimFilter === 'all'
    || automationItemClaimStatus(item) === automationPoolClaimFilter);
}
function setAutomationPoolClaimFilter(filter) {
  if (!['all', 'pending', 'claimed'].includes(filter) || automationPoolClaimFilter === filter) return;
  automationPoolClaimFilter = filter;
  automationState.poolPage = 1;
  automationState.selected.clear();
  renderAutomationPool();
}
function automationPoolPageItems() {
  const filtered = filteredAutomationPoolItems();
  const pages = Math.max(1, Math.ceil(filtered.length / AUTOMATION_POOL_PAGE_SIZE));
  automationState.poolPage = Math.max(1, Math.min(pages, automationState.poolPage || 1));
  return filtered.slice((automationState.poolPage - 1) * AUTOMATION_POOL_PAGE_SIZE,
    automationState.poolPage * AUTOMATION_POOL_PAGE_SIZE);
}
function selectableAutomationPoolPage() {
  return automationPoolPageItems().filter((item) => !automationState.jobs.some((job) =>
    job.detailId === Number(item.detailId) && ['queued', 'running', 'needs_input', 'ready_publish'].includes(job.status)));
}
function changeAutomationPoolPage(page) {
  if (automationState.submitting) return;
  const pages = Math.max(1, Math.ceil(filteredAutomationPoolItems().length / AUTOMATION_POOL_PAGE_SIZE));
  if (!Number.isInteger(page) || page < 1 || page > pages) {
    showToast(`请输入 1 到 ${pages} 之间的页码`); return;
  }
  if (page === automationState.poolPage) return;
  automationState.poolPage = page;
  automationState.selected.clear();
  automationClaimFeedback('');
  renderAutomationPool();
}
function selectAutomationPoolPage(checked) {
  if (automationState.submitting) return;
  automationState.selected = checked ? new Set(selectableAutomationPoolPage().map(item => Number(item.detailId))) : new Set();
  renderAutomationPool();
}
function updateAutomationSelectionControls() {
  // A single checkbox has already updated its own checked state.  Do not
  // rebuild every product card here: rebuilding a 50-item page also restarts
  // image layout and makes quick multi-select feel unresponsive.
  const selected = automationState.selected.size;
  const shopCount = selectedAutomationShopIds().length;
  const bound = automationState.targetMode === 'personnel';
  const targets = bound ? [...automationState.selected].map(automationItemShopIds) : [];
  const taskCount = bound ? targets.reduce((total, shops) => total + shops.length, 0) : selected * shopCount;
  $('#automationSelectedCount').textContent = selected ? bound
    ? `已选 ${selected} 件 · 按人员绑定共 ${taskCount} 个任务 · 将跳过 ${targets.filter(shops => !shops.length).length} 件`
    : `已选 ${selected} 件商品 × ${shopCount} 家店铺，共 ${taskCount} 个任务` : '未选商品';
  $('#automationClaimSubmit').disabled = automationState.submitting || selected < 1 ||
    (bound ? !automationPersonnelReady() || automationState.routingLoading || automationState.bindingSaving || !taskCount : !$('#automationClaimShop').value)
    || !$('#automationPublishSite').value || liveState.mode !== 'live';
  $('#automationBatchRemove').disabled = automationState.submitting || selected < 1;
  const selectableIds = automationState.visibleSelectableIds || new Set();
  const selectAll = $('#automationSelectAll');
  selectAll.disabled = automationState.submitting || !selectableIds.size;
  selectAll.checked = selectableIds.size > 0 && [...selectableIds].every(id => automationState.selected.has(id));
  selectAll.indeterminate = selected > 0 && !selectAll.checked;
}
function updateAutomationControls() {
  const pageItems = automationPoolPageItems();
  const selectable = selectableAutomationPoolPage();
  const selectableIds = new Set(selectable.map(item => Number(item.detailId)));
  automationState.selected = new Set([...automationState.selected].filter(id => selectableIds.has(id)));
  const filtered = filteredAutomationPoolItems();
  const pages = Math.max(1, Math.ceil(filtered.length / AUTOMATION_POOL_PAGE_SIZE));
  $('#automationPoolPageLabel').textContent = `第 ${automationState.poolPage} / ${pages} 页 · 本页 ${pageItems.length} 件 · 当前分类 ${filtered.length} 件 / 共 ${automationState.items.length} 件`;
  $('#automationPoolPageInput').value = String(automationState.poolPage);
  $('#automationPoolPageInput').max = String(pages);
  $('#automationPoolPageInput').disabled = automationState.submitting || !filtered.length;
  $('#automationPoolPageGo').disabled = automationState.submitting || !filtered.length;
  $('#automationPoolPrev').disabled = automationState.submitting || automationState.poolPage <= 1;
  $('#automationPoolNext').disabled = automationState.submitting || automationState.poolPage >= pages;
  const selected = automationState.selected.size;
  const shopCount = selectedAutomationShopIds().length;
  const bound = automationState.targetMode === 'personnel';
  const targets = bound ? [...automationState.selected].map(automationItemShopIds) : [];
  const taskCount = bound ? targets.reduce((total, shops) => total + shops.length, 0) : selected * shopCount;
  $('#automationSelectedCount').textContent = selected ? bound
    ? `已选 ${selected} 件 · 按人员绑定共 ${taskCount} 个任务 · 将跳过 ${targets.filter(shops => !shops.length).length} 件`
    : `已选 ${selected} 件商品 × ${shopCount} 家店铺，共 ${taskCount} 个任务` : '未选商品';
  $('#automationClaimSubmit').disabled = automationState.submitting || selected < 1 ||
    (bound ? !automationPersonnelReady() || automationState.routingLoading || automationState.bindingSaving || !taskCount : !$('#automationClaimShop').value)
    || !$('#automationPublishSite').value || liveState.mode !== 'live';
  $('#automationClaimShop').disabled = bound || automationState.submitting;
  $('#automationTargetMode').disabled = automationState.submitting || automationState.bindingSaving;
  $('#automationPersonnelBindings').disabled = automationState.submitting || automationState.bindingSaving;
  $('#automationBatchRemove').disabled = automationState.submitting || selected < 1;
  const selectAll = $('#automationSelectAll');
  selectAll.disabled = automationState.submitting || !selectable.length;
  selectAll.checked = selectable.length > 0 && selectable.every((item) => automationState.selected.has(Number(item.detailId)));
  selectAll.indeterminate = selected > 0 && !selectAll.checked;
  const counts = {all: automationState.items.length, pending: 0, claimed: 0};
  automationState.items.forEach(item => { counts[automationItemClaimStatus(item)] += 1; });
  $('#automationClaimAllCount').textContent = String(counts.all);
  $('#automationClaimPendingCount').textContent = String(counts.pending);
  $('#automationClaimedCount').textContent = String(counts.claimed);
  if (typeof document.querySelectorAll === 'function') document.querySelectorAll('[data-automation-claim-filter]').forEach(button => {
    const active = button.dataset.automationClaimFilter === automationPoolClaimFilter;
    button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
  });
}
function renderAutomationPool() {
  const list = $('#automationPoolList');
  const items = automationPoolPageItems();
  const visibleIds = new Set(items.map(item => Number(item.detailId)));
  automationState.selected = new Set([...automationState.selected].filter(id => visibleIds.has(id)));
  const shopId = Number($('#automationClaimShop').value);
  const visibleSelectableIds = new Set();
  $('#automationPoolCount').textContent = String(automationState.items.length);
  list.replaceChildren();
  if (!items.length) {
    const empty = document.createElement('div'); empty.className = 'automation-empty';
    empty.textContent = '池子还是空的。请先在采集箱选中商品，再点击“加入自动化池”。';
    list.append(empty);
    updateAutomationControls();
    return;
  }
  items.forEach((item) => {
    const detailId = Number(item.detailId);
    const locked = automationState.jobs.some((job) => job.detailId === detailId && ['queued', 'running', 'needs_input', 'ready_publish'].includes(job.status));
    if (locked) automationState.selected.delete(detailId);
    else visibleSelectableIds.add(detailId);
    const row = document.createElement('article'); row.className = 'automation-item';
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.className = 'automation-item-check';
    checkbox.checked = automationState.selected.has(detailId);
    checkbox.disabled = locked || automationState.submitting;
    checkbox.setAttribute('aria-label', `选择采集箱商品 ${item.title || detailId}`);
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) automationState.selected.add(detailId);
      else automationState.selected.delete(detailId);
      updateAutomationSelectionControls();
    });
    const image = document.createElement('img'); image.alt = ''; image.loading = 'lazy';
    if (/^https?:\/\//.test(item.thumbnail || '')) image.src = item.thumbnail;
    const content = document.createElement('div'); content.className = 'automation-item-content';
    const title = document.createElement('strong'); title.textContent = item.title || '未命名商品';
    const meta = document.createElement('small');
    meta.textContent = `采集箱 ID ${detailId} · 入池于 ${new Date(item.createdAt).toLocaleString('zh-CN')}`;
    const targetShops = automationItemShopIds(detailId);
    const targetShopId = automationState.targetMode === 'personnel' ? targetShops[0] : shopId;
    const claim = (item.claims || []).find((entry) => Number(entry.shopId) === targetShopId)
      || (!targetShopId && automationState.targetMode !== 'personnel' ? (item.claims || []).find((entry) => entry.status === 'claimed') : null);
    const note = document.createElement('span');
    const claimedCount = targetShops.filter(id => (item.claims || []).some(entry => Number(entry.shopId) === id && entry.status === 'claimed')).length;
    note.textContent = targetShops.length > 1 ? `选中店铺已同步 ${claimedCount}/${targetShops.length} 家` : claim?.status === 'claimed' ? '该目标店铺已同步' : claim?.status === 'failed'
      ? `上次同步失败：${claim.error || '请稍后重试'}` : '完整资料已保存到本机 · 等待同步到店铺';
    content.append(title, meta, note);
    if (automationState.targetMode === 'personnel') {
      const owner = document.createElement('small');
      owner.textContent = `所属人员：${automationPersonnelReady() ? automationPersonnelLabel(automationState.routing.owners?.[detailId]) : '待刷新核对'} · ${targetShops.length ? `绑定 ${targetShops.length} 家店铺` : '将跳过（未知归属或未绑定）'}`;
      content.append(owner);
    }
    const actions = document.createElement('div'); actions.className = 'automation-item-actions';
    const status = document.createElement('span');
    const allClaimed = automationItemClaimStatus(item) === 'claimed';
    status.className = `status-pill ${allClaimed ? 'success' : 'neutral'}`;
    status.textContent = allClaimed ? '已同步' : claimedCount ? '部分同步' : '待同步';
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'button subtle'; remove.textContent = '移出池子';
    remove.disabled = locked;
    remove.addEventListener('click', () => removeAutomationPoolItem(detailId));
    actions.append(status, remove); row.append(checkbox, image, content, actions); list.append(row);
  });
  automationState.visibleSelectableIds = visibleSelectableIds;
  updateAutomationControls();
}
async function loadAutomationPool() {
  if (window.sharedAutomation) return window.sharedAutomation.refresh('pool');
  const savedPrice = automationPreferences().price;
  if (savedPrice && !$('#automationSkuPrice').dataset.restored) {
    $('#automationSkuPrice').value = savedPrice;
    $('#automationSkuPrice').dataset.restored = 'true';
  }
  const list = $('#automationPoolList');
  if (!automationState.items.length) {
    list.replaceChildren();
    const loading = document.createElement('p'); loading.className = 'live-empty'; loading.textContent = '正在读取自动化池…'; list.append(loading);
  }
  try {
    const data = await apiGet('/api/automation/pool');
    automationState.items = data.items || [];
    const available = new Set(automationState.items.map((item) => Number(item.detailId)));
    automationState.selected = new Set([...automationState.selected].filter((id) => available.has(id)));
    renderAutomationPool();
  } catch (error) {
    automationState.items = [];
    automationState.selected.clear();
    $('#automationPoolCount').textContent = '—';
    setLiveError(list, `自动化池读取失败：${error.message}`);
    updateAutomationControls();
  }
}
async function loadAutomationShops() {
  const select = $('#automationClaimShop');
  const preferences = automationPreferences();
  const previous = new Set((selectedAutomationShopIds().length ? selectedAutomationShopIds()
    : preferences.shopIds || [preferences.shopId]).map(String));
  let shops = [...$('#liveShop').options].filter((option) => /^\d+$/.test(option.value))
    .map((option) => ({ id: option.value, label: option.textContent }));
  if (!shops.length) {
    try {
      const response = await apiGet('/api/shops');
      shops = (response.data?.shopList || []).map((shop) => ({
        id: String(shop.shopId), label: shop.shopNick || shop.platformShopName || `店铺 ${shop.shopId}`,
      }));
    } catch (error) {
      select.replaceChildren(); appendOption(select, '', `店铺加载失败：${error.message}`);
      updateAutomationControls();
      return;
    }
  }
  select.replaceChildren();
  shops.forEach((shop) => appendOption(select, shop.id, shop.label));
  [...select.options].forEach(option => { option.selected = previous.has(option.value); });
  updateAutomationControls();
  renderAutomationPool();
}
async function loadAutomationSites() {
  const select = $('#automationPublishSite');
  const previous = select.value || automationPreferences().site || '';
  let sites = [...$('#liveSite').options].filter((option) => /^[a-z0-9]+$/i.test(option.value))
    .map((option) => ({ key: option.value, label: option.textContent }));
  if (!sites.length) {
    try {
      const response = await apiGet('/api/sites');
      sites = (response.data?.publishSiteOptions || []).map((site) => ({
        key: site.key, label: `${site.value || site.key} (${site.key})`,
      }));
    } catch (error) {
      select.replaceChildren(); appendOption(select, '', `站点加载失败：${error.message}`);
      updateAutomationControls(); return;
    }
  }
  select.replaceChildren(); appendOption(select, '', '请选择发布站点');
  sites.filter((site) => site.key !== 'EU').forEach((site) => appendOption(select, site.key, site.label));
  if (sites.some((site) => site.key === previous)) select.value = previous;
  updateAutomationControls();
}
const automationStepNames = {
  queued: '排队', preflight: '配置核对', titles: 'AI 标题', claim: '店铺认领',
  freight: '运费模板', warehouse: '发货仓库', prepare: '商品编辑', save: '保存妙手',
  verify: '读回校验', publish: '提交发布', complete: '完成',
};
function allReadyPublishJobs() {
  return automationState.jobs.filter(Boolean).filter((job) => job.status === 'ready_publish');
}
function selectedPublishShopIds() {
  // Legacy single-shop preferences remain supported; [] explicitly means none.
  if (automationState.publishQueueShopIds !== undefined) return automationState.publishQueueShopIds;
  return automationState.publishQueueShopId ? [String(automationState.publishQueueShopId)] : null;
}
function changePublishQueueShops(select) {
  const chosen = [...select.selectedOptions].map(option => option.value);
  const ids = chosen.filter(Boolean);
  const wasAll = selectedPublishShopIds() === null;
  automationState.publishQueueShopIds = chosen.includes('') && (!wasAll || !ids.length) ? null : ids;
  automationState.selectedPublishJobIds.clear();
  saveAutomationPreferences();
  renderPublishQueue();
}
function visibleReadyPublishJobs() {
  const shopIds = selectedPublishShopIds();
  return allReadyPublishJobs().filter((job) => shopIds === null || shopIds.includes(String(job.shopId)));
}
function renderPublishQueueShopFilter(allJobs = allReadyPublishJobs()) {
  const select = $('#publishQueueShop');
  if (!select || typeof appendOption !== 'function' || typeof select.replaceChildren !== 'function') return;
  const previous = selectedPublishShopIds();
  const counts = new Map();
  allJobs.forEach((job) => {
    const shopId = String(job.shopId || '');
    if (shopId) counts.set(shopId, (counts.get(shopId) || 0) + 1);
  });
  select.replaceChildren();
  appendOption(select, '', `全部店铺（${allJobs.length}）`);
  // Keep exhausted/missing selected stores visible at zero, never widen to all.
  (previous || []).forEach(id => { if (!counts.has(id)) counts.set(id, 0); });
  [...counts.entries()].sort((left, right) => automationShopName(left[0]).localeCompare(automationShopName(right[0]), 'zh-CN'))
    .forEach(([shopId, count]) => appendOption(select, shopId, `${automationShopName(shopId)}（${count}）`));
  [...select.options].forEach(option => { option.selected = previous === null ? option.value === '' : previous.includes(option.value); });
}
function renderPublishProgress() {
  const progress = automationState.publishProgress;
  const panel = $('#publishProgress');
  panel.hidden = !progress;
  const active = progress?.status === 'running';
  const button = $('#publishReadyAutomationJobs');
  const rawCount = String($('#publishQueuePublishCount').value || '').trim();
  button.disabled = automationState.removingPublishJobs || automationState.publishStarting || active;
  button.textContent = automationState.publishStarting ? '正在启动…' : active ? '正在发布…' : rawCount ? `发布前 ${rawCount} 件` : '发布队列商品';
  $('#publishQueuePublishInterval').disabled = automationState.publishStarting || active;
  $('#publishQueuePublishCount').disabled = automationState.publishStarting || active;
  $('#publishQueueShop').disabled = automationState.publishStarting || active;
  if (!progress) return;
  const percent = progress.total ? Math.min(100, Math.floor(progress.handled * 100 / progress.total)) : 0;
  $('#publishProgressTitle').textContent = active ? '发布进行中' : progress.status === 'interrupted' ? '发布已中断' :
    progress.needsReview ? '本轮处理结束，有任务需核查' : '本轮处理完成';
  $('#publishProgressCount').textContent = `处理进度 ${progress.handled}/${progress.total}（${percent}%）`;
  const bar = $('#publishProgressBar'); bar.max = progress.total || 1; bar.value = progress.handled;
  bar.setAttribute('aria-valuetext', `本轮共 ${progress.total} 件，已处理 ${progress.handled} 件`);
  $('#publishProgressSummary').textContent = `已提交 ${progress.submitted} · 失败/需核查 ${progress.needsReview} · 已移出 ${progress.removed} · 剩余 ${progress.remaining}`;
  $('#publishProgressCurrent').textContent = automationState.publishProgressStale ? '进度更新暂时失败，显示上次结果，正在重新连接…' :
    active ? (progress.currentDetailId ? `正在提交采集箱 ID ${progress.currentDetailId}` :
      progress.remaining ? `等待下一件，提交间隔 ${progress.intervalSeconds} 秒` : '正在汇总本轮结果…') :
    progress.status === 'interrupted' ? '后台任务已停止；请先核查不确定的提交，剩余商品不会自动发布。' :
      '“已提交”表示妙手已接受任务，最终上架结果请到发布记录核对。';
}
function selectAllPublishQueue() {
  if (automationState.removingPublishJobs) return;
  const ids = visibleReadyPublishJobs().slice(0, 100).map(job => job.jobId);
  const allSelected = ids.length && ids.every(id => automationState.selectedPublishJobIds.has(id));
  automationState.selectedPublishJobIds = new Set(allSelected ? [] : ids);
  renderPublishQueue();
}
async function removePublishQueueJobs(jobIds) {
  if (automationState.removingPublishJobs) return;
  const readyIds = new Set(visibleReadyPublishJobs().map(job => job.jobId));
  const ids = [...new Set(jobIds)].filter(id => readyIds.has(id));
  if (!ids.length) return;
  if (ids.length > 100) { automationJobFeedback('一次最多删除 100 件待发布商品。', 'error'); return; }
  if (!window.confirm(`从发布队列删除 ${ids.length} 件商品？仅移除待发布任务，保留妙手商品和保存记录。已开始发布的任务不能删除。`)) return;
  automationState.removingPublishJobs = true;
  renderPublishQueue();
  try {
    const response = await fetch('/api/automation/publish-queue', {
      method: 'DELETE', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({jobIds: ids, confirmation: 'REMOVE_PUBLISH_QUEUE_ITEMS'}),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || '删除失败');
    (body.data?.jobIds || []).forEach(id => automationState.selectedPublishJobIds.delete(id));
    automationJobFeedback(`已从发布队列删除 ${body.data.removed} 件商品，妙手商品和保存记录仍保留。`, 'success');
  } catch (error) { automationJobFeedback(`删除发布队列失败：${error.message}`, 'error'); }
  finally {
    await loadAutomationJobs();
    automationState.removingPublishJobs = false;
    renderPublishQueue();
  }
}
function renderPublishQueue() {
  const list = $('#publishQueueList');
  const allJobs = allReadyPublishJobs();
  renderPublishQueueShopFilter(allJobs);
  const jobs = visibleReadyPublishJobs();
  const readyIds = new Set(jobs.map(job => job.jobId));
  automationState.selectedPublishJobIds = new Set([...automationState.selectedPublishJobIds].filter(id => readyIds.has(id)));
  const selectAll = $('#selectAllPublishQueue');
  const allSelected = jobs.length > 0 && jobs.slice(0, 100).every(job => automationState.selectedPublishJobIds.has(job.jobId));
  selectAll.textContent = allSelected ? '取消全选' : jobs.length > 100 ? '全选前 100 件' : '全选队列';
  selectAll.disabled = !jobs.length || automationState.removingPublishJobs;
  const removeBatch = $('#removePublishQueueBatch');
  removeBatch.textContent = automationState.removingPublishJobs ? '正在删除…' : `批量删除（${automationState.selectedPublishJobIds.size}）`;
  removeBatch.disabled = !automationState.selectedPublishJobIds.size || automationState.removingPublishJobs;
  renderPublishProgress();
  const badge = $('#publishQueueBadge'); badge.textContent = String(allJobs.length); badge.classList.toggle('hidden', allJobs.length === 0);
  list.replaceChildren();
  if (!jobs.length) {
    const empty = document.createElement('p'); empty.className = 'automation-empty';
    empty.textContent = selectedPublishShopIds() !== null ? '所选店铺暂无待发布商品，请选择有待发布商品的店铺。' : '暂无待发布商品。编辑并保存成功的商品会自动进入这里。';
    list.append(empty); return;
  }
  jobs.forEach((job) => {
    const row = document.createElement('article'); row.className = 'automation-item publish-queue-item';
    const image = document.createElement('div'); image.className = 'automation-item-image';
    if (/^https?:\/\//i.test(job.thumbnail || '')) {
      const picture = document.createElement('img'); picture.src = job.thumbnail;
      picture.alt = job.title ? `${job.title}的商品图片` : `采集箱 ID ${job.detailId} 的商品图片`;
      picture.loading = 'lazy'; picture.addEventListener('error', () => { picture.remove(); image.textContent = '无图'; }); image.append(picture);
    } else image.textContent = '无图';
    const content = document.createElement('div'); content.className = 'automation-item-content';
    const title = document.createElement('strong'); title.textContent = job.title || `采集箱 ID ${job.detailId}`;
    const meta = document.createElement('small');
    meta.textContent = `采集箱 ID ${job.detailId} · ${automationShopName(job.shopId)} · ${job.site} · 申报价 ${job.price} 元`;
    const detail = document.createElement('small'); detail.textContent = '已保存妙手 · 将按上方发布间隔依次提交';
    content.append(title, meta, detail);
    const status = document.createElement('span'); status.className = 'status-pill neutral'; status.textContent = '待统一发布';
    const actions = document.createElement('div'); actions.className = 'publish-queue-item-actions';
    const remove = document.createElement('button'); remove.className = 'button subtle'; remove.type = 'button';
    remove.textContent = '删除'; remove.disabled = automationState.removingPublishJobs;
    remove.addEventListener('click', () => removePublishQueueJobs([job.jobId]));
    const select = document.createElement('input'); select.type = 'checkbox'; select.className = 'automation-job-select';
    select.checked = automationState.selectedPublishJobIds.has(job.jobId); select.disabled = automationState.removingPublishJobs;
    select.setAttribute('aria-label', `选择删除待发布商品 ${job.detailId}`);
    select.addEventListener('change', () => {
      if (select.checked) automationState.selectedPublishJobIds.add(job.jobId);
      else automationState.selectedPublishJobIds.delete(job.jobId);
      renderPublishQueue();
    });
    actions.append(status, remove, select);
    row.append(image, content, actions); list.append(row);
  });
}
function renderAutomationJobs() {
  const list = $('#automationJobList');
  const previousChoices = new Map([...list.querySelectorAll('select[data-job-id]')]
    .map((select) => [select.dataset.jobId, select.value]));
  list.replaceChildren();
  const submitted = automationState.jobs.length;
  const badge = $('#taskBadge'); badge.textContent = String(submitted); badge.classList.toggle('hidden', submitted === 0);
  const savedCount = automationState.jobs.filter(job => job && automationJobSaved(job)).length;
  $('#savedTaskCount').textContent = String(savedCount);
  $('#unsavedTaskCount').textContent = String(submitted - savedCount);
  $$('[data-save-filter]').forEach(button => {
    const selected = button.dataset.saveFilter === automationSaveFilter;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  renderPublishQueue();
  const visibleJobs = visibleAutomationJobs();
  if (!visibleJobs.length) {
    const empty = document.createElement('p'); empty.className = 'automation-empty';
    empty.textContent = !submitted ? '还没有提交记录。' : automationSaveFilter === 'saved' ? '暂无成功保存的记录。' : '暂无未成功的任务。';
    list.append(empty); updateAutomationJobBatchControls(); return;
  }
  visibleJobs.forEach((job) => {
    const card = document.createElement('article'); card.className = 'automation-job';
    const terminal = automationTerminalStatuses.has(job.status);
    const expanded = automationState.jobRecordExpanded.has(job.jobId)
      ? automationState.jobRecordExpanded.get(job.jobId) : !terminal && !automationJobSaved(job);
    const overview = document.createElement('div'); overview.className = 'automation-job-overview automation-job-overview-toggle';
    overview.tabIndex = 0; overview.setAttribute('role', 'button');
    overview.setAttribute('aria-expanded', String(expanded));
    const thumbnail = document.createElement('div'); thumbnail.className = 'automation-job-thumbnail';
    if (/^https?:\/\//i.test(job.thumbnail || '')) {
      const image = document.createElement('img'); image.src = job.thumbnail;
      image.alt = job.title ? `${job.title}的商品图片` : `采集箱 ID ${job.detailId} 的商品图片`;
      image.loading = 'lazy';
      image.addEventListener('error', () => { image.remove(); thumbnail.textContent = '无图'; });
      thumbnail.append(image);
    } else thumbnail.textContent = '无图';
    const overviewText = document.createElement('div'); overviewText.className = 'automation-job-overview-text';
    const head = document.createElement('div'); head.className = 'automation-job-head';
    const title = document.createElement('strong');
    title.textContent = `采集箱 ID ${job.detailId} · ${automationShopName(job.shopId)} · ${job.site}`;
    const status = document.createElement('span');
    status.className = `status-pill ${job.status === 'done' ? 'success' : ['failed', 'interrupted', 'needs_review'].includes(job.status) ? 'failed' : 'neutral'}`;
    status.textContent = job.status === 'done' ? '已提交妙手' : job.status === 'queued' ? '排队中' : job.status === 'running' ? '编辑保存中' : job.status === 'ready_publish' ? '待统一发布' : job.status === 'needs_input' ? '待你选择' : job.status === 'needs_review' ? '重试失败·需核查' : job.status === 'interrupted' ? '已中断' : job.status === 'canceled' ? '已结束' : '失败';
    const headActions = document.createElement('div'); headActions.className = 'automation-job-head-actions';
    headActions.append(status);
    if (canCancelEditAutomationJob(job)) {
      const cancel = document.createElement('button'); cancel.type = 'button';
      cancel.className = 'button subtle automation-job-cancel';
      cancel.disabled = automationState.cancelingJobIds.has(job.jobId);
      cancel.textContent = cancel.disabled ? '正在停止…' : '停止此任务';
      cancel.setAttribute('aria-label', `停止采集箱 ID ${job.detailId} 的编辑保存任务`);
      cancel.addEventListener('click', () => cancelEditAutomationJob(job.jobId));
      headActions.append(cancel);
    }
    let batchSelect = null;
    if (terminal) {
      const select = document.createElement('input'); select.type = 'checkbox';
      select.className = 'automation-job-select'; select.checked = automationState.selectedJobIds.has(job.jobId);
      select.setAttribute('aria-label', `选择采集箱 ID ${job.detailId} 的任务记录`);
      select.addEventListener('change', () => {
        if (select.checked) automationState.selectedJobIds.add(job.jobId);
        else automationState.selectedJobIds.delete(job.jobId);
        updateAutomationJobBatchControls();
      });
      batchSelect = select;
      const remove = document.createElement('button'); remove.type = 'button';
      remove.className = 'button subtle automation-job-delete'; remove.textContent = '删除记录';
      remove.setAttribute('aria-label', `删除采集箱 ID ${job.detailId} 的任务记录`);
      remove.addEventListener('click', () => deleteAutomationJob(job.jobId));
      headActions.append(remove);
    }
    if (job.status === 'needs_input' && job.step === 'freight' && job.choices?.freight?.options?.length) {
      const selectFreight = document.createElement('input'); selectFreight.type = 'checkbox';
      selectFreight.className = 'automation-job-select';
      selectFreight.checked = automationState.selectedFreightJobIds.has(job.jobId);
      selectFreight.disabled = automationState.batchFreightUpdating || automationState.resumingJobIds.has(job.jobId);
      selectFreight.setAttribute('aria-label', `选择采集箱 ID ${job.detailId} 批量修改运费模板`);
      selectFreight.addEventListener('change', () => {
        if (selectFreight.checked) automationState.selectedFreightJobIds.add(job.jobId);
        else automationState.selectedFreightJobIds.delete(job.jobId);
        updateAutomationJobBatchControls();
      });
      headActions.append(selectFreight);
    }
    if (job.status === 'needs_input' && job.step === 'warehouse' && job.choices?.warehouse?.options?.length) {
      const selectWarehouse = document.createElement('input'); selectWarehouse.type = 'checkbox';
      selectWarehouse.className = 'automation-job-select';
      selectWarehouse.checked = automationState.selectedWarehouseJobIds.has(job.jobId);
      selectWarehouse.disabled = automationState.batchWarehouseUpdating || automationState.resumingJobIds.has(job.jobId);
      selectWarehouse.setAttribute('aria-label', `选择采集箱 ID ${job.detailId} 批量选择发货仓库`);
      selectWarehouse.addEventListener('change', () => {
        if (selectWarehouse.checked) automationState.selectedWarehouseJobIds.add(job.jobId);
        else automationState.selectedWarehouseJobIds.delete(job.jobId);
        updateAutomationJobBatchControls();
      });
      headActions.append(selectWarehouse);
    }
    if (['needs_input', 'failed', 'needs_review', 'interrupted'].includes(job.status)) {
      const edit = document.createElement('button'); edit.type = 'button';
      edit.className = 'button subtle automation-job-edit'; edit.textContent = '编辑商品';
      edit.setAttribute('aria-label', `编辑采集箱 ID ${job.detailId} 的商品`);
      edit.addEventListener('click', () => editAutomationJob(job));
      headActions.prepend(edit);
    }
    if (['failed', 'needs_review', 'interrupted'].includes(job.status)) {
      const retry = document.createElement('button'); retry.type = 'button';
      retry.className = 'button primary automation-job-retry';
      retry.disabled = automationState.batchRetrying || automationState.batchRestoring || automationState.retryingJobIds.has(job.jobId);
      retry.textContent = retry.disabled ? '正在重试…' : '重试';
      retry.setAttribute('aria-label', `重试采集箱 ID ${job.detailId} 的编辑保存任务`);
      retry.addEventListener('click', () => retryAutomationJob(job));
      headActions.prepend(retry);
    }
    if (batchSelect) headActions.append(batchSelect);
    head.append(title, headActions); overviewText.append(head);
    if (job.title) {
      const productTitle = document.createElement('div'); productTitle.className = 'automation-job-product-title';
      productTitle.textContent = job.title; overviewText.append(productTitle);
    }
    const time = document.createElement('small'); time.textContent = `提交于 ${new Date(job.createdAt).toLocaleString('zh-CN')}`;
    overviewText.append(time); overview.append(thumbnail, overviewText); card.append(overview);
    const current = document.createElement('div'); current.className = `automation-current-problem ${job.status}`;
    current.textContent = `当前阶段：${automationStepNames[job.step] || job.step} · ${job.message}`;
    card.append(current);
    const details = document.createElement('div'); details.className = 'automation-job-details';
    details.id = `automation-job-details-${job.jobId}`;
    details.hidden = !expanded;
    overview.setAttribute('aria-controls', details.id);
    const toggleDetails = () => {
      const next = details.hidden;
      details.hidden = !next;
      automationState.jobRecordExpanded.set(job.jobId, next);
      overview.setAttribute('aria-expanded', String(next));
    };
    overview.addEventListener('click', (event) => {
      if (event.target.closest('button, a, input, select, label')) return;
      toggleDetails();
    });
    overview.addEventListener('keydown', (event) => {
      if (!['Enter', ' '].includes(event.key)) return;
      event.preventDefault(); toggleDetails();
    });
    if (job.status === 'needs_input') {
      const controls = document.createElement('div'); controls.className = 'automation-resume-controls';
      const deletedFromCollectBox = String(job.message || '').includes('采集箱中已被删除');
      const choice = job.choices?.[job.step];
      let select = null;
      if (!deletedFromCollectBox && choice?.options?.length) {
        select = document.createElement('select'); select.setAttribute('aria-label', `选择${automationStepNames[job.step] || '配置'}`);
        select.dataset.jobId = job.jobId;
        appendOption(select, '', `请选择${automationStepNames[job.step] || '配置'}`);
        choice.options.forEach((option) => appendOption(select, option.id, option.name));
        if ([...select.options].some((option) => option.value === previousChoices.get(job.jobId)))
          select.value = previousChoices.get(job.jobId);
        controls.append(select);
      }
      const button = document.createElement('button'); button.type = 'button'; button.className = 'button primary';
      button.disabled = automationState.batchRetrying || automationState.batchRestoring || automationState.resumingJobIds.has(job.jobId);
      button.textContent = button.disabled ? '正在恢复…' : deletedFromCollectBox ? '恢复到采集箱并重新保存' : select ? '确认选择并继续' : '已处理问题，重新检查';
      button.addEventListener('click', () => deletedFromCollectBox
        ? restoreDeletedAutomationJob(job) : resumeAutomationJob(job.jobId, select?.value || ''));
      controls.append(button);
      const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'button subtle';
      cancel.textContent = '结束任务并重新选择';
      cancel.disabled = automationState.batchRetrying || automationState.batchRestoring || automationState.resumingJobIds.has(job.jobId);
      cancel.addEventListener('click', () => cancelPausedAutomationJob(job.jobId));
      controls.append(cancel); details.append(controls);
    }
    const progress = document.createElement('progress'); progress.max = 8;
    progress.value = new Set((job.events || []).filter((event) => event.status === 'done').map((event) => event.step)).size - 1;
    progress.value = Math.max(0, Math.min(8, progress.value)); details.append(progress);
    const events = document.createElement('ol'); events.className = 'automation-job-events';
    (job.events || []).filter((event) => event.step !== 'queued').forEach((event) => {
      const row = document.createElement('li'); row.className = `automation-event-${event.status}`;
      row.textContent = `${automationStepNames[event.step] || event.step} · ${event.status === 'running' ? '进行中' : event.status === 'done' ? '完成' : event.status === 'ready_publish' ? '待统一发布' : event.status === 'needs_input' ? '待处理' : event.status === 'needs_review' ? '需核查' : '失败'} · ${event.message}`;
      events.append(row);
    });
    details.append(events); card.append(details); list.append(card);
  });
  updateAutomationJobBatchControls();
}
async function resumeAutomationJob(jobId, selectedId) {
  if (automationState.batchRetrying || automationState.resumingJobIds.has(jobId)) return;
  const job = automationState.jobs.find((entry) => entry.jobId === jobId);
  if (!job) return;
  if (job.choices?.[job.step]?.options?.length && !selectedId) {
    automationJobFeedback('请先选择一个可用配置。', 'error'); return;
  }
  automationState.resumingJobIds.add(jobId);
  renderAutomationJobs();
  try {
    const response = await fetch('/api/automation/resume', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jobId, selectedId, confirmation: 'RESUME_AUTOMATION_JOB' }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || '继续任务失败');
    automationJobFeedback(`采集箱 ID ${job.detailId} 已继续编辑保存，可以同时继续其他商品；完成后进入发布队列等待统一发布。`, 'success');
    await loadAutomationJobs();
  } catch (error) { automationJobFeedback(`采集箱 ID ${job.detailId} 继续失败：${error.message}`, 'error'); }
  finally {
    automationState.resumingJobIds.delete(jobId);
    renderAutomationJobs();
  }
}
async function batchUpdateFreightTemplates() {
  if (automationState.batchFreightUpdating) return;
  const jobs = selectedBatchFreightJobs();
  const selectedId = String($('#batchFreightTemplate')?.value || '').trim();
  const option = sharedBatchFreightOptions(jobs).find((entry) => entry.id === selectedId);
  if (!jobs.length || !option) {
    automationJobFeedback('请勾选商品并选择它们共同可用的运费模板。', 'error'); return;
  }
  if (!window.confirm(`将为所选 ${jobs.length} 件商品设置运费模板“${option.name}”，并继续编辑保存；不会自动提交发布。是否继续？`)) return;
  automationState.batchFreightUpdating = true;
  const waiting = [...jobs];
  const failures = [];
  const succeeded = new Set();
  automationJobFeedback(`正在为 ${jobs.length} 件商品批量设置运费模板并继续保存…`, 'info');
  renderAutomationJobs();
  try {
    await Promise.all(Array.from({ length: Math.min(4, jobs.length) }, async () => {
      while (waiting.length) {
        const job = waiting.shift();
        automationState.resumingJobIds.add(job.jobId);
        try {
          const response = await fetch('/api/automation/resume', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ jobId: job.jobId, selectedId, confirmation: 'RESUME_AUTOMATION_JOB' }),
          });
          const body = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(body.error || '继续保存失败');
          succeeded.add(job.jobId);
        } catch (error) { failures.push(`采集箱 ID ${job.detailId}：${error.message}`); }
        finally { automationState.resumingJobIds.delete(job.jobId); }
      }
    }));
    succeeded.forEach((jobId) => automationState.selectedFreightJobIds.delete(jobId));
    await loadAutomationJobs();
    automationJobFeedback(`批量修改运费模板：已继续 ${succeeded.size}/${jobs.length} 件商品的编辑保存，完成后进入发布队列，不会自动发布。`
      + (failures.length ? ` 未继续：${failures.join('；')}` : ''), failures.length ? 'error' : 'success');
  } finally {
    automationState.batchFreightUpdating = false;
    renderAutomationJobs();
  }
}
async function batchUpdateWarehouses() {
  if (automationState.batchWarehouseUpdating) return;
  const jobs = selectedBatchWarehouseJobs();
  const selectedId = String($('#batchWarehouse')?.value || '').trim();
  const option = sharedBatchWarehouseOptions(jobs).find((entry) => entry.id === selectedId);
  if (!jobs.length || !option) {
    automationJobFeedback('请勾选商品并选择它们共同可用的发货仓库。', 'error'); return;
  }
  if (!window.confirm(`将为所选 ${jobs.length} 件商品设置发货仓库“${option.name}”，并继续编辑保存；不会自动提交发布。是否继续？`)) return;
  automationState.batchWarehouseUpdating = true;
  const waiting = [...jobs];
  const failures = [];
  const succeeded = new Set();
  automationJobFeedback(`正在为 ${jobs.length} 件商品批量设置发货仓库并继续保存…`, 'info');
  renderAutomationJobs();
  try {
    await Promise.all(Array.from({ length: Math.min(4, jobs.length) }, async () => {
      while (waiting.length) {
        const job = waiting.shift();
        automationState.resumingJobIds.add(job.jobId);
        try {
          const response = await fetch('/api/automation/resume', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ jobId: job.jobId, selectedId, confirmation: 'RESUME_AUTOMATION_JOB' }),
          });
          const body = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(body.error || '继续保存失败');
          succeeded.add(job.jobId);
        } catch (error) { failures.push(`采集箱 ID ${job.detailId}：${error.message}`); }
        finally { automationState.resumingJobIds.delete(job.jobId); }
      }
    }));
    succeeded.forEach((jobId) => automationState.selectedWarehouseJobIds.delete(jobId));
    await loadAutomationJobs();
    automationJobFeedback(`批量选择发货仓库：已继续 ${succeeded.size}/${jobs.length} 件商品的编辑保存，完成后进入发布队列，不会自动发布。`
      + (failures.length ? ` 未继续：${failures.join('；')}` : ''), failures.length ? 'error' : 'success');
  } finally {
    automationState.batchWarehouseUpdating = false;
    renderAutomationJobs();
  }
}
async function restoreDeletedAutomationJob(job) {
  if (automationState.batchRetrying || automationState.batchRestoring || automationState.resumingJobIds.has(job.jobId)) return;
  if (!window.confirm(`将用本机快照重新创建“${job.title || `采集箱 ID ${job.detailId}`}”到妙手采集箱，并以原店铺、站点和申报价重新编辑保存。会生成新的采集箱 ID，保存后仍需你在发布队列手动统一发布。是否继续？`)) return;
  automationState.resumingJobIds.add(job.jobId);
  renderAutomationJobs();
  try {
    const response = await fetch('/api/automation/restore-deleted', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jobId: job.jobId, confirmation: 'RESTORE_DELETED_AUTOMATION_ITEM' }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || '恢复失败');
    automationJobFeedback(`已恢复为新的采集箱 ID ${body.data.detailId}，正在重新编辑保存；完成后会进入发布队列。`, 'success');
    await Promise.all([loadAutomationJobs(), loadAutomationPool()]);
  } catch (error) { automationJobFeedback(`恢复采集箱商品失败：${error.message}`, 'error'); }
  finally {
    automationState.resumingJobIds.delete(job.jobId);
    renderAutomationJobs();
  }
}
async function restoreDeletedAutomationJobsBatch() {
  if (automationState.batchRetrying || automationState.batchRestoring) return;
  const jobs = deletedCollectBoxRestoreCandidates();
  if (!jobs.length) return;
  if (!window.confirm(`将从本机快照恢复 ${jobs.length} 件已被妙手采集箱删除的商品，并按各自原店铺、站点和申报价重新编辑保存。每件都会生成新的采集箱 ID；保存完成后仍需你手动统一发布。是否继续？`)) return;
  automationState.batchRestoring = true;
  setAutomationSaveFilter('unsaved');
  const waiting = [...jobs];
  const failures = [];
  let restored = 0;
  automationJobFeedback(`正在恢复 ${jobs.length} 件采集箱商品；妙手接口会按安全频率逐件创建，编辑保存将自动排队。`, 'info');
  try {
    await Promise.all(Array.from({ length: Math.min(4, jobs.length) }, async () => {
      while (waiting.length) {
        const job = waiting.shift();
        automationState.resumingJobIds.add(job.jobId);
        try {
          const response = await fetch('/api/automation/restore-deleted', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ jobId: job.jobId, confirmation: 'RESTORE_DELETED_AUTOMATION_ITEM' }),
          });
          const body = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(body.error || '恢复失败');
          restored += 1;
        } catch (error) { failures.push(`采集箱 ID ${job.detailId}：${error.message}`); }
        finally { automationState.resumingJobIds.delete(job.jobId); }
      }
    }));
    await Promise.all([loadAutomationJobs(), loadAutomationPool()]);
    automationJobFeedback(`一键恢复：已创建并重新保存 ${restored}/${jobs.length} 件商品，完成后会进入发布队列。`
      + (failures.length ? ` 未恢复：${failures.join('；')}` : ''), failures.length ? 'error' : 'success');
  } finally {
    automationState.batchRestoring = false;
    renderAutomationJobs();
  }
}
async function cancelPausedAutomationJob(jobId) {
  if (!window.confirm('结束这次暂停的自动任务？进度记录会保留，之后可以重新选择商品、店铺和站点。')) return;
  try {
    const response = await fetch('/api/automation/cancel', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jobId, confirmation: 'CANCEL_PAUSED_AUTOMATION' }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || '结束任务失败');
    automationJobFeedback('暂停任务已结束，可以回到自动化池重新选择店铺或站点。', 'success');
    await loadAutomationJobs();
  } catch (error) { automationJobFeedback(`结束任务失败：${error.message}`, 'error'); }
}
async function cancelEditAutomationJob(jobId) {
  const job = automationState.jobs.find((entry) => entry.jobId === jobId);
  if (!job || !canCancelEditAutomationJob(job) || automationState.cancelingJobIds.has(jobId)) return;
  if (!window.confirm(`停止采集箱 ID ${job.detailId} 的编辑保存任务？此操作不会撤销或影响发布队列。`)) return;
  automationState.cancelingJobIds.add(jobId);
  renderAutomationJobs();
  try {
    const response = await fetch('/api/automation/cancel-edit', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jobId, confirmation: 'CANCEL_EDIT_AUTOMATION_JOB' }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || '停止任务失败');
    automationState.jobs = automationState.jobs.map((entry) => entry.jobId === jobId ? body.data : entry);
    automationJobFeedback(`采集箱 ID ${job.detailId} 的编辑保存任务已停止；发布队列未受影响。`, 'success');
    await loadAutomationJobs();
  } catch (error) { automationJobFeedback(`停止任务失败：${error.message}`, 'error'); }
  finally {
    automationState.cancelingJobIds.delete(jobId);
    renderAutomationJobs();
  }
}
async function deleteAutomationJob(jobId) {
  const job = automationState.jobs.find((entry) => entry.jobId === jobId);
  if (!job || !automationTerminalStatuses.has(job.status)) return;
  if (!window.confirm(`从任务中心删除采集箱 ID ${job.detailId} 的这条记录？这不会删除妙手商品或撤销发布；本机会保留防重复提交的安全记录。`)) return;
  try {
    const response = await fetch(`/api/automation/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || '删除记录失败');
    automationState.jobs = automationState.jobs.filter((entry) => entry.jobId !== jobId);
    renderAutomationJobs();
    automationJobFeedback('任务记录已从任务中心移除；妙手商品和发布状态未改变。', 'success');
    await loadAutomationJobs();
  } catch (error) { automationJobFeedback(`删除记录失败：${error.message}`, 'error'); }
}
async function deleteSelectedAutomationJobs() {
  const jobIds = [...automationState.selectedJobIds].filter((jobId) =>
    visibleAutomationJobs().some((job) => job.jobId === jobId && automationTerminalStatuses.has(job.status)));
  if (!jobIds.length) return;
  if (!window.confirm(`从任务中心删除所选 ${jobIds.length} 条记录？这不会删除妙手商品或撤销已提交发布。`)) return;
  try {
    const response = await fetch('/api/automation/jobs/batch', {
      method: 'DELETE', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jobIds, confirmation: 'DELETE_AUTOMATION_JOB_RECORDS' }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || '批量删除记录失败');
    const deleted = new Set(body.data?.jobIds || []);
    automationState.selectedJobIds.clear();
    automationState.jobs = automationState.jobs.filter((job) => !deleted.has(job.jobId));
    renderAutomationJobs();
    automationJobFeedback(`已从任务中心删除 ${deleted.size} 条记录；妙手商品和发布状态未改变。`, 'success');
    await loadAutomationJobs();
  } catch (error) { automationJobFeedback(`批量删除记录失败：${error.message}`, 'error'); }
}
async function clearAutomationJobs() {
  if (automationState.clearingJobs || !clearableAutomationJobs().length) return;
  const confirmed = window.confirm(
    '将停止所有尚未完成的编辑保存任务，并清空本地编辑记录。发布队列会保持不变，仍会继续发布。\n\n'
    + '正在进行中的一条妙手请求可能会先结束，随后不会再继续下一步。确定继续吗？'
  );
  if (!confirmed) return;
  automationState.clearingJobs = true;
  renderAutomationJobs();
  try {
    const response = await fetch('/api/automation/jobs/clear', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmation: 'STOP_AND_CLEAR_AUTOMATION_JOBS' }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || '停止并清空任务列表失败');
    const cleared = Number(body.data?.cleared || 0);
    const stopped = Number(body.data?.stopped || 0);
    automationState.jobs = automationState.jobs.filter(isPublishingAutomationJob);
    automationState.selectedJobIds.clear();
    automationState.selectedFreightJobIds.clear();
    automationState.selectedWarehouseJobIds.clear();
    automationState.selectedPublishJobIds.clear();
    automationState.jobRecordExpanded.clear();
    renderAutomationJobs();
    automationJobFeedback(`已清空 ${cleared} 条编辑记录，并请求停止 ${stopped} 个编辑任务；发布队列保持不变。`, 'success');
    await loadAutomationJobs();
  } catch (error) { automationJobFeedback(`停止并清空任务列表失败：${error.message}`, 'error'); }
  finally {
    automationState.clearingJobs = false;
    renderAutomationJobs();
  }
}
function editAutomationJob(job) {
  if (typeof window.openEditorForAutomationTask !== 'function') {
    automationJobFeedback('编辑器尚未加载完成，请刷新页面后再试。', 'error'); return;
  }
  window.openEditorForAutomationTask(job);
}
async function retryAutomationJob(job) {
  if (automationState.batchRetrying || automationState.retryingJobIds.has(job.jobId)) return;
  automationState.retryingJobIds.add(job.jobId);
  renderAutomationJobs();
  try {
    const response = await fetch('/api/automation/retry', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jobId: job.jobId, confirmation: 'RETRY_AUTOMATION_SAVE' }),
    });
    if (response.status === 404) throw new Error('当前后台尚未更新，请重启本地后端服务后重试');
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || '重试失败');
    automationState.jobs = automationState.jobs.map(item => item.jobId === job.jobId ? body.data : item);
    automationState.selectedJobIds.delete(job.jobId);
    automationState.jobRecordExpanded.set(job.jobId, true);
    automationJobFeedback(`采集箱 ID ${job.detailId} 已开始重新编辑保存，完成后进入发布队列，等待你发布。`, 'success');
    await loadAutomationJobs();
  } catch (error) { automationJobFeedback(`重试失败：${error.message}`, 'error'); }
  finally {
    automationState.retryingJobIds.delete(job.jobId);
    renderAutomationJobs();
  }
}
async function retryAutomationJobsBatch() {
  if (automationState.batchRetrying || automationState.batchRestoring) return;
  const jobs = batchRetryCandidates();
  if (!jobs.length) return;
  automationState.batchRetrying = true;
  setAutomationSaveFilter('unsaved');
  const failures = [];
  let started = 0;
  const waiting = [...jobs];
  automationJobFeedback(`正在提交 ${jobs.length} 个编辑保存重试任务；运行中、待选择配置及重复记录已跳过。`, 'info');
  try {
    // Bound submission requests, not edit concurrency. Each accepted job runs independently.
    await Promise.all(Array.from({ length: Math.min(4, jobs.length) }, async () => {
      while (waiting.length) {
        const job = waiting.shift();
        const resume = job.status === 'needs_input';
        const pending = resume ? automationState.resumingJobIds : automationState.retryingJobIds;
        pending.add(job.jobId);
        try {
          const response = await fetch(resume ? '/api/automation/resume' : '/api/automation/retry', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ jobId: job.jobId, ...(resume ? { selectedId: '' } : {}),
              confirmation: resume ? 'RESUME_AUTOMATION_JOB' : 'RETRY_AUTOMATION_SAVE' }),
          });
          const body = await response.json();
          if (!response.ok) throw new Error(body.error || '提交失败');
          started += 1;
          const existing = automationState.jobs.find(entry => entry.jobId === job.jobId);
          if (existing) Object.assign(existing, body.data || { status: 'running' });
        } catch (error) { failures.push(`采集箱 ID ${job.detailId}：${error.message}`); }
        finally { pending.delete(job.jobId); }
      }
    }));
    await loadAutomationJobs();
    automationJobFeedback(`批量重试：已启动 ${started}/${jobs.length} 个任务，保存完成后进入发布队列，不自动发布。`
      + (failures.length ? ` 未启动：${failures.join('；')}` : ''), failures.length ? 'error' : 'success');
  } finally {
    automationState.batchRetrying = false;
    renderAutomationJobs();
  }
}
async function publishReadyAutomationJobs() {
  if (automationState.removingPublishJobs || automationState.publishStarting || automationState.publishProgress?.status === 'running') return;
  const shopIds = selectedPublishShopIds();
  if (shopIds?.length === 0) { automationJobFeedback('请至少选择一个发布店铺。', 'error'); return; }
  if (shopIds?.length > 1 && !liveState.multiShopPublishing) {
    automationJobFeedback('后台尚未启用多店铺发布，请更新服务并刷新页面。', 'error'); return;
  }
  const intervalSeconds = Number($('#publishQueuePublishInterval').value);
  if (!Number.isInteger(intervalSeconds) || intervalSeconds < 1 || intervalSeconds > 600) {
    automationJobFeedback('发布间隔应为 1 到 600 秒。', 'error'); return;
  }
  const rawCount = String($('#publishQueuePublishCount').value || '').trim();
  const publishCount = rawCount ? Number(rawCount) : null;
  if (rawCount && (!Number.isSafeInteger(publishCount) || publishCount < 1)) {
    automationJobFeedback('本次发布数量应为大于 0 的整数，留空则发布全部队列。', 'error'); return;
  }
  automationState.publishStarting = true;
  renderPublishProgress();
  try {
    const response = await fetch('/api/automation/publish-ready', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmation: 'PUBLISH_READY_AUTOMATION_JOBS', intervalSeconds, publishCount,
        shopId: shopIds?.[0] || null, shopIds: shopIds === null ? null : shopIds.map(Number) }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || '统一发布失败');
    automationState.publishProgress = body.data?.publishProgress || null;
    automationState.publishProgressStale = false;
    renderPublishProgress();
    const count = body.data?.jobs?.length || 0;
    const scope = shopIds ? `所选 ${shopIds.length} 家店铺的` : '全部店铺的';
    automationJobFeedback(`已开始发布 ${count} 件${scope}已保存商品，将每隔 ${intervalSeconds} 秒依次提交。`, 'success');
    await loadAutomationJobs();
  } catch (error) { automationJobFeedback(`统一发布失败：${error.message}`, 'error'); }
  finally { automationState.publishStarting = false; renderPublishProgress(); }
}
async function loadAutomationJobs() {
  if (window.sharedAutomation) return window.sharedAutomation.refresh('jobs');
  if (automationState.jobsLoading) { automationState.jobsRefreshQueued = true; return; }
  automationState.jobsLoading = true;
  window.clearTimeout(loadAutomationJobs.timer);
  try {
    const data = await apiGet('/api/automation/jobs');
    automationState.publishProgress = data.publishProgress || null;
    automationState.publishProgressStale = false;
    renderPublishProgress();
    const jobs = (data.jobs || []).filter(Boolean);
    const changed = !automationState.jobsLoaded || JSON.stringify(jobs) !== JSON.stringify(automationState.jobs);
    const locked = (items) => items.filter((job) => ['queued', 'running', 'needs_input', 'ready_publish'].includes(job.status))
      .map((job) => job.detailId).sort((left, right) => left - right).join(',');
    const lockChanged = locked(jobs) !== locked(automationState.jobs);
    automationState.jobs = jobs;
    automationState.jobsLoaded = true;
    if (changed) renderAutomationJobs();
    if (lockChanged && $('#automation').classList.contains('active')) renderAutomationPool();
    loadAutomationJobShopNames();
  } catch (error) {
    automationState.publishProgressStale = true;
    renderPublishProgress();
    automationJobFeedback(`读取任务进度失败：${error.message}`, 'error');
  }
  finally {
    automationState.jobsLoading = false;
    const running = automationState.publishProgress?.status === 'running' || automationState.jobs.some((job) => ['queued', 'running'].includes(job.status));
    const delay = automationState.jobsRefreshQueued ? 0 : running ? 1500 : 5000;
    automationState.jobsRefreshQueued = false;
    loadAutomationJobs.timer = window.setTimeout(loadAutomationJobs, delay);
  }
}
async function addSelectedToAutomationPool() {
  if (liveState.poolTransferring) return;
  const selected = liveState.selected;
  if (!selected) { showToast('请先从采集箱选择一个商品'); return; }
  const detailId = Number(selected.collectBoxDetailId);
  const mode = selected.editModel === 'shop' ? 'shop' : 'site';
  const shopId = Number($('#liveShop').value || selected.collectBoxDetailShopList?.[0]?.shopId || 0);
  if (!Number.isInteger(detailId) || detailId <= 0 || (mode === 'shop' && (!Number.isInteger(shopId) || shopId <= 0))) {
    poolTransferStatus('商品或店铺信息不完整，无法加入自动化池。', 'error');
    return;
  }
  const button = $('#liveAddAutomation');
  liveState.poolTransferring = true;
  button.disabled = true; button.textContent = '正在传入池子…';
  poolTransferStatus('正在读取妙手完整商品资料并保存到本机自动化池…');
  try {
    const response = await fetch('/api/automation/pool', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ detailId, mode, shopId }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || '商品入池失败');
    const skipped = Boolean(body.data?.skipped);
    poolTransferStatus(`采集箱 ID ${detailId} ${skipped ? '已在自动化池中，已跳过' : '已加入自动化池'}。`, 'success');
    showToast(skipped ? '重复 ID 已跳过' : '商品完整资料已加入自动化池');
  } catch (error) {
    poolTransferStatus(`加入自动化池失败：${error.message}`, 'error');
    showToast(`加入自动化池失败：${error.message}`);
  } finally {
    liveState.poolTransferring = false;
    updateLivePoolSelectionControls();
    button.textContent = '加入自动化池';
  }
}
async function addToAutomationPool() {
  if (liveState.poolSelected.size) return addPoolSelectionToAutomationPool();
  return addSelectedToAutomationPool();
}
async function addPoolSelectionToAutomationPool() {
  if (liveState.poolTransferring) return;
  const selected = [...liveState.poolSelected.values()];
  if (!selected.length) { poolTransferStatus('请先勾选要加入自动化池的商品。', 'error'); return; }
  const items = selected.map((product) => {
    const detailId = Number(product.collectBoxDetailId);
    const mode = product.editModel === 'shop' ? 'shop' : 'site';
    const shopId = Number($('#liveShop').value || product.collectBoxDetailShopList?.[0]?.shopId || 0);
    return { detailId, mode, shopId };
  });
  if (items.some((item) => !Number.isInteger(item.detailId) || item.detailId <= 0 ||
    (item.mode === 'shop' && (!Number.isInteger(item.shopId) || item.shopId <= 0)))) {
    poolTransferStatus('部分商品或店铺信息不完整，无法批量加入自动化池。', 'error'); return;
  }
  const button = $('#liveAddAutomation');
  liveState.poolTransferring = true;
  button.disabled = true; button.textContent = '正在并发入池…';
  // This is an API-request size, not remote concurrency.  The server keeps a
  // single global Miaoshou rate limiter, so larger local batches remove idle
  // request turnarounds without increasing vendor request pressure.
  const batchSize = 500;
  const totalBatches = Math.ceil(items.length / batchSize);
  poolTransferStatus(`准备将 ${items.length} 件商品分 ${totalBatches} 批入池（每批最多 ${batchSize} 件，最多 12 路读取并遵守妙手自适应限流）…`);
  try {
    const addedIds = new Set();
    const skippedIds = new Set();
    let failures = [];
    let compatibilityMode = false;
    let interruptedError = '';
    for (let offset = 0; offset < items.length; offset += batchSize) {
      const batch = items.slice(offset, offset + batchSize);
      const batchIndex = Math.floor(offset / batchSize) + 1;
      poolTransferStatus(`正在入池：第 ${batchIndex}/${totalBatches} 批，已完成 ${addedIds.size + skippedIds.size + failures.length}/${items.length} 件，跳过重复 ${skippedIds.size} 件…`);
      try {
        const response = await fetch('/api/automation/pool/batch', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ items: batch }),
        });
        const body = await response.json().catch(() => ({}));
        if (response.status === 404) {
          compatibilityMode = true;
          for (const item of batch) {
            try {
              const singleResponse = await fetch('/api/automation/pool', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(item),
              });
              const singleBody = await singleResponse.json().catch(() => ({}));
              if (!singleResponse.ok) throw new Error(singleBody.error || '商品入池失败');
              (singleBody.data?.skipped ? skippedIds : addedIds).add(item.detailId);
            } catch (error) {
              failures.push({ detailId: item.detailId, error: error.message });
            }
          }
        } else {
          if (!response.ok) throw new Error(body.error || '批量入池失败');
          for (const item of body.data?.items || []) addedIds.add(Number(item.detailId));
          for (const item of body.data?.skipped || []) skippedIds.add(Number(item.detailId));
          failures = failures.concat(body.data?.failures || []);
        }
      } catch (error) {
        interruptedError = error.message || '批量入池失败';
        break;
      }
    }
    for (const detailId of [...addedIds, ...skippedIds]) liveState.poolSelected.delete(detailId);
    const unsubmitted = items.length - new Set([...addedIds, ...skippedIds]).size - failures.length;
    const summary = `已加入 ${addedIds.size} 件，跳过重复 ${skippedIds.size} 件，失败 ${failures.length} 件${unsubmitted ? `，未提交 ${unsubmitted} 件` : ''}${compatibilityMode ? '（兼容模式）' : ''}。`;
    const failureDetails = failures.slice(0, 50).map(item => `采集箱 ID ${item.detailId}：${item.error || '商品入池失败'}`).join('\n');
    const moreFailures = failures.length > 50 ? `\n其余 ${failures.length - 50} 件失败商品已保留勾选。` : '';
    poolTransferStatus(interruptedError
      ? `${summary}\n批次处理已停止：${interruptedError}\n未成功或未提交商品已保留勾选，可稍后重试。${failureDetails ? `\n${failureDetails}${moreFailures}` : ''}`
      : failures.length
        ? `${summary}\n失败商品已保留勾选，处理后可再次加入：\n${failureDetails}${moreFailures}`
        : summary, failures.length || interruptedError ? 'error' : 'success');
    showToast(summary);
    await Promise.all([loadAutomationPool(), loadLiveProducts()]);
  } catch (error) {
    poolTransferStatus(`批量加入自动化池失败：${error.message}`, 'error');
    showToast(`批量加入自动化池失败：${error.message}`);
  } finally {
    liveState.poolTransferring = false;
    button.textContent = '加入自动化池';
    updateLivePoolSelectionControls();
  }
}
async function removeAutomationPoolItem(detailId) {
  if (!window.confirm(`只从本机自动化池移除采集箱 ID ${detailId}？妙手原商品不会被删除。`)) return;
  try {
    const response = await fetch(`/api/automation/pool/${encodeURIComponent(detailId)}`, { method: 'DELETE' });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || '移出失败');
    showToast('已从本机自动化池移出');
    await loadAutomationPool();
  } catch (error) { showToast(`移出自动化池失败：${error.message}`); }
}
async function removeAutomationPoolSelection() {
  const detailIds = [...automationState.selected].sort((left, right) => left - right);
  if (!detailIds.length) return;
  if (!window.confirm(`只从本机自动化池移除所选 ${detailIds.length} 件商品？妙手原商品不会被删除。`)) return;
  automationState.submitting = true; updateAutomationControls();
  try {
    const response = await fetch('/api/automation/pool/batch', {
      method: 'DELETE', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ detailIds, confirmation: 'REMOVE_AUTOMATION_POOL_BATCH' }),
    });
    const body = await response.json().catch(() => ({}));
    const legacyRoute = response.status === 404 || /invalid literal.*['"]batch['"]|未找到接口/i.test(body.error || '');
    let removedIds = body.data?.detailIds || [];
    if (legacyRoute) {
      removedIds = [];
      for (const detailId of detailIds) {
        const singleResponse = await fetch(`/api/automation/pool/${encodeURIComponent(detailId)}`, { method: 'DELETE' });
        const singleBody = await singleResponse.json().catch(() => ({}));
        if (!singleResponse.ok) {
          throw new Error(`兼容模式已移出 ${removedIds.length}/${detailIds.length} 件：${singleBody.error || '商品移出失败'}`);
        }
        removedIds.push(detailId);
      }
    } else if (!response.ok) throw new Error(body.error || '批量移出失败');
    automationState.selected.clear();
    showToast(`已从本机自动化池移出 ${removedIds.length} 件商品${legacyRoute ? '（已使用兼容接口）' : ''}`);
    await loadAutomationPool();
  } catch (error) { automationClaimFeedback(`批量移出失败：${error.message}`, 'error'); }
  finally { automationState.submitting = false; updateAutomationControls(); }
}
async function claimAutomationSelection() {
  if (automationState.submitting) return;
  const bound = automationState.targetMode === 'personnel';
  const shopIds = selectedAutomationShopIds();
  const site = $('#automationPublishSite').value;
  const detailIds = [...automationState.selected].sort((left, right) => left - right);
  const price = $('#automationSkuPrice').value.trim();
  const priceMode = $('#automationPriceMode').value;
  const priceMultiplier = $('#automationPriceMultiplier').value.trim();
  const editConcurrency = Number($('#automationEditConcurrency').value);
  if ((!bound && !shopIds.length) || !site || !detailIds.length) {
    automationClaimFeedback('请选择池内商品、目标店铺和发布站点。', 'error'); return;
  }
  if (detailIds.length > AUTOMATION_POOL_PAGE_SIZE) {
    automationClaimFeedback('每批最多 50 件，请按页选择后提交。', 'error'); return;
  }
  if (bound && (!automationPersonnelReady() || automationState.routingLoading || automationState.bindingSaving)) {
    automationClaimFeedback('请先完成“刷新人员归属”，核对绑定后再提交。', 'error'); return;
  }
  const expectedRouting = bound ? automationExpectedRouting(detailIds) : [];
  const routedCount = expectedRouting.filter(row => row.shopIds.length).length;
  const taskCount = bound ? expectedRouting.reduce((count, row) => count + row.shopIds.length, 0) : detailIds.length * shopIds.length;
  if (bound && !taskCount) { automationClaimFeedback('所选商品没有可用的人员店铺绑定，请先设置绑定。', 'error'); return; }
  if (!Number.isSafeInteger(editConcurrency) || editConcurrency < 1) {
    automationClaimFeedback('编辑并发数应为大于 0 的整数。', 'error'); return;
  }
  if (!['floor', 'source_multiplier'].includes(priceMode)) {
    automationClaimFeedback('请选择有效的申报价方式。', 'error'); return;
  }
  if (priceMode === 'floor' && (!/^\d+(?:\.\d{1,2})?$/.test(price) || Number(price) <= 0 || Number(price) > 999980)) {
    automationClaimFeedback('SKU 申报价下限 X 应为大于 0、最多两位小数且不超过 999980 的金额；每件商品会随机取 X 到 X+20。', 'error'); return;
  }
  if (priceMode === 'source_multiplier' && (!/^\d+(?:\.\d{1,4})?$/.test(priceMultiplier) || Number(priceMultiplier) <= 0 || Number(priceMultiplier) > 1000)) {
    automationClaimFeedback('原始价格倍数应大于 0、最多四位小数且不超过 1000。', 'error'); return;
  }
  if (liveState.mode !== 'live') { automationClaimFeedback('当前服务为只读模式，不能自动编辑保存。', 'error'); return; }
  automationState.submitting = true; updateAutomationControls();
  const priceDescription = priceMode === 'source_multiplier'
    ? `按原始 SKU 价格 × ${priceMultiplier} 计算`
    : `按固定下限 ${price} ～ ${Number(price) + 20} 随机生成`;
  automationClaimFeedback(`正在创建 ${taskCount} 个后台编辑保存任务${bound ? `（按所属人员绑定，${routedCount} 件匹配，${detailIds.length - routedCount} 件将跳过）` : ''}（${priceDescription}）…`);
  try {
    const response = await fetch('/api/automation/batch-run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ detailIds, shopIds, site, price, priceMode, priceMultiplier, editConcurrency, confirmation: 'AUTOMATE_AND_SAVE_BATCH',
        ...(bound ? {targetMode: 'personnel', accountId: activeMiaoshouCollectionAccountId, bindingScope: automationState.routing.scope, expectedRouting} : {}),
        duplicateConfirmation: 'ALLOW_DUPLICATE_PUBLISH' }),
    });
    const body = await response.json().catch(() => ({}));
    if (response.status === 404) {
      throw new Error('当前后台尚未更新，请重启本地后端服务后再提交多线程编辑任务。');
    }
    if (!response.ok) throw new Error(body.error || '任务创建失败');
    const jobs = body.data?.jobs || [];
    jobs.slice().reverse().forEach((job) => {
      if (!automationState.jobs.some((existing) => existing.jobId === job.jobId)) automationState.jobs.unshift(job);
    });
    if (jobs.length) {
      automationState.jobsLoaded = true;
      renderAutomationJobs();
    }
    const skipped = body.data?.skipped || [];
    automationState.selected = new Set(skipped.map(row => row.detailId));
    const effectiveConcurrency = body.data?.editConcurrency || editConcurrency;
    automationClaimFeedback(`已创建 ${jobs.length} 个任务，最多同时编辑保存 ${effectiveConcurrency} 件；保存成功后进入发布队列，等待手动发布。${skipped.length ? `已跳过 ${skipped.length} 件并保留勾选：${skipped.map(row => `${row.detailId}（${row.reason}）`).join('、')}` : ''}`, jobs.length ? 'success' : 'error');
    await loadAutomationJobs();
  } catch (error) { automationClaimFeedback(`提交失败：${error.message}`, 'error'); }
  finally { automationState.submitting = false; updateAutomationControls(); }
}
function appendOption(select, value, label) {
  const option = document.createElement('option');
  option.value = String(value); option.textContent = label; select.append(option);
}
function setLiveError(container, message) {
  container.replaceChildren();
  const p = document.createElement('p'); p.className = 'live-error'; p.textContent = message; container.append(p);
}
async function loadLiveCatalog(refresh = false) {
  const shopSelect = $('#liveShop'), siteSelect = $('#liveSite');
  // Start the collector request before optional configuration reads. MiaoShou
  // requests are intentionally serialized to stay under the account limit,
  // so this keeps the visible product list from waiting behind shop/site data.
  const productTask = loadLiveProducts(false, refresh);
  const suffix = refresh ? '?refresh=1' : '';
  const results = await Promise.allSettled([apiGet('/api/shops' + suffix), apiGet('/api/sites' + suffix)]);
  if (results[0].status === 'fulfilled') {
    const shops = results[0].value.data?.shopList || [];
    shopSelect.replaceChildren(); appendOption(shopSelect, '', '请选择店铺');
    shops.forEach((shop) => appendOption(shopSelect, shop.shopId, shop.shopNick || shop.platformShopName || `店铺 ${shop.shopId}`));
    // Keep the user's usual store selected when available; fall back to the
    // only store instead of leaving a newly-created item without a shop.
    const defaultShop = shops.find((shop) => (shop.shopNick || shop.platformShopName) === 'WangdanMart');
    if (defaultShop) shopSelect.value = String(defaultShop.shopId);
    else if (shops.length === 1) shopSelect.value = String(shops[0].shopId);
  } else {
    shopSelect.replaceChildren(); appendOption(shopSelect, '', '店铺加载失败');
    // Optional configuration failures must not replace successfully loaded products.
    showToast(`店铺读取失败：${results[0].reason.message}`);
  }
  if (results[1].status === 'fulfilled') {
    const sites = results[1].value.data?.publishSiteOptions || [];
    siteSelect.replaceChildren(); appendOption(siteSelect, '', '请选择站点');
    sites.forEach((site) => appendOption(siteSelect, site.key, `${site.value || site.key} (${site.key})`));
    if ([...siteSelect.options].some((option) => option.value === 'US')) siteSelect.value = 'US';
  } else {
    siteSelect.replaceChildren(); appendOption(siteSelect, '', '站点加载失败');
  }
  // Publication history belongs to its own page; do not re-read it on every collection reload.
  await productTask;
  return results.flatMap(result => result.status === 'rejected' ? [result.reason.message]
    : result.value?.cache?.error ? [result.value.cache.error] : []);
}
async function syncCollectionInformation() {
  if (syncCollectionInformation.busy) return;
  syncCollectionInformation.busy = true;
  const buttons = [$('#syncCollectionInfo'), $('#configureCollectionSync')].filter(Boolean);
  buttons.forEach(button => { button.disabled = true; });
  const status = $('#collectionSyncStatus');
  status.textContent = `正在同步采集箱 ${activeMiaoshouCollectionAccountId} 的信息…`;
  try {
    await loadMiaoshouCollectionAccounts();
    if (!accountById(activeMiaoshouCollectionAccountId)?.configured)
      throw new Error('此账号 API 尚未生效。请先在采集箱配置页保存 App Key 和 App Secret，再点击同步。');
    const health = await (await fetch('/api/health', {cache: 'no-store'})).json();
    if (!health.multiAccountWorkbench || health.accountId !== activeMiaoshouCollectionAccountId)
      throw new Error('请先重启更新后的后台，再同步信息');
    liveState.mode = health.mode;
    $('#livePanel').classList.remove('hidden');
    openMiaoshouCollectionAccount(activeMiaoshouCollectionAccountId);
    // Refresh the unfiltered list first; filtered scans keep their separate refresh control.
    resetLiveCollectorAccount(activeMiaoshouCollectionAccountId);
    const errors = await loadLiveCatalog(true);
    if (liveState.productLoadError) errors.push(liveState.productLoadError);
    let orders = '';
    try {
      const response = await fetch('/api/orders/sync', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({accountId: activeMiaoshouCollectionAccountId})});
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || '订单读取失败');
      if (body.data?.accountId !== activeMiaoshouCollectionAccountId) throw new Error('订单账号不一致');
      orders = `订单已同步 ${body.data.imported || 0} 条`;
    } catch (error) { errors.push(`订单：${error.message}`); }
    status.textContent = errors.length ? `同步部分信息失败：${errors.join('；')}。${orders}`
      : `同步完成 · 当前商品页已增量校对，店铺和站点已更新；${orders}。`;
    showToast(errors.length ? '部分信息未同步，请查看上方提示' : '当前采集箱信息同步完成');
  } catch (error) {
    status.textContent = error.message;
    showToast(error.message);
  } finally {
    syncCollectionInformation.busy = false;
    buttons.forEach(button => { button.disabled = false; });
  }
}
function liveCollectorId(value) {
  if (typeof value !== 'string' && !(typeof value === 'number' && Number.isSafeInteger(value))) return '';
  const text = String(value).trim();
  return /^[0-9]{1,20}$/.test(text) && !/^0+$/.test(text) ? text.replace(/^0+/, '') : '';
}
function readLivePageSize() {
  try {
    const saved = Number(window.localStorage.getItem('temu.collection.pageSize'));
    if ([20, 50, 100].includes(saved)) return saved;
  } catch { /* Browsing/storage restrictions must not prevent product reads. */ }
  return 50;
}
function livePageSize() {
  return [20, 50, 100].includes(liveState.pageSize) ? liveState.pageSize : 50;
}
function changeLivePageSize(event) {
  const size = Number(event.target.value);
  if (![20, 50, 100].includes(size) || liveState.poolTransferring || liveState.pageSizeLegacy) {
    event.target.value = String(livePageSize()); return;
  }
  if (size === livePageSize()) return;
  liveState.pageSize = size;
  try { window.localStorage.setItem('temu.collection.pageSize', String(size)); } catch { /* Optional preference only. */ }
  liveState.page = 1;
  resetLivePageSummary();
  return loadLiveProducts();
}
function liveCollectorLabel(id) {
  if (!id) return '未返回所属人员';
  const person = liveState.people.get(id);
  const name = person?.alias || person?.name || person?.account || '未匹配人员';
  const account = person?.name && person?.account ? `${person.account} · ` : '';
  return `${name}（${account}账号 ID ${id}）`;
}
function mergeLivePeople(people, acceptAliases = true) {
  for (const person of people || []) {
    const id = liveCollectorId(person.id);
    if (id) liveState.people.set(id, {id, name: typeof person.name === 'string' ? person.name : '',
      account: typeof person.account === 'string' ? person.account : '',
      alias: acceptAliases ? (typeof person.alias === 'string' ? person.alias : '') : (liveState.people.get(id)?.alias || '')});
  }
  for (const product of liveState.products) {
    const id = liveCollectorId(product.subAppAccountId);
    if (id && !liveState.people.has(id)) liveState.people.set(id, {id});
  }
  renderLiveCollectorFilter();
}
function renderLiveCollectorFilter() {
  const select = $('#liveCollectorFilter');
  if (!select) return;
  select.replaceChildren();
  const all = document.createElement('option'); all.value = ''; all.textContent = '全部人员'; select.append(all);
  const ids = new Set(liveState.people.keys());
  if (liveState.collectorId) ids.add(liveState.collectorId);
  [...ids].sort((a, b) => liveCollectorLabel(a).localeCompare(liveCollectorLabel(b), 'zh-CN')).forEach(id => {
    const option = document.createElement('option'); option.value = id; option.textContent = liveCollectorLabel(id); select.append(option);
  });
  select.value = liveState.collectorId;
}
function openPersonnelAliases() {
  if (liveState.aliasSaving || activeMiaoshouCollectionAccountId !== liveState.collectorAccountId) return;
  const dialog = {accountId: activeMiaoshouCollectionAccountId, rows: []};
  liveState.aliasDialog = dialog;
  const body = $('#personnelAliasRows'); body.replaceChildren();
  [...liveState.people.values()].sort((a, b) => a.id.localeCompare(b.id, 'en', {numeric: true})).forEach(person => {
    const row = document.createElement('tr');
    const id = document.createElement('td'); id.textContent = person.id;
    const reference = document.createElement('td');
    reference.textContent = person.name ? `${person.name}${person.account ? `（${person.account}）` : ''}` : person.account || '未返回姓名';
    const cell = document.createElement('td'), input = document.createElement('input');
    input.type = 'text'; input.maxLength = 80; input.value = person.alias || '';
    input.placeholder = '输入姓名或备注'; input.setAttribute('aria-label', `账号 ID ${person.id} 的本地备注`);
    cell.append(input); row.append(id, reference, cell); body.append(row);
    dialog.rows.push({id: person.id, input, original: person.alias || ''});
  });
  $('#personnelAliasTitle').textContent = `人员备注 · 采集箱 ${dialog.accountId}`;
  $('#personnelAliasStatus').textContent = dialog.rows.length ? `共 ${dialog.rows.length} 位人员；只保存修改的备注。` : '暂无已发现的人员，请关闭后点击“刷新人员”。';
  $('#personnelAliasSave').disabled = !dialog.rows.length;
  $('#personnelAliasSave').textContent = '保存备注';
  $('#personnelAliasClose').disabled = false; $('#personnelAliasCancel').disabled = false;
  $('#personnelAliasModal').classList.remove('hidden');
  (dialog.rows[0]?.input || $('#personnelAliasClose')).focus();
}
function closePersonnelAliases(force = false) {
  if (liveState.aliasSaving && !force) return;
  liveState.aliasDialog = null; liveState.aliasSaving = false;
  $('#personnelAliasRows').replaceChildren();
  $('#personnelAliasModal').classList.add('hidden');
  if (!force) $('#liveEditPeople').focus();
}
function personnelAliasKeydown(event) {
  if (!liveState.aliasDialog) return;
  if (event.key === 'Escape') { event.preventDefault(); closePersonnelAliases(); }
  if (event.key === 'Tab') {
    const fields = [...$('#personnelAliasModal').querySelectorAll('button:not(:disabled), input:not(:disabled)')];
    const first = fields[0], last = fields.at(-1);
    if (!first) { event.preventDefault(); return; }
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
}
async function savePersonnelAliases() {
  const dialog = liveState.aliasDialog;
  if (!dialog || liveState.aliasSaving || dialog.accountId !== activeMiaoshouCollectionAccountId) return;
  const aliases = dialog.rows.map(row => ({id: row.id, alias: row.input.value.trim(), original: row.original}))
    .filter(row => row.alias !== row.original).map(({id, alias}) => ({id, alias}));
  if (!aliases.length) { closePersonnelAliases(); return; }
  if (aliases.some(row => [...row.alias].length > 80 || /[\u0000-\u001f\u007f]/.test(row.alias))) {
    $('#personnelAliasStatus').textContent = '备注最多 80 个字符，不能包含换行或控制字符。'; return;
  }
  const current = () => liveState.aliasDialog === dialog && dialog.accountId === activeMiaoshouCollectionAccountId;
  liveState.aliasSaving = true; liveState.aliasRevision++;
  dialog.rows.forEach(row => { row.input.disabled = true; });
  $('#personnelAliasSave').disabled = true; $('#personnelAliasSave').textContent = '正在保存…';
  $('#personnelAliasClose').disabled = true; $('#personnelAliasCancel').disabled = true;
  $('#personnelAliasStatus').textContent = '正在保存到本机，不修改妙手…';
  try {
    const response = await fetch('/api/collection-personnel/aliases', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({accountId: dialog.accountId, aliases})});
    const body = await response.json();
    if (!current()) return;
    if (!response.ok) throw new Error(body.error || '保存失败');
    if (Number(body.data?.accountId) !== dialog.accountId || !Array.isArray(body.data?.people)) throw new Error('人员账号或返回数据不一致，请重试');
    liveState.aliasRevision++;
    mergeLivePeople(body.data.people);
    if (!liveState.productsLoading) renderLiveProductRows();
    liveState.aliasSaving = false; closePersonnelAliases();
    showToast(`已保存 ${aliases.length} 位人员的本地备注`);
  } catch (error) {
    if (!current()) return;
    $('#personnelAliasStatus').textContent = `保存失败：${error.message}。填写内容已保留，可重试。`;
  } finally {
    if (current()) {
      liveState.aliasRevision++; liveState.aliasSaving = false;
      dialog.rows.forEach(row => { row.input.disabled = false; });
      $('#personnelAliasSave').disabled = false; $('#personnelAliasSave').textContent = '保存备注';
      $('#personnelAliasClose').disabled = false; $('#personnelAliasCancel').disabled = false;
    }
  }
}
function resetLiveCollectorAccount(accountId) {
  closePersonnelAliases(true); liveState.aliasRevision++;
  liveState.productRequest++; liveState.personnelRequest++;
  window.clearTimeout(liveState.productTimer); window.clearTimeout(liveState.personnelTimer);
  liveState.collectorAccountId = Number(accountId); liveState.collectorId = '';
  liveState.searchText = ''; $('#liveKeyword').value = '';
  liveState.people = new Map(); liveState.products = []; liveState.page = 1;
  liveState.productsLoading = false; liveState.filterProgress = null;
  resetLivePageSummary(); renderLiveCollectorFilter();
  $('#liveCollectorStatus').textContent = '';
  $('#liveRefreshPeople').disabled = false;
  $('#liveProducts').replaceChildren();
}
async function loadLivePersonnel(scan = false, refresh = false, request = ++liveState.personnelRequest) {
  const accountId = activeMiaoshouCollectionAccountId;
  const aliasRevision = liveState.aliasRevision, aliasSaving = liveState.aliasSaving;
  if (accountId !== liveState.collectorAccountId) return;
  window.clearTimeout(liveState.personnelTimer);
  const current = () => request === liveState.personnelRequest && accountId === activeMiaoshouCollectionAccountId;
  if (scan) { $('#liveRefreshPeople').disabled = true; $('#liveCollectorStatus').textContent = '正在读取全部人员…'; }
  try {
    const params = new URLSearchParams({accountId: String(accountId)});
    if (scan) params.set('scan', '1');
    if (refresh) params.set('refresh', '1');
    const result = await apiGet(`/api/collection-personnel?${params}`);
    if (!current()) return;
    if (Number(result.accountId) !== accountId) throw new Error('人员账号不一致');
    mergeLivePeople(result.people, !aliasSaving && !liveState.aliasSaving && aliasRevision === liveState.aliasRevision);
    if (!liveState.productsLoading) renderLiveProductRows();
    if (result.scan?.status === 'loading') {
      $('#liveCollectorStatus').textContent = `正在读取全部人员，已检查 ${result.scan.scanned} 件商品…`;
      liveState.personnelTimer = window.setTimeout(() => loadLivePersonnel(true, false, request), 300);
      return;
    }
    $('#liveRefreshPeople').disabled = false;
    $('#liveCollectorStatus').textContent = result.scan?.status === 'failed' ? result.scan.message
      : result.mappingStatus === 'ready' ? '人员名称已更新；人员筛选使用本机完整快照。'
      : result.mappingStatus === 'loading' ? '正在读取人员名称…'
      : '未匹配姓名时显示账号 ID；刷新人员只更新名称，不扫描商品。';
    if (result.mappingStatus === 'loading') liveState.personnelTimer = window.setTimeout(() => loadLivePersonnel(false, false, request), 1200);
    if (scan && (liveState.collectorId || liveState.searchText)) { liveState.page = 1; resetLivePageSummary(); loadLiveProducts(); }
  } catch {
    if (!current()) return;
    $('#liveRefreshPeople').disabled = false;
    $('#liveCollectorStatus').textContent = '人员名称暂不可用，仍可按账号 ID 筛选。';
  }
}
function renderLiveProductRows() {
  const list = $('#liveProducts'); list.replaceChildren();
  if (!liveState.products.length) {
    const empty = document.createElement('p'); empty.className = 'live-empty';
    empty.textContent = liveState.collectorId || liveState.searchText ? '没有符合当前人员和搜索条件的商品。' : '这一页暂无商品。'; list.append(empty);
  }
  liveState.products.forEach((product) => {
    const detailId = Number(product.collectBoxDetailId);
    const row = document.createElement('div'); row.className = 'live-item-row';
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.className = 'live-item-select';
    checkbox.checked = liveState.poolSelected.has(detailId);
    checkbox.setAttribute('aria-label', `选择采集箱商品 ${product.title || detailId} 加入自动化池`);
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) liveState.poolSelected.set(detailId, product);
      else liveState.poolSelected.delete(detailId);
      updateLivePoolSelectionControls();
    });
    const button = document.createElement('button'); button.type = 'button'; button.className = 'live-item';
    if (liveState.selected && String(product.collectBoxDetailId) === String(liveState.selected.collectBoxDetailId)) button.classList.add('selected');
    const img = document.createElement('img'); img.alt = ''; img.loading = 'lazy';
    if (/^https?:\/\//.test(product.thumbnail || '')) img.src = product.thumbnail;
    const info = document.createElement('span'), title = document.createElement('strong'), meta = document.createElement('small');
    title.textContent = product.title || '未命名商品';
    meta.textContent = `采集箱 ID ${product.collectBoxDetailId} · ${product.stock || '—'} 库存 · ${product.price || '—'} 价格`;
    const owner = document.createElement('small'); owner.className = 'live-collector';
    owner.textContent = `所属人员：${liveCollectorLabel(liveCollectorId(product.subAppAccountId))}`;
    const collectedTime = document.createElement('small'); collectedTime.className = 'live-collected-time';
    // gmtCreate is MiaoShou's collection timestamp, already in its display timezone.
    const collectedAt = typeof product.gmtCreate === 'string' ? product.gmtCreate.trim() : '';
    collectedTime.textContent = `采集时间：${collectedAt || '妙手未返回'}`;
    info.append(title, meta, owner, collectedTime); button.append(img, info);
    button.addEventListener('click', () => selectLiveProduct(product));
    row.append(checkbox, button); list.append(row);
  });
  updateLivePoolSelectionControls();
}
function renderLivePageTools() {
  const summary = liveState.pageSummary;
  const itemCount = liveState.products.length;
  const hasExactTotal = summary?.status === 'ready' && Number.isInteger(summary.totalPages);
  let label = `第 ${liveState.page} 页 ${itemCount} 条`;
  if (hasExactTotal) label = summary.totalPages
    ? `第 ${liveState.page} / ${summary.totalPages} 页 · 共 ${summary.totalItems} 条`
    : '暂无商品 · 共 0 条';
  else if (summary?.status === 'calculating') label += ' · 正在统计总页数…';
  $('#livePageLabel').textContent = label;
  $('#livePageInput').value = String(liveState.page);
  $('#livePageSize').value = String(livePageSize());
  $('#livePageSize').disabled = Boolean(liveState.poolTransferring || liveState.pageSizeLegacy);
  $('#livePrev').disabled = liveState.productsLoading || liveState.page <= 1;
  $('#liveNext').disabled = liveState.productsLoading || (hasExactTotal ? liveState.page >= summary.totalPages : itemCount < livePageSize());
  $('#livePageGo').disabled = liveState.productsLoading;
}

function resetLivePageSummary() {
  liveState.pageSummary = null;
  liveState.pageSummaryRequest++;
  window.clearTimeout(liveState.pageSummaryTimer);
}

async function loadLivePageSummary(request = ++liveState.pageSummaryRequest) {
  try {
    const pageSize = livePageSize();
    const summary = await apiGet(`/api/products/page-summary?pageSize=${pageSize}`);
    if (request !== liveState.pageSummaryRequest) return;
    if (summary.pageSize !== undefined && summary.pageSize !== pageSize) throw new Error('页数统计与每页数量不一致');
    liveState.pageSummary = summary;
    renderLivePageTools();
    if (summary.status === 'calculating') {
      window.clearTimeout(liveState.pageSummaryTimer);
      liveState.pageSummaryTimer = window.setTimeout(() => loadLivePageSummary(request), 2000);
    }
  } catch {
    if (request !== liveState.pageSummaryRequest) return;
    liveState.pageSummary = { status: 'unavailable' };
    renderLivePageTools();
  }
}

function submitLiveSearch() {
  const text = $('#liveKeyword').value.trim();
  if ([...text].length > 200 || /[\u0000-\u001f\u007f]/.test(text)) {
    showToast('搜索内容最多 200 个字符，不能包含控制字符'); return;
  }
  liveState.searchText = text; liveState.page = 1; liveState.filterProgress = null;
  resetLivePageSummary();
  return loadLiveProducts();
}
function liveSnapshotBeijingTime(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return '';
  return new Date(seconds * 1000).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai', hour12: false,
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit'
  });
}
async function loadLiveProducts(quiet = false, refresh = false) {
  const accountId = activeMiaoshouCollectionAccountId;
  const pageSize = livePageSize();
  const aliasRevision = liveState.aliasRevision, aliasSaving = liveState.aliasSaving;
  if (accountId !== liveState.collectorAccountId) return;
  const request = ++liveState.productRequest;
  // Keep the submitted query stable while polling or changing pages. Typing alone is not a new search.
  const searchText = liveState.searchText || '';
  const collectBoxDetailId = /^[0-9]+$/.test(searchText) ? searchText : '';
  const titleKeyword = collectBoxDetailId ? '' : searchText;
  const fullBoxFilter = Boolean(liveState.collectorId || titleKeyword);
  const current = () => request === liveState.productRequest && accountId === activeMiaoshouCollectionAccountId && pageSize === livePageSize();
  window.clearTimeout(liveState.productTimer);
  liveState.productsLoading = !quiet;
  if (!quiet) liveState.products = [];
  liveState.productLoadError = '';
  updateLivePoolSelectionControls(); renderLivePageTools();
  const list = $('#liveProducts');
  if (!quiet) list.replaceChildren();
  const loading = document.createElement('p'); loading.className = 'live-empty';
  loading.textContent = fullBoxFilter && liveState.filterProgress?.status === 'loading'
    ? `正在筛选整个采集箱，已检查 ${liveState.filterProgress.scanned} 件商品…`
    : fullBoxFilter ? '正在筛选整个采集箱，首次读取可能需要一些时间…' : '正在读取本机缓存／妙手采集箱商品…';
  if (!quiet) list.append(loading);
  const params = new URLSearchParams({ page: String(liveState.page), accountId: String(accountId), pageSize: String(pageSize) });
  if (refresh) params.set('refresh', '1');
  if (liveState.collectorId) params.set('collectorId', liveState.collectorId);
  if (collectBoxDetailId) params.set('collectBoxDetailId', collectBoxDetailId);
  if (titleKeyword) params.set('titleKeyword', titleKeyword);
  try {
    const response = await apiGet(`/api/products?${params}`);
    if (!current()) return;
    if (Number(response.accountId) !== accountId) throw new Error('后台尚未支持分账号人员识别，请更新服务');
    if (response.pageSize !== undefined && response.pageSize !== pageSize) throw new Error('商品分页数量不一致，请刷新后重试');
    // Static assets can update while a busy backend cannot safely restart yet.
    // An old backend ignores pageSize and still returns 20; never label it 50/100.
    liveState.pageSizeLegacy = response.pageSize === undefined;
    if (liveState.pageSizeLegacy) liveState.pageSize = 20;
    const cache = response.cache;
    const cacheStatus = $('#liveCacheStatus');
    if (cacheStatus) cacheStatus.textContent = cache ? (cache.error
      ? `已保留本机缓存；增量校对失败：${cache.error}`
      : cache.refreshing ? '已显示本机缓存，正在后台增量校对当前页…'
      : `${cache.cached ? '已显示本机缓存' : '本页增量校对完成'}；上次校对新增 ${cache.added || 0}、更新 ${cache.updated || 0}、未变化 ${cache.unchanged || 0} 件。`)
      : fullBoxFilter ? '正在使用全箱筛选结果；刷新人员可进行全量校对。' : '';
    if (cacheStatus && liveState.pageSizeLegacy) cacheStatus.textContent = '后台仍是旧版分页接口，暂按 20 件/页显示；任务结束并安全重启后台后，可选择 50 或 100 件/页。';
    if (cache?.error) liveState.productLoadError = cache.error;
    else if (refresh && cache?.refreshing) liveState.productLoadError = '当前商品页仍在后台校对，请稍后查看校对结果';
    liveState.products = response.data?.detailList || [];
    // A read started before/during a save may contain the previous local alias.
    mergeLivePeople(response.people, !aliasSaving && !liveState.aliasSaving && aliasRevision === liveState.aliasRevision);
    liveState.filterProgress = response.filter || null;
    if (response.filter?.status === 'loading') {
      loading.textContent = `正在筛选整个采集箱，已检查 ${response.filter.scanned} 件商品…`;
      liveState.productTimer = window.setTimeout(() => { if (current()) loadLiveProducts(); }, 300);
      return;
    }
    if (response.filter?.status === 'failed') throw new Error(response.filter.message);
    if (fullBoxFilter) {
      if (response.filter?.status !== 'ready') throw new Error('后台未返回完整采集箱筛选结果，请确认服务已更新');
      if (liveState.collectorId && liveState.products.some(product => liveCollectorId(product.subAppAccountId) !== liveState.collectorId)) throw new Error('人员筛选结果不一致，请重试');
      resetLivePageSummary();
      liveState.pageSummary = {status: 'ready', totalPages: response.filter.totalPages, totalItems: response.filter.totalItems};
      if (cacheStatus && response.filter.source === 'local_snapshot') {
        const updated = liveSnapshotBeijingTime(response.filter.snapshotAt) || '未知时间';
        const failed = response.filter.snapshotError ? `；最近一次同步失败：${response.filter.snapshotError}` : '';
        cacheStatus.textContent = `人员筛选使用本机完整快照（北京时间 ${updated}，共 ${response.filter.snapshotItems || response.filter.totalItems} 件）；新商品将在下次每小时同步后出现${failed}。`;
      }
    } else if (collectBoxDetailId) {
      resetLivePageSummary();
      liveState.pageSummary = { status: 'ready', totalPages: liveState.products.length ? 1 : 0, totalItems: liveState.products.length };
    } else if (!liveState.pageSummary) {
      loadLivePageSummary();
    }
    liveState.productsLoading = false;
    renderLiveProductRows();
    renderLivePageTools();
    if (!quiet) loadLivePersonnel();
    if (cache?.refreshing) liveState.productTimer = window.setTimeout(() => { if (current()) loadLiveProducts(true); }, 1500);
  } catch (error) {
    if (!current()) return;
    liveState.productsLoading = false;
    liveState.productLoadError = error.message;
    if (quiet && liveState.products.length) {
      if ($('#liveCacheStatus')) $('#liveCacheStatus').textContent = `已保留本机缓存；校对失败：${error.message}`;
      renderLivePageTools(); return;
    }
    liveState.products = [];
    setLiveError(list, `商品读取失败：${error.message}`);
    updateLivePoolSelectionControls(); renderLivePageTools();
  }
}
function updateLivePoolSelectionControls() {
  const selected = liveState.poolSelected.size;
  $('#livePoolSelectedCount').textContent = selected ? `已选 ${selected} 件入池` : '未选入池商品';
  $('#liveAddAutomation').disabled = liveState.poolTransferring || liveState.mode !== 'live' || (!liveState.selected && selected < 1);
  const selectAll = $('#livePoolSelectAll');
  const pageIds = liveState.products.map((product) => Number(product.collectBoxDetailId));
  selectAll.disabled = liveState.productsLoading || !pageIds.length;
  $('#livePoolClearSelection').disabled = liveState.productsLoading;
  $('#livePageSize').disabled = Boolean(liveState.poolTransferring || liveState.pageSizeLegacy);
  selectAll.checked = pageIds.length > 0 && pageIds.every((detailId) => liveState.poolSelected.has(detailId));
  selectAll.indeterminate = pageIds.some((detailId) => liveState.poolSelected.has(detailId)) && !selectAll.checked;
}
async function loadLiveRecords() {
  const container = $('#publishRecordsList');
  try {
    const response = await apiGet('/api/records?page=1');
    const records = response.data?.moveCollectDetailList || [];
    container.replaceChildren();
    if (!records.length) {
      const empty = document.createElement('p'); empty.className = 'live-empty'; empty.textContent = '暂无发布记录。'; container.append(empty);
      return;
    }
    records.slice(0, 10).forEach((record) => {
      const row = document.createElement('div'); row.className = 'publish-record-row';
      const thumbnail = document.createElement('div'); thumbnail.className = 'publish-record-thumbnail';
      const thumbnailUrl = typeof record.thumbnail === 'string' ? record.thumbnail.trim() : '';
      if (/^https?:\/\//i.test(thumbnailUrl)) {
        const image = document.createElement('img'); image.src = thumbnailUrl;
        image.alt = record.title ? `${record.title}的产品缩略图` : '产品缩略图';
        image.loading = 'lazy';
        image.addEventListener('error', () => { image.remove(); thumbnail.textContent = '无图'; });
        thumbnail.append(image);
      } else thumbnail.textContent = '无图';
      const left = document.createElement('div'), title = document.createElement('strong'), meta = document.createElement('small');
      title.textContent = record.title || '未命名商品';
      meta.textContent = `${record.shopName || '店铺'} · ${record.siteName || record.site || '站点'} · ${record.gmtCreate || ''}`;
      left.append(title, meta);
      if (record.reason) { const reason = document.createElement('small'); reason.className = 'reason'; reason.textContent = record.reason; left.append(reason); }
      const status = document.createElement('span'); status.className = `status-pill ${record.status === 'success' ? 'success' : record.status === 'fail' ? 'failed' : 'neutral'}`;
      status.textContent = ({ success: '成功', fail: '失败', processing: '处理中', cancel: '已取消' })[record.status] || record.status || '未知';
      row.append(thumbnail, left, status); container.append(row);
    });
  } catch (error) {
    setLiveError(container, `发布记录读取失败：${error.message}`);
  }
}
async function selectLiveProduct(product) {
  liveState.selected = product; liveState.readiness = null;
  poolTransferStatus('');
  renderLiveFlow();
  $('#liveTargetConfig').classList.remove('hidden');
  $('#liveEdit').disabled = false;
  updateLivePoolSelectionControls();
  window.dispatchEvent(new CustomEvent('live-product-selected'));
  $('#liveRecheck').disabled = false;
  $$('.live-item').forEach((button) => button.classList.remove('selected'));
  const matching = liveState.products.indexOf(product);
  if (matching >= 0) $$('.live-item')[matching]?.classList.add('selected');
  $('#liveSelection').textContent = `${product.title || '未命名商品'} · 采集箱 ID ${product.collectBoxDetailId}`;
  $('#liveReadiness').classList.remove('hidden');
  $('#livePublish').disabled = true;
  await checkLiveReadiness();
}
async function checkLiveReadiness() {
  const container = $('#liveReadiness'), selected = liveState.selected;
  const shopId = $('#liveShop').value, site = $('#liveSite').value;
  const checkVersion = ++liveState.checkVersion;
  liveState.readiness = null; $('#livePublish').disabled = true;
  renderLiveFlow();
  if (!selected) return;
  if (!shopId || !site) { container.textContent = '请先选择目标店铺和发布站点。'; return; }
  container.textContent = '正在检查妙手中的上架资料…';
  const params = new URLSearchParams({ detailId: selected.collectBoxDetailId, shopId, site });
  try {
    const response = await apiGet(`/api/readiness?${params}`);
    if (checkVersion !== liveState.checkVersion) return;
    liveState.readiness = response;
    renderLiveFlow();
    container.replaceChildren();
    const headline = document.createElement('div'); headline.className = `live-check ${response.ready ? 'ready' : 'warning'}`;
    const strong = document.createElement('strong'); strong.textContent = response.ready ? '✓ 资料检查通过' : '资料尚未完整';
    const sub = document.createElement('span'); sub.textContent = `已配置运费模板 ${response.freightCount} 个、发货仓 ${response.warehouseCount} 个`;
    headline.append(strong, sub); container.append(headline);
    if (response.issues?.length) {
      const ul = document.createElement('ul'); ul.className = 'live-issues';
      response.issues.forEach((issue) => { const li = document.createElement('li'); li.textContent = issue; ul.append(li); });
      container.append(ul);
    }
    $('#livePublish').disabled = !response.ready || liveState.mode !== 'live';
    $('#livePublishHint').textContent = liveState.mode === 'live'
      ? (response.ready ? '提交后由妙手处理，结果可在下方发布记录查看。' : '请先在妙手中补齐缺失配置，再刷新检查。')
      : '当前仅可查看真实数据；要启用发布，请将本地 .env 的 APP_MODE 改为 live 并重启服务。';
  } catch (error) {
    if (checkVersion !== liveState.checkVersion) return;
    liveState.readiness = { ready: false };
    renderLiveFlow();
    setLiveError(container, `检查失败：${error.message}`);
  }
}
async function publishLiveProduct() {
  const selected = liveState.selected;
  if (!selected || !liveState.readiness?.ready || liveState.mode !== 'live') return;
  const shop = $('#liveShop').selectedOptions[0]?.textContent || '目标店铺';
  const site = $('#liveSite').selectedOptions[0]?.textContent || '站点';
  if (!window.confirm(`确认通过妙手发布“${selected.title}”到 ${shop} · ${site} 吗？`)) return;
  $('#livePublish').disabled = true; $('#livePublish').textContent = '正在提交…';
  renderLiveFlow(true);
  try {
    const response = await fetch('/api/publish', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ detailId: selected.collectBoxDetailId, shopId: $('#liveShop').value, sites: [$('#liveSite').value], confirmation: 'PUBLISH_TEMU_HALF' })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '发布请求失败');
    $('#resultTitle').textContent = '妙手已接受发布任务';
    $('#resultBody').textContent = '发布正在由妙手处理。请查看下方发布记录确认最终结果。';
    $('#resultModal').classList.remove('hidden');
    await loadLiveRecords();
  } catch (error) { showToast(`提交失败：${error.message}`); }
  finally { $('#livePublish').textContent = '通过妙手提交发布'; $('#livePublish').disabled = !liveState.readiness?.ready; renderLiveFlow(); }
}
function updateApplicationVersion(health) {
  const label=document.querySelector('.app-version');if(!label)return;
  const runtime=health.runtimeVersion || health.version;
  const resource=label.dataset.resourceVersion || health.resourceVersion;
  if(typeof runtime!=='string')return;
  const installed=health.resourceVersion || resource;
  const pending=health.pendingUpdate===true || Boolean(installed && installed!==runtime);
  const reload=Boolean(resource && installed && resource!==installed);
  const suffix=pending?(reload?` · 新版 V${installed} 待安全更新`:' · 待安全更新'):reload?' · 请刷新网页':'';
  label.textContent=resource?`界面 V${resource} · 后台 V${runtime}${suffix}`:`后台版本 V${runtime}`;
  label.title=pending?'本机已有待启用的更新；后台安全更新后新功能将全部生效。':reload?'后台已更新，请刷新网页加载对应的界面资源。':'界面与后台版本一致';
  label.dataset.pendingUpdate=String(pending);
}
window.updateApplicationVersion=updateApplicationVersion;
async function checkApiStatus() {
  try {
    const response = await fetch('/api/health', { cache: 'no-store' });
    if (!response.ok) return;
    const health = await response.json();
    updateApplicationVersion(health);
    liveState.multiShopPublishing = health.multiShopPublishing === true;
    if (health.miaoShouConfigured) {
      liveState.mode = health.mode;
      organizeLiveModules();
      $('#apiStateText').textContent = health.mode === 'live' ? '妙手半托管已接入' : '妙手已连接（只读）';
      $('#apiStateDot').style.background = '#1fbf96';
      $('#miaoShouSettingsStatus').textContent = '已连接';
      $('#miaoShouSettingsStatus').className = 'status-pill success';
      $('#miaoShouSettingsDescription').textContent = '凭证已在服务端加载，可读取半托管店铺、采集箱和发布记录。';
      $('#temuSettingsStatus').textContent = health.mode === 'live' ? '可提交发布' : '只读模式';
      $('#temuSettingsStatus').className = `status-pill ${health.mode === 'live' ? 'success' : 'neutral'}`;
      $('#temuSettingsDescription').textContent = '发布由妙手 API 执行；提交前会检查商品、店铺、站点与物流配置。';
      $('#livePanel').classList.remove('hidden');
      $('.pipeline-card').classList.add('hidden');
      $('.workspace-grid').classList.add('hidden');
      $('#editorPanel').classList.add('hidden');
      $('#loadDemo')?.classList.add('hidden');
      $('#resetDemo').classList.add('hidden');
      renderLiveFlow();
      await loadLiveCatalog();
    }
  } catch (_) {
    // Static-file mode has no backend; the interface remains safely in demo mode.
  }
}

$('#openProductPicker').addEventListener('click', () => { renderProductOptions(); $('#productModal').classList.remove('hidden'); $('#productSearch').focus(); });
$('#loadDemo')?.addEventListener('click', () => selectProduct(products[0]));
$('#productSearch').addEventListener('input', (event) => renderProductOptions(event.target.value));
$('#generateContent').addEventListener('click', generateContent);
$('#validateListing').addEventListener('click', validateListing);
$('#listingTitle').addEventListener('input', updateTitleCount);
$('#cost').addEventListener('input', () => { $('#price').value = priceFromCost($('#cost').value || 0, $('#margin').value); });
$('#margin').addEventListener('input', () => { $('#price').value = priceFromCost($('#cost').value || 0, $('#margin').value); });
$$('[data-close]').forEach((button) => button.addEventListener('click', () => closeModal(button.dataset.close)));
$$('.modal').forEach((modal) => modal.addEventListener('click', (event) => { if (event.target === modal) closeModal(modal.id); }));
$('#showApiHelp')?.addEventListener('click', () => { $('.nav-item[data-page="settings"]').click(); });
$('#resetDemo').addEventListener('click', resetDemo);
$('#liveSearch').addEventListener('click', submitLiveSearch);
$('#liveCollectorFilter').addEventListener('change', (event) => {
  liveState.collectorId = liveCollectorId(event.target.value); liveState.page = 1;
  liveState.filterProgress = null;
  resetLivePageSummary(); loadLiveProducts();
});
// Personnel names can change; product ownership does not.  Do not turn a
// name refresh into another full-box scan.
$('#liveRefreshPeople').addEventListener('click', () => loadLivePersonnel(false, true));
$('#liveEditPeople').addEventListener('click', openPersonnelAliases);
$('#personnelAliasSave').addEventListener('click', savePersonnelAliases);
$('#personnelAliasModal').addEventListener('keydown', personnelAliasKeydown);
$('#liveKeyword').addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); submitLiveSearch(); } });
$('#liveKeyword').addEventListener('search', () => { if (!$('#liveKeyword').value.trim() && liveState.searchText) submitLiveSearch(); });
$('#livePrev').addEventListener('click', () => { if (liveState.page > 1) { liveState.page--; loadLiveProducts(); } });
$('#livePageSize').addEventListener('change', changeLivePageSize);
$('#liveNext').addEventListener('click', () => { liveState.page++; loadLiveProducts(); });
function jumpToLivePage() {
  const page = Number.parseInt($('#livePageInput').value, 10);
  if (!Number.isInteger(page) || page < 1) { showToast('请输入大于 0 的页码'); $('#livePageInput').focus(); return; }
  if (liveState.pageSummary?.status === 'ready' && page > liveState.pageSummary.totalPages) {
    showToast(`采集箱共 ${liveState.pageSummary.totalPages} 页`); $('#livePageInput').focus(); return;
  }
  if (page === liveState.page) return;
  liveState.page = page; loadLiveProducts();
}
$('#livePageGo').addEventListener('click', jumpToLivePage);
$('#livePageInput').addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); jumpToLivePage(); } });
$$('.live-flow-step').forEach((button) => button.addEventListener('click', () => navigateLiveFlow(Number(button.dataset.flowStep))));
$('#liveShop').addEventListener('change', checkLiveReadiness);
$('#liveSite').addEventListener('change', checkLiveReadiness);
$('#liveRecheck').addEventListener('click', checkLiveReadiness);
$('#livePublish').addEventListener('click', publishLiveProduct);
$('#liveAddAutomation').addEventListener('click', addToAutomationPool);
$('#livePoolSelectAll').addEventListener('change', (event) => {
  if (event.target.checked) liveState.products.forEach((product) => liveState.poolSelected.set(Number(product.collectBoxDetailId), product));
  else liveState.products.forEach((product) => liveState.poolSelected.delete(Number(product.collectBoxDetailId)));
  renderLiveProductRows();
});
$('#livePoolClearSelection').addEventListener('click', () => { liveState.poolSelected.clear(); renderLiveProductRows(); });
$('#refreshAutomationPool').addEventListener('click', loadAutomationPool);
$('#refreshAutomationJobs').addEventListener('click', loadAutomationJobs);
$$('[data-save-filter]').forEach(button => button.addEventListener('click', () => setAutomationSaveFilter(button.dataset.saveFilter)));
$$('[data-automation-claim-filter]').forEach(button => button.addEventListener('click', () => setAutomationPoolClaimFilter(button.dataset.automationClaimFilter)));
$('#retryAutomationJobsBatch').addEventListener('click', retryAutomationJobsBatch);
$('#batchFreightTemplate').addEventListener('change', (event) => {
  automationState.batchFreightTemplateId = event.target.value;
  renderBatchFreightTemplateControls();
});
$('#batchFreightTemplateSubmit').addEventListener('click', batchUpdateFreightTemplates);
$('#batchWarehouse').addEventListener('change', (event) => {
  automationState.batchWarehouseId = event.target.value;
  renderBatchWarehouseControls();
});
$('#batchWarehouseSubmit').addEventListener('click', batchUpdateWarehouses);
$('#selectAllAutomationJobs').addEventListener('click', toggleSelectAllAutomationJobs);
$('#deleteAutomationJobsBatch').addEventListener('click', deleteSelectedAutomationJobs);
$('#clearAutomationJobs').addEventListener('click', clearAutomationJobs);
$('#refreshPublishQueue').addEventListener('click', loadAutomationJobs);
$('#selectAllPublishQueue').addEventListener('click', selectAllPublishQueue);
$('#removePublishQueueBatch').addEventListener('click', () => removePublishQueueJobs([...automationState.selectedPublishJobIds]));
$('#publishReadyAutomationJobs').addEventListener('click', publishReadyAutomationJobs);
$('#automationOpenTasks').addEventListener('click', () => activatePage('tasks'));
$('#automationClaimShop').addEventListener('change', () => { saveAutomationPreferences(); renderAutomationPool(); automationClaimFeedback(''); });
$('#automationTargetMode').addEventListener('change', () => {
  automationState.targetMode = $('#automationTargetMode').value;
  renderAutomationPool(); automationClaimFeedback('');
  if (automationState.targetMode === 'personnel') loadAutomationPersonnel(true);
});
$('#automationPersonnelRefresh').addEventListener('click', () => loadAutomationPersonnel(true, true));
$('#automationPersonnelBindings').addEventListener('click', openPersonnelBindings);
$('#personnelBindingPerson').addEventListener('change', renderPersonnelBindingShops);
$('#personnelBindingSave').addEventListener('click', savePersonnelBinding);
$('#personnelBindingModal').addEventListener('keydown', event => {
  if (event.key === 'Escape') { event.preventDefault(); closePersonnelBindings(); }
  if (event.key === 'Tab') {
    const fields = [...$('#personnelBindingModal').querySelectorAll('button:not(:disabled), select:not(:disabled), input:not(:disabled), summary')];
    const first = fields[0], last = fields.at(-1);
    if (!first) { event.preventDefault(); return; }
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});
$('#automationPublishSite').addEventListener('change', () => { saveAutomationPreferences(); updateAutomationControls(); });
$('#publishQueuePublishInterval').addEventListener('change', saveAutomationPreferences);
$('#publishQueuePublishCount').addEventListener('change', () => { saveAutomationPreferences(); renderPublishProgress(); });
$('#publishQueueShop').addEventListener('change', (event) => {
  changePublishQueueShops(event.target);
});
$('#automationSkuPrice').addEventListener('change', saveAutomationPreferences);
$('#automationPriceMode').addEventListener('change', () => { updateAutomationPriceMode(); saveAutomationPreferences(); });
$('#automationPriceMultiplier').addEventListener('change', saveAutomationPreferences);
$('#automationEditConcurrency').addEventListener('change', saveAutomationPreferences);
$('#automationSelectAll').addEventListener('change', (event) => selectAutomationPoolPage(event.target.checked));
$('#automationPoolPrev').addEventListener('click', () => changeAutomationPoolPage(automationState.poolPage - 1));
$('#automationPoolNext').addEventListener('click', () => changeAutomationPoolPage(automationState.poolPage + 1));
$('#automationPoolPageGo').addEventListener('click', () => changeAutomationPoolPage(Number($('#automationPoolPageInput').value)));
$('#automationPoolPageInput').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') { event.preventDefault(); changeAutomationPoolPage(Number(event.target.value)); }
});
$('#automationClearSelection').addEventListener('click', () => { automationState.selected.clear(); renderAutomationPool(); });
$('#automationBatchRemove').addEventListener('click', removeAutomationPoolSelection);
$('#automationClaimSubmit').addEventListener('click', claimAutomationSelection);
$('#restoreDeletedAutomationJobsBatch').addEventListener('click', restoreDeletedAutomationJobsBatch);
$('#refreshPublishRecords').addEventListener('click', loadLiveRecords);
$('#syncOrders').addEventListener('click', syncOrders);
$('#syncCollectionInfo')?.addEventListener('click', syncCollectionInformation);
$('#configureCollectionSync')?.addEventListener('click', syncCollectionInformation);
$('#collectionCredentialForm')?.addEventListener('submit', saveCollectionCredentials);
$('#refreshCollectionAccountManager')?.addEventListener('click', loadCollectionAccountManager);
$('#deepSeekSettingsForm')?.addEventListener('submit', saveDeepSeekSettings);
$('#removeDeepSeekSettings')?.addEventListener('click', () => saveDeepSeekSettings(null, true));
$('#refreshDeepSeekSettings')?.addEventListener('click', loadDeepSeekSettings);
$('#refreshMiaoshouShops')?.addEventListener('click', refreshMiaoshouShops);
$('#deepSeekProfileForm')?.addEventListener('submit', saveDeepSeekProfile);
$('#refreshDeepSeekProfiles')?.addEventListener('click', loadDeepSeekProfiles);
window.addEventListener('pagehide', clearDeepSeekInput);
window.addEventListener('pageshow', clearDeepSeekInput);
window.addEventListener('pagehide', clearCollectionCredentialInputs);
window.addEventListener('pageshow', clearCollectionCredentialInputs);
$('#openCollectionOrders')?.addEventListener('click', () => openOrderAccount(activeMiaoshouCollectionAccountId));
$('#orderShopFilter').addEventListener('change', () => { if (latestOrderData) renderOrders(latestOrderData); });
window.addEventListener('live-editor-open', () => { liveState.editing = true; liveState.editorStage = 2; renderLiveFlow(); });
window.addEventListener('live-editor-stage', (event) => { liveState.editorStage = event.detail?.stage || 2; if (liveState.editing) renderLiveFlow(); });
window.addEventListener('live-editor-closed', () => { liveState.editing = false; liveState.editorStage = 2; renderLiveFlow(); });
window.addEventListener('live-content-saved', () => { liveState.editing = false; liveState.editorStage = 2; renderLiveFlow(); });
$$('.nav-item').forEach((button) => {
  if (button.id === 'miaoshouCollectionToggle') bindCollectionFolderNavigation(button);
  else if (button.id === 'orderAccountsToggle') button.addEventListener('click', () => setOrderAccountsExpanded(!orderAccountsExpanded));
  else if (button.dataset.orderAccountId) button.addEventListener('click', () => openOrderAccount(Number(button.dataset.orderAccountId)));
  else if (button.dataset.accountId) bindCollectionAccountNavigation(button);
  else button.addEventListener('click', () => activatePage(button.dataset.page));
});
$('#addEmptyCollection')?.addEventListener('click', () => mutateEmptyCollection());
$('#deleteEmptyCollection')?.addEventListener('click', () => mutateEmptyCollection(activeMiaoshouCollectionAccountId));
renderHistory(); renderTasks();
document.body.classList.add('workbench-active');
loadMiaoshouCollectionAccounts();
const rememberedAutomationPreferences = automationPreferences();
// Migrate the previous unversioned 10-second default once; explicit newer choices win.
if (/^\d+$/.test(String(rememberedAutomationPreferences.intervalSeconds || '')) &&
    (rememberedAutomationPreferences.publishIntervalDefaultVersion === 2 ||
     Number(rememberedAutomationPreferences.intervalSeconds) !== 10))
  $('#publishQueuePublishInterval').value = rememberedAutomationPreferences.intervalSeconds;
if (/^\d+$/.test(String(rememberedAutomationPreferences.publishCount || '')))
  $('#publishQueuePublishCount').value = rememberedAutomationPreferences.publishCount;
if (/^\d+$/.test(String(rememberedAutomationPreferences.publishQueueShopId || '')))
  automationState.publishQueueShopId = rememberedAutomationPreferences.publishQueueShopId;
if (Array.isArray(rememberedAutomationPreferences.publishQueueShopIds))
  automationState.publishQueueShopIds = [...new Set(rememberedAutomationPreferences.publishQueueShopIds.map(String).filter(id => /^\d+$/.test(id) && Number.isSafeInteger(Number(id)) && Number(id) > 0))];
if (/^\d+$/.test(String(rememberedAutomationPreferences.editConcurrency || '')))
  $('#automationEditConcurrency').value = rememberedAutomationPreferences.editConcurrency;
if (['floor', 'source_multiplier'].includes(String(rememberedAutomationPreferences.priceMode || '')))
  $('#automationPriceMode').value = rememberedAutomationPreferences.priceMode;
if (/^\d+(?:\.\d{1,4})?$/.test(String(rememberedAutomationPreferences.priceMultiplier || '')))
  $('#automationPriceMultiplier').value = rememberedAutomationPreferences.priceMultiplier;
updateAutomationPriceMode();
organizeLiveModules();
setupPaneResizers();
checkApiStatus();
loadAutomationJobs();
