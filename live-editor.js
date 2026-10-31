/* Real MiaoShou half-managed collect-box editor. No request is sent until Save. */
const editorState = { kind: null, mode: 'site', detailId: null, version: null, item: null, targetShopId: '', targetSite: '', logisticsVersion: 0, categoryTree: null, categoryPath: [], categoryRequestVersion: 0, attributeRules: [], attributeRulesCid: '', attributeRequestVersion: 0, requestedAttributeNames: [], claimedShopIds: [], shopConfigs: {}, claimSubmitted: false, descriptionLocalImages: [] };
const editEl = (id) => document.getElementById(id);
const cloneItem = (value) => JSON.parse(JSON.stringify(value));
const CATEGORY_SESSION_KEY = 'temu-miaoshou-category-tree-v1';
let titlePolishTarget = '';
let imageAiSourceUrl = '';
let imageAiResultDataUrl = '';
let imageDragState = null;
let touchImageDragState = null;

function titleWeightedLength(value) {
  return [...value].reduce((total, character) => total + (character.codePointAt(0) > 127 ? 2 : 1), 0);
}

function updateTitleCounts() {
  [['editTitle', 'editTitleCount'], ['editEnglishTitle', 'editEnglishTitleCount']].forEach(([inputId, countId]) => {
    const count = titleWeightedLength(editEl(inputId).value);
    const indicator = editEl(countId);
    indicator.textContent = `${count} / 240 加权字符（中文计 2）`;
    indicator.classList.toggle('over-limit', count > 240);
  });
}

function updateTitlePolishCount() {
  const count = titleWeightedLength(editEl('aiTitlePolishDraft').value.trim());
  editEl('aiTitlePolishMeta').textContent = count > 240
    ? `建议稿 ${count} / 240 加权字符，已超限；请删减后应用。`
    : `建议稿 ${count} / 240 加权字符；目标 230-240（中文计 2）。`;
}

function editorError(message) {
  const target = editEl('liveEditorError');
  target.textContent = message || '';
  target.classList.toggle('hidden', !message);
}

function claimStatus(message, tone = 'error') {
  const target = editEl('shopClaimStatus');
  target.textContent = message || '';
  target.className = `claim-status ${tone}${message ? '' : ' hidden'}`;
}

function copySelectOptions(source, target, placeholder) {
  target.replaceChildren();
  const initial = document.createElement('option');
  initial.value = ''; initial.textContent = placeholder; target.append(initial);
  [...source.options].filter((option) => option.value).forEach((option) => {
    const copied = document.createElement('option');
    copied.value = option.value; copied.textContent = option.textContent; target.append(copied);
  });
}

function availableEditorShops() {
  return [...editEl('liveShop').options].filter((option) => option.value)
    .map((option) => ({ id: String(option.value), label: option.textContent.trim() }));
}

function rememberCurrentShopConfig() {
  const shopId = editEl('editShop').value;
  if (!shopId) return;
  const freightId = editEl('editFreight').value, warehouseId = editEl('editWarehouse').value;
  if (freightId && warehouseId) editorState.shopConfigs[shopId] = { freightId, warehouseId };
}

function populateConfiguredShopSelect() {
  const select = editEl('editShop');
  const previous = select.value;
  select.replaceChildren();
  const shops = availableEditorShops().filter((shop) => editorState.claimedShopIds.includes(shop.id));
  addSelectOption(select, '', shops.length ? '请选择要配置的店铺' : '请先认领目标店铺');
  shops.forEach((shop) => addSelectOption(select, shop.id, shop.label));
  const chosen = shops.some((shop) => shop.id === previous) ? previous : shops[0]?.id || '';
  select.value = chosen;
  select.disabled = editorState.mode === 'shop';
}

function renderClaimedShops() {
  const choices = availableEditorShops();
  const holder = editEl('claimShopChecks');
  const summary = editEl('claimedShopsSummary');
  holder.replaceChildren(); summary.replaceChildren();
  if (!choices.length) {
    const empty = document.createElement('p'); empty.className = 'claimed-shops-empty'; empty.textContent = '店铺正在读取中…'; holder.append(empty);
    return;
  }
  choices.forEach((shop) => {
    const label = document.createElement('label'); label.className = 'claim-shop-option';
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.value = shop.id;
    checkbox.checked = editorState.claimedShopIds.includes(shop.id);
    checkbox.disabled = editorState.mode === 'shop';
    checkbox.addEventListener('change', () => {
      rememberCurrentShopConfig();
      editorState.claimedShopIds = [...holder.querySelectorAll('input:checked')].map((input) => input.value);
      editorState.claimSubmitted = false;
      populateConfiguredShopSelect();
      renderClaimedShops();
      loadEditorLogistics();
    });
    const text = document.createElement('span'); text.textContent = shop.label;
    label.append(checkbox, text); holder.append(label);
  });
  const claimed = choices.filter((shop) => editorState.claimedShopIds.includes(shop.id));
  if (!claimed.length) {
    const empty = document.createElement('span'); empty.className = 'claimed-shops-empty'; empty.textContent = '请在第 2 步至少选择一个目标店铺'; summary.append(empty);
  } else {
    claimed.forEach((shop) => { const tag = document.createElement('span'); tag.className = 'claimed-shop-tag'; tag.textContent = shop.label; summary.append(tag); });
  }
}

function initializeClaimedShops(item) {
  const available = availableEditorShops();
  const existing = (item.collectBoxDetailShopList || []).map((row) => String(row.shopId));
  const fallback = editorState.targetShopId || editEl('liveShop').value || String(item.shopId || '');
  editorState.claimedShopIds = [...new Set([...existing, fallback])].filter((id) => available.some((shop) => shop.id === id));
  editorState.claimSubmitted = false;
  editorState.shopConfigs = {};
  const site = editEl('editSite').value;
  (item.collectBoxDetailShopList || []).forEach((row) => {
    const freightId = (row.siteFreightTemplateList || []).find((entry) => entry.site === site)?.freightTemplateId;
    const warehouseId = (row.siteWarehouseList || []).find((entry) => entry.site === site)?.warehouseId;
    if (freightId && warehouseId) editorState.shopConfigs[String(row.shopId)] = { freightId: String(freightId), warehouseId: String(warehouseId) };
  });
  populateConfiguredShopSelect();
  renderClaimedShops();
}

function blankItem() {
  return {
    title: '', itemNum: '', cid: '', productOriginCountry: 'CN', productOriginProvince: '', manufacturingLocationRegionShortNameList: ['CN'],
    imgUrls: [], goodsLayerDecorationReqs: [], attributes: [],
    saleAttributes: [{ name: '规格', parentSpecId: 0, values: [{ name: '默认', skuKey: '18c63459' }] }],
    skuMap: { ';18c63459;': { itemNum: '', length: '', width: '', height: '', price: '', weight: '', imgUrl: '' } },
    publishSiteList: [], collectBoxDetailShopList: []
  };
}

function currentEditorRow() {
  const shopId = editEl('editShop').value;
  const item = editorState.item || {};
  return editorState.mode === 'site'
    ? (item.collectBoxDetailShopList || []).find((row) => String(row.shopId) === shopId) || {}
    : item;
}

function addSelectOption(select, value, label) {
  const option = document.createElement('option');
  option.value = String(value); option.textContent = label; select.append(option);
}

function categoryNodes(tree) {
  return Object.values(tree || {}).filter((node) => node && typeof node === 'object')
    .sort((left, right) => categoryName(left).localeCompare(categoryName(right), 'zh-CN'));
}

function categoryName(node) {
  return String(node?.nameChinese || node?.name || node?.cid || '未命名类目');
}

function categoryIsLeaf(node) {
  const lastLevel = String(node?.isLastLevel || '').toLowerCase();
  return lastLevel === '1' || lastLevel === 'true' || !categoryNodes(node?.children).length;
}

function findCategoryPath(tree, cid, trail = []) {
  for (const node of categoryNodes(tree)) {
    const nextTrail = [...trail, node];
    if (String(node.cid) === String(cid)) return nextTrail;
    const found = findCategoryPath(node.children, cid, nextTrail);
    if (found) return found;
  }
  return null;
}

function updateCategorySummary() {
  const currentId = editEl('editCid').value.trim();
  const path = editorState.categoryPath;
  const pathText = path.length ? path.map(categoryName).join(' / ') : '';
  editEl('categoryPath').textContent = pathText || (currentId ? `已同步类目 ID：${currentId}` : '选择产品类别');
  const hint = editEl('categoryHint');
  hint.textContent = pathText && categoryIsLeaf(path[path.length - 1])
    ? '已选择末级类目，保存商品时会写入妙手。'
    : currentId && !pathText
      ? '已保留同步类目；打开选择器可查看或更换层级。'
      : '按层级选择，最后一级会自动保存为妙手类目。';
}

function renderCategoryPicker() {
  const holder = editEl('categoryColumns');
  holder.replaceChildren();
  const tree = editorState.categoryTree;
  if (!tree) {
    const empty = document.createElement('p'); empty.className = 'category-picker-empty'; empty.textContent = '正在读取产品类别…'; holder.append(empty);
    return;
  }
  let currentLevel = tree;
  for (let depth = 0; ; depth += 1) {
    const nodes = categoryNodes(currentLevel);
    if (!nodes.length) break;
    const column = document.createElement('div'); column.className = 'category-column';
    const selected = editorState.categoryPath[depth];
    nodes.forEach((node) => {
      const option = document.createElement('button'); option.type = 'button'; option.className = 'category-option';
      option.classList.toggle('selected', String(selected?.cid) === String(node.cid));
      option.disabled = Boolean(node.disabled);
      const name = document.createElement('span'); name.textContent = categoryName(node);
      option.append(name);
      if (!categoryIsLeaf(node)) { const arrow = document.createElement('span'); arrow.className = 'category-next'; arrow.textContent = '›'; option.append(arrow); }
      option.addEventListener('click', () => selectCategoryNode(node, depth));
      column.append(option);
    });
    holder.append(column);
    if (!selected || categoryIsLeaf(selected)) break;
    currentLevel = selected.children;
  }
  if (!holder.childElementCount) {
    const empty = document.createElement('p'); empty.className = 'category-picker-empty'; empty.textContent = '暂无可选择的产品类别'; holder.append(empty);
  }
}

function selectCategoryNode(node, depth) {
  editorState.categoryPath = [...editorState.categoryPath.slice(0, depth), node];
  editEl('editCid').value = categoryIsLeaf(node) ? String(node.cid) : '';
  updateCategorySummary();
  if (categoryIsLeaf(node)) { closeCategoryPicker(); loadAttributeRules(String(node.cid)); }
  else renderCategoryPicker();
}

function closeCategoryPicker() {
  editEl('categoryPicker').classList.add('hidden');
  editEl('categoryTrigger').setAttribute('aria-expanded', 'false');
}

