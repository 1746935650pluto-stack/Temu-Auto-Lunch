const test=require('node:test');const assert=require('node:assert/strict');
const {empty,fingerprint,mergeValues}=require('../size-reference.js');
const fixture=()=>[{sizeTemplateId:'77',elementIds:[1,2],sizeItems:[{sizeName:'S',elementValueList:[{elementId:1,elementValue:'56',retain:true},{elementId:2,elementValue:''}]}]}];
test('preview application preserves all existing values, metadata and source object',()=>{
 const charts=fixture(),before=structuredClone(charts);
 const result=mergeValues(charts,0,[{sizeName:'S',elementId:1,value:'999'},{sizeName:'S',elementId:2,value:'42'},{sizeName:'M',elementId:1,value:'60'}]);
 assert.deepEqual(charts,before);assert.deepEqual(result[0].sizeItems[0].elementValueList[0],before[0].sizeItems[0].elementValueList[0]);
 assert.equal(result[0].sizeItems[0].elementValueList[1].elementValue,'42');assert.equal(result[0].sizeItems[1].sizeName,'M');assert.equal(result[0].sizeTemplateId,'77');
});
test('user-edited reference cells reject invalid numeric values',()=>{
 for(const value of ['',0,-1,'NaN','1.234','1001','12cm'])assert.throws(()=>mergeValues(fixture(),0,[{sizeName:'S',elementId:2,value}]));
 assert.throws(()=>mergeValues(fixture(),0,[{sizeName:'S',elementId:9,value:'42'}]));
});
test('account, shop, title, category, size cells and specs invalidate preview fingerprint',()=>{
 const base={accountId:5,shopId:9,item:{cid:'123',title:'shorts',sizeCharts:fixture(),saleAttributes:[]}};
 for(const mutate of [c=>c.accountId++,c=>c.shopId++,c=>c.item.title='other',c=>c.item.cid='124',c=>c.item.sizeCharts=[],c=>c.item.saleAttributes.push({name:'size'})]){
  const copy=structuredClone(base);mutate(copy);assert.notEqual(fingerprint(copy),fingerprint(base));
 }
 assert.equal(empty(0),false);assert.equal(empty('0'),false);assert.equal(empty('  '),true);
});

function uiFixture() {
 const fs=require('node:fs'),vm=require('node:vm');
 class Element {
  constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.value='';this.events={};this.classList={toggle(){}};}
  append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}
  addEventListener(event,fn){this.events[event]=fn;}setAttribute(){}
  querySelectorAll(selector){const all=this.children.flatMap(c=>[c,...c.querySelectorAll(selector)]);return selector==='[data-size-column]'?all.filter(c=>c.dataset.sizeColumn!=null):all;}
 }
 const fields=Object.fromEntries(['sizeReferenceStatus','sizeReferenceControls','sizeReferencePreview','sizeReferenceReload','editShop','editCid'].map(id=>[id,new Element()]));
 const all=()=>Object.values(fields).flatMap(c=>[c,...c.querySelectorAll('*')]);
 const document={createElement:tag=>new Element(tag),getElementById:id=>fields[id]||all().find(n=>n.id===id)};
 const token={},context={accountId:5,shopId:9,token,item:{cid:'123',title:'shorts',saleAttributes:[],sizeCharts:fixture()}};
 const pending=[],requests=[],applied=[];
 const window={document,sizeReferenceContext:()=>context,applySizeReferenceCharts:charts=>applied.push(charts),addEventListener(){}};
 vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'..','size-reference.js'),'utf8'),{window,document,URLSearchParams,AbortController,setTimeout,clearTimeout,
  fetch:(url,options)=>{requests.push({url,options});return new Promise(resolve=>pending.push(data=>resolve({ok:true,json:async()=>({data})})));}});
 return {window,fields,context,pending,requests,applied,all,button:text=>all().find(n=>n.tag==='button'&&n.textContent===text)};
}
test('late template fetch preserves manual column, name and toggle edits',async()=>{
 const f=uiFixture(),loading=f.window.sizeReferenceEditor.load();
 const inputs=f.fields.sizeReferenceControls.querySelectorAll('[data-size-column]');inputs[0].value='手动腰围';
 f.all().find(n=>n.id==='sizeReferenceName').value='手动模板';f.all().find(n=>n.id==='sizeReferenceEnabled').checked=true;
 f.pending.shift()({template:{revision:7,name:'old',enabled:false,columns:[{elementId:1,name:'old1'},{elementId:2,name:'old2'}]}});await loading;
 assert.equal(inputs[0].value,'手动腰围');assert.equal(f.all().find(n=>n.id==='sizeReferenceName').value,'手动模板');
});
test('late AI result cannot populate or apply to another account or edited item',async()=>{
 const f=uiFixture(),loading=f.window.sizeReferenceEditor.load();f.pending.shift()({template:null});await loading;
 const inputs=f.fields.sizeReferenceControls.querySelectorAll('[data-size-column]');inputs[0].value='腰围全围';inputs[1].value='裤长';
 const generating=f.button('AI 补齐缺失尺寸').events.click();
 assert.equal(f.requests[1].options.method,'POST');f.context.accountId=6;
 f.pending.shift()({generated:1,values:[{sizeName:'S',elementId:2,value:'42'}]});await generating;
 assert.equal(f.fields.sizeReferencePreview.children.length,0);assert.equal(f.applied.length,0);
});
test('generated draft is editable and applies locally without a MiaoShou save request',async()=>{
 const f=uiFixture(),loading=f.window.sizeReferenceEditor.load();f.pending.shift()({template:null});await loading;
 const inputs=f.fields.sizeReferenceControls.querySelectorAll('[data-size-column]');inputs[0].value='腰围全围';inputs[1].value='裤长';
 const generating=f.button('AI 补齐缺失尺寸').events.click();f.pending.shift()({generated:1,values:[{sizeName:'S',elementId:2,value:'42'}]});await generating;
 const cell=f.fields.sizeReferencePreview.querySelectorAll('*').find(n=>n.tag==='input');cell.value='44';cell.events.input();
 f.button('应用到当前尺码表').events.click();assert.equal(f.applied[0][0].sizeItems[0].elementValueList[1].elementValue,'44');
 assert.equal(f.applied[0][0].sizeItems[0].elementValueList[0].elementValue,'56');assert.equal(f.requests.length,2);
 assert.match(f.fields.sizeReferenceStatus.textContent,/本页编辑稿/);
});
