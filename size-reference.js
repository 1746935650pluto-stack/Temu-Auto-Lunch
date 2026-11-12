(function(root) {
  'use strict';
  const clone = value => JSON.parse(JSON.stringify(value));
  const empty = value => value == null || (typeof value === 'string' && !value.trim());
  const fingerprint = context => JSON.stringify([context.accountId, context.shopId, context.item]);
  function mergeValues(charts, index, values) {
    const next = clone(charts), chart = next[index];
    if (!chart) throw new Error('尺码表已变化，请重新读取');
    chart.sizeItems ||= [];
    values.forEach(value => {
      const number = Number(value.value);
      if (!String(value.value).trim() || !Number.isFinite(number) || number <= 0 || number > 1000
          || !/^\d+(?:\.\d{1,2})?$/.test(String(value.value))) throw new Error('参考尺寸应为 0–1000 cm 的正数，最多两位小数');
      if (!chart.elementIds.includes(value.elementId)) throw new Error('尺寸列已变化');
      let row = chart.sizeItems.find(row => row.sizeName === value.sizeName);
      if (!row) { row = {sizeName:value.sizeName,elementValueList:[],groupValueList:[],groupIdAndValueMap:{}}; chart.sizeItems.push(row); }
      row.elementValueList ||= [];
      let cell = row.elementValueList.find(cell => cell.elementId === value.elementId);
      if (!cell) row.elementValueList.push({elementId:value.elementId,elementValue:String(value.value)});
      else if (empty(cell.elementValue)) cell.elementValue = String(value.value);
    });
    return next;
  }
  const api = {empty, fingerprint, mergeValues};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (!root.document) return;
  const el = id => document.getElementById(id);
  const state = {sequence:0, index:0, revision:0, busy:false, context:null, preview:null};
  const node = (tag, text, className) => { const n=document.createElement(tag); if(text!=null)n.textContent=text; if(className)n.className=className; return n; };
  function status(text, error=false) { const n=el('sizeReferenceStatus'); n.textContent=text; n.classList.toggle('error',error); }
  function same(context) {
    try { const current=root.sizeReferenceContext(); return current.token===context.token && fingerprint(current)===fingerprint(context); }
    catch { return false; }
  }
  function columns() {
    return [...el('sizeReferenceControls').querySelectorAll('[data-size-column]')].map(n => ({elementId:Number(n.dataset.sizeColumn),name:n.value.trim(),unit:'cm'}));
  }
  function jsonColumns() { return JSON.stringify(columns()); }
  function label(text, control) { const n=node('label',text); n.append(control); return n; }
  function button(text, action, primary=false) { const n=node('button',text,`button ${primary?'primary':'subtle'}`);n.type='button';n.addEventListener('click',action);return n; }
  async function request(path, payload, context, timeout=105000) {
    const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),timeout);
    try {
      const response=await fetch(path,{method:payload?'POST':'GET',headers:{'Content-Type':'application/json','X-Miaoshou-Account-Id':String(context.accountId)},signal:controller.signal,...(payload?{body:JSON.stringify(payload)}:{})});
      const data=await response.json(); if(!response.ok)throw new Error(data.error||'尺码参考请求失败');return data.data;
    } catch(error) { if(controller.signal.aborted)throw new Error('尺码参考请求超时，请重新读取后检查结果');throw error; }
    finally {clearTimeout(timer);}
  }
  function reset() {
    state.sequence++;state.context=null;state.preview=null;state.busy=false;state.revision=0;
    el('sizeReferenceControls').replaceChildren();el('sizeReferencePreview').replaceChildren();
    status('读取商品尺码表后，可核对尺寸列名并生成通用参考值。');
  }
  async function load(index=0) {
    reset();state.index=index;const seq=state.sequence;
    try {
      const current=root.sizeReferenceContext(), context={...current,item:clone(current.item)}, charts=context.item.sizeCharts||[];
      if(!charts.length)throw new Error('此商品未选择尺码模板。请先在妙手选择模板并重新读取商品，模板中的尺寸无需预先填满。');
      if(!context.shopId)throw new Error('请先选择当前配置店铺');
      state.context=context;const chart=charts[index];
      if(!chart || !Array.isArray(chart.elementIds) || !chart.elementIds.length)throw new Error('此尺码模板尚未选择尺寸列，请先在妙手选择腰围、裤长等参数');
      const holder=el('sizeReferenceControls'),fields=node('div',null,'size-reference-fields');holder.append(fields);
      if(charts.length>1) {
        const select=node('select');charts.forEach((c,i)=>{const o=node('option',`尺码表 ${i+1}`);o.value=i;select.append(o);});select.value=index;
        select.addEventListener('change',()=>load(Number(select.value)));fields.append(label('当前尺码表',select));
      }
      chart.elementIds.forEach((id,i)=>{
        const input=node('input');input.dataset.sizeColumn=id;input.maxLength=40;input.placeholder=i===0?'例如：腰围全围':'例如：裤长';
        input.addEventListener('input',()=>{state.preview=null;el('sizeReferencePreview').replaceChildren();});
        fields.append(label(`第 ${i+1} 列名称（cm）`,input));
      });
      const name=node('input');name.id='sizeReferenceName';name.maxLength=80;name.value='通用参考尺码';fields.append(label('本地模板名称',name));
      const toggle=node('input');toggle.type='checkbox';toggle.id='sizeReferenceEnabled';toggle.checked=false;
      const toggleLabel=node('label');toggleLabel.append(toggle,node('span','同店铺同类目任务自动补齐缺失尺寸')); toggleLabel.className='size-reference-toggle';fields.append(toggleLabel);
      const actions=node('div',null,'size-reference-actions');actions.append(button('AI 补齐缺失尺寸',generate,true),button('保存列名与自动补齐设置',save));holder.append(actions);
      status('请按妙手尺码表的列顺序核对名称。单位为 cm；腰围全围、平铺半围和裤内长应分别注明。');
      const before=JSON.stringify([jsonColumns(),name.value,toggle.checked]);const query=new URLSearchParams({shopId:context.shopId,cid:context.item.cid,source:chart.sizeTemplateSource,templateId:chart.sizeTemplateId});
      const result=await request(`/api/size-reference/template?${query}`,null,context,12000);
      if(seq!==state.sequence || !same(context))return;
      const binding=result.template;state.revision=binding?.revision||0;
      if(before!==JSON.stringify([jsonColumns(),name.value,toggle.checked]))return;
      if(binding) {
        const inputs=[...holder.querySelectorAll('[data-size-column]')];
        if(binding.columns.length!==inputs.length || binding.columns.some((c,i)=>c.elementId!==Number(inputs[i].dataset.sizeColumn))) {
          status('模板的尺寸列已变化，请重新核对列名并保存。',true);return;
        }
        inputs.forEach((n,i)=>n.value=binding.columns[i].name);name.value=binding.name;toggle.checked=binding.enabled;
        status(binding.enabled?'已加载本地模板；后续匹配任务自动补齐缺失尺寸，已有数值保留。':'已加载本地模板；当前仅手动生成参考尺寸。');
      }
    } catch(error) { if(seq===state.sequence)status(error.message,true); }
  }
  function capture() {
    const context=state.context;if(!context || !same(context))throw new Error('商品、账号、店铺或尺码表已变化，请点击“读取当前尺码表”');
    const cols=columns();if(cols.some(c=>!c.name))throw new Error('请先核对并填写每一列的名称');
    return {context,cols,columnText:JSON.stringify(cols),seq:state.sequence,index:state.index};
  }
  async function generate() {
    if(state.busy)return;
    let captured;
    try {
      captured=capture();state.busy=true;status('正在生成通用参考尺寸，已有数值保留…');
      const {context,cols,index}=captured;
      const result=await request('/api/ai/size-reference',{item:context.item,chartIndex:index,columns:cols},context);
      if(captured.seq!==state.sequence || !same(context) || captured.columnText!==jsonColumns())return;
      state.preview={...captured,values:result.values};renderPreview();
      status(result.generated?`已生成 ${result.generated} 个参考尺寸（非实测）。可修改蓝色单元格，再应用到当前编辑稿。`:'已有尺寸完整，无需调用 AI。');
    } catch(error) {if(!captured || captured.seq===state.sequence)status(error.message,true);}
    finally {if(!captured || captured.seq===state.sequence)state.busy=false;}
  }
  function renderPreview() {
    const preview=state.preview, holder=el('sizeReferencePreview');holder.replaceChildren();
    const charts=mergeValues(preview.context.item.sizeCharts,preview.index,preview.values),chart=charts[preview.index];
    const table=node('table'),head=node('tr');head.append(node('th','尺码'));preview.cols.forEach(c=>head.append(node('th',`${c.name}（cm）`)));table.append(head);
    (chart.sizeItems||[]).forEach(row=>{
      const tr=node('tr');tr.append(node('th',row.sizeName));
      preview.cols.forEach(col=>{
        const td=node('td'),generated=preview.values.find(v=>v.sizeName===row.sizeName&&v.elementId===col.elementId);
        const value=row.elementValueList?.find(v=>v.elementId===col.elementId)?.elementValue;
        if(generated) {const input=node('input');input.type='number';input.min='0.01';input.max='1000';input.step='0.01';input.value=generated.value;input.setAttribute('aria-label',`${row.sizeName} ${col.name}`);input.addEventListener('input',()=>generated.value=input.value);td.append(input,node('small','AI 通用参考'));}
        else td.append(node('span',empty(value)?'—':String(value)),node('small','已有数值'));
        tr.append(td);
      });table.append(tr);
    });holder.append(table);
    const actions=node('div',null,'size-reference-actions');actions.append(button('应用到当前尺码表',apply,true));holder.append(actions);
  }
  function apply() {
    try {
      const preview=state.preview;if(!preview || !same(preview.context) || preview.columnText!==jsonColumns())throw new Error('商品或列名已变化，请重新生成');
      const charts=mergeValues(preview.context.item.sizeCharts,preview.index,preview.values);
      root.applySizeReferenceCharts(charts);state.preview=null;el('sizeReferencePreview').replaceChildren();state.context=root.sizeReferenceContext();
      status('已应用到本页编辑稿；点击“保存到妙手采集箱”后才写入商品。');
    } catch(error) {status(error.message,true);}
  }
  async function save() {
    if(state.busy)return;let captured;
    try {
      captured=capture();state.busy=true;
      const {context,cols,index}=captured;
      const result=await request('/api/size-reference/template',{item:context.item,chartIndex:index,columns:cols,shopId:context.shopId,name:el('sizeReferenceName').value.trim(),enabled:el('sizeReferenceEnabled').checked,revision:state.revision,confirmation:'SAVE_SIZE_REFERENCE_TEMPLATE'},context,12000);
      if(captured.seq!==state.sequence || !same(context) || captured.columnText!==jsonColumns())return;
      state.revision=result.template.revision;
      status(result.template.enabled?'已保存到本机；后续同账号、店铺、类目和模板的任务将自动补齐缺失尺寸。':'已保存到本机；自动补齐已关闭。');
    } catch(error) {if(!captured || captured.seq===state.sequence)status(error.message,true);}
    finally {if(!captured || captured.seq===state.sequence)state.busy=false;}
  }
  root.sizeReferenceEditor={reset,load};el('sizeReferenceReload')?.addEventListener('click',()=>load());
  ['editShop','editCid'].forEach(id=>el(id)?.addEventListener('change',reset));
  root.addEventListener('collection-account-changed',reset);
})(typeof window!=='undefined'?window:globalThis);