async function loadCategoryTree() {
  if (editorState.categoryTree) {
    renderCategoryPicker();
    return;
  }
  const requestVersion = ++editorState.categoryRequestVersion;
  renderCategoryPicker();
  try {
    const stored = sessionStorage.getItem(CATEGORY_SESSION_KEY);
    if (stored) {
      const cachedTree = JSON.parse(stored);
      if (cachedTree && typeof cachedTree === 'object') {
        editorState.categoryTree = cachedTree;
        const cid = editEl('editCid').value.trim();
        editorState.categoryPath = cid ? (findCategoryPath(cachedTree, cid) || []) : [];
        updateCategorySummary();
        renderCategoryPicker();
        return;
      }
    }
    const response = await apiGet('/api/categories');
    if (requestVersion !== editorState.categoryRequestVersion) return;
    const tree = response?.cateTree;
    if (!tree || typeof tree !== 'object') throw new Error('妙手未返回产品类别');
    editorState.categoryTree = tree;
    try {
      const encoded = JSON.stringify(tree);
      if (encoded.length < 3_500_000) sessionStorage.setItem(CATEGORY_SESSION_KEY, encoded);
    } catch (_) { /* Cache is an optional speed-up only. */ }
    const cid = editEl('editCid').value.trim();
    editorState.categoryPath = cid ? (findCategoryPath(tree, cid) || []) : [];
    updateCategorySummary();
    renderCategoryPicker();
  } catch (error) {
    if (requestVersion !== editorState.categoryRequestVersion) return;
    editorState.categoryTree = null;
    const hint = editEl('categoryHint');
    hint.textContent = `产品类别读取失败：${error.message}。点击后可重试。`;
    const holder = editEl('categoryColumns'); holder.replaceChildren();
    const empty = document.createElement('p'); empty.className = 'category-picker-empty'; empty.textContent = '产品类别读取失败，点击输入框重试'; holder.append(empty);
  }
}

async function toggleCategoryPicker() {
  setEditorStage(3);
  const picker = editEl('categoryPicker');
  const opening = picker.classList.contains('hidden');
  if (!opening) { closeCategoryPicker(); return; }
  picker.classList.remove('hidden');
  editEl('categoryTrigger').setAttribute('aria-expanded', 'true');
  await loadCategoryTree();
}

async function loadEditorLogistics() {
  const shopId = editEl('editShop').value, site = editEl('editSite').value;
  const requestVersion = ++editorState.logisticsVersion;
  const freightSelect = editEl('editFreight'), warehouseSelect = editEl('editWarehouse');
  freightSelect.replaceChildren(); warehouseSelect.replaceChildren();
  addSelectOption(freightSelect, '', '正在读取…'); addSelectOption(warehouseSelect, '', '正在读取…');
  if (!shopId || !site) {
    freightSelect.options[0].textContent = '先选择店铺和站点';
    warehouseSelect.options[0].textContent = '先选择店铺和站点';
    return;
  }
  try {
    const response = await apiGet(`/api/logistics?shopId=${encodeURIComponent(shopId)}`);
    if (requestVersion !== editorState.logisticsVersion) return;
    const freight = response.freight?.shopIdAndFreightTemplateListMap?.[shopId] || [];
    const warehouses = response.warehouse?.shopIdAndWarehouseListMap?.[shopId] || [];
    freightSelect.replaceChildren(); warehouseSelect.replaceChildren();
    addSelectOption(freightSelect, '', '请选择运费模板');
    addSelectOption(warehouseSelect, '', '请选择发货仓库');
    freight.filter((row) => row.site === site).forEach((row) => addSelectOption(freightSelect, row.templateId, row.templateName || String(row.templateId)));
    // MiaoShou documents warehouseDisable as a currently unused field. Do not
    // discard returned warehouses based on it, otherwise valid warehouses with
    // the common value "1" disappear from the selector.
    warehouses.filter((row) => row.site === site).forEach((row) => addSelectOption(warehouseSelect, row.warehouseId, row.warehouseName || String(row.warehouseId)));
    const saved = currentEditorRow();
    const configured = editorState.shopConfigs[shopId] || {};
    const savedFreight = configured.freightId || (saved.siteFreightTemplateList || []).find((row) => row.site === site)?.freightTemplateId;
    const savedWarehouse = configured.warehouseId || (saved.siteWarehouseList || []).find((row) => row.site === site)?.warehouseId;
    if (savedFreight && [...freightSelect.options].some((option) => option.value === String(savedFreight))) {
      freightSelect.value = String(savedFreight);
    } else {
      const defaultFreight = [...freightSelect.options].find((option) => option.textContent.trim() === '通用');
      if (defaultFreight) freightSelect.value = defaultFreight.value;
    }
    if (savedWarehouse && [...warehouseSelect.options].some((option) => option.value === String(savedWarehouse))) {
      warehouseSelect.value = String(savedWarehouse);
    } else {
      const defaultWarehouse = [...warehouseSelect.options].find((option) => option.textContent.trim() === '发货');
      if (defaultWarehouse) warehouseSelect.value = defaultWarehouse.value;
    }
    if (site === 'EU') editorError('欧盟站需要配置多个子站点仓库；目前请在妙手后台完成该站物流配置。');
    else editorError('');
    rememberCurrentShopConfig();
    renderEditorSkus();
  } catch (error) {
    if (requestVersion !== editorState.logisticsVersion) return;
    freightSelect.options[0].textContent = '读取失败';
    warehouseSelect.options[0].textContent = '读取失败';
    editorError(`物流选项读取失败：${error.message}`);
  }
}

function skuInput(label, value, field, type = 'text') {
  const wrapper = document.createElement('label');
  wrapper.className = 'sku-table-cell';
  const accessibleLabel = document.createElement('span'); accessibleLabel.className = 'sr-only'; accessibleLabel.textContent = label;
  const input = document.createElement('input');
  input.type = type; input.value = value == null ? '' : String(value); input.dataset.field = field;
  if (type === 'number') { input.min = '0'; input.step = field === 'stock' || field === 'weight' ? '1' : '0.01'; }
  wrapper.append(accessibleLabel, input);
  return wrapper;
}

function skuVariantName(key, index) {
  const keys = key.split(';').filter(Boolean);
  const names = [];
  (editorState.item?.saleAttributes || []).forEach((attribute) => {
    (attribute.values || []).forEach((value) => {
      if (keys.includes(String(value.skuKey)) && value.name) names.push(`${attribute.name || '规格'}：${value.name}`);
    });
  });
  return names.length ? names.join(' / ') : `变体 ${index + 1}`;
}

function skuImageCell(value, key) {
  const cell = document.createElement('div'); cell.className = 'sku-image-cell';
  const frame = document.createElement('div'); frame.className = 'sku-image-frame';
  const image = document.createElement('img'); image.alt = 'SKU 图片预览'; image.loading = 'lazy';
  const fallback = document.createElement('span'); fallback.textContent = '暂无图片';
  const localHint = document.createElement('small'); localHint.className = 'sku-image-local-hint hidden'; localHint.textContent = '本地预览，保存前需图片链接';
  let localPreviewUrl = '';
  const refresh = () => {
    const valid = Boolean(localPreviewUrl) || /^https?:\/\//i.test(input.value.trim());
    image.hidden = !valid; fallback.hidden = valid;
    if (valid) image.src = localPreviewUrl || input.value.trim(); else image.removeAttribute('src');
  };
  image.addEventListener('error', () => { image.hidden = true; fallback.hidden = false; fallback.textContent = '图片无法加载'; });
  frame.append(image, fallback);
  const input = document.createElement('input');
  input.type = 'url'; input.dataset.field = 'imgUrl'; input.value = value == null ? '' : String(value);
  input.className = 'sku-image-url hidden'; input.placeholder = '粘贴 SKU 图片链接';
  input.addEventListener('input', () => {
    if (localPreviewUrl) URL.revokeObjectURL(localPreviewUrl);
    localPreviewUrl = ''; delete input.dataset.localPreview; localHint.classList.add('hidden'); refresh();
  });
  const localFile = document.createElement('input'); localFile.type = 'file'; localFile.accept = 'image/*'; localFile.className = 'hidden';
  localFile.addEventListener('change', () => {
    const file = localFile.files?.[0];
    if (!file) return;
    if (!file.type.startsWith('image/')) { showToast('请选择图片文件'); localFile.value = ''; return; }
    if (localPreviewUrl) URL.revokeObjectURL(localPreviewUrl);
    localPreviewUrl = URL.createObjectURL(file); input.value = ''; input.dataset.localPreview = 'true'; localHint.classList.remove('hidden'); refresh();
  });
  const change = document.createElement('button'); change.type = 'button'; change.className = 'sku-image-change'; change.setAttribute('aria-label', '本地上传 SKU 图片'); change.textContent = '⇧';
  change.addEventListener('click', () => localFile.click());
  const toolbar = document.createElement('div'); toolbar.className = 'sku-image-toolbar';
  const network = document.createElement('button'); network.type = 'button'; network.textContent = '本地上传';
  network.addEventListener('click', () => localFile.click());
  const useUrl = document.createElement('button'); useUrl.type = 'button'; useUrl.textContent = '图片链接';
  useUrl.addEventListener('click', () => { input.classList.remove('hidden'); input.focus(); });
  toolbar.append(change, network, useUrl);
  cell.append(frame, toolbar, localHint, localFile, input); refresh();
  return cell;
}

function renderEditorSkus() {
  const holder = editEl('editSkuRows');
  const prior = new Map([...holder.querySelectorAll('.live-sku-row')].map((row) =>
    [row.dataset.skuKey, Object.fromEntries([...row.querySelectorAll('[data-field]')].map((input) => [input.dataset.field, input.value]))]));
  holder.replaceChildren();
  const site = editEl('editSite').value, warehouseId = editEl('editWarehouse').value;
  const table = document.createElement('div'); table.className = 'live-sku-table';
  const header = document.createElement('div'); header.className = 'live-sku-table-header';
  ['SKU 图片', '规格', 'SKU 货号', '申报价 (人民币)', '库存', '重量 (g)', '包装尺寸 (cm)'].forEach((name) => {
    const cell = document.createElement('span'); cell.textContent = name; header.append(cell);
  });
  table.append(header);
  Object.entries(editorState.item?.skuMap || {}).forEach(([key, sku], index) => {
    const row = document.createElement('div'); row.className = 'live-sku-row'; row.dataset.skuKey = key;
    const stock = sku.siteAndWarehouseIdAndStockMap?.[site]?.[warehouseId] ?? sku.stock ?? '';
    row.append(skuImageCell(prior.get(key)?.imgUrl ?? sku.imgUrl, key));
    const variant = document.createElement('div'); variant.className = 'sku-variant';
    const variantName = document.createElement('strong'); variantName.textContent = skuVariantName(key, index);
    variant.append(variantName); row.append(variant);
    row.append(skuInput('SKU 货号', prior.get(key)?.itemNum ?? sku.itemNum, 'itemNum', 'text'));
    row.append(skuInput('申报价（人民币）', prior.get(key)?.price ?? sku.price, 'price', 'number'));
    row.append(skuInput('库存', prior.get(key)?.stock ?? stock, 'stock', 'number'));
    row.append(skuInput('重量（g）', prior.get(key)?.weight ?? sku.weight, 'weight', 'number'));
    const dimensions = document.createElement('div'); dimensions.className = 'sku-dimensions';
    [['长', 'length'], ['宽', 'width'], ['高', 'height']].forEach(([label, field]) => {
      const input = document.createElement('input'); input.type = 'number'; input.min = '0'; input.step = '0.01'; input.dataset.field = field; input.value = prior.get(key)?.[field] ?? sku[field] ?? ''; input.setAttribute('aria-label', `${label}（cm）`); dimensions.append(input);
    });
    row.append(dimensions); table.append(row);
  });
  holder.append(table);
}

