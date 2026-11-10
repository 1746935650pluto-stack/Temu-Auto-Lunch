const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createDocument}=require('./dom_fixture');
function setup(){const document=createDocument(),window={};document.createElementNS=(ns,tag)=>document.createElement(tag);vm.runInNewContext(fs.readFileSync(path.join(__dirname,'..','proportion-chart.js'),'utf8'),{window,document});return {document,chart:window.proportionCharts};}
test('percentages use selected values, retain zero rows and round to exactly 100 percent',()=>{
  const {chart}=setup(),result=chart.shares([1,1,1,0].map((value,key)=>({key,value})));
  assert.equal(result.total,3);assert.equal(result.rows.map(r=>Number.parseFloat(r.percentage)*100).reduce((a,b)=>a+b,0),10000);
  assert.deepEqual(Array.from(result.rows,r=>r.percentage),['33.34%','33.33%','33.33%','0.00%']);
});
test('zero data creates no sector and a single category renders a full circle with safe geometry',()=>{
  const {chart,document}=setup(),root=document.createElement('div');
  chart.render(root,{entries:[{key:'zero',label:'Zero',value:0}],label:'订单数',description:'test'});
  assert.match(root.textContent,/暂无占比/);assert.equal(root.querySelectorAll('g').length,0);assert.match(root.textContent,/0.00%/);
  chart.render(root,{entries:[{key:'one',label:'Only',value:4}],label:'订单数',description:'test'});
  assert.match(root.textContent,/100.00%/);assert.equal(root.querySelectorAll('g').length,1);
  assert.equal(root.querySelectorAll('g')[0].querySelectorAll('circle').length,1);
  assert.doesNotMatch(JSON.stringify(root.querySelectorAll('path').map(n=>n.attrs)),/NaN|Infinity/);
});
test('all stores are accessible and names remain text while sector keyboard and legend clicks select exact identity',async()=>{
  const {chart,document}=setup(),root=document.createElement('div'),selected=[];
  const entries=Array.from({length:30},(_,index)=>({key:String(index),label:index?'Store '+index:'<img onerror=x>',value:index+1}));
  chart.render(root,{entries,label:'订单数',description:'stores',onSelect:row=>selected.push(row.key)});
  assert.equal(root.querySelectorAll('button').length,30);assert.match(root.textContent,/<img onerror=x>/);assert.equal(root.querySelectorAll('img').length,0);
  await root.querySelectorAll('g')[0].dispatch('keydown',{key:'Enter'});await root.querySelectorAll('button')[29].dispatch('click');assert.deepEqual(selected,['0','29']);
});
test('nonfinite and negative values never fabricate shares or invalid arcs',()=>{
  const {chart,document}=setup(),root=document.createElement('div');
  const r=chart.render(root,{entries:[NaN,Infinity,-2,2].map((value,key)=>({key,label:String(key),value})),label:'件数',description:'test'});
  assert.equal(r.total,2);assert.equal(r.rows[3].percentage,'100.00%');assert.equal(root.querySelectorAll('g').length,1);
});
test('small positive shares are distinguished from zero with two decimal precision',()=>{
  const {chart}=setup();
  assert.equal(chart.shares([{value:2},{value:6018}]).rows[0].percentage,'0.03%');
  assert.equal(chart.shares([{value:1},{value:1000000}]).rows[0].percentage,'<0.01%');
  assert.equal(chart.shares([{value:0},{value:1000000}]).rows[0].percentage,'0.00%');
});