function syncSaleAttributesJson() {
  editEl('editSaleAttributes').value = JSON.stringify(editorState.item?.saleAttributes || [], null, 2);
}

function newSkuKey() {
  const occupied = new Set((editorState.item?.saleAttributes || []).flatMap((attribute) =>
    (attribute.values || []).map((value) => String(value.skuKey || ''))));
  let key = '';
  do { key = Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, '0'); } while (occupied.has(key));
  return key;
}

function emptySku() {
  return { itemNum: '', length: '', width: '', height: '', price: '', weight: '', imgUrl: '', siteAndWarehouseIdAndStockMap: {} };
}

function skuKeyFor(values) {
  return `;${values.map((value) => value.skuKey).join(';')};`;
}

function skuCombinations(attributes) {
  const groups = attributes.filter((attribute) => Array.isArray(attribute.values) && attribute.values.length);
  return groups.reduce((all, attribute) => all.flatMap((prefix) => attribute.values.map((value) => [...prefix, value])), [[]]);
}

function rebuildSkuMap() {
  const item = editorState.item;
  const previous = item.skuMap || {};
  const combinations = skuCombinations(item.saleAttributes || []);
  const next = {};
  combinations.forEach((values) => {
    const key = skuKeyFor(values);
    const keys = new Set(values.map((value) => String(value.skuKey)));
    const closest = previous[key] || Object.entries(previous).find(([oldKey]) => {
      const oldKeys = new Set(oldKey.split(';').filter(Boolean));
      return [...keys].every((value) => oldKeys.has(value));
    })?.[1] || Object.values(previous)[0] || emptySku();
    next[key] = cloneItem(closest);
  });
  item.skuMap = next;
}

function updateSkuStructure() {
  rebuildSkuMap();
  syncSaleAttributesJson();
  renderSkuSpecs();
  renderEditorSkus();
}

function renderSkuSpecs() {
  const holder = editEl('skuSpecGroups');
  holder.replaceChildren();
  const attributes = editorState.item?.saleAttributes || [];
  if (!attributes.length) {
    const empty = document.createElement('p'); empty.className = 'sku-spec-empty'; empty.textContent = '暂无规格。可点击“新增规格”创建。'; holder.append(empty);
    return;
  }
  attributes.forEach((attribute, attributeIndex) => {
    const group = document.createElement('section'); group.className = 'sku-spec-group';
    const head = document.createElement('div'); head.className = 'sku-spec-group-head';
    const nameLabel = document.createElement('label'); nameLabel.textContent = '规格名称';
    const nameInput = document.createElement('input'); nameInput.value = attribute.name || ''; nameInput.placeholder = '如：颜色';
    nameInput.addEventListener('input', () => { attribute.name = nameInput.value; syncSaleAttributesJson(); });
    nameLabel.append(nameInput);
    const removeGroup = document.createElement('button'); removeGroup.type = 'button'; removeGroup.className = 'sku-spec-remove'; removeGroup.textContent = '删除规格';
    removeGroup.addEventListener('click', () => { editorState.item.saleAttributes.splice(attributeIndex, 1); updateSkuStructure(); });
    head.append(nameLabel, removeGroup); group.append(head);
    const values = document.createElement('div'); values.className = 'sku-spec-values';
    (attribute.values || []).forEach((value, valueIndex) => {
      const row = document.createElement('div'); row.className = 'sku-spec-value';
      const input = document.createElement('input'); input.value = value.name || ''; input.placeholder = '规格值';
      input.addEventListener('input', () => { value.name = input.value; syncSaleAttributesJson(); });
      input.addEventListener('change', renderEditorSkus);
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'sku-spec-value-remove'; remove.textContent = '×'; remove.title = '删除该规格值';
      remove.addEventListener('click', () => { attribute.values.splice(valueIndex, 1); updateSkuStructure(); });
      row.append(input, remove); values.append(row);
    });
    const addValue = document.createElement('button'); addValue.type = 'button'; addValue.className = 'sku-spec-add-value'; addValue.textContent = '＋ 新增规格值';
    addValue.addEventListener('click', () => {
      attribute.values ||= []; attribute.values.push({ name: '新规格值', skuKey: newSkuKey() }); updateSkuStructure();
    });
    group.append(values, addValue); holder.append(group);
  });
}

function addSkuSpec() {
  editorState.item.saleAttributes ||= [];
  editorState.item.saleAttributes.push({ name: '新规格', parentSpecId: 0, values: [{ name: '新规格值', skuKey: newSkuKey() }] });
  updateSkuStructure();
}

function editorDescription(item) {
  return (item.goodsLayerDecorationReqs || []).find((module) => module.type === 'text')?.content?.text || '';
}

function getDescriptionImageModules() {
  return (editorState.item?.goodsLayerDecorationReqs || [])
    .map((module, index) => ({ module, index, url: String(module?.content?.imgUrl || '').trim() }))
    .filter(({ module, url }) => module?.type === 'image' && url);
}

function clearDescriptionLocalImages() {
  editorState.descriptionLocalImages.forEach((entry) => URL.revokeObjectURL(entry.previewUrl));
  editorState.descriptionLocalImages = [];
}

function openImagePreview(url, caption) {
  if (!url) return;
  const preview = editEl('imagePreviewLarge');
  preview.src = url; preview.alt = caption || '图片预览';
  editEl('imagePreviewCaption').textContent = caption || '图片预览';
  editEl('imagePreviewModal').classList.remove('hidden');
}

function clearImageDragState() {
  document.querySelectorAll('.image-sortable-dragging, .image-sortable-over').forEach((card) => {
    card.classList.remove('image-sortable-dragging', 'image-sortable-over');
    card.setAttribute('aria-grabbed', 'false');
  });
  imageDragState = null;
}

function clearTouchImageDragState() {
  document.querySelectorAll('.image-sortable-over, .image-sortable-dragging').forEach((card) => {
    card.classList.remove('image-sortable-over', 'image-sortable-dragging');
    card.setAttribute('aria-grabbed', 'false');
  });
  touchImageDragState = null;
}

function enableImageReorder(card, kind, index, onMove) {
  card.classList.add('image-sortable');
  card.draggable = true;
  card.dataset.imageKind = kind;
  card.dataset.imageIndex = String(index);
  card.title = '按住并拖到另一张图片上，可交换顺序';
  card.setAttribute('aria-grabbed', 'false');
  card.addEventListener('dragstart', (event) => {
    if (event.target.closest('button, input')) { event.preventDefault(); return; }
    imageDragState = { kind, index };
    card.classList.add('image-sortable-dragging');
    card.setAttribute('aria-grabbed', 'true');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', `${kind}:${index}`);
  });
  card.addEventListener('dragover', (event) => {
    if (!imageDragState || imageDragState.kind !== kind || imageDragState.index === index) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    card.classList.add('image-sortable-over');
  });
  card.addEventListener('dragleave', () => card.classList.remove('image-sortable-over'));
  card.addEventListener('drop', (event) => {
    if (!imageDragState || imageDragState.kind !== kind || imageDragState.index === index) return;
    event.preventDefault();
    const from = imageDragState.index;
    clearImageDragState();
    onMove(from, index);
  });
  card.addEventListener('dragend', clearImageDragState);
  card.addEventListener('pointerdown', (event) => {
    if (event.pointerType !== 'touch' || event.target.closest('button, input')) return;
    touchImageDragState = { kind, index, onMove, startX: event.clientX, startY: event.clientY, card, moved: false, target: null };
    card.setPointerCapture(event.pointerId);
  });
  card.addEventListener('pointermove', (event) => {
    const state = touchImageDragState;
    if (!state || state.card !== card) return;
    if (!state.moved && Math.hypot(event.clientX - state.startX, event.clientY - state.startY) < 12) return;
    state.moved = true;
    event.preventDefault();
    card.classList.add('image-sortable-dragging');
    const target = document.elementFromPoint(event.clientX, event.clientY)?.closest('.image-sortable');
    document.querySelectorAll('.image-sortable-over').forEach((entry) => entry.classList.remove('image-sortable-over'));
    if (target && target.dataset.imageKind === kind && Number(target.dataset.imageIndex) !== index) {
      target.classList.add('image-sortable-over');
      state.target = target;
    } else state.target = null;
  });
  card.addEventListener('pointerup', (event) => {
    const state = touchImageDragState;
    if (!state || state.card !== card) return;
    if (card.hasPointerCapture(event.pointerId)) card.releasePointerCapture(event.pointerId);
    const targetIndex = state.target ? Number(state.target.dataset.imageIndex) : -1;
    const shouldMove = state.moved && targetIndex >= 0 && targetIndex !== index;
    clearTouchImageDragState();
    if (shouldMove) onMove(index, targetIndex);
  });
  card.addEventListener('pointercancel', clearTouchImageDragState);
}

function moveProductImage(from, to) {
  const urls = getProductImageUrls();
  if (from < 0 || to < 0 || from >= urls.length || to >= urls.length || from === to) return;
  const [moved] = urls.splice(from, 1);
  urls.splice(to, 0, moved);
  setProductImageUrls(urls);
}

function moveDescriptionImage(from, to) {
  const images = getDescriptionImageModules();
  if (from < 0 || to < 0 || from >= images.length || to >= images.length || from === to) return;
  const modules = editorState.item.goodsLayerDecorationReqs;
  const [moved] = modules.splice(images[from].index, 1);
  modules.splice(images[to].index, 0, moved);
  renderDescriptionImagePreview();
}

function openImageAiEditor(url, caption) {
  if (!/^https:\/\//i.test(url)) {
    showToast('AI 修改图片需要公开的 HTTPS 图片链接');
    return;
  }
  imageAiSourceUrl = url;
  imageAiResultDataUrl = '';
  editEl('imageAiEditSource').src = url;
  editEl('imageAiEditSource').alt = caption || '待修改的商品图片';
  editEl('imageAiEditInstruction').value = '以 <image1> 中的商品为唯一主体，仅将背景替换为干净纯白的电商背景。必须保持商品款式、颜色、材质、细节、标识、标签和现有文字完全不变。不要添加其他物品、文字或水印。';
  editEl('imageAiEditStatus').textContent = '';
  editEl('imageAiEditProgress').replaceChildren();
  editEl('imageAiEditProgress').classList.add('hidden');
  editEl('imageAiEditOutput').removeAttribute('src');
  editEl('imageAiEditResult').classList.add('hidden');
  editEl('imageAiEditModal').classList.remove('hidden');
}

function renderImageAiProgress(events) {
  const progress = editEl('imageAiEditProgress');
  progress.replaceChildren();
  for (const event of events || []) {
    const item = document.createElement('li');
    item.textContent = event;
    progress.append(item);
  }
  progress.classList.toggle('hidden', !events?.length);
}

function wait(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function waitForLocalImageJob(jobId) {
  const deadline = Date.now() + 450000;
  while (Date.now() < deadline) {
    const response = await fetch(`/api/ai/local-image-edit/${jobId}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '无法读取本地生成进度');
    const job = result.data || {};
    renderImageAiProgress(job.events);
    editEl('imageAiEditStatus').textContent = `本机 ComfyUI 正在处理，已耗时 ${job.elapsedSeconds || 0} 秒。`;
    if (job.status === 'completed') return job.imageDataUrl || '';
    if (job.status === 'failed') throw new Error(job.error || '本地生成失败');
    await wait(1500);
  }
  throw new Error('等待本地生成超时，请检查 ComfyUI 队列');
}

async function submitImageAiEdit(provider = 'api') {
  const instruction = editEl('imageAiEditInstruction').value.trim();
  if (!imageAiSourceUrl || instruction.length < 2) {
    showToast('请输入中文正向提示词');
    return;
  }
  const isLocal = provider === 'local';
  const submit = editEl(isLocal ? 'imageAiEditLocalSubmit' : 'imageAiEditSubmit');
  const otherSubmit = editEl(isLocal ? 'imageAiEditSubmit' : 'imageAiEditLocalSubmit');
  submit.disabled = true;
  otherSubmit.disabled = true;
  submit.textContent = '正在生成…';
  editEl('imageAiEditStatus').textContent = isLocal
    ? '正在调用本机 ComfyUI + Qwen-Image-Edit。首次加载模型可能需要数分钟。'
    : '正在调用云端 API 生成修改稿，通常需要几十秒。';
  try {
    const response = await fetch(isLocal ? '/api/ai/local-image-edit' : '/api/ai/image-edit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sourceUrl: imageAiSourceUrl, instruction }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'AI 修改图片失败');
    if (isLocal) {
      const jobId = result.data?.jobId;
      if (!/^[a-f0-9]{32}$/.test(jobId || '')) throw new Error('本地任务创建失败');
      renderImageAiProgress(['已提交本机 ComfyUI 队列，正在读取执行状态']);
      imageAiResultDataUrl = await waitForLocalImageJob(jobId);
    } else {
      imageAiResultDataUrl = result.data?.imageDataUrl || '';
    }
    if (!imageAiResultDataUrl.startsWith('data:image/')) throw new Error('AI 未返回图片');
    editEl('imageAiEditOutput').src = imageAiResultDataUrl;
    editEl('imageAiEditResult').classList.remove('hidden');
    editEl('imageAiEditStatus').textContent = `${isLocal ? '本地修改稿' : '修改稿'}已生成。请下载后上传到妙手图片空间，再替换商品图片链接。`;
  } catch (error) {
    const detail = error instanceof TypeError && error.message === 'Failed to fetch'
      ? '本地服务未连接，请从 http://127.0.0.1:8787 打开工作台并刷新页面'
      : error.message;
    editEl('imageAiEditStatus').textContent = `生成失败：${detail}`;
  } finally {
    submit.disabled = false;
    otherSubmit.disabled = false;
    submit.textContent = isLocal ? '▣ 本地生成（ComfyUI）' : '☁ API 生成';
  }
}

function downloadImageAiResult() {
  if (!imageAiResultDataUrl) return;
  const link = document.createElement('a');
  link.href = imageAiResultDataUrl;
  link.download = `temu-ai-edited-${Date.now()}.png`;
  document.body.append(link);
  link.click();
  link.remove();
}

async function showTitlePolish(language) {
  const targetId = language === 'zh' ? 'editTitle' : 'editEnglishTitle';
  const source = editEl(targetId).value.trim();
  if (!source) { showToast(language === 'zh' ? '请先填写商品标题' : '请先填写英文标题'); editEl(targetId).focus(); return; }
  const trigger = editEl(language === 'zh' ? 'polishChineseTitle' : 'polishEnglishTitle');
  trigger.disabled = true; trigger.textContent = 'AI 润色中…';
  titlePolishTarget = targetId;
  try {
    const response = await fetch('/api/ai/title-polish', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: source, language }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'AI 润色失败');
    editEl('aiTitlePolishLabel').textContent = language === 'zh' ? 'DeepSeek 中文标题润色建议' : 'DeepSeek 英文标题润色建议';
    const suggestion = String(result.data?.title || '').trim();
    if (!suggestion) throw new Error('AI 未返回有效标题');
    // The API result is the new working title. Keep the draft panel open only
    // for optional fine-tuning, instead of requiring a second click to replace
    // the old field value.
    editEl('aiTitlePolishDraft').value = suggestion;
    editEl(targetId).value = suggestion;
    updateTitlePolishCount();
    updateTitleCounts();
    editEl('aiTitlePolish').classList.remove('hidden');
    editEl('aiTitlePolishDraft').focus();
    setEditorStage(4);
  } catch (error) {
    const detail = error instanceof TypeError && error.message === 'Failed to fetch'
      ? '本地服务未连接，请从 http://127.0.0.1:8787 打开工作台并刷新页面'
      : error.message;
    showToast(`AI 润色失败：${detail}`);
  }
  finally { trigger.disabled = false; trigger.textContent = '✦ AI 润色'; }
}

function applyTitlePolish() {
  const target = editEl(titlePolishTarget);
  const suggestion = editEl('aiTitlePolishDraft').value.trim();
  if (!target || !suggestion) { showToast('请保留有效的标题建议'); return; }
  if (titleWeightedLength(suggestion) > 240) { showToast('标题超过 240 加权字符，请先删减'); return; }
  target.value = suggestion; target.focus();
  updateTitleCounts();
  editEl('aiTitlePolish').classList.add('hidden');
  showToast('DeepSeek 润色标题已应用');
}

function renderDescriptionImagePreview() {
  const holder = editEl('descriptionImagePreview');
  const images = getDescriptionImageModules();
  holder.replaceChildren();
  images.forEach(({ index: moduleIndex, url }, imageIndex) => {
    const card = document.createElement('div'); card.className = 'description-image-card';
    enableImageReorder(card, 'description', imageIndex, moveDescriptionImage);
    const frame = document.createElement('div'); frame.className = 'description-image-frame';
    const image = document.createElement('img'); image.alt = `描述图片 ${imageIndex + 1}`; image.loading = 'lazy'; image.src = url; image.title = '点击放大预览';
    image.addEventListener('click', () => openImagePreview(url, `描述图片 ${imageIndex + 1}`));
    const label = document.createElement('span'); label.textContent = String(imageIndex + 1);
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'product-image-remove'; remove.textContent = '×'; remove.title = `删除第 ${imageIndex + 1} 张描述图片`;
    remove.addEventListener('click', () => {
      editorState.item.goodsLayerDecorationReqs.splice(moduleIndex, 1);
      renderDescriptionImagePreview();
    });
    image.addEventListener('error', () => { image.hidden = true; frame.classList.add('invalid'); });
    const urlInput = document.createElement('input'); urlInput.type = 'url'; urlInput.value = url; urlInput.setAttribute('aria-label', `第 ${imageIndex + 1} 张描述图片链接`);
    urlInput.addEventListener('focus', () => setEditorStage(4));
    urlInput.addEventListener('change', () => {
      const nextUrl = urlInput.value.trim();
      if (!nextUrl) { editorState.item.goodsLayerDecorationReqs.splice(moduleIndex, 1); renderDescriptionImagePreview(); return; }
      if (!/^https?:\/\//i.test(nextUrl)) { showToast('请输入有效的描述图片链接'); renderDescriptionImagePreview(); return; }
      editorState.item.goodsLayerDecorationReqs[moduleIndex].content.imgUrl = nextUrl;
      renderDescriptionImagePreview();
    });
    if (/^https:\/\//i.test(url)) {
      const aiEdit = document.createElement('button'); aiEdit.type = 'button'; aiEdit.className = 'product-image-ai-edit'; aiEdit.textContent = '✦ AI 修改'; aiEdit.title = `AI 修改第 ${imageIndex + 1} 张描述图片`;
      aiEdit.addEventListener('click', () => openImageAiEditor(url, `描述图片 ${imageIndex + 1}`));
      frame.append(aiEdit);
    }
    frame.append(image, label, remove); card.append(frame, urlInput); holder.append(card);
  });
  editorState.descriptionLocalImages.forEach((entry, localIndex) => {
    const card = document.createElement('div'); card.className = 'description-image-card local-preview';
    const frame = document.createElement('div'); frame.className = 'description-image-frame';
    const image = document.createElement('img'); image.alt = `本地描述图片 ${localIndex + 1}`; image.src = entry.previewUrl; image.title = '点击放大预览';
    image.addEventListener('click', () => openImagePreview(entry.previewUrl, `本地描述图片 ${localIndex + 1}`));
    const label = document.createElement('span'); label.textContent = '本地';
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'product-image-remove'; remove.textContent = '×'; remove.title = `移除本地描述图片 ${localIndex + 1}`;
    remove.addEventListener('click', () => {
      URL.revokeObjectURL(entry.previewUrl);
      editorState.descriptionLocalImages.splice(localIndex, 1);
      renderDescriptionImagePreview();
    });
    const hint = document.createElement('small'); hint.className = 'description-local-hint'; hint.textContent = '本地预览，保存前需替换为妙手图片链接';
    frame.append(image, label, remove); card.append(frame, hint); holder.append(card);
  });
  if (!images.length && !editorState.descriptionLocalImages.length) {
    const hint = document.createElement('span'); hint.className = 'product-image-empty'; hint.textContent = '暂无描述图片，可添加妙手图片链接或本地图片预览。'; holder.append(hint);
  }
}

function addDescriptionImage() {
  const source = editEl('descriptionImageUrl');
  const url = source.value.trim();
  if (!/^https?:\/\//i.test(url)) { showToast('请输入有效的描述图片链接'); source.focus(); return; }
  editorState.item.goodsLayerDecorationReqs ||= [];
  editorState.item.goodsLayerDecorationReqs.push({ type: 'image', content: { imgUrl: url, text: null, textModuleDetails: null } });
  source.value = ''; renderDescriptionImagePreview(); setEditorStage(4);
}

function addLocalDescriptionImages(files) {
  const selected = [...(files || [])];
  const invalid = selected.find((file) => !file.type.startsWith('image/'));
  if (invalid) { showToast('请选择图片文件'); return; }
  selected.forEach((file) => editorState.descriptionLocalImages.push({ fileName: file.name, previewUrl: URL.createObjectURL(file) }));
  renderDescriptionImagePreview(); setEditorStage(4);
}

function renderProductImagePreview() {
  const holder = editEl('productImagePreview');
  const urls = getProductImageUrls();
  holder.replaceChildren();
  if (!urls.length) {
    const hint = document.createElement('span'); hint.className = 'product-image-empty'; hint.textContent = '暂无商品图片'; holder.append(hint);
    return;
  }
  urls.forEach((url, index) => {
    const card = document.createElement('div'); card.className = 'product-image-card';
    enableImageReorder(card, 'product', index, moveProductImage);
    const frame = document.createElement('div'); frame.className = 'product-image-frame';
    const image = document.createElement('img'); image.alt = `商品图片 ${index + 1}`; image.loading = 'lazy'; image.title = '点击放大预览';
    const label = document.createElement('span'); label.textContent = String(index + 1);
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'product-image-remove'; remove.textContent = '×'; remove.title = `删除第 ${index + 1} 张图片`;
    remove.addEventListener('click', () => { urls.splice(index, 1); setProductImageUrls(urls); });
    if (/^https?:\/\//i.test(url)) { image.src = url; image.addEventListener('click', () => openImagePreview(url, `商品图片 ${index + 1}`)); }
    else { image.hidden = true; frame.classList.add('invalid'); }
    image.addEventListener('error', () => { image.hidden = true; frame.classList.add('invalid'); });
    const urlInput = document.createElement('input'); urlInput.type = 'url'; urlInput.className = 'product-image-url'; urlInput.value = url; urlInput.setAttribute('aria-label', `第 ${index + 1} 张商品图片链接`);
    urlInput.addEventListener('focus', () => setEditorStage(4));
    urlInput.addEventListener('change', () => { urls[index] = urlInput.value.trim(); setProductImageUrls(urls); });
    if (/^https:\/\//i.test(url)) {
      const aiEdit = document.createElement('button'); aiEdit.type = 'button'; aiEdit.className = 'product-image-ai-edit'; aiEdit.textContent = '✦ AI 修改'; aiEdit.title = `AI 修改第 ${index + 1} 张商品图片`;
      aiEdit.addEventListener('click', () => openImageAiEditor(url, `商品图片 ${index + 1}`));
      frame.append(aiEdit);
    }
    frame.append(image, label, remove); card.append(frame, urlInput); holder.append(card);
  });
}

function getProductImageUrls() {
  return editEl('editImages').value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
}

function setProductImageUrls(urls) {
  editEl('editImages').value = urls.filter(Boolean).join('\n');
  renderProductImagePreview();
}

function addProductImage() {
  const source = editEl('productImageUrl');
  const url = source.value.trim();
  if (!/^https?:\/\//i.test(url)) { showToast('请输入有效的图片链接'); source.focus(); return; }
  const urls = getProductImageUrls();
  urls.push(url); source.value = ''; setProductImageUrls(urls); setEditorStage(4);
}

function setEditorStage(stage) {
  window.dispatchEvent(new CustomEvent('live-editor-stage', { detail: { stage } }));
}

function attributeInputValue(value) {
  if (value?.numberValue !== null && value?.numberValue !== undefined && value.numberValue !== '') {
    return `${value.numberValue}${value.valueUnit || ''}`;
  }
  return value?.name || '';
}

function syncAttributeJson() {
  editEl('editAttributes').value = JSON.stringify(editorState.item?.attributes || [], null, 2);
}

function ruleForAttribute(attribute) {
  return editorState.attributeRules.find((rule) =>
    String(rule.templatePid) === String(attribute.templatePid) || String(rule.pid) === String(attribute.pid));
}

function attributeForRule(rule) {
  return editorState.item?.attributes?.find((attribute) =>
    String(attribute.templatePid) === String(rule.templatePid) || String(attribute.pid) === String(rule.pid));
}

function isNumericAttributeRule(rule) {
  if (!rule || rule.valueType === 'TABLE' || rule.valueType === 'SELECT') return false;
  return ['NUMBER', 'NUMERIC', 'INPUT_NUMBER'].includes(String(rule.valueType || '').toUpperCase())
    || (rule.valueType === 'STRING' && Number(rule.propertyValueType) === 1)
    || rule.numberInputTitle != null || rule.minValue != null || rule.maxValue != null;
}

function newAttributeFromRule(rule) {
  return {
    templatePid: rule.templatePid, pid: rule.pid, refPid: rule.refPid, name: rule.name,
    values: isNumericAttributeRule(rule) ? [{
      vid: 0, name: '', numberValue: '',
      valueUnit: Array.isArray(rule.valueUnit) ? (rule.valueUnit[0] || '') : (rule.valueUnit || ''),
    }] : [],
  };
}

function isCompositionRule(rule) {
  return rule?.name === '成分' && rule?.valueType === 'TABLE';
}

function automationTaskAttributeNames(task) {
  const messages = [task?.message, ...(task?.events || []).map((event) => event?.message)];
  const names = new Set();
  messages.filter((message) => typeof message === 'string').forEach((message) => {
    for (const match of message.matchAll(/【([^】\r\n]{1,80})】/g)) names.add(match[1].trim());
  });
  return [...names].filter(Boolean);
}

async function ensureRequiredAttributes() {
  const item = editorState.item;
  if (!item || !editorState.attributeRules.length) return true;
  const rules=editorState.attributeRules, requested = new Set(editorState.requestedAttributeNames || []);
  const before = JSON.stringify(item.attributes || []), rawBefore=editEl('editAttributes').value;
  const attributes=JSON.parse(before);
  rules.filter(rule => isCompositionRule(rule) || requested.has(String(rule.name || '').trim())).forEach(rule => {
    if (!attributes.some(attribute=>String(attribute.templatePid)===String(rule.templatePid) || String(attribute.pid)===String(rule.pid)))
      attributes.push(newAttributeFromRule(rule));
  });
  const cid = editorState.attributeRulesCid;
  const requestVersion=editorState.attributeDefaultsVersion=(editorState.attributeDefaultsVersion || 0)+1;
  const categoryVersion=editorState.attributeRequestVersion;
  const controller=new AbortController(), timer=window.setTimeout(()=>controller.abort(),12000);
  try {
    const response = await fetch('/api/item/defaults', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },signal:controller.signal,
      body: JSON.stringify({ item:{...item,attributes}, rules, targets: [...requested] }),
    });
    const result = await response.json().catch(()=>({}));
    if (!response.ok) throw new Error(result.error || '默认值读取失败，请安全更新后台后重试');
    if(!Array.isArray(result.data?.attributes))throw new Error('后台未返回有效的属性默认值');
    // Preserve edits to either the visible controls or the advanced JSON, and
    // invalidate work when a newer item/category/default request has started.
    if (editorState.item !== item || editorState.attributeRules !== rules || editorState.attributeRulesCid !== cid
        || editorState.attributeRequestVersion !== categoryVersion || editorState.attributeDefaultsVersion !== requestVersion
        || JSON.stringify(item.attributes || []) !== before || editEl('editAttributes').value !== rawBefore) return false;
    item.attributes = result.data.attributes;
    syncAttributeJson();return true;
  } catch(error) {
    if(controller.signal.aborted)throw new Error('默认值读取超时，可重新选择类目后重试');
    throw error;
  } finally {window.clearTimeout(timer);}
}
function valueFromRuleChoice(choice, rule, prior = {}) {
  return {
    ...prior,
    vid: choice.vid,
    name: choice.name,
    valueUnit: Array.isArray(rule.valueUnit) ? (rule.valueUnit[0] || '') : (rule.valueUnit || ''),
    numberValue: prior.numberValue ?? null,
  };
}

async function loadAttributeRules(cid) {
  cid=String(cid || '');
  const requestVersion = editorState.attributeRequestVersion=(editorState.attributeRequestVersion || 0)+1;
  const item=editorState.item;
  const current=()=>requestVersion===editorState.attributeRequestVersion && editorState.item===item && editEl('editCid').value===cid;
  if (!cid) { editorState.attributeRules = []; editorState.attributeRulesCid = ''; renderProductAttributes(); return; }
  let rulesReady=editorState.attributeRulesCid === cid && editorState.attributeRules.length>0;
  editEl('liveAttributesHint').textContent = '正在读取妙手类目属性与可选项…';
  try {
    if(!rulesReady){
      const data = await apiGet(`/api/category-attributes?cid=${encodeURIComponent(cid)}`);
      if (!current()) return;
      editorState.attributeRules = Array.isArray(data?.productAttributeRules) ? data.productAttributeRules : [];
      editorState.attributeRulesCid = cid;rulesReady=true;
    }
    const applied=await ensureRequiredAttributes();
    if (!current()) return;
    if(applied!==false)renderProductAttributes();
    else editEl('liveAttributesHint').textContent='已保留你正在编辑的属性；保存时会再次校验默认规则。';
  } catch (error) {
    if (!current()) return;
    if(!rulesReady){editorState.attributeRules = []; editorState.attributeRulesCid = '';}
    renderProductAttributes();
    editEl('liveAttributesHint').textContent = rulesReady
      ? `默认值读取失败：${error.message}；已保留类目选项和现有填写。`
      : `属性选项读取失败：${error.message}；可在高级资料中补充。`;
  }
}

function renderAttributeSelect(rule, attribute, attributeIndex, valueIndex) {
  const current = attribute.values?.[valueIndex] || {};
  const select = document.createElement('select');
  const multi = Number(rule.chooseMaxNum) > 1;
  const choices = Array.isArray(rule.values) ? rule.values : [];
  select.multiple = multi;
  if (multi) select.size = Math.min(5, Math.max(2, choices.length));
  if (!multi) addSelectOption(select, '', `请选择${rule.name}`);
  const chosenIds = new Set((attribute.values || []).map((value) => String(value.vid)));
  choices.forEach((choice) => {
    const option = document.createElement('option'); option.value = String(choice.vid); option.textContent = choice.name;
    option.selected = chosenIds.has(String(choice.vid)); select.append(option);
  });
  select.addEventListener('change', () => {
    const picked = [...select.selectedOptions].map((option) => choices.find((choice) => String(choice.vid) === option.value)).filter(Boolean);
    if (multi) attribute.values = picked.map((choice) => valueFromRuleChoice(choice, rule));
    else {
      const choice = picked[0];
      attribute.values ||= [{}];
      attribute.values[valueIndex] = choice ? valueFromRuleChoice(choice, rule, current) : {};
    }
    syncAttributeJson();
  });
  return select;
}

function renderTableAttribute(rule, attribute, attributeIndex) {
  attribute.values ||= [];
  if (!attribute.values.length) attribute.values.push({ valueUnit: rule.valueUnit?.[0] || '%', numberValue: '' });
  const section = document.createElement('section'); section.className = 'live-attribute-table';
  const heading = document.createElement('div'); heading.className = 'live-attribute-table-head';
  const title = document.createElement('strong'); title.textContent = `${rule.required ? '* ' : ''}${rule.name || '商品属性'}`;
  const note = document.createElement('span'); note.textContent = `${rule.propertyChooseTitle || '选项'} + ${rule.numberInputTitle || '数值'}${rule.valueInputSum ? `（合计 ${rule.valueInputSum}${rule.valueUnit?.[0] || ''}）` : ''}`;
  heading.append(title, note); section.append(heading);
  const rows = document.createElement('div'); rows.className = 'live-attribute-table-rows';
  const choices = Array.isArray(rule.values) ? rule.values : [];
  attribute.values.forEach((value, valueIndex) => {
    const row = document.createElement('div'); row.className = 'live-attribute-table-row';
    const select = document.createElement('select'); addSelectOption(select, '', `选择${rule.propertyChooseTitle || '选项'}`);
    choices.forEach((choice) => addSelectOption(select, choice.vid, choice.name));
    select.value = value.vid == null ? '' : String(value.vid);
    select.addEventListener('change', () => {
      const choice = choices.find((candidate) => String(candidate.vid) === select.value);
      attribute.values[valueIndex] = choice ? valueFromRuleChoice(choice, rule, value) : { valueUnit: rule.valueUnit?.[0] || '%', numberValue: value.numberValue || '' };
      syncAttributeJson();
    });
    const ratio = document.createElement('input'); ratio.type = 'number'; ratio.min = rule.minValue || '0'; ratio.max = rule.maxValue || '100'; ratio.step = '0.01'; ratio.placeholder = rule.numberInputTitle || '比例'; ratio.value = value.numberValue ?? '';
    ratio.addEventListener('input', () => { value.numberValue = ratio.value; value.valueUnit = rule.valueUnit?.[0] || '%'; syncAttributeJson(); });
    const unit = document.createElement('span'); unit.className = 'attribute-unit'; unit.textContent = rule.valueUnit?.[0] || '';
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'attribute-row-remove'; remove.textContent = '删除';
    remove.disabled = attribute.values.length === 1;
    remove.addEventListener('click', () => { attribute.values.splice(valueIndex, 1); syncAttributeJson(); renderProductAttributes(); });
    row.append(select, ratio, unit, remove); rows.append(row);
  });
  const add = document.createElement('button'); add.type = 'button'; add.className = 'attribute-row-add'; add.textContent = '＋ 添加成分';
  add.addEventListener('click', () => { attribute.values.push({ valueUnit: rule.valueUnit?.[0] || '%', numberValue: '' }); syncAttributeJson(); renderProductAttributes(); });
  section.append(rows, add); return section;
}

function renderAttributeAddControl() {
  if (!editorState.attributeRules.length) return null;
  const configured = new Set((editorState.item?.attributes || []).map((attribute) =>
    String(attribute.templatePid ?? attribute.pid ?? attribute.name ?? '')));
  const available = editorState.attributeRules.filter((rule) => {
    const identity = String(rule.templatePid ?? rule.pid ?? rule.name ?? '');
    return rule?.name && identity && !configured.has(identity);
  });
  if (!available.length) return null;
  const section = document.createElement('label'); section.className = 'live-attribute-add';
  const title = document.createElement('span'); title.textContent = '补充其他类目属性';
  const select = document.createElement('select');
  addSelectOption(select, '', '选择需要补充的属性');
  available.forEach((rule) => addSelectOption(select, String(rule.templatePid ?? rule.pid), `${rule.required ? '* ' : ''}${rule.name}`));
  select.addEventListener('change', () => {
    const rule = available.find((candidate) => String(candidate.templatePid ?? candidate.pid) === select.value);
    if (!rule) return;
    editorState.item.attributes.push(newAttributeFromRule(rule));
    syncAttributeJson();
    renderProductAttributes();
  });
  section.append(title, select);
  return section;
}

function renderProductAttributes() {
  const holder = editEl('editAttributeFields');
  const hint = editEl('liveAttributesHint');
  const attributes = editorState.item?.attributes || [];
  holder.replaceChildren();
  const addControl = renderAttributeAddControl();
  if (!attributes.length) {
    hint.textContent = editorState.kind === 'new'
      ? '新建商品需根据所选妙手类目补充属性。'
      : '当前商品没有可编辑的类目属性。';
    const empty = document.createElement('p'); empty.className = 'live-attribute-empty';
    empty.textContent = editorState.kind === 'new' ? '请从上方选择需要补充的类目属性。' : '请选择需要补充的类目属性。';
    if (addControl) holder.append(addControl);
    holder.append(empty);
    return;
  }
  hint.textContent = editorState.attributeRules.length
    ? `已载入 ${attributes.length} 项类目属性；带 * 的属性为妙手必填，可直接选择可用选项。`
    : `已载入 ${attributes.length} 项类目属性；不同商品会显示各自的属性。`;
  attributes.forEach((attribute, attributeIndex) => {
    const rule = ruleForAttribute(attribute);
    if (rule?.valueType === 'TABLE') { holder.append(renderTableAttribute(rule, attribute, attributeIndex)); return; }
    if (rule?.valueType === 'SELECT' && Number(rule.chooseMaxNum) > 1 && Array.isArray(rule.values) && rule.values.length) {
      const label = document.createElement('label'); label.className = 'live-attribute-field';
      const name = document.createElement('span'); name.textContent = `${rule.required ? '* ' : ''}${rule.name || attribute.name || '商品属性'}`;
      label.append(name, renderAttributeSelect(rule, attribute, attributeIndex, 0)); holder.append(label); return;
    }
    const values = Array.isArray(attribute.values) && attribute.values.length ? attribute.values : [{}];
    values.forEach((value, valueIndex) => {
      const label = document.createElement('label'); label.className = 'live-attribute-field';
      const name = document.createElement('span');
      const attributeName = rule?.name || attribute.name || '商品属性';
      name.textContent = `${rule?.required ? '* ' : ''}${values.length > 1 ? `${attributeName} · ${valueIndex + 1}` : attributeName}`;
      if (rule?.valueType === 'SELECT' && Array.isArray(rule.values) && rule.values.length) {
        label.append(name, renderAttributeSelect(rule, attribute, attributeIndex, valueIndex)); holder.append(label); return;
      }
      const numeric = isNumericAttributeRule(rule);
      const input = document.createElement('input'); input.type = numeric ? 'number' : 'text'; input.value = numeric ? (value.numberValue ?? '') : attributeInputValue(value);
      if (numeric) {
        if (rule.minValue != null) input.min = String(rule.minValue);
        if (rule.maxValue != null) input.max = String(rule.maxValue);
        input.step = 'any';
      }
      input.placeholder = `填写${attributeName}`;
      input.addEventListener('input', () => {
        const target = editorState.item.attributes?.[attributeIndex]?.values?.[valueIndex];
        if (!target) return;
        const inputValue = input.value.trim();
        // Numeric attributes retain their numeric API field; ordinary attributes
        // retain their selected value name and all MiaoShou metadata (vid/pid).
        if (numeric || (target.numberValue !== null && target.numberValue !== undefined)) {
          const unit = target.valueUnit || '';
          const numberText = unit && inputValue.endsWith(unit) ? inputValue.slice(0, -unit.length).trim() : inputValue;
          target.numberValue = numberText;
          target.name = numberText;
          target.vid ??= 0;
          target.valueUnit ||= Array.isArray(rule?.valueUnit) ? (rule.valueUnit[0] || '') : (rule?.valueUnit || '');
        } else target.name = inputValue;
        syncAttributeJson();
      });
      label.append(name, input); holder.append(label);
    });
  });
  if (addControl) holder.append(addControl);
}

function validateProductAttributes(attributes) {
  editorState.attributeRules.filter((rule) => isCompositionRule(rule) || attributes.some((entry) =>
    String(entry.templatePid) === String(rule.templatePid) || String(entry.pid) === String(rule.pid))).forEach((rule) => {
    const attribute = attributes.find((entry) => String(entry.templatePid) === String(rule.templatePid) || String(entry.pid) === String(rule.pid));
    if (!attribute || !Array.isArray(attribute.values) || !attribute.values.length) throw new Error(`产品属性【${rule.name}】必填`);
    if (rule.valueType === 'TABLE') {
      let total = 0;
      attribute.values.forEach((value) => {
        const number = Number(value.numberValue);
        if (!value.vid || !Number.isFinite(number) || number < 0) throw new Error(`产品属性【${rule.name}】请填写有效的材质和比例`);
        total += number;
      });
      if (rule.valueInputSum != null && Math.abs(total - Number(rule.valueInputSum)) > 0.01) {
        throw new Error(`产品属性【${rule.name}】的比例合计必须为 ${rule.valueInputSum}${rule.valueUnit?.[0] || ''}`);
      }
    } else if (isNumericAttributeRule(rule)) {
      attribute.values.forEach((value) => {
        const number = Number(value.numberValue);
        if (!Number.isFinite(number)
            || (rule.minValue != null && number < Number(rule.minValue))
            || (rule.maxValue != null && number > Number(rule.maxValue))) {
          throw new Error(`产品属性【${rule.name}】请填写类目允许范围内的数值`);
        }
      });
    } else if (!attribute.values.some((value) => value.vid != null || String(value.name || '').trim())) {
      throw new Error(`产品属性【${rule.name}】必填`);
    }
  });
}

function fillEditor() {
  const item = editorState.item;
  claimStatus('');
  editEl('editSkuRows').replaceChildren();
  copySelectOptions(editEl('liveSite'), editEl('editSite'), '请选择站点');
  const linked = item.collectBoxDetailShopList?.[0];
  editEl('editSite').value = editorState.targetSite || editEl('liveSite').value || linked?.publishSiteList?.[0] || item.publishSiteList?.[0] || '';
  initializeClaimedShops(item);
  editEl('editTitle').value = item.title || '';
  editEl('editEnglishTitle').value = item.multiLanguageTitleMap?.en || item.title || '';
  updateTitleCounts();
  editEl('editCid').value = item.cid || '';
  editorState.categoryPath = editorState.categoryTree && item.cid ? (findCategoryPath(editorState.categoryTree, item.cid) || []) : [];
  updateCategorySummary();
  loadCategoryTree();
  editEl('editCountry').value = item.productOriginCountry || '';
  editEl('editProvince').value = item.productOriginProvince || '';
  editEl('editManufacturingLocations').value = (item.manufacturingLocationRegionShortNameList || []).join(', ');
  editEl('editImages').value = (item.imgUrls || []).join('\n');
  editEl('productImageUrl').value = '';
  renderProductImagePreview();
  editEl('editDescription').value = editorDescription(item);
  titlePolishTarget = '';
  editEl('aiTitlePolish').classList.add('hidden');
  clearDescriptionLocalImages();
  editEl('descriptionImageUrl').value = '';
  editEl('descriptionImageLocal').value = '';
  renderDescriptionImagePreview();
  editEl('editAttributes').value = JSON.stringify(item.attributes || [], null, 2);
  editEl('editSaleAttributes').value = JSON.stringify(item.saleAttributes || [], null, 2);
  editEl('editSizeCharts').value = JSON.stringify(item.sizeCharts || [], null, 2);
  renderProductAttributes();
  loadAttributeRules(String(item.cid || ''));
  renderSkuSpecs();
  editEl('liveEditorTitle').textContent = editorState.kind === 'edit' ? '编辑妙手商品' : '新建空白商品';
  editEl('liveEditorNote').textContent = editorState.kind === 'edit'
    ? '保存会更新妙手采集箱中的原商品，但不会自动发布。未显示的高级字段会保留。'
    : '从零新建需要填写妙手类目对应的属性和规格；保存不会自动发布。';
  editEl('liveEditor').classList.remove('hidden');
  editEl('liveEditor').scrollIntoView({ behavior: 'smooth', block: 'start' });
  loadEditorLogistics();
}

async function openEditor(kind) {
  editorError('');
  claimStatus('');
  [...document.querySelectorAll('.nav-item[data-page="workbench"]')].find(button =>
    Number(button.dataset.accountId) === Number(window.collectionAccountContext?.id || 1))?.click();
  window.dispatchEvent(new CustomEvent('live-editor-open'));
  if (kind === 'new') {
    Object.assign(editorState, { kind, mode: 'site', detailId: null, version: null, item: blankItem(), targetShopId: '', targetSite: '', requestedAttributeNames: [] });
    fillEditor();
    return;
  }
  const selected = liveState.selected;
  if (!selected) { showToast('请先选择商品'); return; }
  const mode = selected.editModel === 'shop' ? 'shop' : 'site';
  const shopId = editEl('liveShop').value || selected.collectBoxDetailShopList?.[0]?.shopId || '';
  const params = new URLSearchParams({ detailId: selected.collectBoxDetailId, mode, shopId });
  editEl('liveEditorTitle').textContent = '正在读取商品详情…';
  editEl('liveEditor').classList.remove('hidden');
  try {
    const response = await apiGet(`/api/item?${params}`);
    const item = cloneItem(response.item);
    Object.assign(editorState, { kind, mode,
      detailId: kind === 'edit' ? Number(selected.collectBoxDetailId) : null,
      version: kind === 'edit' ? response.version : null, item, targetShopId: '', targetSite: '', requestedAttributeNames: [] });
    fillEditor();
  } catch (error) {
    editorError(`商品详情读取失败：${error.message}`);
  }
}

async function openEditorForAutomationTask(task) {
  const detailId = Number(task?.detailId), shopId = Number(task?.shopId), site = String(task?.site || '');
  if (!Number.isInteger(detailId) || detailId <= 0 || !Number.isInteger(shopId) || shopId <= 0 || !/^[A-Za-z0-9]{1,20}$/.test(site)) {
    showToast('任务缺少有效的商品、店铺或站点信息'); return;
  }
  editorError('');
  claimStatus('');
  activatePage('workbench');
  window.dispatchEvent(new CustomEvent('live-editor-open'));
  editEl('liveEditorTitle').textContent = '正在读取需处理商品…';
  editEl('liveEditor').classList.remove('hidden');
  try {
    const response = await apiGet(`/api/item?${new URLSearchParams({ detailId, shopId, mode: 'shop' })}`);
    Object.assign(editorState, {
      kind: 'edit', mode: 'shop', detailId, version: response.version,
      item: cloneItem(response.item), targetShopId: String(shopId), targetSite: site,
      requestedAttributeNames: automationTaskAttributeNames(task),
    });
    fillEditor();
    editEl('liveEditorNote').textContent = task.status === 'needs_input'
      ? '这是任务中心暂停等待处理的商品。请补充图片或修正属性并保存，再返回任务中心点击“已处理问题，重新检查”；保存不会自动发布。'
      : '这是从任务中心打开的需处理商品。请修正问题后手动保存；保存不会自动再次发布。';
  } catch (error) {
    editorError(`商品详情读取失败：${error.message}`);
  }
}

function scrollEditorStage(stage) {
  setEditorStage(stage);
  const targetId = stage === 2 ? 'liveClaimStep' : stage === 4 ? 'liveMediaStep' : stage === 5 ? 'liveSkuStep' : 'liveDetailsStep';
  window.requestAnimationFrame(() => editEl(targetId)?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
}

function putSiteValue(list, site, row) {
  const result = (Array.isArray(list) ? list : []).filter((entry) => entry.site !== site);
  result.push(row);
  return result;
}

function collectEditorItem() {
  const item = cloneItem(editorState.item);
  const shopId = editEl('editShop').value, site = editEl('editSite').value;
  rememberCurrentShopConfig();
  const claimedShopIds = [...new Set(editorState.claimedShopIds)];
  if (!shopId || !site || !claimedShopIds.length || site === 'EU')
    throw new Error('请选择至少一个目标店铺、非欧盟站点及物流配置');
  if (editorState.mode === 'site' && !editorState.claimSubmitted)
    throw new Error('请先在第 2 步提交店铺认领');
  item.title = editEl('editTitle').value.trim();
  const englishTitle = editEl('editEnglishTitle').value.trim();
  if (!englishTitle) throw new Error('请填写英文标题');
  if ((editorState.kind !== 'edit' || item.title !== (editorState.item.title || '')) && titleWeightedLength(item.title) > 240)
    throw new Error('商品标题超过 240 加权字符（中文每字计 2）');
  const previousEnglishTitle = editorState.item.multiLanguageTitleMap?.en || editorState.item.title || '';
  if ((editorState.kind !== 'edit' || englishTitle !== previousEnglishTitle) && titleWeightedLength(englishTitle) > 240)
    throw new Error('英文标题超过 240 加权字符');
  item.cid = editEl('editCid').value.trim();
  if (!item.cid) throw new Error('请选择产品类别的最后一级');
  item.productOriginCountry = editEl('editCountry').value.trim().toUpperCase();
  item.productOriginProvince = editEl('editProvince').value.trim();
  const manufacturingLocations = editEl('editManufacturingLocations').value
    .split(/[,，\s]+/).map((value) => value.trim().toUpperCase()).filter(Boolean);
  if (manufacturingLocations.some((value) => !/^[A-Z0-9]{2,3}$/.test(value)))
    throw new Error('制造地点必须使用国家或地区简称，例如 CN');
  item.manufacturingLocationRegionShortNameList = [...new Set(manufacturingLocations.length ? manufacturingLocations : [item.productOriginCountry])];
  item.imgUrls = editEl('editImages').value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
  item.attributes = JSON.parse(editEl('editAttributes').value || '[]');
  item.saleAttributes = JSON.parse(editEl('editSaleAttributes').value || '[]');
  item.sizeCharts = JSON.parse(editEl('editSizeCharts').value || '[]');
  if (!Array.isArray(item.attributes) || !Array.isArray(item.saleAttributes) || !Array.isArray(item.sizeCharts))
    throw new Error('类目属性、销售规格与尺码表必须是 JSON 数组');
  validateProductAttributes(item.attributes);
  item.multiLanguageTitleMap = typeof item.multiLanguageTitleMap === 'object' && item.multiLanguageTitleMap ? item.multiLanguageTitleMap : {};
  item.multiLanguageTitleMap.en = englishTitle;
  const description = editEl('editDescription').value.trim();
  const modules = Array.isArray(item.goodsLayerDecorationReqs) ? item.goodsLayerDecorationReqs : [];
  const textModule = modules.find((module) => module.type === 'text');
  if (textModule) { textModule.content ||= {}; textModule.content.text = description; }
  else if (description) modules.push({ type: 'text', content: { text: description } });
  item.goodsLayerDecorationReqs = modules;
  if (!item.imgUrls.length) throw new Error('请至少填写一张商品图片');
  if (editorState.descriptionLocalImages.length) throw new Error('描述图片中含有本地预览文件；请先上传到妙手图片空间并替换为图片链接后再保存');
  if (editorState.kind !== 'edit' && !description && !getDescriptionImageModules().length) throw new Error('新建商品请填写产品描述或描述图片');
  editEl('editSkuRows').querySelectorAll('.live-sku-row').forEach((row) => {
    const sku = item.skuMap[row.dataset.skuKey];
    const value = (field) => row.querySelector(`[data-field="${field}"]`).value.trim();
    if (row.querySelector('[data-field="imgUrl"]')?.dataset.localPreview) {
      throw new Error('SKU 本地图片仅用于预览；请先上传到妙手图片空间后使用图片链接保存');
    }
    for (const field of ['itemNum', 'price', 'length', 'width', 'height', 'imgUrl']) sku[field] = value(field);
    sku.weight = Number(value('weight'));
    const stock = Number(value('stock'));
    if (!Number.isInteger(stock) || stock < 0) throw new Error('SKU 库存必须是非负整数');
    sku.siteAndWarehouseIdAndStockMap ||= {};
    sku.siteAndWarehouseIdAndStockMap[site] ||= {};
    claimedShopIds.forEach((id) => {
      const config = editorState.shopConfigs[id];
      if (!config?.warehouseId) throw new Error('请逐个选择目标店铺并配置发货仓库');
      sku.siteAndWarehouseIdAndStockMap[site][config.warehouseId] = stock;
    });
  });
  item.publishSiteList = editorState.kind === 'edit'
    ? [...new Set([...(item.publishSiteList || []), site])]
    : [site];
  if (editorState.mode === 'site') {
    if (editorState.kind !== 'edit') item.collectBoxDetailShopList = [];
    item.collectBoxDetailShopList = (item.collectBoxDetailShopList || [])
      .filter((entry) => claimedShopIds.includes(String(entry.shopId)));
    claimedShopIds.forEach((id) => {
      const config = editorState.shopConfigs[id];
      if (!config?.freightId || !config?.warehouseId)
        throw new Error('请逐个选择目标店铺并配置运费模板和发货仓库');
      let row = item.collectBoxDetailShopList.find((entry) => String(entry.shopId) === id);
      if (!row) { row = { shopId: Number(id), publishSiteList: [] }; item.collectBoxDetailShopList.push(row); }
      row.publishSiteList = [...new Set([...(row.publishSiteList || []), site])];
      row.siteFreightTemplateList = putSiteValue(row.siteFreightTemplateList, site, { site, shopId: id, freightTemplateId: config.freightId });
      row.siteWarehouseList = putSiteValue(row.siteWarehouseList, site, { site, shopId: Number(id), warehouseId: config.warehouseId });
    });
  } else {
    const config = editorState.shopConfigs[shopId];
    if (!config?.freightId || !config?.warehouseId)
      throw new Error('请选择有效的店铺、运费模板和发货仓库');
    item.siteFreightTemplateList = putSiteValue(item.siteFreightTemplateList, site, { site, shopId, freightTemplateId: config.freightId });
    item.siteWarehouseList = putSiteValue(item.siteWarehouseList, site, { site, shopId: Number(shopId), warehouseId: config.warehouseId });
  }
  return item;
}

async function saveEditor(event) {
  event.preventDefault(); editorError('');
  if (liveState.mode !== 'live') { editorError('当前是只读模式，不能保存'); return; }
  let item;
  try { item = collectEditorItem(); }
  catch (error) { editorError(`请检查表单：${error.message}`); return; }
  const creating = editorState.kind !== 'edit';
  const action = creating ? '新建' : '更新';
  if (!window.confirm(`确认将“${item.title}”${action}到妙手半托管采集箱吗？这一步不会发布到 TEMU。`)) return;
  const button = editEl('editSave'); button.disabled = true; button.textContent = '正在保存…';
  const path = creating ? '/api/item/create' : '/api/item/save';
  const payload = creating
    ? { item, confirmation: 'CREATE_MIAOSHOU_ITEM' }
    : { item, detailId: editorState.detailId, shopId: Number(editEl('editShop').value),
        mode: editorState.mode, version: editorState.version, confirmation: 'SAVE_MIAOSHOU_ITEM' };
  try {
    const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '妙手保存失败');
    const newId = result.data?.data?.collectBoxDetailId;
    showToast(creating ? `商品已新建到妙手采集箱${newId ? `（ID ${newId}）` : ''}` : '商品修改已保存到妙手采集箱');
    editEl('liveEditor').classList.add('hidden');
    window.dispatchEvent(new CustomEvent('live-content-saved'));
    liveState.page = 1; editEl('liveKeyword').value = '';
    await loadLiveProducts();
    if (!creating && liveState.selected) await checkLiveReadiness();
  } catch (error) { editorError(`保存失败：${error.message}`); }
  finally { button.disabled = false; button.textContent = '保存到妙手采集箱'; }
}

function applySkuBatch() {
  setEditorStage(5);
  const fields = [
    ['price', 'batchSkuPrice'], ['stock', 'batchSkuStock'], ['weight', 'batchSkuWeight'],
    ['length', 'batchSkuLength'], ['width', 'batchSkuWidth'], ['height', 'batchSkuHeight']
  ].filter(([, id]) => editEl(id).value.trim() !== '');
  if (!fields.length) { showToast('请至少填写一个需要批量更新的字段'); return; }
  const rows = [...editEl('editSkuRows').querySelectorAll('.live-sku-row')];
  if (!rows.length) { showToast('当前没有可填写的 SKU'); return; }
  rows.forEach((row) => fields.forEach(([field, id]) => {
    const target = row.querySelector(`[data-field="${field}"]`);
    if (target) target.value = editEl(id).value.trim();
  }));
  showToast(`已批量填写 ${rows.length} 个 SKU，保存商品后生效`);
}

editEl('liveNew').addEventListener('click', () => openEditor('new'));
editEl('liveEdit').addEventListener('click', () => openEditor('edit'));
const closeLiveEditor = () => { editEl('liveEditor').classList.add('hidden'); window.dispatchEvent(new CustomEvent('live-editor-closed')); };
editEl('liveEditorClose').addEventListener('click', closeLiveEditor);
editEl('editCancel').addEventListener('click', closeLiveEditor);
editEl('editShop').addEventListener('change', loadEditorLogistics);
editEl('editFreight').addEventListener('change', rememberCurrentShopConfig);
editEl('editWarehouse').addEventListener('change', rememberCurrentShopConfig);
editEl('editSite').addEventListener('change', loadEditorLogistics);
editEl('categoryTrigger').addEventListener('click', toggleCategoryPicker);
document.addEventListener('click', (event) => {
  const field = editEl('categoryField');
  if (field && !field.contains(event.target)) closeCategoryPicker();
});
editEl('editImages').addEventListener('input', renderProductImagePreview);
window.addEventListener('open-live-editor-request', async (event) => {
  const requested = event.detail?.stage;
  const stage = [2, 3, 4, 5].includes(requested) ? requested : 3;
  if (editEl('liveEditor').classList.contains('hidden')) await openEditor('edit');
  scrollEditorStage(stage);
});
editEl('editTitle').addEventListener('focus', () => setEditorStage(4));
editEl('editEnglishTitle').addEventListener('focus', () => setEditorStage(4));
editEl('editTitle').addEventListener('input', updateTitleCounts);
editEl('editEnglishTitle').addEventListener('input', updateTitleCounts);
editEl('polishChineseTitle').addEventListener('click', () => showTitlePolish('zh'));
editEl('polishEnglishTitle').addEventListener('click', () => showTitlePolish('en'));
editEl('aiTitlePolishDraft').addEventListener('input', updateTitlePolishCount);
editEl('imageAiEditWhiteBg').addEventListener('click', () => {
  editEl('imageAiEditInstruction').value = '以 <image1> 中的商品为唯一主体，仅将背景替换为干净纯白的电商背景。必须保持商品款式、颜色、材质、细节、标识、标签和现有文字完全不变。不要添加其他物品、文字或水印。';
});
editEl('imageAiEditLifestyle').addEventListener('click', () => {
  editEl('imageAiEditInstruction').value = '以 <image1> 中的商品为唯一主体，仅将背景替换为适合该商品的简洁生活场景。必须保持商品款式、颜色、材质、细节、标识、标签和现有文字完全不变。不要添加其他商品、文字或水印。';
});
editEl('imageAiEditSubmit').addEventListener('click', () => submitImageAiEdit('api'));
editEl('imageAiEditLocalSubmit').addEventListener('click', () => submitImageAiEdit('local'));
editEl('imageAiEditDownload').addEventListener('click', downloadImageAiResult);
editEl('discardTitlePolish').addEventListener('click', () => { titlePolishTarget = ''; editEl('aiTitlePolish').classList.add('hidden'); });
editEl('applyTitlePolish').addEventListener('click', applyTitlePolish);
editEl('productImageUrl').addEventListener('focus', () => setEditorStage(4));
editEl('editDescription').addEventListener('focus', () => setEditorStage(3));
editEl('descriptionImageUrl').addEventListener('focus', () => setEditorStage(4));
editEl('productImageUrl').addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); addProductImage(); } });
editEl('addProductImage').addEventListener('click', addProductImage);
editEl('descriptionImageUrl').addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); addDescriptionImage(); } });
editEl('addDescriptionImage').addEventListener('click', addDescriptionImage);
editEl('uploadDescriptionImage').addEventListener('click', () => editEl('descriptionImageLocal').click());
editEl('descriptionImageLocal').addEventListener('change', (event) => { addLocalDescriptionImages(event.target.files); event.target.value = ''; });
editEl('toggleSkuBatch').addEventListener('click', () => {
  setEditorStage(5);
  const panel = editEl('skuBatchPanel'); panel.classList.toggle('hidden');
  editEl('toggleSkuBatch').textContent = panel.classList.contains('hidden') ? '批量填写' : '收起批量填写';
});
editEl('applySkuBatch').addEventListener('click', applySkuBatch);
editEl('addSkuSpec').addEventListener('click', () => { setEditorStage(5); addSkuSpec(); });
editEl('editSkuRows').addEventListener('focusin', () => setEditorStage(5));
editEl('submitShopClaim').addEventListener('click', async () => {
  editorError('');
  claimStatus('');
  if (liveState.mode !== 'live') {
    const message = '当前服务为只读模式，无法提交店铺认领。请将本机服务的 APP_MODE 设为 live 并重启。';
    claimStatus(message);
    showToast(message);
    return;
  }
  if (editorState.kind !== 'edit' || !editorState.detailId) {
    claimStatus('新建商品请先保存到妙手采集箱，再进行店铺认领。');
    return;
  }
  if (!editorState.claimedShopIds.length) { claimStatus('请至少选择一个目标店铺'); return; }
  const button = editEl('submitShopClaim');
  button.disabled = true; button.textContent = '正在提交到妙手…';
  claimStatus('正在提交店铺认领到妙手…', 'info');
  try {
    const response = await fetch('/api/item/claim', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        detailId: editorState.detailId,
        version: editorState.version,
        shopIds: editorState.claimedShopIds.map(Number),
        confirmation: 'CLAIM_MIAOSHOU_SHOPS'
      })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '妙手店铺认领失败');
    const refreshed = result.data?.item;
    const version = result.data?.version;
    if (!refreshed || typeof version !== 'string') throw new Error('妙手认领成功，但未返回最新商品资料');
    const confirmed = new Set((result.data?.claimedShopIds || []).map(String));
    const requested = editorState.claimedShopIds.map(String);
    if (requested.some((shopId) => !confirmed.has(shopId))) {
      throw new Error('妙手未确认全部店铺认领，请刷新商品后重试');
    }
    editorState.item = cloneItem(refreshed);
    editorState.version = version;
    editorState.claimSubmitted = true;
    fillEditor(editorState.item);
    editorState.claimSubmitted = true;
    claimStatus(`妙手已确认：${requested.length} 个店铺认领成功`, 'success');
    showToast(`妙手已确认：${requested.length} 个店铺认领成功`);
    scrollEditorStage(3);
  } catch (error) {
    const message = `店铺认领提交失败：${error.message}`;
    claimStatus(message);
    showToast(message);
  }
  finally { button.disabled = false; button.textContent = '提交店铺认领到妙手'; }
});
editEl('liveItemForm').addEventListener('submit', saveEditor);
// Start loading the category tree as soon as the workbench opens.  The picker
// stays closed, but most users will have the hierarchy ready before editing.
window.setTimeout(() => { loadCategoryTree(); }, 0);
